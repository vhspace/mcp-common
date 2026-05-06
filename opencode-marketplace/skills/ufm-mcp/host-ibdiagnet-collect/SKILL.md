---
name: host-ibdiagnet-collect
description: Use when collecting host-side ibdiagnet evidence for SI/FEC/PHY triage on H100/H200/B200 InfiniBand hosts, when preparing support evidence for a flagged HCA, or when ad-hoc importing a fabric snapshot into Topaz. Triggers on host ibdiagnet, mlxlink, FEC investigation, signal integrity, port_fec_uncorrectable, ibdiagnet2.pm, Topaz collection import.
---

# Host-side ibdiagnet collection — mlxlink-first ordering for SI/FEC investigation

## When to use

Host-level FEC / signal-integrity / PHY investigation on a single compute node, support-evidence preparation for a flagged HCA, or ad-hoc Topaz collection from a node that's already drained.

For fabric-wide correlation (UFM events, alarms, switch ports), pair this skill with `ufm-fabric-ops`.

## Pre-flight (on the host)

```bash
ssh <host>
which ibdiagnet                                # MFT must be installed
ibstat | head -30                              # confirm HCAs are reachable from host's CA
nvidia-smi 2>/dev/null | grep "No running"     # host should be idle (no NCCL traffic)
df -h /var/tmp                                 # ibdiagnet writes ~50-200MB to /var/tmp/ibdiagnet2/
```

If NCCL/training is running, counters will be churning during capture — drain or schedule a brief idle window first.

## Ordering rule (the "--pc" gotcha)

**Run `mlxlink -c` BEFORE `ibdiagnet --pc`.**

`ibdiagnet --pc` clears every IB port's PerfCounters at the start of its run, then re-reads them once a second later. The re-read gives a 1-second sample — not the operating-history view. `mlxlink -c` is read-only with respect to counters and shows the long-window pre/post-FEC BER picture (typically 100+ minutes of accumulated data). If you run them in the wrong order, the long-window mlxlink data is gone.

```bash
# Step 1 — capture mlxlink (read-only, preserves counters):
for hca in $(ls /sys/class/infiniband/); do
  echo "=== $hca ==="
  sudo mlxlink -d $hca -p 1 -c -m | tee -a /var/tmp/mlxlink-$(date +%Y%m%d-%H%M%S).txt
done

# Step 2 — now run ibdiagnet (clears + re-reads counters):
sudo ibdiagnet --pc --get_phy_info --get_cable_info --extended_speeds
```

Output lands in `/var/tmp/ibdiagnet2/`.

## Magnitude vs presence

ibdiagnet's `port_fec_correctable_block_counter` numbers are tiny (1-second sample) and **not** comparable to mlxlink's 100-min view. The valuable ibdiagnet signal is the *presence* of `port_fec_uncorrectable_block_counter > 0` anywhere on the fabric — that single fact justifies the cable reseat / replacement, regardless of magnitude. Don't try to compute BER from these counters.

## Artifact map

Four files worth pulling back from `/var/tmp/ibdiagnet2/`:

| File | What it carries |
|---|---|
| `ibdiagnet2.log` | Run log, peer-port mapping, errors detected |
| `ibdiagnet2.pm` | Performance counters (FEC, BER, link errors) |
| `ibdiagnet2.cables` | Cable / transceiver info per port |
| `ibdiagnet2.db_csv` | Topology in CSV form (consumed by Topaz import) |

The rest of `/var/tmp/ibdiagnet2/` is noise for SI/FEC triage.

```bash
# Tar the four artifacts:
sudo tar -czf /var/tmp/ibdiagnet-$(hostname)-$(date +%Y%m%dT%H%M%SZ).tar.gz \
  -C /var/tmp/ibdiagnet2 \
  ibdiagnet2.log ibdiagnet2.pm ibdiagnet2.cables ibdiagnet2.db_csv

# Pull back to your workstation:
scp <host>:/var/tmp/ibdiagnet-*.tar.gz ./
```

## Scope filtering

ibdiagnet flags fabric-wide errors. Before drawing conclusions about the host under investigation, filter the output by the host's BDFs and peer leaf ports:

```bash
HOSTNAME=<host>
# Per-HCA peer leaf names (from ibdiagnet2.log "Discovery" section):
grep -A1 "$HOSTNAME" ibdiagnet2.log | grep "peer"
```

A `port_rcv_switch_relay_errors` finding on a leaf you're not connected to has nothing to do with this host's HCA.

## Topaz upload — the natural last step

```bash
ufm-cli upload-ibdiagnet ./ibdiagnet-<host>-<timestamp>.tar.gz --site ori
# prints: collection_id=<id>
```

(MCP equivalent: `ufm_upload_ibdiagnet(site="ori", ibdiagnet_path=<path>)`.)

## Validate the import

After upload, query the freshly-imported view:

```bash
ufm-cli topaz-cables --site ori --collection <id> --alarms-only
ufm-cli topaz-port-counters --site ori --collection <id> --errors-only
```

If `topaz-cables --alarms-only` returns the expected per-cable signals, the import landed. If it returns 0 alarms but you saw `port_fec_uncorrectable_block_counter > 0` in the local files, double-check the upload exit code and `collection_id`.

## Companion skills

- `skills/ufm-fabric-ops/SKILL.md` — fabric-wide correlation (UFM alarms/events, switch port health).
- `skills/ufm-stale-inventory-recovery/SKILL.md` — when UFM under-reports HCAs on a host that ibdiagnet says is healthy.
