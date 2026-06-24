"""REST client for the Topaz fabric health service.

Provides the same interface as TopazClient (gRPC) but uses the Topaz REST API
at https://topaz.internal.together.ai/. Useful from environments where the
gRPC endpoint (localhost:50051) is unreachable — dev containers, CI, remote
agents, etc.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 30.0


class TopazRestClient:
    """Topaz client that speaks REST instead of gRPC.

    Implements the same method signatures as TopazClient so it can be used
    as a drop-in replacement via ``_get_topaz_client()`` in cli.py.
    """

    def __init__(
        self,
        base_url: str,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = httpx.Client(
            base_url=self._base_url,
            timeout=timeout,
            verify=False,  # internal service, self-signed certs
        )

    def __enter__(self) -> TopazRestClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    # ------------------------------------------------------------------
    # AZ discovery (no gRPC equivalent)
    # ------------------------------------------------------------------

    def list_availability_zones(self) -> list[dict[str, Any]]:
        """Fetch all known AZs from ``GET /api/az``.

        Returns a list of dicts, each containing at least ``id`` and ``name``.
        On failure returns a single-element list with an error dict.
        """
        try:
            resp = self._client.get("/api/az")
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, list):
                return data
            return [data] if isinstance(data, dict) else []
        except httpx.HTTPError as exc:
            logger.warning("Topaz REST /api/az failed: %s", exc)
            return [{"ok": False, "error": f"REST /api/az failed: {exc}"}]

    def discover_az_map(self) -> dict[str, str]:
        """Build a site-name -> AZ-ID mapping from the REST API.

        Uses the ``name`` field as the site key and ``id`` as the AZ
        identifier.  Returns an empty dict on failure.
        """
        azs = self.list_availability_zones()
        mapping: dict[str, str] = {}
        for az in azs:
            if not isinstance(az, dict) or az.get("ok") is False:
                continue
            az_id = az.get("id") or az.get("azId") or ""
            name = az.get("name") or ""
            if az_id and name:
                mapping[name] = az_id
        return mapping

    # ------------------------------------------------------------------
    # Fabric health
    # ------------------------------------------------------------------

    def get_fabric_health(self, az_id: str) -> dict[str, Any]:
        try:
            resp = self._client.get("/api/health", params={"azId": az_id})
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as exc:
            return _rest_error_dict("get_fabric_health", exc)

    # ------------------------------------------------------------------
    # Switches — derived from topology data
    # ------------------------------------------------------------------

    def list_switches(
        self,
        az_id: str,
        errors_only: bool = False,
    ) -> dict[str, Any]:
        try:
            topo = self._fetch_topology(az_id)
            if topo.get("ok") is False:
                return topo

            nodes = topo.get("nodes") or topo.get("switches") or []
            switches = [n for n in nodes if _is_switch(n)]

            if errors_only:
                switches = [s for s in switches if (s.get("total_errors") or 0) > 0]

            return {
                "switches": switches,
                "total_count": len(switches),
            }
        except httpx.HTTPError as exc:
            return _rest_error_dict("list_switches", exc)

    # ------------------------------------------------------------------
    # Port counters — derived from topology links
    # ------------------------------------------------------------------

    def list_port_counters(
        self,
        az_id: str,
        errors_only: bool = False,
        guid_filter: str | None = None,
    ) -> dict[str, Any]:
        try:
            topo = self._fetch_topology(az_id)
            if topo.get("ok") is False:
                return topo

            links = topo.get("links") or []
            counters: list[dict[str, Any]] = []
            for link in links:
                if guid_filter and guid_filter not in str(link):
                    continue
                if errors_only and not (link.get("total_errors") or 0):
                    continue
                counters.append(link)

            return {
                "port_counters": counters,
                "total_count": len(counters),
            }
        except httpx.HTTPError as exc:
            return _rest_error_dict(
                "list_port_counters",
                exc,
            )

    # ------------------------------------------------------------------
    # Cables — not directly available in REST API
    # ------------------------------------------------------------------

    def list_cables(
        self,
        az_id: str,
        alarms_only: bool = False,
    ) -> dict[str, Any]:
        return {
            "ok": False,
            "error": (
                "Cable data is not available via the Topaz REST API. "
                "Use TOPAZ_TRANSPORT=grpc or upload an ibdiagnet tarball."
            ),
            "cables": [],
            "total_count": 0,
        }

    # ------------------------------------------------------------------
    # Upload ibdiagnet — not supported via REST
    # ------------------------------------------------------------------

    def upload_ibdiagnet(
        self,
        az_id: str,
        tarball_data: bytes,
        filename: str = "",
    ) -> dict[str, Any]:
        return {
            "ok": False,
            "error": (
                "ibdiagnet upload is not available via the Topaz REST API. "
                "Use TOPAZ_TRANSPORT=grpc for upload functionality."
            ),
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _fetch_topology(self, az_id: str) -> dict[str, Any]:
        resp = self._client.get("/api/topology/server", params={"azId": az_id})
        resp.raise_for_status()
        return resp.json()


def _is_switch(node: dict) -> bool:
    """Heuristic: a topology node is a switch if its type field says so."""
    node_type = str(node.get("type") or node.get("nodeType") or "").lower()
    return "switch" in node_type


def _rest_error_dict(method: str, exc: object) -> dict[str, Any]:
    status = None
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
    return {
        "ok": False,
        "error": f"Topaz REST error in {method}",
        "http_status": status,
        "details": str(exc),
    }
