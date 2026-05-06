---
name: ufm-stale-inventory-recovery
description: Use when UFM under-reports HCAs on a host that ibstat/ibdiagnet say is healthy, when `ufm-cli inventory-doctor` reports `stale_anchor`, `ghost_ports`, or `record_undercount`, or when `system_record.guid` in UFM points at a pre-rebuild HCA. Triggers on UFM stale anchor, ghost ports, record undercount, post-HCA-swap inventory drift, peer=N/A on switch port to a healthy HCA.
---

# UFM stale-inventory recovery — safe procedure (DRBD-HA aware)

## When to use

UFM's per-system inventory cache can latch on to a previous HCA's GUID after a host
HCA swap or rebuild. The fabric is fine; UFM's *model* of it is wrong.

Symptom set:

- Host: `ibstatus` shows all HCAs `Active/LinkUp`; `ibdiagnet` agrees.
- Switch: port to the "missing" HCA reports `Active/LinkUp` but `peer = N/A`.
- UFM: `system_record.guid` matches a *previous* HCA — often visible in older
  ibdiagnet captures with the pre-rebuild `HCA-N` naming style instead of
  `mlx5_N`.
- `ufm-cli ports <system>` returns fewer ports than the host actually has
  (the stale-anchor case fixed in #49 surfaces an `inventory_warnings` block;
  this skill covers cleaning the cache itself).

Confirm with `ufm-cli inventory-doctor <system>` (or `ufm_inventory_doctor` from MCP) — expect `inferred_diagnosis: stale_anchor`, `ghost_ports`, or `record_undercount`.

## Pre-flight monitoring (second SSH session on UFM HA primary)

```bash
watch -n 2 '
  echo --- drbd ---;       cat /proc/drbd | head -3
  echo --- ufm pcs ---;    sudo pcs status resources | grep ufm-enterprise
  echo --- ufm api ---;    curl -ksS -o /dev/null -w "%{http_code}\n" https://localhost/ufmRestV3/version
'
```

During the restart, expect:
- `pcs status resources` shows `ufm-enterprise` go Stopped → Started on the primary.
- DRBD role stays `Primary/Secondary UpToDate` throughout.
- API HTTP code briefly returns `000`/`502`, then `200` again.

If DRBD flips to `Secondary/Primary`, abort further work and consult the
`ufm-opensm-restart` skill — we've hit an unexpected failover.

## Recovery

```bash
ssh <UFM_HA_PRIMARY>
sudo pcs resource restart ufm-enterprise
```

This restarts only the UFM model layer:

- ~1-2 min UFM API/UI downtime
- **Zero fabric impact** — OpenSM keeps running; no link flaps
- DRBD role stays `Primary/Secondary` throughout (no failover)
- Inventory cache is rebuilt from /resources/systems on next sweep

**Do not** use `pcs resource disable ufm-enterprise; pcs resource enable ufm-enterprise`
— that flips DRBD roles. `restart` is the right verb.

## What does NOT help (do not bother)

- `mlxlink --port_state DN/UP` from the host — toggles a healthy port; UFM
  inventory cache is unaffected.
- `ibportstate` on the local CA — same, host-side state, no UFM impact.
- `DELETE /ufmRestV3/resources/systems/{guid}` — UFM returns HTTP 405.
- Heavy SM sweep alone — does NOT rebuild UFM's inventory cache. Sweeps
  refresh routing/topology, not the system-record-to-port mapping cache.

## If recovery doesn't work

If `pcs resource restart ufm-enterprise` leaves UFM in a bad state (container
won't start, API stays 502 past ~3 min, or `ufm-cli inventory-doctor` still
reports drift after a successful restart), escalate stepwise:

```bash
# Step 1: explicit stop/start with a delay (cleaner than restart)
sudo pcs resource stop ufm-enterprise
sleep 10
sudo pcs resource start ufm-enterprise
sudo pcs status resources | grep ufm-enterprise   # confirm Started
```

If that doesn't recover the API, the symptom may overlap with a stuck OpenSM —
switch to `skills/ufm-opensm-restart/SKILL.md` and run `ufmd_ib restart` inside
the UFM container.

**Last resort (HA failover):** `docker restart ufm` tears down the whole
container and typically triggers a DRBD role flip to the secondary. Only use
when nothing else works and you've confirmed the secondary is healthy
(`cat /proc/drbd` shows `Secondary/Primary UpToDate` from the secondary's
viewpoint before the failover).

## Verification

```bash
ufm-cli inventory-doctor <SYSTEM_NAME> --site <SITE>
# expect: inferred_diagnosis: clean

ufm-cli ports <SYSTEM_NAME> --site <SITE>
# expect: full port count, no inventory_warnings block in --json output
```

## Why "fix the bug + still need this skill"

#49 made `ufm_get_ports_health` return correct ports even when the anchor is stale
(by falling back to `?system_name=`). That preserves agent productivity. But the
stale anchor itself is still wrong in UFM's database — alarms, events, and
third-party UFM consumers will keep mis-attributing port state until the cache
is rebuilt. This recovery is the cleanup step.

## Companion skill

`skills/ufm-opensm-restart/SKILL.md` — also DRBD-HA aware. Use it when the
symptom is a stuck OpenSM (silent log, sweeps not running) rather than stale
inventory.
