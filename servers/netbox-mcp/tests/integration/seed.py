"""Idempotent seeder for the NetBox integration simulator.

Builds a small, synthetic, realistic topology against a running NetBox via the
REST API (using ``requests``, already a project dependency). Safe to run
repeatedly: every object is looked up by a natural key first and only created
when missing, so re-running against a persisted DB volume is a cheap no-op.

Used by BOTH the pytest session fixture (``conftest.py`` imports
:func:`seed_netbox`) and the Makefile (``make sim-up`` runs this file as a
script, reading ``NETBOX_URL`` / ``NETBOX_TOKEN`` / ``VERIFY_SSL`` from the
environment), so the seed logic lives in exactly one place.
"""

from __future__ import annotations

import os
import sys
from typing import Any

import requests

# Fixed, NON-SECRET test fixtures. The token default must match the one in
# ``docker-compose.yaml`` / ``conftest.py`` (kept in sync by hand).
DEFAULT_TOKEN = "0123456789abcdef0123456789abcdef0123abcd"

TIMEOUT = 30


class SeedError(RuntimeError):
    """Raised when the simulator cannot be seeded."""


def _url(base_url: str, endpoint: str) -> str:
    return f"{base_url.rstrip('/')}/api/{endpoint.strip('/')}/"


def _get_or_create(
    session: requests.Session,
    base_url: str,
    endpoint: str,
    lookup: dict[str, Any],
    payload: dict[str, Any],
    *,
    verify_ssl: bool,
) -> dict[str, Any]:
    """Return an existing object matching *lookup*, or create it from *payload*."""
    resp = session.get(
        _url(base_url, endpoint), params={**lookup, "limit": 1}, verify=verify_ssl, timeout=TIMEOUT
    )
    resp.raise_for_status()
    results = resp.json().get("results", [])
    if results:
        return results[0]

    created = session.post(
        _url(base_url, endpoint), json=payload, verify=verify_ssl, timeout=TIMEOUT
    )
    if not created.ok:
        raise SeedError(
            f"Failed to create {endpoint} {lookup}: HTTP {created.status_code} {created.text[:500]}"
        )
    obj: dict[str, Any] = created.json()
    return obj


def _set_device_ips(
    session: requests.Session,
    base_url: str,
    device: dict[str, Any],
    *,
    primary_ip4_id: int | None = None,
    oob_ip_id: int | None = None,
    verify_ssl: bool,
) -> None:
    """PATCH a device's primary_ip4 / oob_ip only when not already assigned."""
    patch: dict[str, Any] = {}
    if primary_ip4_id is not None and not device.get("primary_ip4"):
        patch["primary_ip4"] = primary_ip4_id
    if oob_ip_id is not None and not device.get("oob_ip"):
        patch["oob_ip"] = oob_ip_id
    if not patch:
        return
    resp = session.patch(
        _url(base_url, f"dcim/devices/{device['id']}"),
        json=patch,
        verify=verify_ssl,
        timeout=TIMEOUT,
    )
    if not resp.ok:
        raise SeedError(
            f"Failed to set IPs on device {device['name']}: "
            f"HTTP {resp.status_code} {resp.text[:500]}"
        )


def _assign_ip(
    session: requests.Session,
    base_url: str,
    device_id: int,
    iface_name: str,
    iface_type: str,
    address: str,
    *,
    verify_ssl: bool,
) -> int:
    """Ensure an interface + IP exist on a device; return the IP address id."""
    iface = _get_or_create(
        session,
        base_url,
        "dcim/interfaces",
        {"device_id": device_id, "name": iface_name},
        {"device": device_id, "name": iface_name, "type": iface_type},
        verify_ssl=verify_ssl,
    )
    ip = _get_or_create(
        session,
        base_url,
        "ipam/ip-addresses",
        {"address": address},
        {
            "address": address,
            "status": "active",
            "assigned_object_type": "dcim.interface",
            "assigned_object_id": iface["id"],
        },
        verify_ssl=verify_ssl,
    )
    return int(ip["id"])


def seed_netbox(base_url: str, token: str, *, verify_ssl: bool = False) -> dict[str, int]:
    """Seed the NetBox simulator with a synthetic topology (idempotent).

    Returns a small summary counting the top-level objects of interest, useful
    for sanity logging from the fixture / CLI.
    """
    session = requests.Session()
    session.headers.update(
        {
            "Authorization": f"Token {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
    )

    # --- tags ---
    for name, slug, color in [("GPU", "gpu", "00bcd4"), ("Integration", "integration", "9e9e9e")]:
        _get_or_create(
            session,
            base_url,
            "extras/tags",
            {"slug": slug},
            {"name": name, "slug": slug, "color": color},
            verify_ssl=verify_ssl,
        )

    # --- custom field (text) used by the hostname-lookup convention ---
    _get_or_create(
        session,
        base_url,
        "extras/custom-fields",
        {"name": "Provider_Machine_ID"},
        {
            "name": "Provider_Machine_ID",
            "label": "Provider Machine ID",
            "type": "text",
            "object_types": ["dcim.device"],
            "description": "Vendor/site-operator hostname for this node.",
        },
        verify_ssl=verify_ssl,
    )

    # --- manufacturers ---
    nvidia = _get_or_create(
        session,
        base_url,
        "dcim/manufacturers",
        {"slug": "nvidia"},
        {"name": "NVIDIA", "slug": "nvidia"},
        verify_ssl=verify_ssl,
    )
    arista = _get_or_create(
        session,
        base_url,
        "dcim/manufacturers",
        {"slug": "arista"},
        {"name": "Arista", "slug": "arista"},
        verify_ssl=verify_ssl,
    )

    # --- device types ---
    dgx = _get_or_create(
        session,
        base_url,
        "dcim/device-types",
        {"slug": "dgx-h100"},
        {"manufacturer": nvidia["id"], "model": "DGX H100", "slug": "dgx-h100", "u_height": 8},
        verify_ssl=verify_ssl,
    )
    switch_type = _get_or_create(
        session,
        base_url,
        "dcim/device-types",
        {"slug": "arista-7280r3"},
        {"manufacturer": arista["id"], "model": "7280R3", "slug": "arista-7280r3", "u_height": 1},
        verify_ssl=verify_ssl,
    )

    # --- device roles (include a gpu role) ---
    gpu_role = _get_or_create(
        session,
        base_url,
        "dcim/device-roles",
        {"slug": "gpu"},
        {"name": "GPU", "slug": "gpu", "color": "00bcd4"},
        verify_ssl=verify_ssl,
    )
    switch_role = _get_or_create(
        session,
        base_url,
        "dcim/device-roles",
        {"slug": "leaf-switch"},
        {"name": "Leaf Switch", "slug": "leaf-switch", "color": "9c27b0"},
        verify_ssl=verify_ssl,
    )

    # --- region + sites ---
    region = _get_or_create(
        session,
        base_url,
        "dcim/regions",
        {"slug": "us-east"},
        {"name": "US East", "slug": "us-east"},
        verify_ssl=verify_ssl,
    )
    ori = _get_or_create(
        session,
        base_url,
        "dcim/sites",
        {"slug": "ori-tx"},
        {"name": "ORI-TX", "slug": "ori-tx", "status": "active", "region": region["id"]},
        verify_ssl=verify_ssl,
    )
    oh1 = _get_or_create(
        session,
        base_url,
        "dcim/sites",
        {"slug": "5c-oh1"},
        {"name": "5C-OH1", "slug": "5c-oh1", "status": "active", "region": region["id"]},
        verify_ssl=verify_ssl,
    )

    # --- cluster type + clusters ---
    cluster_type = _get_or_create(
        session,
        base_url,
        "virtualization/cluster-types",
        {"slug": "gpu-cluster"},
        {"name": "GPU Cluster", "slug": "gpu-cluster"},
        verify_ssl=verify_ssl,
    )
    cartesia5 = _get_or_create(
        session,
        base_url,
        "virtualization/clusters",
        {"name": "cartesia5"},
        {"name": "cartesia5", "type": cluster_type["id"], "status": "active"},
        verify_ssl=verify_ssl,
    )
    research = _get_or_create(
        session,
        base_url,
        "virtualization/clusters",
        {"name": "research-h100"},
        {"name": "research-h100", "type": cluster_type["id"], "status": "active"},
        verify_ssl=verify_ssl,
    )

    # --- devices ---
    devices_spec: list[dict[str, Any]] = [
        {
            "name": "sim-gpu-01",
            "device_type": dgx["id"],
            "role": gpu_role["id"],
            "site": ori["id"],
            "cluster": cartesia5["id"],
            "status": "active",
            "custom_fields": {"Provider_Machine_ID": "GPU-SIM-001"},
        },
        {
            "name": "sim-gpu-02",
            "device_type": dgx["id"],
            "role": gpu_role["id"],
            "site": ori["id"],
            "cluster": cartesia5["id"],
            "status": "active",
        },
        {
            "name": "sim-gpu-03",
            "device_type": dgx["id"],
            "role": gpu_role["id"],
            "site": ori["id"],
            "cluster": research["id"],
            "status": "active",
        },
        {
            "name": "sim-gpu-04",
            "device_type": dgx["id"],
            "role": gpu_role["id"],
            "site": oh1["id"],
            "cluster": research["id"],
            "status": "active",
        },
        {
            "name": "sim-leaf-01",
            "device_type": switch_type["id"],
            "role": switch_role["id"],
            "site": ori["id"],
            "status": "active",
        },
        {
            "name": "sim-spine-01",
            "device_type": switch_type["id"],
            "role": switch_role["id"],
            "site": oh1["id"],
            "status": "planned",
        },
    ]
    devices: dict[str, dict[str, Any]] = {}
    for spec in devices_spec:
        devices[spec["name"]] = _get_or_create(
            session,
            base_url,
            "dcim/devices",
            {"name": spec["name"]},
            spec,
            verify_ssl=verify_ssl,
        )

    # --- IP addresses + interfaces (in-band + OOB) ---
    gpu01 = devices["sim-gpu-01"]
    gpu01_data = _assign_ip(
        session,
        base_url,
        gpu01["id"],
        "enp1s0",
        "1000base-t",
        "10.10.0.11/24",
        verify_ssl=verify_ssl,
    )
    gpu01_oob = _assign_ip(
        session,
        base_url,
        gpu01["id"],
        "bmc0",
        "1000base-t",
        "192.168.196.11/24",
        verify_ssl=verify_ssl,
    )
    _set_device_ips(
        session,
        base_url,
        gpu01,
        primary_ip4_id=gpu01_data,
        oob_ip_id=gpu01_oob,
        verify_ssl=verify_ssl,
    )

    gpu02 = devices["sim-gpu-02"]
    gpu02_data = _assign_ip(
        session,
        base_url,
        gpu02["id"],
        "enp1s0",
        "1000base-t",
        "10.10.0.12/24",
        verify_ssl=verify_ssl,
    )
    _set_device_ips(session, base_url, gpu02, primary_ip4_id=gpu02_data, verify_ssl=verify_ssl)

    return {"devices": len(devices), "sites": 2, "clusters": 2}


def main() -> int:
    """Entry point for ``python seed.py`` (reads config from the environment)."""
    base_url = os.environ.get("NETBOX_URL")
    token = os.environ.get("NETBOX_TOKEN", DEFAULT_TOKEN)
    verify_ssl = os.environ.get("VERIFY_SSL", "false").lower() not in ("false", "0", "no", "")
    if not base_url:
        print("NETBOX_URL is required (e.g. http://127.0.0.1:8080)", file=sys.stderr)
        return 2
    try:
        summary = seed_netbox(base_url, token, verify_ssl=verify_ssl)
    except (requests.RequestException, SeedError) as exc:
        print(f"Seeding failed: {exc}", file=sys.stderr)
        return 1
    print(f"Seeded NetBox at {base_url}: {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
