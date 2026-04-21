# Design: Strip Remediation Markdown from MCP Tool Responses

**Date:** 2026-04-20
**Repo:** `vhspace/mcp-common`
**Target version:** `v0.8.0`
**Related issue:** [#31](https://github.com/vhspace/mcp-common/issues/31) — implements step 3 of its transition plan standalone.

## Problem

`mcp_remediation_wrapper` in `src/mcp_common/agent_remediation.py` catches exceptions from FastMCP tool handlers and re-raises them as `ToolError` with a full agent-directed remediation block (approximately 20 lines of markdown: "search GitHub issues", "add a 👍 to duplicates", "open a new issue", "continue the primary task"). This text lands in the agent's chat on every tool failure. Downstream effects:

- Every tool error response costs hundreds of tokens of context.
- The agent may follow the instructions and derail from its primary task.
- Issue filing done by agents is low quality and prone to duplicates.
- Humans reading logs already have the exception + stack trace; they don't need prose instructions mixed into machine output.

The full design rationale is in issue #31. This spec implements the strip portion only, decoupled from #31's other work (automated fingerprint-to-issue correlation), so the strip can land immediately.

## Goal

Tool failures seen by MCP agents are a slim, machine-friendly error string. Full failure context — stack trace, fingerprint, tool name, repo, version — flows to the trace log via `log_trace_event`. CLI stderr behavior is unchanged.

## Non-goals

- Automated fingerprint-to-issue correlation (remains in #31).
- Changes to `install_cli_exception_handler` or CLI stderr output.
- Removal of `format_agent_exception_remediation` / `mcp_tool_error_with_remediation` helpers — they stay exported for direct consumers (e.g., `maas-mcp-server/src/maas_mcp/cli.py`) and for any CLI path that wants the full block.
- Changes to `serverUseInstructions` snippets in plugin manifests (separate concern).

## Architecture

One change, localized to `src/mcp_common/agent_remediation.py::_handle_exc` (the inner function of `mcp_remediation_wrapper`). Public API signatures remain unchanged. No new modules.

### Current behavior (v0.7.1)

```python
def _handle_exc(exc: Exception, fn_name: str) -> None:
    from fastmcp.exceptions import ToolError

    if isinstance(exc, ToolError):
        raise
    if logger is not None:
        from mcp_common.logging import log_trace_event

        log_trace_event(logger, f"{fn_name} failed: {exc}", exc_info=exc)
    try:
        msg = mcp_tool_error_with_remediation(
            exc,
            project_repo=project_repo,
            issue_tracker_url=issue_tracker_url,
            tool_name=fn_name,
            version=version,
        )
    except Exception:
        msg = f"{type(exc).__name__}: {exc}"
    raise ToolError(msg) from exc
```

The `ToolError(msg)` payload contains the full remediation markdown. The trace log call is optional (only when `logger` is passed) and does not carry a fingerprint.

### New behavior (v0.8.0)

```python
def _handle_exc(exc: Exception, fn_name: str) -> None:
    from fastmcp.exceptions import ToolError
    from mcp_common.logging import compute_error_fingerprint, log_trace_event

    if isinstance(exc, ToolError):
        raise

    try:
        fingerprint = compute_error_fingerprint(exc)
    except Exception:
        fingerprint = "unknown"

    if logger is not None:
        try:
            log_trace_event(
                logger,
                f"{fn_name} failed",
                exc_info=exc,
                error_fingerprint=fingerprint,
                tool_name=fn_name,
                project_repo=project_repo,
                version=version,
            )
        except Exception:
            pass

    try:
        exc_str = str(exc)
    except Exception:
        exc_str = "(unprintable exception)"

    slim_msg = (
        f"{type(exc).__name__}: {exc_str} (ref: {fingerprint})\n"
        "This failure has been logged. Continue with the primary task."
    )
    raise ToolError(slim_msg) from exc
```

Key deltas:

1. Fingerprint is computed via `compute_error_fingerprint`, a pure function over the exception's type, message, and final traceback frame — already part of the v0.7.x public API. It internally calls `str(exc)`, so it's wrapped in a `try/except` that falls back to `"unknown"` for exceptions whose `__str__` raises (matching the safety net the old `try/except` around `mcp_tool_error_with_remediation` provided).
2. `str(exc)` is also guarded with a fallback to `"(unprintable exception)"`. This preserves the current contract that the wrapper ALWAYS raises `ToolError` (tested by `test_wrapper_fallback_on_broken_exception_str`).
3. The trace log emission is wrapped in `try/except: pass` — if structured logging itself fails for any reason (malformed extras, formatter bug), it must not prevent the `ToolError` from reaching the agent. Silent-swallow is deliberate here: the wrapper's job is to surface an error, not to surface a meta-error about logging.
4. The trace-event `message` is now a short `"{fn_name} failed"` (not `"{fn_name} failed: {exc}"`) — the exception detail travels structurally via `exc_info`, not embedded in the message string, so we avoid a second `str(exc)` call site that would need its own guard.
5. The trace log carries `error_fingerprint`, `tool_name`, `project_repo`, and `version` as structured fields (confirmed not colliding with `_TRACE_EVENT_RESERVED_EXTRA_KEYS = {"log_channel", "http_status", "request_id", "error_fingerprint"}`).
6. The `ToolError` payload is two lines: `<ExcType>: <msg> (ref: <fingerprint>)\n<one-sentence nudge>`. No markdown, no URLs, no issue-triage instructions. The nudge explicitly tells the agent not to act on the error beyond handling it in its primary task.

### `ToolError` message grammar

Contract for the new message shape (so downstream agent prompts or log scrapers can rely on it):

```
<ExceptionTypeName>: <str(exception)> (ref: <16-hex-chars>)
This failure has been logged. Continue with the primary task.
```

- Line 1: the identity and short message of the exception, plus the fingerprint in `ref: <sha-prefix>` form.
- Line 2: a fixed string that asserts the failure is logged and instructs the agent to return to primary work.
- Line 2 is intentionally not a list, not markdown, not a link — agents should parse this as "the error happened, keep going," nothing more.

### Trace log contract

Structured fields emitted on every MCP tool failure (when `logger` is provided):

| Field | Source |
|-------|--------|
| `log_channel` | `trace` (set by `log_trace_event`) |
| `level` | `ERROR` (set by `log_trace_event`) |
| `message` | `"{fn_name} failed"` (short — exception detail rides on `exc_info`) |
| `exception` | formatted traceback (via `exc_info=exc`) |
| `error_fingerprint` | `compute_error_fingerprint(exc)` — same value as in `ToolError` |
| `tool_name` | the decorated function's `__name__` |
| `project_repo` | decorator kwarg (forwarded as-is) |
| `version` | decorator kwarg (forwarded as-is) |

This matches what `log_trace_event` already accepts (`error_fingerprint` is declared in its signature; `tool_name`, `project_repo`, `version` ride on `**extra`).

## What stays unchanged

- `install_cli_exception_handler` — identical behavior. User explicitly confirmed CLI stderr should still print the full remediation block.
- `format_agent_exception_remediation` — public helper, still used by the CLI handler and by direct callers (e.g., `maas-mcp-server/src/maas_mcp/cli.py`).
- `mcp_tool_error_with_remediation` — public helper, still exported (for consumers who want to compose their own MCP error responses with remediation; now a deliberate opt-in rather than the wrapper default).
- Public function signatures across the module.
- `serverUseInstructions` / plugin manifest snippets — out of scope.

## Testing

### File: `tests/unit/test_agent_remediation.py`

Existing tests that assert on the full-remediation `ToolError` content will fail after the change. Those assertions are rewritten to match the slim shape. New cases are added for the new invariants.

Changes:

1. **Replace** assertions of the form `"search.*issue" in str(exc.value)` or `"## Agent remediation" in str(exc.value)` in MCP-wrapper tests with assertions that:
   - `str(exc.value).splitlines()[0]` starts with `"{ExcType}: {msg} (ref: "`
   - `"This failure has been logged" in str(exc.value)`
   - `str(exc.value).count("\n") == 1` (exactly two lines, one newline)
   - The `(ref: ...)` token contains 16 hex characters.

2. **Add** a test: fingerprint in `ToolError` message equals `compute_error_fingerprint(exc)` called directly with the same exception.

3. **Add** a test: with `logger=None`, the wrapper still raises `ToolError` with a fingerprint present (and does not call `log_trace_event`).

4. **Add** a test: with a logger set, the emitted trace record has matching `error_fingerprint`, `tool_name`, `project_repo`, and `version` fields.

5. **Keep unchanged**: all existing tests for `install_cli_exception_handler`, `format_agent_exception_remediation`, and `mcp_tool_error_with_remediation` (these helpers are not modified).

6. **Keep (unchanged contract)**: `test_wrapper_fallback_on_broken_exception_str` — asserts that a `BrokenStrError` (where `__str__` raises) still results in a `ToolError`. The new `_handle_exc` guards both `compute_error_fingerprint` and `str(exc)` to preserve this contract; verify this test still passes without modification.

### Regression safety

- `uv run pytest -q` must pass (193 tests at baseline + net change from the edits above, which is small: ~4 rewritten assertions, +3 new tests).
- `uv run ruff check src/ tests/` and `uv run mypy src/` must be clean.

## Versioning and release

- Bump `pyproject.toml` version to `0.8.0`.
- CHANGELOG entry under **Breaking changes**:
  > `mcp_remediation_wrapper` no longer includes the agent-directed remediation markdown in `ToolError` responses. Failures now return a two-line string of the form `<ExcType>: <msg> (ref: <fingerprint>)` / `This failure has been logged. Continue with the primary task.` Full failure context (stack trace, fingerprint, tool name, repo, version) flows to the trace log via `log_trace_event`. `install_cli_exception_handler` and the `format_agent_exception_remediation` helper are unchanged.
- CHANGELOG upgrade note for downstream MCPs: no code change required; bump the pin. Agent prompts that reference "follow the remediation block" should be updated — triage should now happen via ops tooling on the trace log (see #31 for the correlation pipeline).
- Semver rationale: the module's public function signatures are unchanged, but the string payload every downstream MCP agent sees on failure is different. Minor-version bump makes this visible in release notes rather than quietly shipping as a patch.

## Rollout

1. PR into `mcp-common:main` implementing this spec and bumping to `0.8.0`.
2. Release `v0.8.0` via the existing semantic-release workflow.
3. Comment on issue #31 noting step 3 is complete and #31 can be narrowed to the correlation pipeline.
4. Downstream MCPs (netbox-mcp, maas-mcp-server, awx-mcp, others) bump their pins opportunistically — no code changes required.

## Open questions

None at design time. Any surprises encountered during implementation (e.g., `compute_error_fingerprint` returning different output than expected for a specific exception class) are to be handled inline during the implementation plan, not escalated back to this spec.
