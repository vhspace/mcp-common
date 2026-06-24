from __future__ import annotations

import contextlib
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from types import TracebackType
from typing import Any

import requests
from requests.auth import HTTPBasicAuth

logger = logging.getLogger("redfish_mcp.redfish")


@dataclass(frozen=True)
class RedfishEndpoint:
    base_url: str
    system_path: str

    @property
    def system_url(self) -> str:
        return f"{self.base_url}{self.system_path}"

    @property
    def reset_url(self) -> str:
        return f"{self.system_url}/Actions/ComputerSystem.Reset"


class RedfishClient:
    """Minimal Redfish client tailored for BMCs that can be… creative.

    Notes:
    - Redfish implementations may return non-JSON or 404 for seemingly-valid endpoints.
    - We keep methods small and predictable so higher-level code can implement heuristics.
    - Supports use as a context manager to ensure session cleanup.
    """

    def __init__(
        self, host: str, user: str, password: str, verify_tls: bool, timeout_s: int
    ) -> None:
        self.host = host
        self.base_url = f"https://{host}".rstrip("/")
        self.timeout_s = timeout_s

        self.session = requests.Session()
        self.session.auth = HTTPBasicAuth(user, password)
        self.session.headers.update({"Accept": "application/json"})
        self.session.verify = bool(verify_tls)

        if not self.session.verify:
            from mcp_common.logging import suppress_ssl_warnings

            suppress_ssl_warnings()

    def close(self) -> None:
        """Close the underlying HTTP session."""
        with contextlib.suppress(Exception):
            self.session.close()

    def __enter__(self) -> RedfishClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.close()

    def get_json(self, url: str) -> dict[str, Any]:
        logger.debug("GET %s", url)
        r = self.session.get(url, timeout=self.timeout_s)
        r.raise_for_status()
        return r.json()

    def get_json_maybe(self, url: str) -> tuple[dict[str, Any] | None, str | None]:
        """GET and attempt JSON parse; return (json, error_str)."""
        try:
            logger.debug("GET (maybe) %s", url)
            r = self.session.get(url, timeout=self.timeout_s)
            if r.status_code >= 400:
                logger.warning("GET %s returned %d", url, r.status_code)
                return None, f"{r.status_code} {r.text[:500]}"
            try:
                return r.json(), None
            except Exception:
                return None, f"non-json response (status {r.status_code}): {r.text[:200]}"
        except Exception as e:
            logger.warning("GET %s failed: %s", url, e)
            return None, str(e)

    def patch_json(self, url: str, payload: dict[str, Any]) -> requests.Response:
        logger.info("PATCH %s", url)
        return self.session.patch(
            url,
            headers={"Content-Type": "application/json"},
            data=json.dumps(payload),
            timeout=self.timeout_s,
        )

    def post_json(self, url: str, payload: dict[str, Any]) -> requests.Response:
        logger.info("POST %s", url)
        return self.session.post(
            url,
            headers={"Content-Type": "application/json"},
            data=json.dumps(payload),
            timeout=self.timeout_s,
        )

    def discover_system(self) -> RedfishEndpoint:
        """Discover the primary host system from /redfish/v1/Systems.

        When multiple members exist (e.g. Dell XE9780 with HGX baseboard),
        prefers ``System.Embedded.1`` over ``HGX_*`` GPU-tray members so
        that commands target the host server rather than the GPU baseboard.
        """
        data = self.get_json(f"{self.base_url}/redfish/v1/Systems")
        members = data.get("Members")
        if not isinstance(members, list) or not members:
            msg = "No Systems members found at /redfish/v1/Systems"
            raise RuntimeError(msg)

        chosen = _pick_host_system(members)
        odata_id = str(chosen["@odata.id"])
        if not odata_id.startswith("/"):
            odata_id = "/" + odata_id
        return RedfishEndpoint(base_url=self.base_url, system_path=odata_id)

    def discover_managers(self) -> list[dict[str, Any]]:
        """Return the Members list from ``/redfish/v1/Managers``."""
        data = self.get_json(f"{self.base_url}/redfish/v1/Managers")
        members = data.get("Members")
        if not isinstance(members, list):
            return []
        return members

    def discover_dell_manager(self) -> str | None:
        """Discover the Dell iDRAC manager path on multi-manager systems.

        Returns the ``@odata.id`` of ``iDRAC.Embedded.1`` if present, else None.
        """
        try:
            members = self.discover_managers()
        except Exception:
            return None
        for m in members:
            oid = m.get("@odata.id", "") if isinstance(m, dict) else ""
            if oid.rstrip("/").endswith("/iDRAC.Embedded.1"):
                return oid
        return None


PARALLEL_MEMBER_THRESHOLD = 5

DEFAULT_PARALLEL_WORKERS = 8
DEFAULT_PER_REQUEST_TIMEOUT_S = 15
DEFAULT_COLLECTION_TIMEOUT_S = 60


def parallel_get_json(
    client: RedfishClient,
    urls: list[str],
    *,
    max_workers: int = DEFAULT_PARALLEL_WORKERS,
    per_request_timeout_s: int = DEFAULT_PER_REQUEST_TIMEOUT_S,
    collection_timeout_s: int = DEFAULT_COLLECTION_TIMEOUT_S,
) -> list[tuple[str, dict[str, Any] | None, str | None]]:
    """Fetch multiple Redfish URLs concurrently via the client's session.

    Returns a list of ``(url, json_data_or_None, error_str_or_None)`` in the
    same order as *urls*.  Individual failures never abort the batch; the
    caller gets partial results.

    A per-request timeout and a wall-clock collection timeout both apply.
    Futures still running when the collection deadline expires are cancelled
    and returned as timeout errors.

    Thread safety: ``requests.Session`` is not officially thread-safe, but
    GET-only workloads with Basic Auth are safe in practice because urllib3's
    underlying connection pool is thread-safe and we never mutate session state.
    """
    if not urls:
        return []

    ordered: dict[str, tuple[dict[str, Any] | None, str | None]] = {u: (None, None) for u in urls}
    deadline = time.monotonic() + collection_timeout_s

    def _fetch(url: str) -> tuple[str, dict[str, Any] | None, str | None]:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return url, None, "collection timeout exceeded before request started"
        effective_timeout = min(per_request_timeout_s, remaining)
        try:
            logger.debug("parallel GET %s (timeout=%.1fs)", url, effective_timeout)
            r = client.session.get(url, timeout=effective_timeout)
            if r.status_code >= 400:
                return url, None, f"{r.status_code} {r.text[:500]}"
            try:
                return url, r.json(), None
            except Exception:
                return url, None, f"non-json response (status {r.status_code})"
        except Exception as e:
            return url, None, str(e)

    with ThreadPoolExecutor(max_workers=min(max_workers, len(urls))) as pool:
        futures = {pool.submit(_fetch, u): u for u in urls}
        for future in as_completed(futures):
            url = futures[future]
            try:
                _, data, err = future.result()
            except Exception as e:
                data, err = None, str(e)
            ordered[url] = (data, err)

    return [(u, *ordered[u]) for u in urls]


def batch_get_json(
    client: RedfishClient,
    urls: list[str],
    *,
    threshold: int = PARALLEL_MEMBER_THRESHOLD,
) -> list[tuple[str, dict[str, Any] | None, str | None]]:
    """Fetch *urls* serially or in parallel depending on collection size.

    Below *threshold* members, uses serial ``get_json_maybe`` to avoid
    thread-pool overhead.  Above it, delegates to ``parallel_get_json``.
    """
    if len(urls) > threshold:
        return parallel_get_json(client, urls)
    return [(u, *client.get_json_maybe(u)) for u in urls]


def _pick_host_system(members: list[dict[str, Any]]) -> dict[str, Any]:
    """Choose the host-server system member from a Systems collection.

    Priority:
      1. Member whose @odata.id ends with ``System.Embedded.1`` (Dell host)
      2. First member whose ID segment does *not* start with ``HGX_``
      3. ``Members[0]`` as last resort
    """
    if len(members) == 1:
        m = members[0]
        if not isinstance(m, dict) or "@odata.id" not in m:
            msg = "Unexpected Systems Members payload"
            raise RuntimeError(msg)
        return m

    for m in members:
        oid = m.get("@odata.id", "") if isinstance(m, dict) else ""
        if oid.rstrip("/").endswith("/System.Embedded.1"):
            return m

    for m in members:
        oid = m.get("@odata.id", "") if isinstance(m, dict) else ""
        segment = oid.rstrip("/").rsplit("/", 1)[-1]
        if not segment.startswith("HGX_"):
            return m

    m = members[0]
    if not isinstance(m, dict) or "@odata.id" not in m:
        msg = "Unexpected Systems Members payload"
        raise RuntimeError(msg)
    return m


_HGX_PREFIXES = ("HGX_", "ERoT_", "IRoT_")

MAX_HOST_CHASSIS = 10


def _iter_chassis_segments(members: list[dict[str, Any]]) -> list[tuple[dict[str, Any], str]]:
    """Return (member, segment) tuples from a chassis Members list."""
    result = []
    for m in members:
        if not isinstance(m, dict):
            continue
        oid = m.get("@odata.id", "")
        if not isinstance(oid, str) or not oid:
            continue
        segment = oid.rstrip("/").rsplit("/", 1)[-1]
        result.append((m, segment))
    return result


def filter_host_chassis(
    members: list[dict[str, Any]], *, max_chassis: int = MAX_HOST_CHASSIS
) -> list[dict[str, Any]]:
    """Filter a Chassis Members list to only host-relevant entries.

    Drops HGX/ERoT/IRoT satellite chassis and caps the result to *max_chassis*
    to guard against unexpectedly large collections on exotic hardware.
    """
    filtered = [
        m for m, seg in _iter_chassis_segments(members) if not seg.startswith(_HGX_PREFIXES)
    ]
    if len(filtered) < len(members):
        skipped = len(members) - len(filtered)
        logger.info(
            "Chassis filter: kept %d of %d members (skipped %d HGX/ERoT/IRoT)",
            len(filtered),
            len(members),
            skipped,
        )
    if len(filtered) > max_chassis:
        logger.warning("Chassis cap: truncating %d members to %d", len(filtered), max_chassis)
        filtered = filtered[:max_chassis]
    return filtered


_HGX_PCIE_PREFIXES = ("HGX_GPU_", "HGX_ConnectX_")

MAX_HGX_PCIE_CHASSIS = 20


def filter_hgx_pcie_chassis(
    members: list[dict[str, Any]], *, max_members: int = MAX_HGX_PCIE_CHASSIS
) -> list[dict[str, Any]]:
    """Return chassis members whose IDs start with HGX_GPU_ or HGX_ConnectX_.

    These are the B300 GPU-tray and NIC chassis that expose per-device
    PCIeDevices collections not visible under the host System resource.
    Excludes HGX_GPU_Baseboard (aggregate tray, not an individual GPU).
    Caps the result to *max_members* to guard against unexpectedly large collections.
    """
    result = [
        m
        for m, seg in _iter_chassis_segments(members)
        if seg.startswith(_HGX_PCIE_PREFIXES) and not seg.startswith("HGX_GPU_Baseboard")
    ]
    if len(result) > max_members:
        logger.warning(
            "HGX PCIe chassis cap: truncating %d members to %d", len(result), max_members
        )
        result = result[:max_members]
    return result


_HGX_MANAGER_PREFIXES = ("HGX_",)


def _pick_host_manager(members: list[dict[str, Any]]) -> dict[str, Any]:
    """Choose the host-server manager from a Managers collection.

    Priority:
      1. Member whose @odata.id ends with ``iDRAC.Embedded.1`` (Dell host)
      2. First member whose ID segment does *not* start with ``HGX_``
      3. ``Members[0]`` as last resort
    """
    if len(members) == 1:
        m = members[0]
        if not isinstance(m, dict) or "@odata.id" not in m:
            msg = "Unexpected Managers Members payload"
            raise RuntimeError(msg)
        return m

    for m in members:
        oid = m.get("@odata.id", "") if isinstance(m, dict) else ""
        if oid.rstrip("/").endswith("/iDRAC.Embedded.1"):
            return m

    for m in members:
        oid = m.get("@odata.id", "") if isinstance(m, dict) else ""
        segment = oid.rstrip("/").rsplit("/", 1)[-1]
        if not segment.startswith(_HGX_MANAGER_PREFIXES):
            return m

    m = members[0]
    if not isinstance(m, dict) or "@odata.id" not in m:
        msg = "Unexpected Managers Members payload"
        raise RuntimeError(msg)
    return m


def to_abs(base_url: str, odata_id: str) -> str:
    if odata_id.startswith(("http://", "https://")):
        return odata_id
    if not odata_id.startswith("/"):
        odata_id = "/" + odata_id
    return f"{base_url}{odata_id}"
