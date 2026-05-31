# Batch C — Marketplace pin & skill tool list drift

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix two drift bugs in one PR — the stale `v0.4.1` pin in marketplace manifests and the fabricated tool list in the two fabric skills — and add CI guards so neither drifts again.

**Architecture:** The fix itself is text edits to four files (two manifests + two SKILL.md). The CI guards are two pytest tests (in the existing test suite, not bash scripts) that fail loudly if (a) the git tag pinned in `.mcp.json` / `.claude-plugin/plugin.json` doesn't match `[project].version` from `pyproject.toml`, or (b) any `ufm_*` symbol named in a skill file is not registered as an `@mcp.tool` in `server.py`.

**Tech Stack:** Python 3.12, pytest, ruff. No new runtime deps.

**Closes:** vhspace/ufm-mcp#50, vhspace/ufm-mcp#51

---

## File Structure

| File | Purpose | Action |
|---|---|---|
| `.mcp.json` | Claude Code marketplace manifest | Modify: bump pin v0.4.1 → v1.6.0 |
| `.claude-plugin/plugin.json` | Claude Code plugin manifest | Modify: bump pin v0.4.1 → v1.6.0 |
| `skills/ufm-fabric-ops/SKILL.md` | UFM fabric ops skill | Rewrite: real tool list |
| `skills/fabric-monitoring/SKILL.md` | Fabric monitoring skill (currently identical to ufm-fabric-ops, also wrong `name:` in front-matter) | Rewrite: real tool list, fix front-matter `name:` |
| `tests/test_marketplace_pin.py` | NEW pytest module | Create: assert pin tag matches pyproject version |
| `tests/test_skill_tool_lists.py` | NEW pytest module | Create: assert every `ufm_*` symbol in any SKILL.md resolves to an `@mcp.tool` in server.py |

Both manifests pin the same git tag; both skills cite the same set of tools. We DRY the validation logic by parameterizing the pytest test over the file list.

---

## Pre-flight

- [ ] **Step 0a: Branch from main**

```bash
cd /workspaces/together/ufm-mcp
git status                                  # expect: clean tree on main
git checkout -b chore/fix-drift-marketplace-and-skills
```

- [ ] **Step 0b: Capture the real registered-tool list for reference**

```bash
grep -n '@mcp.tool' src/ufm_mcp/server.py | wc -l        # informational
grep -E '^def ufm_|^async def ufm_' src/ufm_mcp/server.py | awk '{print $2}' | sed 's/(.*//' | sort
```

The set returned here is the source of truth for the rewrites in Tasks 3 and 4.

---

## Task 1: Marketplace pin sync test (fail-first)

**Files:**
- Create: `tests/test_marketplace_pin.py`

We write the test before bumping the pin so it fails on `main` and passes after the fix. This is the "RED" step in TDD; without it we have no proof the regression is detected.

- [ ] **Step 1: Write the failing test**

Create `tests/test_marketplace_pin.py`:

```python
"""Asserts that marketplace manifests pin the same git tag as pyproject.toml's package version.

Both `.mcp.json` and `.claude-plugin/plugin.json` use a uvx --from git+...@vX.Y.Z
arg to install the server. If the tag drifts from `[project].version` in
pyproject.toml, fresh marketplace installs run an old version that is missing
tools shipped in newer releases. See vhspace/ufm-mcp#50.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

MANIFEST_FILES = [
    REPO_ROOT / ".mcp.json",
    REPO_ROOT / ".claude-plugin" / "plugin.json",
]

# Matches `git+https://github.com/vhspace/ufm-mcp@vX.Y.Z` in the args list.
PIN_RE = re.compile(r"git\+https://github\.com/vhspace/ufm-mcp@v(\d+\.\d+\.\d+)")


def _project_version() -> str:
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    return pyproject["project"]["version"]


@pytest.mark.parametrize("manifest_path", MANIFEST_FILES, ids=lambda p: p.name)
def test_manifest_pin_matches_pyproject_version(manifest_path: Path) -> None:
    text = manifest_path.read_text()
    match = PIN_RE.search(text)
    assert match is not None, (
        f"{manifest_path} has no git+https://github.com/vhspace/ufm-mcp@vX.Y.Z pin. "
        "If the manifest stopped using a git pin (e.g. switched to a PyPI install), "
        "update this test or delete it."
    )
    pinned = match.group(1)
    expected = _project_version()
    assert pinned == expected, (
        f"{manifest_path.name} pins v{pinned} but pyproject.toml [project].version is "
        f"{expected}. Bump the pin (or run release tooling) so marketplace installs match."
    )
```

- [ ] **Step 2: Run the test on unmodified manifests — expect FAIL**

```bash
uv run pytest tests/test_marketplace_pin.py -v
```

Expected: both parameterized cases FAIL with `pins v0.4.1 but pyproject.toml [project].version is 1.6.0`. If they pass on `main`, the regression has already been hand-fixed and you can skip Task 2's bump (but still keep the test as a guard).

- [ ] **Step 3: Commit the failing test**

We commit the failing test on a separate commit from the fix so `git bisect` can locate either the regression or the fix cleanly later.

```bash
git add tests/test_marketplace_pin.py
git commit -m "test: add marketplace pin/version sync check (#50)

Fails on main because .mcp.json and .claude-plugin/plugin.json pin
v0.4.1 while pyproject.toml is at 1.6.0. Next commit fixes both."
```

---

## Task 2: Bump the pins

**Files:**
- Modify: `.mcp.json:6`
- Modify: `.claude-plugin/plugin.json:21`

- [ ] **Step 1: Update `.mcp.json`**

Change line 6 from:
```
      "git+https://github.com/vhspace/ufm-mcp@v0.4.1",
```
to:
```
      "git+https://github.com/vhspace/ufm-mcp@v1.6.0",
```

- [ ] **Step 2: Update `.claude-plugin/plugin.json`**

Change line 21 from:
```
        "git+https://github.com/vhspace/ufm-mcp@v0.4.1",
```
to:
```
        "git+https://github.com/vhspace/ufm-mcp@v1.6.0",
```

- [ ] **Step 3: Run the sync test — expect PASS**

```bash
uv run pytest tests/test_marketplace_pin.py -v
```

Expected: both cases PASS.

- [ ] **Step 4: Commit the fix**

```bash
git add .mcp.json .claude-plugin/plugin.json
git commit -m "chore: bump marketplace pins to v1.6.0 (closes #50)

Aligns .mcp.json and .claude-plugin/plugin.json with the package
version in pyproject.toml. Tools added in 1.x (ufm_get_cluster_concerns,
ufm_get_high_ber_ports, ...) are now visible to fresh marketplace
installs. test_marketplace_pin will catch future drift."
```

---

## Task 3: Skill tool list registration test (fail-first)

**Files:**
- Create: `tests/test_skill_tool_lists.py`

Same TDD pattern as Task 1: write the assertion, watch it fail against the current bogus tool list, then fix in Task 4.

- [ ] **Step 1: Write the failing test**

Create `tests/test_skill_tool_lists.py`:

```python
"""Asserts every `ufm_*` symbol mentioned in any SKILL.md resolves to a registered tool.

`server.py` decorates each public tool with `@mcp.tool(...)`. Skills under
`skills/*/SKILL.md` document the tools an agent can call. When the two drift
(skill mentions a tool that was renamed or never existed), agents try to call
phantom tools and fail. See vhspace/ufm-mcp#51.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SERVER_PY = REPO_ROOT / "src" / "ufm_mcp" / "server.py"
SKILLS_DIR = REPO_ROOT / "skills"

# Matches bare `ufm_xxx_yyy` symbol references — function names, not arbitrary
# words that happen to start with `ufm_`. Anchored on a non-word character or
# string start so we don't match inside a longer identifier.
TOOL_REF_RE = re.compile(r"(?<![A-Za-z0-9_])(ufm_[a-z][a-z0-9_]*)(?![A-Za-z0-9_])")


def _registered_tool_names() -> set[str]:
    """Parse server.py and return the set of function names decorated with @mcp.tool."""
    tree = ast.parse(SERVER_PY.read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for dec in node.decorator_list:
            # @mcp.tool(...) → ast.Call(func=ast.Attribute(value=Name(id="mcp"), attr="tool"))
            target = dec.func if isinstance(dec, ast.Call) else dec
            if (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "mcp"
                and target.attr == "tool"
            ):
                names.add(node.name)
                break
    return names


def _skill_tool_references(skill_path: Path) -> set[str]:
    """Extract every `ufm_*` symbol mentioned in a skill file."""
    return set(TOOL_REF_RE.findall(skill_path.read_text()))


def test_registered_tool_set_is_nonempty() -> None:
    """Sanity: if AST parsing breaks, fail loudly here rather than in the real tests."""
    assert len(_registered_tool_names()) >= 30, "Expected ~36 registered tools; AST parse may be broken."


def test_every_skill_tool_reference_is_registered() -> None:
    registered = _registered_tool_names()

    drift: dict[str, set[str]] = {}
    for skill_md in SKILLS_DIR.glob("*/SKILL.md"):
        referenced = _skill_tool_references(skill_md)
        unknown = referenced - registered
        if unknown:
            drift[str(skill_md.relative_to(REPO_ROOT))] = unknown

    assert not drift, (
        "Skill files reference ufm_* symbols that are not registered as @mcp.tool in server.py:\n"
        + "\n".join(f"  {path}: {sorted(missing)}" for path, missing in sorted(drift.items()))
        + "\nEither the symbol was renamed (update the skill) or it never existed (remove it)."
    )
```

- [ ] **Step 2: Run the test against the current SKILL.md files — expect FAIL**

```bash
uv run pytest tests/test_skill_tool_lists.py -v
```

Expected: `test_every_skill_tool_reference_is_registered` FAILS with drift output for both `skills/ufm-fabric-ops/SKILL.md` and `skills/fabric-monitoring/SKILL.md` listing at least: `ufm_get_fabric_summary`, `ufm_get_switch`, `ufm_list_ports`, `ufm_get_port`, `ufm_list_links`. (`test_registered_tool_set_is_nonempty` should PASS.)

- [ ] **Step 3: Commit the failing test**

```bash
git add tests/test_skill_tool_lists.py
git commit -m "test: add skill→server tool registration check (#51)

Fails on main because skills/ufm-fabric-ops/SKILL.md and
skills/fabric-monitoring/SKILL.md cite tools (ufm_get_fabric_summary,
ufm_list_ports, ufm_get_port, ufm_list_links, ufm_get_switch) that
are not registered in server.py. Next commit rewrites both skills."
```

---

## Task 4: Rewrite the two skill files

**Files:**
- Modify: `skills/ufm-fabric-ops/SKILL.md` (full rewrite)
- Modify: `skills/fabric-monitoring/SKILL.md` (full rewrite — also fix `name:` front-matter, currently incorrectly set to `ufm-fabric-ops`)

The tool set we can cite (verified against `server.py` head-of-file decorators):

```
ufm_list_sites           ufm_set_site                 ufm_get_config
ufm_get_version          ufm_list_alarms              ufm_list_unhealthy_ports
ufm_get_unhealthy_ports_policy                        ufm_list_switches
ufm_list_events          ufm_get_concerns             ufm_get_high_ber_ports
ufm_check_high_ber_recent                             ufm_get_ports_health
ufm_check_ports_recent   ufm_check_links_recent       ufm_get_cluster_concerns
ufm_get_log              ufm_search_log               ufm_search_logs
ufm_create_log_history   ufm_download_log_history_file
ufm_create_system_dump   ufm_get_job
ufm_create_and_wait_log_history    ufm_create_and_wait_system_dump
ufm_list_pkeys           ufm_get_pkey                 ufm_add_guids_to_pkey
ufm_remove_guids_from_pkey                            ufm_remove_hosts_from_pkey
ufm_add_hosts_to_pkey    ufm_get_pkey_hosts           ufm_pkey_diff
ufm_topaz_fabric_health  ufm_topaz_port_counters
ufm_topaz_cables         ufm_topaz_switches
```

- [ ] **Step 1: Rewrite `skills/ufm-fabric-ops/SKILL.md`**

Write the complete new file content:

```markdown
---
name: ufm-fabric-ops
description: Use when investigating InfiniBand fabric issues, checking port health, UFM events, or diagnosing network topology problems. Triggers on mentions of UFM, InfiniBand, fabric, switch ports, or link errors.
---

# UFM Fabric Operations

## Real Tools (server.py)

### Health & triage
- `ufm_get_cluster_concerns` — fabric-wide concerns rolled up by host/system
- `ufm_get_concerns` — concerns within a lookback window
- `ufm_list_unhealthy_ports` / `ufm_get_unhealthy_ports_policy`
- `ufm_get_ports_health` — per-port detail (state, BER, FEC counters, peer info, alarms) for one system
- `ufm_check_ports_recent` — `ufm_get_ports_health` + recent log/event slice for the same ports
- `ufm_check_links_recent` — link-state churn for a system in a lookback window
- `ufm_get_high_ber_ports` / `ufm_check_high_ber_recent` — fabric-wide high-BER ports

### Inventory
- `ufm_list_switches` — switch inventory with health summary
- `ufm_list_alarms` — current alarms (GUIDs auto-resolved to hostnames)
- `ufm_list_events` — UFM event log

### Logs
- `ufm_get_log` / `ufm_search_log` / `ufm_search_logs` — direct log queries
- `ufm_create_log_history` / `ufm_create_and_wait_log_history` / `ufm_download_log_history_file`
- `ufm_create_system_dump` / `ufm_create_and_wait_system_dump`
- `ufm_get_job` — poll long-running jobs

### Topaz fabric health (gRPC)
- `ufm_topaz_fabric_health` — overall score
- `ufm_topaz_port_counters` — port error counters
- `ufm_topaz_cables` — cable / transceiver health
- `ufm_topaz_switches` — switch summaries

### Sites & config
- `ufm_list_sites` / `ufm_set_site` / `ufm_get_config` / `ufm_get_version`

### PKey management (write — require explicit intent)
- `ufm_list_pkeys` / `ufm_get_pkey` / `ufm_get_pkey_hosts` / `ufm_pkey_diff`
- `ufm_add_guids_to_pkey` / `ufm_remove_guids_from_pkey`
- `ufm_add_hosts_to_pkey` / `ufm_remove_hosts_from_pkey`

## Common Workflows

### Triage a fabric issue (read-only)
1. `ufm_get_cluster_concerns()` — start here, hosts ranked by recent fabric concern density
2. `ufm_list_alarms()` — currently active alarms with hostnames resolved
3. `ufm_list_events(severity="critical")` — last critical events
4. For a specific host: `ufm_get_ports_health(system="hostname-or-guid")`

### Investigate a port on one system
1. `ufm_get_ports_health(system="sw1", port_numbers=[63])` — full counters & peer info
2. `ufm_check_ports_recent(system="sw1", port_numbers=[63])` — same plus event/log slice
3. Inspect `effective_ber`, `port_fec_uncorrectable_block_counter`, `link_down_counter`, `remote_node_desc`, `remote_guid`

### List all ports on a system
- CLI: `ufm-cli ports SYSTEM_NAME` (omit ports to list all)
- CLI: `ufm-cli ports SYSTEM_NAME --errors-only` (non-Info severity)
- CLI: `ufm-cli ports SYSTEM_NAME --down-only` (physical_state != Active)
- CLI: `ufm-cli ports SYSTEM_NAME --json`
- MCP: `ufm_get_ports_health(system="sw1")` or `ufm_check_ports_recent(system="sw1")`

Output includes: speed, width, FEC mode, effective BER, FEC uncorrectable/correctable counters, symbol errors, link-down count, remote node description with GUID, peer-port summary, and matching active alarms.

### Switches
- CLI: `ufm-cli switches --json`
- CLI: `ufm-cli switches --errors-only`
- MCP: `ufm_list_switches()` or `ufm_list_switches(errors_only=True)`

### Topaz cross-checks (gRPC, per-site)
| Action | CLI |
|---|---|
| Fabric health | `ufm-cli topaz-health --site ori --json` |
| Port counters | `ufm-cli topaz-port-counters --site ori --errors-only --json` |
| Cable health | `ufm-cli topaz-cables --site ori --alarms-only --json` |
| Switch list | `ufm-cli topaz-switches --site ori --json` |

## Design notes
- All listed tools are decorated `@mcp.tool` in `src/ufm_mcp/server.py`. The CI test
  `tests/test_skill_tool_lists.py` enforces that this list cannot drift again.
- All tools accept an optional `site=` parameter when multi-site is configured.
- Write operations (system dumps, log-history downloads, PKey changes) accept a target site
  but otherwise have no `allow_write` gate today; treat them as side-effecting.
```

- [ ] **Step 2: Rewrite `skills/fabric-monitoring/SKILL.md`**

The fabric-monitoring skill currently duplicates ufm-fabric-ops verbatim AND has `name: ufm-fabric-ops` in its front-matter, which is a bug independent of #51. Give it a distinct identity focused on monitoring/triage rather than the whole tool catalog.

Write the complete new file:

```markdown
---
name: fabric-monitoring
description: Use when investigating InfiniBand fabric health, BER, port errors, UFM alarms/events, network fabric triage, or searching UFM/SM logs across one or more sites. Triggers on InfiniBand, fabric health, BER, port errors, UFM, network fabric, link issues, unhealthy ports.
---

# Fabric monitoring

Lightweight read-only triage. For the full tool catalog and write operations, see
`skills/ufm-fabric-ops/SKILL.md`.

## Tools used here
- `ufm_get_cluster_concerns` — host-ranked concerns, lookback-aware
- `ufm_get_ports_health` / `ufm_check_ports_recent` — per-port detail on one system
- `ufm_check_links_recent` — link-state churn for a system in a lookback window
- `ufm_get_high_ber_ports` / `ufm_check_high_ber_recent` — fabric-wide high-BER ports
- `ufm_list_unhealthy_ports`
- `ufm_list_alarms` / `ufm_list_events`
- `ufm_search_log` / `ufm_search_logs`
- `ufm_topaz_fabric_health` / `ufm_topaz_port_counters` — gRPC cross-check

## Triage walk

1. **Cluster-level concerns first**
   `ufm_get_cluster_concerns()` — surfaces hosts that are noisy in the fabric right now.

2. **Active alarms**
   `ufm_list_alarms()` — names resolve to hostnames automatically; alarms auto-group by description in CLI when >50% share the same message.

3. **High-BER scan (fabric-wide)**
   `ufm_get_high_ber_ports()` — flags ports whose effective BER exceeds policy thresholds.

4. **Per-host drill-down**
   `ufm_get_ports_health(system="<host-or-switch>")` — counters, FEC, peer-port info, matching alarms.
   Or with recent events: `ufm_check_ports_recent(system=..., lookback_minutes=15)`.

5. **Link churn**
   `ufm_check_links_recent(system=..., lookback_minutes=60)` — for "is the link bouncing?"

6. **Cross-check via Topaz (if a site has it)**
   `ufm_topaz_fabric_health(site=...)` and `ufm_topaz_port_counters(site=...)`.

## Output you can rely on (`ufm_get_ports_health`)
- physical/logical state, active speed/width, FEC mode
- `effective_ber`, `port_fec_uncorrectable_block_counter`, `port_fec_correctable_block_counter`
- `symbol_error_counter`, `link_down_counter`
- `remote_node_desc`, `remote_guid`, `peer_port_dname`, peer-port counter summary
- matching active alarms

## CLI shortcuts
- `ufm-cli concerns` — cluster concerns
- `ufm-cli ports SYSTEM` — port summary, with `--errors-only` / `--down-only` filters
- `ufm-cli alarms` / `ufm-cli events` — current alarms / events
- `ufm-cli ber` — high-BER scan
- `ufm-cli switches --errors-only` — switch health
```

- [ ] **Step 3: Run the skill→server registration test — expect PASS**

```bash
uv run pytest tests/test_skill_tool_lists.py -v
```

Expected: both tests PASS.

- [ ] **Step 4: Run the full unit test suite (sanity)**

```bash
uv run pytest -q -m "not integration and not e2e and not slow"
```

Expected: PASS (or no new failures vs. main).

- [ ] **Step 5: Lint**

```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
```

Expected: clean. If format check fails on the new test files, run `uv run ruff format src/ tests/` then re-run check.

- [ ] **Step 6: Commit the rewrites**

```bash
git add skills/ufm-fabric-ops/SKILL.md skills/fabric-monitoring/SKILL.md
git commit -m "docs: rewrite fabric skills to match registered tools (closes #51)

Both skills cited tools that don't exist (ufm_get_fabric_summary,
ufm_list_ports, ufm_get_port, ufm_list_links, ufm_get_switch). Replace
with the real tool set from server.py. Differentiate fabric-monitoring
(triage walk) from ufm-fabric-ops (full catalog). Fix
fabric-monitoring's front-matter \`name:\` (was 'ufm-fabric-ops')."
```

---

## Task 5: Wire CI

**Files:**
- (none — `.github/workflows/ci.yml` already runs `uv run pytest -q -m "not integration and not e2e and not slow"`)

The new tests live under `tests/`, are non-marked (so they run in the default selection), and have no external dependencies. They are picked up automatically. No CI change required.

- [ ] **Step 1: Verify by inspection**

```bash
grep -n 'pytest' .github/workflows/ci.yml
```

Confirm the existing `Unit tests` step does not exclude `tests/test_marketplace_pin.py` or `tests/test_skill_tool_lists.py`.

- [ ] **Step 2: Push and watch CI**

```bash
git push -u origin chore/fix-drift-marketplace-and-skills
gh pr create --title "Fix marketplace pin & skill tool list drift (#50, #51)" --body "$(cat <<'EOF'
## Summary
- Bumps the `git+https://github.com/vhspace/ufm-mcp@vX.Y.Z` pin in `.mcp.json` and `.claude-plugin/plugin.json` from v0.4.1 → v1.6.0 to match `pyproject.toml`. Closes #50.
- Rewrites `skills/ufm-fabric-ops/SKILL.md` and `skills/fabric-monitoring/SKILL.md` against the real `@mcp.tool` set in `server.py`. Closes #51.
- Adds two pytest checks (`tests/test_marketplace_pin.py`, `tests/test_skill_tool_lists.py`) that fail loudly if either drift returns.

## Test plan
- [x] `uv run pytest tests/test_marketplace_pin.py tests/test_skill_tool_lists.py -v` — passes locally
- [x] `uv run pytest -q -m "not integration and not e2e and not slow"` — full suite still passes
- [x] `uv run ruff check src/ tests/` — clean
- [ ] CI green on PR
EOF
)"
```

---

## Self-review

### Spec coverage

| Issue ask | Task |
|---|---|
| #50 — bump pin to current release tag | Task 2 |
| #50 — CI check that pinned tag matches package version | Task 1 (`tests/test_marketplace_pin.py`) |
| #51 — rewrite both skill files against real tool list | Task 4 |
| #51 — CI test parsing every `ufm_*` symbol from skills, asserting registration | Task 3 (`tests/test_skill_tool_lists.py`) |

All four asks covered.

### Placeholder scan
None. Each test file is shown in full; each skill file is shown in full; each manifest edit specifies exact line numbers.

### Type consistency
- Function names referenced in the skill rewrites match the registered set discovered in Pre-flight Step 0b.
- The regex in `test_marketplace_pin.py` uses the literal `git+https://github.com/vhspace/ufm-mcp@v` prefix that appears in both manifest files — verified by grepping the current files.
- The AST walk in `test_skill_tool_lists.py` checks for `@mcp.tool(...)` calls; this matches `server.py`'s decorator usage pattern (verified at `server.py:225`, `:239`, `:257`, `:278`, `:297`, `:379`, `:404`, `:426`, etc.).
