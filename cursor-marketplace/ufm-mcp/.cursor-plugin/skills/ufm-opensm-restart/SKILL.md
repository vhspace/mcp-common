---
name: ufm-opensm-restart
description: Use when UFM's OpenSM appears stuck / silent, when fabric-wide NCCL throughput drops far below baseline across many node pairs with near-identical numbers, or when OpenSM is not master despite the local UFM being the DRBD primary. Triggers on osm_enhanced_qos_mgr_parse_config, ERR 0707, 'SM priority changed' clustered in time, stale opensm.log mtime, or symptoms of heavy-sweep-induced fabric churn.
---

# UFM OpenSM restart — safe procedure (DRBD-HA aware)

## When to use

OpenSM can silently drop into a non-master / stuck state when a heavy sweep hits a fatal parser error. Classic signals:

- `ps` shows opensm alive with days of etime
- `opensm.log` mtime is minutes-to-hours behind current time
- `grep 'Entering MASTER state'` has no recent entry
- SIGHUP produces no new log output (`sweep_every_hup_signal TRUE` should trigger a sweep within 10s; it doesn't if opensm is standby)
- Fabric-wide NCCL throughput drops far below baseline with **identical** numbers across unrelated node pairs

## Context (UFM HA model)

Typical UFM Enterprise deployment:
- Two-node HA pair using DRBD for `/opt/ufm/files` replication
- Only the DRBD primary runs OpenSM (`mellanox/ufm-enterprise` container, `--network=host`)
- `/usr/bin/ufm_ha_watcher` on both hosts monitors UFM container health; triggers failover if the primary's UFM stack looks down for too long
- Config files on DRBD replicate automatically — fixing a config on the primary updates the secondary

## Safe restart command

```bash
sudo <docker-bin> exec ufm /opt/ufm/scripts/ufmd_ib restart
```

Where `<docker-bin>` may be just `docker` or on appliance-style hosts `/cm/local/apps/docker/current/bin/docker` (not in sudo's secure_path).

This SysV-init-style script stops/starts in this order: Telemetry Sampling → UnhealthyPorts → UFM Secondary Telemetry → UFM Primary Telemetry → UFM main module → AuthenticationServer → OpenSM, then brings everything back in reverse. Total outage ~10-20s; the UFM container does not restart, so `ufm_ha_watcher` stays happy and DRBD stays `Primary/Secondary UpToDate`.

**Avoid** `docker restart ufm` — it's the nuclear option, tears down the whole container, and typically triggers an HA failover (DRBD role flip).

## Pre-flight monitoring (second SSH session)

```bash
watch -n 2 '
  echo --- drbd ---;       cat /proc/drbd | head -3
  echo --- opensm ---;     pgrep -af /opt/ufm/opensm/sbin/opensm | head -1
  echo --- opensm.log ---; stat -c "%y  size=%s" /opt/ufm/files/log/opensm.log
'
```

During the restart, expect:
- opensm PID changes to a new, low-etime process
- opensm.log mtime starts advancing again
- DRBD role stays `Primary/Secondary` throughout

If DRBD flips to `Secondary/Primary`, abort further work — we've hit an unexpected failover.

## Verification after restart

```bash
pgrep -af /opt/ufm/opensm/sbin/opensm                          # new PID, low etime
grep -E 'Entering MASTER state' /opt/ufm/files/log/opensm.log | tail -5   # confirms fabric-master role
tail -50 /opt/ufm/files/log/opensm.log | grep -E 'ERR|FATAL'   # should be empty
cat /proc/drbd | head -3                                        # Primary/Secondary UpToDate
```

## Known root causes that put OpenSM into this state

- Empty / malformed `/opt/ufm/files/conf/opensm/enhanced_qos_policy.conf` → `ERR 0707` every heavy sweep
- Empty / malformed `cc-policy.conf` when congestion control is configured
- Torus-2QoS config pointing at a missing file

NVIDIA-documented default for disabling Enhanced QoS when not in use:

```
enhanced_qos_policy_file null
```

in `opensm.conf`.

## Rollback

If `ufmd_ib restart` doesn't bring opensm back cleanly:

1. `ufmd_ib stop; sleep 5; ufmd_ib start`
2. If that fails, full container restart (accepts HA failover): `docker restart ufm`
