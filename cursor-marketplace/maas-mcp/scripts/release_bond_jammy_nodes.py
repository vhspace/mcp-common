#!/usr/bin/env python3
"""Central MAAS: release -> bond0 active-backup (API) -> deploy ubuntu/jammy.

  uv run python scripts/release_bond_jammy_nodes.py

Env: load MAAS_CENTRAL_* / .env as for maas-cli.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

# repo root on path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from typing import Any

from maas_mcp.config import Settings
from maas_mcp.maas_client import MaasRestClient


def _maas_bond_put_form_data(
    existing_params: dict[str, Any] | None, target_bond_mode: str
) -> dict[str, str]:
    """Same as maas_mcp.server._maas_bond_put_form_data (avoid importing MCP server)."""
    p = existing_params or {}
    mtu = p.get("mtu", 4092)
    miimon = p.get("bond_miimon", 100)
    updelay = p.get("bond_updelay", 0)
    downdelay = p.get("bond_downdelay", 0)
    num_grat = p.get("bond_num_grat_arp", 1)
    lacp = p.get("bond_lacp_rate", "slow")
    xmit = p.get("bond_xmit_hash_policy", "layer2")
    if target_bond_mode == "active-backup":
        xmit = "layer2"
        lacp = "slow"
    elif target_bond_mode == "802.3ad":
        xmit = p.get("bond_xmit_hash_policy") or "layer3+4"
        lacp = p.get("bond_lacp_rate") or "fast"

    def enc(s: str) -> str:
        return str(s).replace("+", "%2B")

    return {
        "bond_mode": enc(target_bond_mode),
        "bond_miimon": str(miimon),
        "bond_updelay": str(updelay),
        "bond_downdelay": str(downdelay),
        "bond_num_grat_arp": str(num_grat),
        "bond_xmit_hash_policy": enc(str(xmit)),
        "bond_lacp_rate": enc(str(lacp)),
        "mtu": str(mtu),
    }


# research-common-h100 batch (hostname, system_id)
NODES = [
    ("gpu054", "g8xybf"),
    ("gpu047", "xq7dsa"),
    ("gpu069", "nbhcxr"),
    ("gpu081", "k3wweh"),
]

RELEASE_READY_TIMEOUT = 1800
DEPLOY_TIMEOUT = 2400
POLL = 15


def wait_status(client, sid, want, bad, timeout, label):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        m = client.get(f"machines/{sid}")
        last = m
        st = m.get("status_name", "")
        if st in want:
            return m
        if st in bad:
            raise RuntimeError(f"{label}: machine {sid} bad state {st!r}")
        time.sleep(POLL)
    raise TimeoutError(f"{label}: timeout waiting {want}; last={last and last.get('status_name')}")


def ensure_bond(client, sid, hostname):
    m = client.get(f"machines/{sid}")
    bond = next(
        (
            i
            for i in (m.get("interface_set") or [])
            if i.get("type") == "bond" and i.get("name") == "bond0"
        ),
        None,
    )
    if not bond:
        raise RuntimeError(f"{hostname}: no bond0")
    iid = int(bond["id"])
    put_data = _maas_bond_put_form_data(bond.get("params") or {}, "active-backup")
    updated = client.put(f"nodes/{sid}/interfaces/{iid}", data=put_data)
    np = (updated.get("params") or {}) if isinstance(updated, dict) else {}
    if np.get("bond_mode") != "active-backup":
        raise RuntimeError(f"{hostname}: bond_mode still {np.get('bond_mode')!r}")
    print(f"    bond0 id={iid} -> active-backup")


def main() -> int:
    # Line-buffered stdout when redirected (nohup/log).
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass
    s = Settings()
    central = s.get_maas_instances()["central"]
    client = MaasRestClient(
        url=str(central.url),
        api_key=central.api_key.get_secret_value(),
        verify_ssl=s.verify_ssl,
        timeout_seconds=120.0,
    )
    summary = []
    try:
        for hostname, sid in NODES:
            print(f"\n{'=' * 60}\n### {hostname} ({sid})\n")
            m = client.get(f"machines/{sid}")
            print(f"  before: status={m.get('status_name')!r}")

            print("  [1/3] release")
            if m.get("status_name") != "Ready":
                client.post_fire(f"machines/{sid}", data={}, params={"op": "release"}, timeout=30.0)
                print("    release sent, waiting Ready...")
                wait_status(
                    client,
                    sid,
                    {"Ready"},
                    {"Broken", "Failed commissioning", "Failed testing", "Failed deployment"},
                    RELEASE_READY_TIMEOUT,
                    "release",
                )
                print("  -> Ready")
            else:
                print("  (already Ready)")

            print("  [2/3] bond active-backup")
            ensure_bond(client, sid, hostname)

            print("  [3/3] deploy ubuntu jammy")
            client.post_fire(
                f"machines/{sid}",
                data={"osystem": "ubuntu", "distro_series": "jammy"},
                params={"op": "deploy"},
                timeout=30.0,
            )
            print("    deploy sent, waiting Deployed...")
            wait_status(
                client,
                sid,
                {"Deployed"},
                {"Broken", "Failed deployment"},
                DEPLOY_TIMEOUT,
                "deploy",
            )
            final = client.get(f"machines/{sid}")
            print(
                f"  -> Deployed  osystem={final.get('osystem')!r} "
                f"distro_series={final.get('distro_series')!r}"
            )
            summary.append((hostname, sid, final.get("osystem"), final.get("distro_series")))
    finally:
        client.close()

    print(f"\n{'=' * 60}\n### SUMMARY\n")
    for hostname, sid, osys, series in summary:
        print(f"  {hostname:8} {sid:8}  {osys!r}  {series!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
