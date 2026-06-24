---
name: ori-tx-fabric
description: >-
  ORI-TX in-band network fabric knowledge — topology, bonding, MAC aging,
  failover behavior, server connectivity. Use when investigating ORI network
  issues, bonding, failover, MAC aging, active-backup, LACP, spine-leaf,
  MLAG, CLAG, peerlink, NIC redundancy, ConnectX NICs, or answering
  "how are servers connected at ORI".
---

# ORI-TX Fabric — Operational Knowledge

Site-specific knowledge for the ORI-TX (Dallas, DFW01) in-band management
network. This is the Ethernet fabric serving the `research-common-h100`
GPU cluster.

## Topology

```
                  ┌─────────────┐
                  │ dfw-rtr-02  │  (Juniper, upstream)
                  │  ori.co     │
                  └──┬──────┬───┘
                 2×100G   2×100G
                  ┌──┴──┐ ┌──┴──┐
                  │spi-01│─│spi-02│  peerlink: swp63+swp64 (MLAG pair)
                  └┬┬┬┬──┘ └──┬┬┬┬┘
            bond1-4 (2×400G each, CLAG IDs 1-4)
        ┌─────┬─────┬─────┐
     ┌──┴──┐┌──┴──┐┌──┴──┐┌──┴──┐
     │lea-01││lea-02││lea-03││lea-04│
     └──┬───┘└──┬───┘└──┬───┘└──┬───┘
        │       │       │       │
   ~72 swp ports each (breakout 400G→2×200G or 4×100G)
        │       │       │       │
   ┌────┴───────┴───────┴───────┴────┐
   │  GPU servers (single cable each) │
   └─────────────────────────────────┘
```

- **Spines**: `dfw01-inb-sw-spi-01` (192.168.199.201), `dfw01-inb-sw-spi-02` (.202)
- **Leaves**: `dfw01-inb-sw-lea-01` through `lea-04` (192.168.199.203–.206)
- **Hardware**: NVIDIA SN5600 Spectrum-4 (64×800G OSFP, broken out per port group)
- **OS**: Cumulus Linux 5.15.0
- **Bridge**: VLAN-aware, VLANs 1228, 1229 (mgmt-cluster 192.168.229.0/24), 1230, 1231
- **No EVPN** configured

### Spine MLAG

The two spines form an MLAG pair:
- Peerlink: swp63 + swp64 (2×400G bond)
- CLAG bonds to leaves: bond1 (swp1+swp2), bond2 (swp3+swp4), bond3 (swp5+swp6), bond4 (swp7+swp8)
- Each leaf's uplink bond (`bond1`) has 4 members: 2 ports to spi-01 (swp29, swp31) + 2 ports to spi-02 (swp61, swp63)
- LACP mode on all spine-leaf bonds, **fast rate** (1s PDU interval, 3s failure detection)

## Server Connectivity

Servers connect to leaves with **individual cables** — one NIC per leaf port.
There is **no LACP or bonding on the switch side** for server-facing links.

- Server ports are breakout `swp*s0` / `swp*s1` ports on the leaves
- ~72 swp ports up per leaf, ~29 admin-down (not all breakouts are cabled)
- ConnectX NICs **do not emit LLDP** — only switch-to-switch links show LLDP peers
- Port-to-node mapping relies on **MAC learning** (use `find_port_for_node` or `find_port_for_mac`), not LLDP

## Host-Side Bonding

Servers use **active-backup (bond mode 1)** via netplan — NOT 802.3ad/LACP.

| Property | Value |
|----------|-------|
| Bond mode | `active-backup` (mode 1) |
| Primary selection | NIC whose permanent MAC matches the bond MAC |
| Failover | Secondary NIC takes over if primary link drops |
| Configuration | Ansible role `ori-configure-netplan-bond` in `infra-sre-2113` |

**Why not LACP**: each server NIC connects to a single port on one leaf.
LACP (802.3ad) requires multiple links to the same switch (or MLAG pair).
Since each NIC goes to a different individual leaf port, there is no
multi-link bundle to negotiate.

## MAC Aging and Failover Behavior

| Parameter | Value |
|-----------|-------|
| Bridge MAC aging timer | **1800 seconds (30 minutes)** — all 6 switches |
| LACP rate (spine-leaf) | Fast (1s PDU, 3s detection) |
| Host bond failover | Immediate — kernel switches to secondary NIC |

### Failover sequence

1. Primary NIC link drops (cable pull, NIC failure, leaf switch failure)
2. Host kernel detects link-down via MII monitoring, activates secondary NIC
3. Secondary NIC sends **gratuitous ARP** announcing the bond MAC on the new port
4. The leaf switch where the secondary NIC is connected learns the MAC immediately
5. The old leaf's stale MAC entry ages out over time (up to 1800s if no gARP reached it)

### Worst case

If the gratuitous ARP doesn't propagate (e.g. secondary NIC is on the same leaf,
or ARP is suppressed), the stale MAC entry on the old port ages out in up to
**30 minutes**. In practice, gARP works and the switchover is near-instant for
Layer 3 traffic.

## Troubleshooting Tips

- To find which leaf port a server is on: `find_port_for_node(node="hostname")`
- To check if a bond failover happened: look for MAC address moves across ports
  using `get_mac_table` on the relevant leaves
- To verify spine MLAG health: `get_bgp_neighbors` on both spines — all 66 peers
  should be `established`
- To check for hardware drops during failover: `get_wjh(switch="leaf-name")`
- To see recent errors: `get_logs(switch="leaf-name", preset="all-errors")`
