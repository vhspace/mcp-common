# Batch A — Stale-anchor cluster (fix + workaround + diagnose + recover)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make UFM port queries robust to stale system-anchor GUIDs. Fix `ufm_get_ports_health`'s GUID-only filter, add a port-GUID workaround, ship an `inventory_doctor` diagnostic, and document the recovery path.

**Architecture:** Four tightly-coupled changes around the same root cause:
1. **#49 bug fix** — when UFM's `system.guid` no longer matches all current HCAs (post-rebuild HCA swap), `?system=<guid>` returns ghost-only results. Detect the mismatch and retry with `?system_name=<name>`; surface the divergence as `inventory_warnings` so callers can see it.
2. **#56 sidedoor** — let callers skip system resolution entirely by querying with `port_guid` or `node_guid` derived from `ibstat` on the host.
3. **#48 diagnostic** — a new `ufm_inventory_doctor` tool that cross-checks `system_record.ports[]`, `?system_name=`, and `?system=` and reports an `inferred_diagnosis`.
4. **#52 runbook** — a new skill `ufm-stale-inventory-recovery` documenting the symptoms and the `pcs resource restart ufm-enterprise` recovery.

**Tech Stack:** Python 3.12, FastMCP 3, typer, httpx, pytest (with `MagicMock` for client mocking). No new runtime deps.

**Closes:** vhspace/ufm-mcp#49, vhspace/ufm-mcp#56, vhspace/ufm-mcp#48, vhspace/ufm-mcp#52.

**PR strategy:** One PR with four commits (one per issue). The commits build on each other but each is independently reviewable; if review pressure splits the PR, the natural seam is between #49+#56 (read-path changes) and #48+#52 (new diagnostic + skill).

---

## File Structure

| File | Purpose | Action |
|---|---|---|
| `src/ufm_mcp/server.py` | All `@mcp.tool` definitions | Modify `ufm_get_ports_health` (Tasks 1, 2). Add new `ufm_inventory_doctor` (Task 3). |
| `src/ufm_mcp/cli.py` | Typer CLI | Modify `ports` cmd (Task 2). Add `inventory_doctor` cmd (Task 3). |
| `tests/test_server_tools.py` | Tool-level unit tests | Add stale-anchor + port-guid + doctor tests (Tasks 1, 2, 3). |
| `tests/test_cli.py` | NEW — CLI smoke tests via typer's CliRunner | Create with port-guid + inventory-doctor coverage (Tasks 2, 3). |
| `skills/ufm-stale-inventory-recovery/SKILL.md` | NEW skill | Create (Task 4). |
| `mcp-plugin.toml` | Plugin manifest declaring shipped skills | Modify: register the new skill (Task 4). |

---

## Pre-flight

- [ ] **Step 0a: Branch from main**

```bash
cd /workspaces/together/ufm-mcp
git status                                  # expect: clean tree on main
git checkout -b feat/stale-anchor-cluster
```

- [ ] **Step 0b: Note the existing test pattern**

```bash
grep -n 'configured_server\|MagicMock\|get_json' tests/test_server_tools.py | head -20
```

The fixture at `tests/test_server_tools.py:14-34` (`configured_server`) gives you `(srv, mock_client)`. `mock_client.get_json.return_value = ...` for one-shot mocks; `mock_client.get_json.side_effect = [...]` for sequential responses.

---

## Task 1 — Fix `ufm_get_ports_health` stale-anchor (#49)

**Files:**
- Modify: `src/ufm_mcp/server.py:846-877` (the GUID query block inside `ufm_get_ports_health`)
- Modify: `src/ufm_mcp/server.py:946-961` (the return dict — add `inventory_warnings`)
- Modify: `tests/test_server_tools.py` (append new tests)

### Behavioral spec

After resolving `system_obj` from `/resources/systems`:

1. Query `/resources/ports?system=<system_guid>` (current behavior). Call this set `guid_ports`.
2. Compute `expected_port_count = len(system_obj.get("ports", []))` if available.
3. **Detection:** if `len(guid_ports) < expected_port_count` OR `len(guid_ports) == 0` (when `expected_port_count > 0`), the anchor is stale.
4. **Fallback:** retry with `params={"system_name": system_obj["system_name"]}` and treat that response as authoritative. Call it `name_ports`.
5. **Reconcile:** ports keyed by `name` field — `ghost_only` = in `guid_ports` but NOT in `name_ports`; `name_only` = in `name_ports` but NOT in `guid_ports`.
6. Add to the response top-level dict (only when fallback fired):
   ```python
   "inventory_warnings": {
       "stale_anchor_detected": True,
       "anchor_guid": system_guid,
       "system_name": system_obj["system_name"],
       "guid_query_count": len(guid_ports),
       "name_query_count": len(name_ports),
       "expected_port_count": expected_port_count,
       "ghost_port_names": [str(p.get("name")) for p in ghost_only],
       "remediation_hint": (
           "UFM's anchor GUID for this system does not match all current ports. "
           "If post-HCA-swap, see skills/ufm-stale-inventory-recovery/SKILL.md "
           "(or run `ufm-cli inventory-doctor <system>` for a full breakdown)."
       ),
   }
   ```
7. The returned `ports` list comes from `name_ports` (or `guid_ports` if fallback didn't fire), so callers always see the correct full set.

### Tests first

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_server_tools.py`:

```python
def _make_systems_payload(name: str, guid: str, port_count: int) -> list[dict]:
    """Build a /resources/systems response for a single system with `port_count` declared ports."""
    return [
        {
            "system_name": name,
            "name": name,
            "guid": guid,
            "system_guid": guid,
            "model": "QM9700",
            "vendor": "Mellanox",
            "severity": "Info",
            "state": "Active",
            "technology": "InfiniBand",
            "ports": [{"number": i + 1} for i in range(port_count)],
        }
    ]


def _make_port(system_name: str, system_guid: str, number: int, port_guid: str) -> dict:
    return {
        "name": f"{port_guid}_{number}",
        "guid": port_guid,
        "number": number,
        "dname": f"Port {number}",
        "physical_state": "Active",
        "logical_state": "Active",
        "severity": "Info",
        "system_name": system_name,
        "systemID": system_guid,
        "active_speed": "ndr",
        "active_width": "4x",
    }


def test_ufm_get_ports_health_stale_anchor_falls_back_to_system_name(configured_server) -> None:
    """When ?system=<guid> returns ghost-only results, retry with ?system_name=<name>."""
    srv, mock_client = configured_server
    name = "b65c909e-16"
    stale_guid = "aaaa1111aaaa1111"
    fresh_node_guid = "bbbb2222bbbb2222"

    systems = _make_systems_payload(name, stale_guid, port_count=7)

    # GUID query → 1 ghost port; name query → 7 real ports.
    ghost_ports = [_make_port(name, stale_guid, 1, "0xghostghostghost")]
    real_ports = [_make_port(name, fresh_node_guid, i, f"0xrealhca{i:02d}") for i in range(1, 8)]

    mock_client.get_json.side_effect = [
        systems,                  # /resources/systems
        ghost_ports,              # /resources/ports?system=<stale_guid>
        real_ports,               # /resources/ports?system_name=<name>   (fallback)
        [],                       # peer-port resolution (no peers)
        [],                       # alarms
    ]

    result = srv.ufm_get_ports_health(system=name, include_peer_ports=False, include_alarms=False)

    assert result["ok"] is True
    assert len(result["ports"]) == 7
    assert "inventory_warnings" in result
    iw = result["inventory_warnings"]
    assert iw["stale_anchor_detected"] is True
    assert iw["anchor_guid"] == stale_guid
    assert iw["system_name"] == name
    assert iw["guid_query_count"] == 1
    assert iw["name_query_count"] == 7
    assert "0xghostghostghost_1" in iw["ghost_port_names"]


def test_ufm_get_ports_health_clean_anchor_no_warning(configured_server) -> None:
    """When ?system=<guid> returns the full set, no fallback fires and no warning is added."""
    srv, mock_client = configured_server
    name = "hci-clean-01"
    guid = "ccccdddd33334444"

    systems = _make_systems_payload(name, guid, port_count=4)
    ports = [_make_port(name, guid, i, f"0xclean{i:02d}") for i in range(1, 5)]

    mock_client.get_json.side_effect = [systems, ports, [], []]

    result = srv.ufm_get_ports_health(system=name, include_peer_ports=False, include_alarms=False)

    assert result["ok"] is True
    assert len(result["ports"]) == 4
    assert "inventory_warnings" not in result
```

- [ ] **Step 2: Run the new tests — expect FAIL**

```bash
uv run pytest tests/test_server_tools.py::test_ufm_get_ports_health_stale_anchor_falls_back_to_system_name tests/test_server_tools.py::test_ufm_get_ports_health_clean_anchor_no_warning -v
```

Expected: `test_ufm_get_ports_health_stale_anchor_falls_back_to_system_name` FAILS (no fallback yet, returns 1 port and no `inventory_warnings`). The clean-anchor test should already PASS.

### Implementation

- [ ] **Step 3: Implement the fallback in `ufm_get_ports_health`**

In `src/ufm_mcp/server.py`, replace the block from `# fetch ports` through the end of port-list materialization. The current code at `src/ufm_mcp/server.py:852-877`:

```python
    params: dict[str, Any] = {"system": system_guid}
    if include_cable_info:
        params["cable_info"] = "true"

    all_ports = normalize_list_payload(
        client.get_json(f"{resources_base}/resources/ports", params=params)
    )
    ports_by_number: dict[int, dict[str, Any]] = {}
    for p in all_ports:
        try:
            ports_by_number[int(p["number"])] = p
        except (TypeError, ValueError):
            continue

    if port_numbers is not None:
        # ... existing select / missing logic ...
```

Replace with:

```python
    expected_port_count = len(system_obj.get("ports") or [])
    inventory_warnings: dict[str, Any] | None = None

    cable_param: dict[str, str] = {"cable_info": "true"} if include_cable_info else {}

    guid_params: dict[str, Any] = {"system": system_guid, **cable_param}
    guid_ports = normalize_list_payload(
        client.get_json(f"{resources_base}/resources/ports", params=guid_params)
    )

    all_ports = guid_ports
    stale_anchor = expected_port_count > 0 and len(guid_ports) < expected_port_count

    if stale_anchor:
        sys_name = str(system_obj.get("system_name") or system_obj.get("name") or "").strip()
        if sys_name:
            name_params: dict[str, Any] = {"system_name": sys_name, **cable_param}
            name_ports = normalize_list_payload(
                client.get_json(f"{resources_base}/resources/ports", params=name_params)
            )
            if len(name_ports) > len(guid_ports):
                all_ports = name_ports
                ghost_names = {
                    str(p.get("name")) for p in guid_ports
                } - {str(p.get("name")) for p in name_ports}
                inventory_warnings = {
                    "stale_anchor_detected": True,
                    "anchor_guid": system_guid,
                    "system_name": sys_name,
                    "guid_query_count": len(guid_ports),
                    "name_query_count": len(name_ports),
                    "expected_port_count": expected_port_count,
                    "ghost_port_names": sorted(ghost_names),
                    "remediation_hint": (
                        "UFM's anchor GUID for this system does not match all current ports. "
                        "If post-HCA-swap, see skills/ufm-stale-inventory-recovery/SKILL.md "
                        "(or run `ufm-cli inventory-doctor <system>` for a full breakdown)."
                    ),
                }

    ports_by_number: dict[int, dict[str, Any]] = {}
    for p in all_ports:
        try:
            ports_by_number[int(p["number"])] = p
        except (TypeError, ValueError):
            continue
```

Then in the return dict at `src/ufm_mcp/server.py:946-961`, add the warning when present. Replace:

```python
    return _serializable_dict(
        {
            "ok": True,
            "system": { ... },
            "ports": [port_summary(p) for p in selected_ports],
            "missing_ports": missing_ports,
        }
    )
```

with:

```python
    response: dict[str, Any] = {
        "ok": True,
        "system": {
            "system_name": system_obj.get("system_name"),
            "guid": system_guid,
            "model": system_obj.get("model"),
            "vendor": system_obj.get("vendor"),
            "severity": system_obj.get("severity"),
            "state": system_obj.get("state"),
            "technology": system_obj.get("technology"),
        },
        "ports": [port_summary(p) for p in selected_ports],
        "missing_ports": missing_ports,
    }
    if inventory_warnings is not None:
        response["inventory_warnings"] = inventory_warnings
    return _serializable_dict(response)
```

- [ ] **Step 4: Run the new tests — expect PASS**

```bash
uv run pytest tests/test_server_tools.py::test_ufm_get_ports_health_stale_anchor_falls_back_to_system_name tests/test_server_tools.py::test_ufm_get_ports_health_clean_anchor_no_warning -v
```

Expected: both PASS.

- [ ] **Step 5: Run the full test suite (regression check)**

```bash
uv run pytest -q -m "not integration and not e2e and not slow"
```

Expected: PASS. If pre-existing tests for `ufm_get_ports_health` break, the side_effect chain in those tests likely needs an extra entry for the fallback call — but only if their fixture data triggers `stale_anchor=True` (i.e. `system_obj.ports` was populated AND the mocked guid query was short). Inspect any failures and patch fixtures accordingly.

- [ ] **Step 6: Commit**

```bash
git add src/ufm_mcp/server.py tests/test_server_tools.py
git commit -m "fix: ufm_get_ports_health falls back to system_name on stale anchor (#49)

When /resources/ports?system=<system.guid> returns fewer ports than the
system record's ports[] length, retry with ?system_name=<name> and use
the larger result. Surface anchor/name divergence in a new top-level
inventory_warnings field so callers can see it. Empirical case: Ori
post-HCA-swap host returned 1 ghost port via guid, 7 real ports via
name."
```

---

## Task 2 — Add `--port-guid` / `--node-guid` lookup (#56)

**Files:**
- Modify: `src/ufm_mcp/server.py:789-817` (signature & validation), and a small new branch before the system-resolution block.
- Modify: `src/ufm_mcp/cli.py:410-445` (the `ports` command — add flags, plumb through).
- Create: `tests/test_cli.py` (typer CliRunner smoke test for `--port-guid`).
- Modify: `tests/test_server_tools.py` (tool-level test for `port_guid=` and `node_guid=` params).

### Behavioral spec

Add two optional kwargs to `ufm_get_ports_health`:
- `port_guid: str | None = None` — query `/resources/ports?guid=<port_guid>`, return only that port. `system` becomes optional in this mode.
- `node_guid: str | None = None` — query `/resources/ports?system=<node_guid>`, skip the `/resources/systems` lookup entirely. `system` becomes optional.

Validation:
- Exactly one of `system`, `port_guid`, `node_guid` must be provided. If zero or more than one, raise `ToolError("Provide exactly one of: system, port_guid, node_guid")`.
- When `port_guid` or `node_guid` is provided, the response's `system` block reports what the port records carry (`system_name`, `systemID` from the first port), with `inventory_source: "port_guid_query"` or `"node_guid_query"` to make it clear no `/resources/systems` lookup happened.

CLI mirrors:
- `ufm-cli ports --port-guid 0xa088c20300556b96 -s apld2`
- `ufm-cli ports --node-guid bbbb2222bbbb2222 -s apld2`
- `--help` text mentions all three modes.

### Tests first

- [ ] **Step 1: Write the failing tool-level test**

Append to `tests/test_server_tools.py`:

```python
def test_ufm_get_ports_health_port_guid_skips_systems_lookup(configured_server) -> None:
    """With port_guid=, query /resources/ports?guid=<port_guid> directly. No /resources/systems call."""
    srv, mock_client = configured_server
    pg = "0xa088c20300556b96"
    sys_name = "ori-host-024"
    sys_guid = "bbbb2222bbbb2222"

    port_record = _make_port(sys_name, sys_guid, 1, pg)
    mock_client.get_json.side_effect = [
        [port_record],  # /resources/ports?guid=<port_guid>
        [],             # peer-port resolution (no peers)
        [],             # alarms
    ]

    result = srv.ufm_get_ports_health(
        system="",
        port_guid=pg,
        include_peer_ports=False,
        include_alarms=False,
    )

    assert result["ok"] is True
    assert len(result["ports"]) == 1
    assert result["ports"][0]["name"] == f"{pg}_1"
    assert result["system"]["system_name"] == sys_name
    assert result.get("inventory_source") == "port_guid_query"

    # Verify no /resources/systems call happened.
    called_paths = [c.args[0] for c in mock_client.get_json.call_args_list]
    assert all("/resources/systems" not in p for p in called_paths), called_paths


def test_ufm_get_ports_health_requires_exactly_one_selector(configured_server) -> None:
    srv, _ = configured_server
    from fastmcp.exceptions import ToolError

    with pytest.raises(ToolError, match="exactly one"):
        srv.ufm_get_ports_health(system="")  # zero selectors

    with pytest.raises(ToolError, match="exactly one"):
        srv.ufm_get_ports_health(system="x", port_guid="0xfoo")  # two selectors
```

- [ ] **Step 2: Write the failing CLI test**

Create `tests/test_cli.py`:

```python
"""Smoke tests for ufm-cli, dispatched through typer's CliRunner."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from ufm_mcp.cli import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def _stub_init():
    """Skip the env-loading + site-manager init that ufm-cli does at startup."""
    with patch("ufm_mcp.cli._ensure_init"):
        yield


def test_ports_with_port_guid_invokes_tool_with_port_guid_kwarg() -> None:
    fake_response = {
        "ok": True,
        "system": {"system_name": "ori-024", "guid": "bbbb"},
        "health": {
            "system": {"system_name": "ori-024", "model": "?", "state": "?"},
            "ports": [{"number": 1, "dname": "mlx5_6"}],
        },
        "logs": {},
    }
    with patch("ufm_mcp.server.ufm_check_ports_recent", return_value=fake_response) as mock_tool:
        result = runner.invoke(app, ["ports", "--port-guid", "0xa088c20300556b96", "-s", "apld2"])

    assert result.exit_code == 0, result.output
    mock_tool.assert_called_once()
    kwargs = mock_tool.call_args.kwargs
    assert kwargs["port_guid"] == "0xa088c20300556b96"
    assert kwargs["site"] == "apld2"
```

- [ ] **Step 3: Run the new tests — expect FAIL**

```bash
uv run pytest tests/test_server_tools.py::test_ufm_get_ports_health_port_guid_skips_systems_lookup tests/test_server_tools.py::test_ufm_get_ports_health_requires_exactly_one_selector tests/test_cli.py -v
```

Expected: all FAIL — `port_guid` is not yet a parameter.

### Implementation

- [ ] **Step 4: Update `ufm_get_ports_health` signature and add the early branches**

In `src/ufm_mcp/server.py:789-817`, modify the signature. Change:

```python
def ufm_get_ports_health(
    system: Annotated[
        str, Field(description="System name or GUID (e.g. hci-oh1-ibs11 or fc6a1c0300b2ed00)")
    ],
    port_numbers: Annotated[ ... ] = None,
    ...
    site: SiteParam = None,
) -> dict[str, Any]:
```

to:

```python
def ufm_get_ports_health(
    system: Annotated[
        str,
        Field(description="System name or GUID. Omit (pass empty) when using port_guid or node_guid."),
    ] = "",
    port_numbers: Annotated[ ... ] = None,
    port_guid: Annotated[
        str | None,
        Field(default=None, description="Port GUID (e.g. 0xa088c20300556b96 from `ibstat`). Bypasses system resolution."),
    ] = None,
    node_guid: Annotated[
        str | None,
        Field(default=None, description="HCA/system node GUID. Bypasses /resources/systems lookup."),
    ] = None,
    ...
    site: SiteParam = None,
) -> dict[str, Any]:
```

Then immediately after `client = sites.get_client(site)` and before the existing `system_query = system.strip()` line, insert:

```python
    selectors_provided = sum(bool(x) for x in (system, port_guid, node_guid))
    if selectors_provided != 1:
        raise ToolError("Provide exactly one of: system, port_guid, node_guid")

    cfg = sites.get_config(site)
    resources_base = cfg.ufm_resources_base_path
    api_base = cfg.ufm_api_base_path

    if port_guid:
        ports_payload = client.get_json(
            f"{resources_base}/resources/ports", params={"guid": port_guid}
        )
        return _ports_health_from_records(
            normalize_list_payload(ports_payload),
            client=client,
            resources_base=resources_base,
            api_base=api_base,
            port_numbers=port_numbers,
            include_peer_ports=include_peer_ports,
            include_alarms=include_alarms,
            errors_only=errors_only,
            down_only=down_only,
            inventory_source="port_guid_query",
        )

    if node_guid:
        ports_payload = client.get_json(
            f"{resources_base}/resources/ports", params={"system": node_guid}
        )
        return _ports_health_from_records(
            normalize_list_payload(ports_payload),
            client=client,
            resources_base=resources_base,
            api_base=api_base,
            port_numbers=port_numbers,
            include_peer_ports=include_peer_ports,
            include_alarms=include_alarms,
            errors_only=errors_only,
            down_only=down_only,
            inventory_source="node_guid_query",
        )
```

(Note: the `cfg/resources_base/api_base` lines move up because the original code computed them after `system_query`. The original code already had them — keep only one copy by deleting the duplicate that follows `system_query = system.strip()`.)

- [ ] **Step 5: Extract the response-building helper**

The new branches and the existing system-name path both build a port-health response from a list of port records. Pull the shared logic into a helper. Add this near `_find_system` in `src/ufm_mcp/server.py`:

```python
def _ports_health_from_records(
    ports: list[dict[str, Any]],
    *,
    client: Any,
    resources_base: str,
    api_base: str,
    port_numbers: list[int] | None,
    include_peer_ports: bool,
    include_alarms: bool,
    errors_only: bool,
    down_only: bool,
    inventory_source: str | None = None,
    system_obj: dict[str, Any] | None = None,
    inventory_warnings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a ufm_get_ports_health response dict from a list of port records.

    Used by both the system-name path and the port_guid/node_guid sidedoors.
    """
    ports_by_number: dict[int, dict[str, Any]] = {}
    for p in ports:
        try:
            ports_by_number[int(p["number"])] = p
        except (TypeError, ValueError):
            continue

    if port_numbers is not None:
        port_set = {int(p) for p in port_numbers}
        selected_ports: list[dict[str, Any]] = []
        missing_ports: list[int] = []
        for num in sorted(port_set):
            if num in ports_by_number:
                selected_ports.append(ports_by_number[num])
            else:
                missing_ports.append(num)
    else:
        selected_ports = [ports_by_number[n] for n in sorted(ports_by_number)]
        missing_ports = []

    if down_only:
        selected_ports = [
            p for p in selected_ports if str(p.get("physical_state", "")).lower() != "active"
        ]
    if errors_only:
        selected_ports = [
            p
            for p in selected_ports
            if str(p.get("severity", "")).strip().lower() not in ("", "info")
            or str(p.get("high_ber_severity", "")).strip() != ""
        ]

    peer_summaries = (
        _resolve_peer_summaries(client, resources_base, selected_ports)
        if include_peer_ports
        else {}
    )
    alarms_by_object = (
        _collect_port_alarms(client, api_base, selected_ports, include_peer_ports)
        if include_alarms
        else {}
    )

    def port_summary(p: dict[str, Any]) -> dict[str, Any]:
        # ... existing port_summary body — copy verbatim from server.py:902-944 ...

    if system_obj is not None:
        system_block = {
            "system_name": system_obj.get("system_name"),
            "guid": str(system_obj.get("guid") or system_obj.get("system_guid") or ""),
            "model": system_obj.get("model"),
            "vendor": system_obj.get("vendor"),
            "severity": system_obj.get("severity"),
            "state": system_obj.get("state"),
            "technology": system_obj.get("technology"),
        }
    else:
        # Sidedoor path: derive system identity from the port records themselves.
        first = ports[0] if ports else {}
        system_block = {
            "system_name": first.get("system_name"),
            "guid": str(first.get("systemID") or first.get("system_guid") or ""),
            "model": None,
            "vendor": None,
            "severity": None,
            "state": None,
            "technology": None,
        }

    response: dict[str, Any] = {
        "ok": True,
        "system": system_block,
        "ports": [port_summary(p) for p in selected_ports],
        "missing_ports": missing_ports,
    }
    if inventory_source is not None:
        response["inventory_source"] = inventory_source
    if inventory_warnings is not None:
        response["inventory_warnings"] = inventory_warnings
    return _serializable_dict(response)
```

(Lift `port_summary` out as a module-level function or keep it inlined inside the helper — both work; the latter is the smallest diff.)

Then refactor the existing system-name path (the body after the early `port_guid`/`node_guid` returns) to call `_ports_health_from_records(..., system_obj=system_obj, inventory_warnings=inventory_warnings)`.

- [ ] **Step 6: Add `--port-guid` / `--node-guid` to `ufm-cli ports`**

In `src/ufm_mcp/cli.py:410-445`, modify the `ports` command:

```python
@app.command()
def ports(
    system: str | None = typer.Argument(
        None, help="System name or GUID (omit when using --port-guid or --node-guid)"
    ),
    port_numbers: str | None = typer.Argument(
        None, help="Comma-separated port numbers (e.g. 63,64). Omit to list all."
    ),
    site: str | None = typer.Option(None, "--site", "-s", help="Target site"),
    lookback: int = typer.Option(15, "--lookback", "-l", help="Lookback window in minutes"),
    port_guid: str | None = typer.Option(
        None, "--port-guid", help="Port GUID (e.g. 0xa088c20300556b96 from `ibstat`)."
    ),
    node_guid: str | None = typer.Option(
        None, "--node-guid", help="HCA/system node GUID; bypasses system-name resolution."
    ),
    errors_only: bool = typer.Option(
        False, "--errors-only", help="Only show ports with non-Info severity"
    ),
    down_only: bool = typer.Option(
        False, "--down-only", help="Only show ports whose physical_state is not Active"
    ),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Check port health + recent logs/events for ports on a system.

    Selectors (provide exactly one):
      - SYSTEM positional       — system name or GUID
      - --port-guid <guid>      — pivot from `ibstat` Port GUID
      - --node-guid <guid>      — HCA/system node GUID

    Specify port numbers to inspect specific ports, or omit to list all.
    Use --errors-only / --down-only to filter large results.
    """
    _ensure_init()
    from ufm_mcp.server import ufm_check_ports_recent

    selectors = sum(bool(x) for x in (system, port_guid, node_guid))
    if selectors != 1:
        typer.echo("Error: provide exactly one of: SYSTEM, --port-guid, --node-guid", err=True)
        raise typer.Exit(2)

    nums = [int(p.strip()) for p in port_numbers.split(",") if p.strip()] if port_numbers else None
    result = ufm_check_ports_recent(
        system=system or "",
        port_numbers=nums,
        port_guid=port_guid,
        node_guid=node_guid,
        lookback_minutes=lookback,
        errors_only=errors_only,
        down_only=down_only,
        site=site,
    )
    # ... existing rendering logic unchanged ...
```

`ufm_check_ports_recent` (server.py:1078) calls `ufm_get_ports_health` internally; thread `port_guid` and `node_guid` through that wrapper too. Open `server.py:1078` and add the same kwargs; pass them straight through to the inner call.

- [ ] **Step 7: Run the failing tests — expect PASS**

```bash
uv run pytest tests/test_server_tools.py::test_ufm_get_ports_health_port_guid_skips_systems_lookup tests/test_server_tools.py::test_ufm_get_ports_health_requires_exactly_one_selector tests/test_cli.py -v
```

Expected: all PASS.

- [ ] **Step 8: Re-run the full unit suite**

```bash
uv run pytest -q -m "not integration and not e2e and not slow"
```

Expected: PASS. The Task 1 stale-anchor test should still pass because the fallback path now lives inside `_ports_health_from_records` — verify it does.

- [ ] **Step 9: Commit**

```bash
git add src/ufm_mcp/server.py src/ufm_mcp/cli.py tests/test_server_tools.py tests/test_cli.py
git commit -m "feat: add port_guid/node_guid lookup to ufm_get_ports_health (#56)

Lets agents pivot from \`ibstat\` Port GUID or node GUID directly,
skipping the /resources/systems lookup that's the choke point in the
stale-anchor case (#49). New \`ufm-cli ports --port-guid <guid>\` and
\`--node-guid <guid>\` flags. Refactor: extract _ports_health_from_records
helper shared by all three input modes."
```

---

## Task 3 — `ufm_inventory_doctor` tool + CLI (#48)

**Files:**
- Modify: `src/ufm_mcp/server.py` (add new tool near `ufm_get_ports_health`)
- Modify: `src/ufm_mcp/cli.py` (add `inventory-doctor` typer command)
- Modify: `tests/test_server_tools.py` (tool-level tests, ≥3 cases)
- Modify: `tests/test_cli.py` (smoke test for the CLI command)

### Behavioral spec

Signature:

```python
@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
@mcp_remediation_wrapper(project_repo="vhspace/ufm-mcp")
def ufm_inventory_doctor(
    system: Annotated[str, Field(description="System name to diagnose")],
    site: SiteParam = None,
) -> dict[str, Any]:
    ...
```

Cross-checks three sources for one `system_name`:

1. `system_record` — find by name in `/resources/systems`. Capture `guid` (the anchor) and `len(system_record.ports)`.
2. `ports_by_name` — `/resources/ports?system_name=<name>` → live port list for the host.
3. `ports_by_guid` — `/resources/ports?system=<system_record.guid>` → ports under the anchor.

Reconcile:
- `record_port_names`: `{p["name"] for p in system_record["ports"]}`  (use `name` field; if absent, fall back to `f"{system_guid}_{p['number']}"`)
- `name_port_names`: `{p["name"] for p in ports_by_name}`
- `guid_port_names`: `{p["name"] for p in ports_by_guid}`
- `ghost_ports` = `record_port_names - name_port_names` (in record but not on host)
- `name_only` = `name_port_names - record_port_names` (on host but not anchored)

`inferred_diagnosis`:
- `clean` — all three sets agree
- `stale_anchor` — `len(ports_by_guid) < len(ports_by_name)` AND `len(ports_by_name) > 0`
- `ghost_ports` — `len(ghost_ports) > 0` AND `len(name_port_names) >= len(record_port_names)`
- `host_node_desc_missing` — `len(ports_by_name) == 0` AND `len(ports_by_guid) > 0` (UFM only knows the anchor; host stopped reporting node desc)
- `unknown` — anything else

Response:

```python
{
    "ok": True,
    "system": {"name": ..., "anchor_guid": ..., "anchor_record_port_count": ...},
    "counts": {
        "record_ports": len(record_port_names),
        "ports_by_name": len(name_port_names),
        "ports_by_guid": len(guid_port_names),
    },
    "ghost_ports": sorted(ghost_ports),
    "name_only_ports": sorted(name_only),
    "inferred_diagnosis": "stale_anchor",
    "remediation_hint": (
        "On the UFM HA primary: `sudo pcs resource restart ufm-enterprise`. "
        "Restarts UFM model layer (~1-2 min downtime), zero fabric impact, "
        "rebuilds inventory cache. See skills/ufm-stale-inventory-recovery."
    ),
}
```

### Tests first

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_server_tools.py`:

```python
def test_inventory_doctor_clean(configured_server) -> None:
    srv, mock_client = configured_server
    name = "hci-clean-01"
    guid = "ccccdddd33334444"

    systems = _make_systems_payload(name, guid, port_count=4)
    # Make record port names match what the live queries return.
    systems[0]["ports"] = [{"number": i, "name": f"0xclean{i:02d}_{i}"} for i in range(1, 5)]
    by_name = [_make_port(name, guid, i, f"0xclean{i:02d}") for i in range(1, 5)]
    by_guid = list(by_name)

    mock_client.get_json.side_effect = [systems, by_name, by_guid]

    result = srv.ufm_inventory_doctor(system=name)
    assert result["ok"] is True
    assert result["inferred_diagnosis"] == "clean"
    assert result["counts"]["record_ports"] == 4
    assert result["counts"]["ports_by_name"] == 4
    assert result["counts"]["ports_by_guid"] == 4
    assert result["ghost_ports"] == []


def test_inventory_doctor_stale_anchor(configured_server) -> None:
    srv, mock_client = configured_server
    name = "b65c909e-16"
    stale_guid = "aaaa1111aaaa1111"
    fresh_guid = "bbbb2222bbbb2222"

    systems = _make_systems_payload(name, stale_guid, port_count=7)
    by_name = [_make_port(name, fresh_guid, i, f"0xreal{i:02d}") for i in range(1, 8)]
    by_guid = [_make_port(name, stale_guid, 1, "0xghost01")]  # only ghost port lives under stale guid

    mock_client.get_json.side_effect = [systems, by_name, by_guid]

    result = srv.ufm_inventory_doctor(system=name)
    assert result["inferred_diagnosis"] == "stale_anchor"
    assert result["counts"]["ports_by_guid"] == 1
    assert result["counts"]["ports_by_name"] == 7
    assert "pcs resource restart ufm-enterprise" in result["remediation_hint"]


def test_inventory_doctor_host_node_desc_missing(configured_server) -> None:
    srv, mock_client = configured_server
    name = "host-no-desc"
    guid = "1111222233334444"

    systems = _make_systems_payload(name, guid, port_count=4)
    by_name: list[dict] = []  # host stopped reporting node description
    by_guid = [_make_port(name, guid, i, f"0xq{i:02d}") for i in range(1, 5)]

    mock_client.get_json.side_effect = [systems, by_name, by_guid]

    result = srv.ufm_inventory_doctor(system=name)
    assert result["inferred_diagnosis"] == "host_node_desc_missing"
```

Append to `tests/test_cli.py`:

```python
def test_inventory_doctor_cli_renders_text() -> None:
    fake = {
        "ok": True,
        "system": {"name": "b65c909e-16", "anchor_guid": "aaaa1111", "anchor_record_port_count": 7},
        "counts": {"record_ports": 7, "ports_by_name": 7, "ports_by_guid": 1},
        "ghost_ports": ["0xghost01_1"],
        "name_only_ports": [],
        "inferred_diagnosis": "stale_anchor",
        "remediation_hint": "On the UFM HA primary: `sudo pcs resource restart ufm-enterprise`.",
    }
    with patch("ufm_mcp.server.ufm_inventory_doctor", return_value=fake):
        result = runner.invoke(app, ["inventory-doctor", "b65c909e-16", "-s", "ori"])

    assert result.exit_code == 0, result.output
    assert "stale_anchor" in result.output
    assert "ports_by_name=7" in result.output or "Ports by name" in result.output
    assert "pcs resource restart ufm-enterprise" in result.output
```

- [ ] **Step 2: Run the failing tests — expect FAIL**

```bash
uv run pytest tests/test_server_tools.py -k inventory_doctor tests/test_cli.py::test_inventory_doctor_cli_renders_text -v
```

Expected: all FAIL — tool and CLI command don't exist yet.

### Implementation

- [ ] **Step 3: Implement `ufm_inventory_doctor` in `server.py`**

Place it next to `ufm_get_ports_health` (around line 962, after `_find_system`). Sketch:

```python
@mcp.tool(
    annotations={"readOnlyHint": True, "openWorldHint": True},
)
@mcp_remediation_wrapper(project_repo="vhspace/ufm-mcp")
def ufm_inventory_doctor(
    system: Annotated[str, Field(description="System name to diagnose (e.g. b65c909e-16)")],
    site: SiteParam = None,
) -> dict[str, Any]:
    """Cross-check UFM's three inventory views for one system; suggest remediation."""
    client = sites.get_client(site)
    cfg = sites.get_config(site)
    resources_base = cfg.ufm_resources_base_path

    systems = client.get_json(f"{resources_base}/resources/systems")
    if not isinstance(systems, list):
        raise ToolError("Unexpected systems payload (not a list)")

    system_obj = _find_system(systems, system.strip())
    if system_obj is None:
        return _serializable_dict({"ok": False, "error": f"System not found: {system!r}"})

    sys_name = str(system_obj.get("system_name") or system_obj.get("name") or "").strip()
    anchor_guid = str(
        system_obj.get("guid") or system_obj.get("system_guid") or ""
    ).strip()

    record_ports = system_obj.get("ports") or []

    def _port_name(p: dict[str, Any], fallback_guid: str) -> str:
        name = p.get("name")
        if name:
            return str(name)
        num = p.get("number")
        return f"{fallback_guid}_{num}" if num is not None else ""

    record_port_names = {_port_name(p, anchor_guid) for p in record_ports if isinstance(p, dict)}
    record_port_names.discard("")

    by_name = normalize_list_payload(
        client.get_json(
            f"{resources_base}/resources/ports", params={"system_name": sys_name}
        )
    )
    by_guid = normalize_list_payload(
        client.get_json(
            f"{resources_base}/resources/ports", params={"system": anchor_guid}
        )
    )

    name_port_names = {str(p.get("name", "")) for p in by_name}
    name_port_names.discard("")
    guid_port_names = {str(p.get("name", "")) for p in by_guid}
    guid_port_names.discard("")

    ghost = sorted(record_port_names - name_port_names)
    name_only = sorted(name_port_names - record_port_names)

    if (
        not ghost
        and len(name_port_names) == len(record_port_names) == len(guid_port_names)
        and name_port_names == guid_port_names == record_port_names
    ):
        diagnosis = "clean"
    elif len(by_name) == 0 and len(by_guid) > 0:
        diagnosis = "host_node_desc_missing"
    elif len(by_guid) < len(by_name) and len(by_name) > 0:
        diagnosis = "stale_anchor"
    elif ghost:
        diagnosis = "ghost_ports"
    else:
        diagnosis = "unknown"

    hints = {
        "stale_anchor": (
            "On the UFM HA primary: `sudo pcs resource restart ufm-enterprise`. "
            "Restarts UFM model layer (~1-2 min downtime), zero fabric impact, "
            "rebuilds inventory cache. See skills/ufm-stale-inventory-recovery."
        ),
        "ghost_ports": (
            "Some ports listed in the system record are no longer present on the host. "
            "Likely stale entries from a previous configuration. "
            "`sudo pcs resource restart ufm-enterprise` clears the inventory cache."
        ),
        "host_node_desc_missing": (
            "UFM has the anchor GUID but no live ports under the system name — "
            "host's IB stack stopped advertising node_description. Check `ibstat` and node_desc on the host."
        ),
        "clean": "No drift detected.",
        "unknown": "Unrecognized drift pattern; capture this output and ping fabric-team.",
    }

    return _serializable_dict({
        "ok": True,
        "system": {
            "name": sys_name,
            "anchor_guid": anchor_guid,
            "anchor_record_port_count": len(record_port_names),
        },
        "counts": {
            "record_ports": len(record_port_names),
            "ports_by_name": len(name_port_names),
            "ports_by_guid": len(guid_port_names),
        },
        "ghost_ports": ghost,
        "name_only_ports": name_only,
        "inferred_diagnosis": diagnosis,
        "remediation_hint": hints[diagnosis],
    })
```

- [ ] **Step 4: Add `inventory-doctor` to `cli.py`**

Append a new command to `src/ufm_mcp/cli.py` next to `ports`:

```python
@app.command(name="inventory-doctor")
def inventory_doctor_cmd(
    system: str = typer.Argument(help="System name to diagnose"),
    site: str | None = typer.Option(None, "--site", "-s", help="Target site"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Diagnose stale-anchor / ghost-port drift for a UFM system."""
    _ensure_init()
    from ufm_mcp.server import ufm_inventory_doctor

    result = ufm_inventory_doctor(system=system, site=site)
    if json_output:
        _output(result, as_json=True)
        return

    if not result.get("ok"):
        typer.echo(f"Error: {result.get('error', 'unknown')}", err=True)
        raise typer.Exit(1)

    sys_block = result["system"]
    counts = result["counts"]
    diag = result["inferred_diagnosis"]

    typer.echo(f"=== Inventory Doctor: {sys_block['name']} ===")
    typer.echo(f"Anchor GUID: {sys_block['anchor_guid']}")
    typer.echo(
        f"Counts: record_ports={counts['record_ports']}  "
        f"ports_by_name={counts['ports_by_name']}  "
        f"ports_by_guid={counts['ports_by_guid']}"
    )

    if result["ghost_ports"]:
        typer.echo(f"Ghost ports (in record but not on host): {result['ghost_ports']}")
    if result["name_only_ports"]:
        typer.echo(f"Name-only ports (on host but not anchored): {result['name_only_ports']}")

    typer.echo(f"Diagnosis: {diag}")
    typer.echo(f"Remediation: {result['remediation_hint']}")
```

- [ ] **Step 5: Run failing tests — expect PASS**

```bash
uv run pytest tests/test_server_tools.py -k inventory_doctor tests/test_cli.py::test_inventory_doctor_cli_renders_text -v
```

Expected: all PASS.

- [ ] **Step 6: Run full suite + lint**

```bash
uv run pytest -q -m "not integration and not e2e and not slow"
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
```

Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add src/ufm_mcp/server.py src/ufm_mcp/cli.py tests/test_server_tools.py tests/test_cli.py
git commit -m "feat: add ufm_inventory_doctor tool + ufm-cli inventory-doctor (#48)

Cross-checks system_record.ports[], /resources/ports?system_name=, and
/resources/ports?system=<guid> to classify drift as
{clean, stale_anchor, ghost_ports, host_node_desc_missing, unknown}.
For stale_anchor and ghost_ports, points the caller at
\`sudo pcs resource restart ufm-enterprise\` and the recovery skill."
```

---

## Task 4 — `ufm-stale-inventory-recovery` skill (#52)

**Files:**
- Create: `skills/ufm-stale-inventory-recovery/SKILL.md`
- Modify: `mcp-plugin.toml` (append `[[skills]]` block)

### Skill content spec (from issue #52)

Document:
- **Symptoms**: host's `ibstatus` shows all HCAs Active/LinkUp; `ibdiagnet` agrees; switch port to the missing HCA reports `Active/LinkUp` but `peer = N/A`; UFM's `system_record.guid` matches a previous HCA from older ibdiagnet captures (often pre-rebuild `HCA-N` naming style instead of `mlx5_N`).
- **Diagnosis**: `ufm-cli inventory-doctor <system>` (Task 3) → expect `inferred_diagnosis: stale_anchor` or `ghost_ports`.
- **Recovery**: `sudo pcs resource restart ufm-enterprise` on the UFM HA primary. Restarts only the model layer; ~1-2 min UFM API/UI down; zero fabric impact; no DRBD role flip when using `restart`.
- **What does NOT help** (call out explicitly): `mlxlink --port_state DN/UP` from host; `ibportstate` on local CA; `DELETE /ufmRestV3/resources/systems/{guid}` (returns 405); SM heavy-sweep alone.
- **Pre-flight monitoring** (similar to ufm-opensm-restart skill).
- **Verification after restart**: re-run `ufm-cli inventory-doctor <system>` → expect `clean`.

### Implementation

- [ ] **Step 1: Write the skill file**

Create `skills/ufm-stale-inventory-recovery/SKILL.md`:

```markdown
---
name: ufm-stale-inventory-recovery
description: Use when UFM under-reports HCAs on a host that ibstat/ibdiagnet say is healthy, when `ufm-cli inventory-doctor` reports `stale_anchor` or `ghost_ports`, or when `system_record.guid` in UFM points at a pre-rebuild HCA. Triggers on UFM stale anchor, ghost ports, post-HCA-swap inventory drift, peer=N/A on switch port to a healthy HCA.
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

Confirm with `ufm-cli inventory-doctor <system>` — expect `inferred_diagnosis:
stale_anchor` or `ghost_ports`.

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
```

- [ ] **Step 2: Register the skill in `mcp-plugin.toml`**

Open `mcp-plugin.toml`, find the existing `[[skills]]` section(s), and append:

```toml
[[skills]]
name = "ufm-stale-inventory-recovery"
description = "Use when UFM under-reports HCAs on a host that ibstat/ibdiagnet say is healthy, when `ufm-cli inventory-doctor` reports stale_anchor or ghost_ports, or when system_record.guid in UFM points at a pre-rebuild HCA."
path = "skills/ufm-stale-inventory-recovery/SKILL.md"
```

- [ ] **Step 3: Verify the skill picks up `ufm_inventory_doctor` correctly via the test from Batch C**

If Batch C has already merged, re-run:

```bash
uv run pytest tests/test_skill_tool_lists.py -v
```

Expected: PASS — the new skill mentions `ufm_inventory_doctor`, which is now a real registered tool from Task 3.

If Batch C is **not** merged yet, this test won't exist. That's OK; the skill is still correct; the test will catch any future drift once both PRs land.

- [ ] **Step 4: Manual sanity — render the skill front-matter**

```bash
head -3 skills/ufm-stale-inventory-recovery/SKILL.md
```

Confirm the YAML front-matter is valid (`name:` / `description:` keys present, no smart quotes or backticks broken).

- [ ] **Step 5: Commit**

```bash
git add skills/ufm-stale-inventory-recovery/SKILL.md mcp-plugin.toml
git commit -m "docs: add ufm-stale-inventory-recovery skill (closes #52)

Documents the post-HCA-swap stale-anchor symptom set and the
\`sudo pcs resource restart ufm-enterprise\` recovery on the UFM HA
primary. Calls out what does NOT help (mlxlink, ibportstate, DELETE
on /resources/systems, SM sweeps). Companion to ufm-opensm-restart."
```

---

## Final integration

- [ ] **Step 1: Push and open PR**

```bash
git push -u origin feat/stale-anchor-cluster
gh pr create --title "Stale-anchor: fix + workaround + diagnose + recover (#49, #56, #48, #52)" --body "$(cat <<'EOF'
## Summary
Tightly-coupled cluster of changes around UFM's stale-system-anchor failure mode (post-HCA-swap, ports under-counted by `?system=<guid>`):
- **#49 (fix):** `ufm_get_ports_health` falls back to `?system_name=<name>` when the GUID query is short; surfaces divergence as `inventory_warnings`.
- **#56 (workaround):** New `port_guid` / `node_guid` parameters on `ufm_get_ports_health`, plus `--port-guid` / `--node-guid` flags on `ufm-cli ports` — pivot from `ibstat` directly, skipping system resolution.
- **#48 (diagnostic):** New `ufm_inventory_doctor` tool + `ufm-cli inventory-doctor <system>` command. Classifies drift as `clean | stale_anchor | ghost_ports | host_node_desc_missing | unknown`.
- **#52 (runbook):** New `ufm-stale-inventory-recovery` skill — symptoms, `pcs resource restart ufm-enterprise`, what doesn't help.

## Test plan
- [x] `uv run pytest -q -m "not integration and not e2e and not slow"` — full suite green
- [x] `uv run ruff check src/ tests/` — clean
- [x] Manual: `ufm-cli ports --port-guid 0xa088c20300556b96 -s apld2` against a real UFM
- [x] Manual: `ufm-cli inventory-doctor <known-stale-system> -s ori` returns `inferred_diagnosis: stale_anchor`
- [ ] CI green on PR

## Review hints
Commits split by issue — each is independently reviewable. Natural seam if the PR needs splitting: between `feat: add port_guid/node_guid lookup` (#56) and `feat: add ufm_inventory_doctor` (#48).
EOF
)"
```

- [ ] **Step 2: Update memory after merge**

Once merged, update `feedback_ufm_stale_inventory.md` (in user's auto-memory) to point at `ufm-cli inventory-doctor` as the diagnosis step before reaching for `pcs resource restart`. Also update `reference_ufm_mcp_known_issues.md` to remove the "ufm-cli ports latches to stale system.guid" line — that's the bug fixed by #49 in this PR.

---

## Self-review

### Spec coverage

| Issue ask | Task |
|---|---|
| #49 — fall back to `?system_name=` when GUID query is short | Task 1 step 3 |
| #49 — add `inventory_warnings` field on response | Task 1 step 3 |
| #56 — `--port-guid` / `--node-guid` on `ufm-cli ports` | Task 2 step 6 |
| #56 — same on `ufm_get_ports_health` | Task 2 step 4 |
| #56 — pivot via per-port GUIDs from `ibstat` even when anchor is wrong | Task 2 step 4 (port_guid skips /resources/systems) |
| #48 — cross-check three sources for `system_name` | Task 3 step 3 |
| #48 — output counts per source, ghost ports, name-only ports, `inferred_diagnosis` | Task 3 step 3 |
| #48 — hint at `pcs resource restart ufm-enterprise` for stale_anchor | Task 3 step 3 (`hints` dict) |
| #52 — symptoms section | Task 4 step 1 |
| #52 — recovery (`pcs resource restart ufm-enterprise`, ~1-2min, zero fabric impact, no DRBD flip) | Task 4 step 1 |
| #52 — what does NOT help (mlxlink, ibportstate, DELETE 405, SM sweeps) | Task 4 step 1 |

All four issues' requirements covered.

### Placeholder scan
- One spot to watch: Task 2 Step 5 references "copy verbatim from server.py:902-944" for the `port_summary` body. The implementer must literally copy, not paraphrase, to avoid behavior drift. The line range is exact.
- Helper `_ports_health_from_records` is fully sketched; `inferred_diagnosis` decision tree is explicit; remediation hints are written out.

### Type consistency
- `port_guid: str | None`, `node_guid: str | None`, `inventory_warnings: dict[str, Any] | None` — used consistently across signature, helper, and tests.
- Tests build mock data via `_make_systems_payload` and `_make_port` helpers added once in Task 1; Tasks 2 and 3 reuse them rather than redefining.
- The `inferred_diagnosis` literal set `{clean, stale_anchor, ghost_ports, host_node_desc_missing, unknown}` is consistent across implementation, tests, and the runbook skill.
- Server tool signature uses `Annotated[str, Field(...)]` everywhere, matching the existing `ufm_get_ports_health` pattern at `server.py:790-816`.
