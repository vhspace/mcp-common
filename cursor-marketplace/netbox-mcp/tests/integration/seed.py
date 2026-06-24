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

# Shared truthy/falsy token sets for boolean env parsing (see ``_env_bool``).
# Kept here because conftest.py imports this module, so every boolean knob in
# the harness (VERIFY_SSL here, NETBOX_REQUIRE_DOCKER / NETBOX_IT_CLEAN there)
# parses identically.
_TRUE_TOKENS = frozenset({"1", "true", "yes", "on", "y", "t"})
_FALSE_TOKENS = frozenset({"0", "false", "no", "off", "n", "f", ""})


class SeedError(RuntimeError):
    """Raised when the simulator cannot be seeded."""


def _env_bool(name: str, default: bool) -> bool:
    """Parse a boolean environment variable using one shared convention.

    ``1/true/yes/on/y/t`` are truthy and ``0/false/no/off/n/f`` (plus the empty
    string) are falsy, case-insensitively. Returns *default* when the variable
    is unset or holds an unrecognized token.
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in _TRUE_TOKENS:
        return True
    if value in _FALSE_TOKENS:
        return False
    return default


class _Seeder:
    """Stateful, idempotent NetBox seeder.

    Captures the HTTP ``session`` + ``base_url`` + ``verify_ssl`` flag once so
    callers don't have to thread those three values through every helper. Each
    helper still does an idempotent "GET by natural key, then POST only when
    missing", so re-running against a persisted volume is a cheap no-op.
    """

    def __init__(self, base_url: str, token: str, *, verify_ssl: bool) -> None:
        self.base_url = base_url
        self.verify_ssl = verify_ssl
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Token {token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
        )

    def _url(self, endpoint: str) -> str:
        return f"{self.base_url.rstrip('/')}/api/{endpoint.strip('/')}/"

    def get_or_create(
        self, endpoint: str, lookup: dict[str, Any], payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Return an existing object matching *lookup*, or create it from *payload*."""
        resp = self.session.get(
            self._url(endpoint),
            params={**lookup, "limit": 1},
            verify=self.verify_ssl,
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
        if results:
            return results[0]

        created = self.session.post(
            self._url(endpoint), json=payload, verify=self.verify_ssl, timeout=TIMEOUT
        )
        if not created.ok:
            raise SeedError(
                f"Failed to create {endpoint} {lookup}: "
                f"HTTP {created.status_code} {created.text[:500]}"
            )
        obj: dict[str, Any] = created.json()
        return obj

    def assign_ip(self, device_id: int, iface_name: str, iface_type: str, address: str) -> int:
        """Ensure an interface + IP exist on a device; return the IP address id."""
        iface = self.get_or_create(
            "dcim/interfaces",
            {"device_id": device_id, "name": iface_name},
            {"device": device_id, "name": iface_name, "type": iface_type},
        )
        ip = self.get_or_create(
            "ipam/ip-addresses",
            {"address": address},
            {
                "address": address,
                "status": "active",
                "assigned_object_type": "dcim.interface",
                "assigned_object_id": iface["id"],
            },
        )
        return int(ip["id"])

    def set_device_ips(
        self,
        device: dict[str, Any],
        *,
        primary_ip4_id: int | None = None,
        oob_ip_id: int | None = None,
    ) -> None:
        """PATCH a device's primary_ip4 / oob_ip only when not already assigned."""
        patch: dict[str, Any] = {}
        if primary_ip4_id is not None and not device.get("primary_ip4"):
            patch["primary_ip4"] = primary_ip4_id
        if oob_ip_id is not None and not device.get("oob_ip"):
            patch["oob_ip"] = oob_ip_id
        if not patch:
            return
        resp = self.session.patch(
            self._url(f"dcim/devices/{device['id']}"),
            json=patch,
            verify=self.verify_ssl,
            timeout=TIMEOUT,
        )
        if not resp.ok:
            raise SeedError(
                f"Failed to set IPs on device {device['name']}: "
                f"HTTP {resp.status_code} {resp.text[:500]}"
            )


def seed_netbox(base_url: str, token: str, *, verify_ssl: bool = False) -> dict[str, int]:
    """Seed the NetBox simulator with a synthetic topology (idempotent).

    Returns a small summary counting the top-level objects of interest, useful
    for sanity logging from the fixture / CLI.
    """
    seeder = _Seeder(base_url, token, verify_ssl=verify_ssl)

    # --- custom field (text) used by the hostname-lookup convention ---
    seeder.get_or_create(
        "extras/custom-fields",
        {"name": "Provider_Machine_ID"},
        {
            "name": "Provider_Machine_ID",
            "label": "Provider Machine ID",
            "type": "text",
            "object_types": ["dcim.device"],
            "description": "Vendor/site-operator hostname for this node.",
        },
    )

    # --- manufacturers ---
    nvidia = seeder.get_or_create(
        "dcim/manufacturers", {"slug": "nvidia"}, {"name": "NVIDIA", "slug": "nvidia"}
    )
    arista = seeder.get_or_create(
        "dcim/manufacturers", {"slug": "arista"}, {"name": "Arista", "slug": "arista"}
    )

    # --- device types ---
    dgx = seeder.get_or_create(
        "dcim/device-types",
        {"slug": "dgx-h100"},
        {"manufacturer": nvidia["id"], "model": "DGX H100", "slug": "dgx-h100", "u_height": 8},
    )
    switch_type = seeder.get_or_create(
        "dcim/device-types",
        {"slug": "arista-7280r3"},
        {"manufacturer": arista["id"], "model": "7280R3", "slug": "arista-7280r3", "u_height": 1},
    )

    # --- device roles (include a gpu role) ---
    gpu_role = seeder.get_or_create(
        "dcim/device-roles",
        {"slug": "gpu"},
        {"name": "GPU", "slug": "gpu", "color": "00bcd4"},
    )
    switch_role = seeder.get_or_create(
        "dcim/device-roles",
        {"slug": "leaf-switch"},
        {"name": "Leaf Switch", "slug": "leaf-switch", "color": "9c27b0"},
    )

    # --- region + sites ---
    # The region is a real FK on both sites below; test_read.py asserts a
    # site-by-region filter so this seeded relationship is backed by a test.
    region = seeder.get_or_create(
        "dcim/regions", {"slug": "us-east"}, {"name": "US East", "slug": "us-east"}
    )
    ori = seeder.get_or_create(
        "dcim/sites",
        {"slug": "ori-tx"},
        {"name": "ORI-TX", "slug": "ori-tx", "status": "active", "region": region["id"]},
    )
    oh1 = seeder.get_or_create(
        "dcim/sites",
        {"slug": "5c-oh1"},
        {"name": "5C-OH1", "slug": "5c-oh1", "status": "active", "region": region["id"]},
    )

    # --- cluster type + clusters ---
    cluster_type = seeder.get_or_create(
        "virtualization/cluster-types",
        {"slug": "gpu-cluster"},
        {"name": "GPU Cluster", "slug": "gpu-cluster"},
    )
    cartesia5 = seeder.get_or_create(
        "virtualization/clusters",
        {"name": "cartesia5"},
        {"name": "cartesia5", "type": cluster_type["id"], "status": "active"},
    )
    research = seeder.get_or_create(
        "virtualization/clusters",
        {"name": "research-h100"},
        {"name": "research-h100", "type": cluster_type["id"], "status": "active"},
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
        devices[spec["name"]] = seeder.get_or_create("dcim/devices", {"name": spec["name"]}, spec)

    # --- IP addresses + interfaces (in-band + OOB) ---
    gpu01 = devices["sim-gpu-01"]
    gpu01_data = seeder.assign_ip(gpu01["id"], "enp1s0", "1000base-t", "10.10.0.11/24")
    gpu01_oob = seeder.assign_ip(gpu01["id"], "bmc0", "1000base-t", "192.168.196.11/24")
    seeder.set_device_ips(gpu01, primary_ip4_id=gpu01_data, oob_ip_id=gpu01_oob)

    gpu02 = devices["sim-gpu-02"]
    gpu02_data = seeder.assign_ip(gpu02["id"], "enp1s0", "1000base-t", "10.10.0.12/24")
    seeder.set_device_ips(gpu02, primary_ip4_id=gpu02_data)

    return {"devices": len(devices), "sites": 2, "clusters": 2}


def main() -> int:
    """Entry point for ``python seed.py`` (reads config from the environment)."""
    base_url = os.environ.get("NETBOX_URL")
    token = os.environ.get("NETBOX_TOKEN", DEFAULT_TOKEN)
    verify_ssl = _env_bool("VERIFY_SSL", False)
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
