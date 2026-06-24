from __future__ import annotations

import contextlib
import json
import logging
import os
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
        # Lazily-resolved, cached verdict for whether this BMC's vendor is
        # validated to tolerate concurrent reads (see supports_parallel_reads).
        # None = not yet detected. Callers may set it directly to override.
        self._parallel_reads_ok: bool | None = None

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

    def supports_parallel_reads(self) -> bool:
        """Whether this BMC's vendor is validated to tolerate concurrent reads.

        Parallel read fan-out was validated only on robust, high-latency BMCs
        (NVIDIA HGX/OpenBmc — e.g. the B300 GPU-tray BMC, mcp-common PR #85).
        Fragile BMCs (Supermicro/AMI, etc.) must keep "1 concurrent request per
        BMC" for reads too, so they stay serial.

        The verdict is detected once from the Redfish service root / Systems
        collection and cached. It fails safe to ``False`` (serial) on any error
        or unrecognized vendor. Callers may set ``_parallel_reads_ok`` directly
        to bypass detection.
        """
        if self._parallel_reads_ok is None:
            self._parallel_reads_ok = self._detect_parallel_reads()
        return self._parallel_reads_ok

    def _detect_parallel_reads(self) -> bool:
        """Classify the BMC vendor to decide if concurrent reads are safe."""
        root, err = self.get_json_maybe(f"{self.base_url}/redfish/v1")
        vendor = _classify_root_vendor(root) if root and not err else "unknown"
        if vendor in ROBUST_PARALLEL_READ_VENDORS:
            logger.debug("parallel reads OK for %s (vendor=%s)", self.host, vendor)
            return True
        if vendor in _SERIAL_ONLY_ROOT_VENDORS:
            logger.debug("parallel reads disabled for %s (fragile vendor=%s)", self.host, vendor)
            return False
        # dell / hpe / unknown: a robust NVIDIA HGX baseboard BMC may still be
        # present (e.g. Dell XE9780 with an HGX B300 GPU tray exposes an
        # ``HGX_*`` Systems member). Probe the Systems collection for it.
        systems, serr = self.get_json_maybe(f"{self.base_url}/redfish/v1/Systems")
        if systems and not serr and _systems_have_hgx_member(systems):
            logger.debug("parallel reads OK for %s (HGX baseboard member present)", self.host)
            return True
        logger.debug("parallel reads disabled for %s (vendor=%s, no HGX member)", self.host, vendor)
        return False


PARALLEL_MEMBER_THRESHOLD = 5

DEFAULT_PARALLEL_WORKERS = 8
DEFAULT_PER_REQUEST_TIMEOUT_S = 15
DEFAULT_COLLECTION_TIMEOUT_S = 60

# Env knob: override the parallel GET worker count (see resolve_parallel_workers).
PARALLEL_WORKERS_ENV = "REDFISH_PARALLEL_WORKERS"

# BMC vendors validated to tolerate concurrent GETs. The NVIDIA HGX/OpenBmc
# stack (e.g. the B300 GPU-tray BMC) is high-latency and was explicitly
# validated for fan-out (mcp-common PR #85). Everything else stays serial.
ROBUST_PARALLEL_READ_VENDORS = frozenset({"nvidia"})

# Vendors known to be fragile under concurrency — never fan out reads. Listing
# them lets detection short-circuit without a second (Systems) probe.
_SERIAL_ONLY_ROOT_VENDORS = frozenset({"supermicro", "gigabyte"})

_HGX_SYSTEM_PREFIX = "HGX_"


def _classify_root_vendor(root: dict[str, Any]) -> str:
    """Classify BMC vendor from a ``/redfish/v1`` service-root document.

    Focused on the signals that matter for read concurrency. Returns a
    lowercase token: ``nvidia``, ``supermicro``, ``dell``, ``hpe``,
    ``gigabyte``, or ``unknown``.
    """
    oem = root.get("Oem")
    oem_keys = set(oem.keys()) if isinstance(oem, dict) else set()
    if "Nvidia" in oem_keys:
        return "nvidia"
    if "Supermicro" in oem_keys:
        return "supermicro"
    if "Dell" in oem_keys:
        return "dell"
    if "Hpe" in oem_keys or "HPE" in oem_keys:
        return "hpe"
    if "Gbt" in oem_keys or "Ami" in oem_keys:
        return "gigabyte"
    product = str(root.get("Product", "")).lower()
    vendor = str(root.get("Vendor", "")).lower()
    if any(s in product or s in vendor for s in ("nvidia", "openbmc")):
        return "nvidia"
    if "supermicro" in product:
        return "supermicro"
    if "idrac" in product or "dell" in product:
        return "dell"
    if "ami" in product or "ami" in vendor or "giga computing" in vendor:
        return "gigabyte"
    return "unknown"


def _systems_have_hgx_member(systems: dict[str, Any]) -> bool:
    """Return True if a Systems collection contains an ``HGX_*`` member.

    The NVIDIA HGX baseboard BMC exposes ``HGX_Baseboard_0`` (and similar)
    members; their presence is a reliable signal of a robust NVIDIA GPU-tray
    BMC even when the host BMC root reports a different vendor (e.g. Dell).
    """
    members = systems.get("Members")
    if not isinstance(members, list):
        return False
    for m in members:
        oid = m.get("@odata.id", "") if isinstance(m, dict) else ""
        if isinstance(oid, str) and oid.rstrip("/").rsplit("/", 1)[-1].startswith(
            _HGX_SYSTEM_PREFIX
        ):
            return True
    return False


def resolve_parallel_workers() -> int:
    """Resolve the parallel GET worker count from the environment.

    Reads ``REDFISH_PARALLEL_WORKERS`` at call time, defaulting to
    ``DEFAULT_PARALLEL_WORKERS``. A value ``<= 1`` disables parallelism entirely
    (callers fall back to serial reads even on robust BMCs). Invalid values fall
    back to the default.
    """
    raw = os.getenv(PARALLEL_WORKERS_ENV)
    if raw is None or not raw.strip():
        return DEFAULT_PARALLEL_WORKERS
    try:
        workers = int(raw.strip())
    except ValueError:
        logger.warning(
            "Invalid %s=%r; falling back to %d",
            PARALLEL_WORKERS_ENV,
            raw,
            DEFAULT_PARALLEL_WORKERS,
        )
        return DEFAULT_PARALLEL_WORKERS
    return max(workers, 1)


def parallel_get_json(
    client: RedfishClient,
    urls: list[str],
    *,
    max_workers: int | None = None,
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

    if max_workers is None:
        max_workers = resolve_parallel_workers()

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
    parallel_ok: bool | None = None,
) -> list[tuple[str, dict[str, Any] | None, str | None]]:
    """Fetch *urls* serially or in parallel depending on size and vendor.

    Reads are kept serial unless ALL of the following hold:

    1. The collection has more than *threshold* members (else thread-pool
       overhead isn't worth it).
    2. ``REDFISH_PARALLEL_WORKERS`` resolves to ``> 1`` (the knob can force
       fully serial reads on every BMC).
    3. The BMC vendor is validated to tolerate concurrent reads — *parallel_ok*
       when provided, otherwise ``client.supports_parallel_reads()``. Fragile
       BMCs (e.g. Supermicro) stay serial; robust NVIDIA HGX/OpenBmc BMCs fan
       out and keep the high-latency B300 latency win.
    """
    if len(urls) > threshold:
        max_workers = resolve_parallel_workers()
        if max_workers > 1:
            if parallel_ok is None:
                parallel_ok = client.supports_parallel_reads()
            if parallel_ok:
                return parallel_get_json(client, urls, max_workers=max_workers)
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
