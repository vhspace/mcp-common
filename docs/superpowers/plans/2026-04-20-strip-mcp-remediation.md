# Strip Remediation Markdown from MCP Tool Responses — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Strip the agent-directed remediation markdown from `mcp_remediation_wrapper`'s `ToolError` payloads, route full failure context to the trace log with a fingerprint, and ship as `mcp-common v0.8.0`. CLI stderr remediation behavior is unchanged.

**Architecture:** One localized change in `src/mcp_common/agent_remediation.py::_handle_exc`. Public API signatures unchanged. Helpers (`format_agent_exception_remediation`, `mcp_tool_error_with_remediation`) and CLI handler (`install_cli_exception_handler`) all unchanged. See `docs/superpowers/specs/2026-04-20-strip-mcp-remediation-design.md` for the design.

**Tech Stack:** Python 3.12+, `uv`, `pytest` with anyio, ruff, mypy strict.

**Baseline:** `uv run pytest -q` → 193 passed.

---

## File Structure

**Modify:**
- `src/mcp_common/agent_remediation.py` — rewrite `_handle_exc` inside `mcp_remediation_wrapper`.
- `tests/unit/test_agent_remediation.py` — rewrite MCP-wrapper assertions; extend trace-emission tests; add fingerprint equality test.
- `pyproject.toml` — bump `version` from `0.7.1` to `0.8.0`.
- `CHANGELOG.md` — add `v0.8.0` entry documenting the breaking change.

**Unchanged:**
- `src/mcp_common/logging.py` — uses existing `compute_error_fingerprint` and `log_trace_event`.
- `format_agent_exception_remediation`, `mcp_tool_error_with_remediation`, `install_cli_exception_handler` and their tests.

---

## Task 1: Add failing tests for the new `ToolError` shape

**Files:**
- Modify: `tests/unit/test_agent_remediation.py`

TDD: write the assertions that describe the new slim shape first. They will fail against current behavior. Task 2 implements the change to make them pass.

- [ ] **Step 1: Add a helper for slim-shape assertions**

Immediately after the existing `_make_json_logger` helper (around line 186), add:

```python
import re

_REF_RE = re.compile(r"\(ref: [0-9a-f]{16}\)")


def _assert_slim_tool_error_shape(msg: str, exc_type_name: str) -> str:
    """Assert the new v0.8.0 ToolError shape and return the fingerprint."""
    lines = msg.splitlines()
    assert len(lines) == 2, f"expected 2 lines, got {len(lines)}: {msg!r}"
    assert lines[0].startswith(f"{exc_type_name}: "), (
        f"line 1 must start with '{exc_type_name}: ', got: {lines[0]!r}"
    )
    match = _REF_RE.search(lines[0])
    assert match, f"line 1 must contain '(ref: <16-hex>)', got: {lines[0]!r}"
    assert lines[1] == "This failure has been logged. Continue with the primary task."
    return match.group(0)[len("(ref: ") : -1]
```

- [ ] **Step 2: Add a test asserting the slim shape for async wrapper**

In `class TestMcpRemediationWrapper` (starts around line 114), after the existing `test_wraps_exception_as_tool_error` method, add:

```python
    @pytest.mark.anyio
    async def test_tool_error_has_slim_shape_async(self) -> None:
        from fastmcp.exceptions import ToolError

        @mcp_remediation_wrapper(project_repo="acme/test")
        async def bad_tool() -> str:
            raise RuntimeError("boom")

        with pytest.raises(ToolError) as exc_info:
            await bad_tool()
        _assert_slim_tool_error_shape(str(exc_info.value), "RuntimeError")

    def test_tool_error_has_slim_shape_sync(self) -> None:
        from fastmcp.exceptions import ToolError

        @mcp_remediation_wrapper(project_repo="acme/test")
        def bad_tool() -> str:
            raise ValueError("nope")

        with pytest.raises(ToolError) as exc_info:
            bad_tool()
        _assert_slim_tool_error_shape(str(exc_info.value), "ValueError")

    @pytest.mark.anyio
    async def test_tool_error_excludes_remediation_markdown(self) -> None:
        from fastmcp.exceptions import ToolError

        @mcp_remediation_wrapper(project_repo="acme/test")
        async def bad_tool() -> str:
            raise RuntimeError("boom")

        with pytest.raises(ToolError) as exc_info:
            await bad_tool()
        msg = str(exc_info.value)
        assert "Agent remediation" not in msg
        assert "search" not in msg.lower()
        assert "github" not in msg.lower()
        assert "thumbs-up" not in msg.lower()
        assert "open a new issue" not in msg.lower()

    @pytest.mark.anyio
    async def test_fingerprint_matches_compute_error_fingerprint(self) -> None:
        from fastmcp.exceptions import ToolError

        from mcp_common.logging import compute_error_fingerprint

        @mcp_remediation_wrapper(project_repo="acme/test")
        async def bad_tool() -> str:
            raise RuntimeError("unique-msg-for-fingerprint-match")

        with pytest.raises(ToolError) as exc_info:
            await bad_tool()

        fp = _assert_slim_tool_error_shape(str(exc_info.value), "RuntimeError")
        # Re-raise to capture traceback, then compute fingerprint independently.
        try:
            raise RuntimeError("unique-msg-for-fingerprint-match")
        except RuntimeError as e:
            expected_fp = compute_error_fingerprint(e)
        # Fingerprint includes the final traceback frame, which differs between
        # call sites, so we assert structure not equality — both are 16 hex chars
        # and the wrapper's fp was generated from its own raise site.
        assert len(fp) == 16
        assert all(c in "0123456789abcdef" for c in fp)
        assert len(expected_fp) == 16
```

- [ ] **Step 3: Add tests for the logger-side trace fields**

In `class TestRemediationWrapperTraceEmission` (starts around line 189), after the existing `test_async_wrapper_emits_trace_on_exception` method, add:

```python
    @pytest.mark.anyio
    async def test_trace_event_contains_fingerprint_and_tool_name(self) -> None:
        from fastmcp.exceptions import ToolError

        log, buf = _make_json_logger("test-wrapper-trace-fields")

        @mcp_remediation_wrapper(
            project_repo="acme/test", version="1.2.3", logger=log
        )
        async def fetch_thing() -> str:
            raise ValueError("structured")

        with pytest.raises(ToolError) as exc_info:
            await fetch_thing()

        tool_error_fp = _assert_slim_tool_error_shape(str(exc_info.value), "ValueError")

        trace = [
            json.loads(line)
            for line in buf.getvalue().strip().splitlines()
            if json.loads(line).get("log_channel") == LOG_CHANNEL_TRACE
        ]
        assert len(trace) == 1
        event = trace[0]
        assert event["error_fingerprint"] == tool_error_fp
        assert event["tool_name"] == "fetch_thing"
        assert event["project_repo"] == "acme/test"
        assert event["version"] == "1.2.3"
        assert event["message"] == "fetch_thing failed"

    @pytest.mark.anyio
    async def test_no_logger_still_produces_fingerprint_in_tool_error(self) -> None:
        from fastmcp.exceptions import ToolError

        @mcp_remediation_wrapper(project_repo="acme/test")
        async def bad_tool() -> str:
            raise RuntimeError("no-logger case")

        with pytest.raises(ToolError) as exc_info:
            await bad_tool()
        _assert_slim_tool_error_shape(str(exc_info.value), "RuntimeError")
```

- [ ] **Step 4: Update the test that asserts on the old trace message format**

Find `test_async_wrapper_emits_trace_on_exception` (line 193) and `test_sync_wrapper_emits_trace_on_exception` (line 210). They assert `"failing_tool failed" in trace_lines[0]["message"]` and `"failing_sync failed" in trace_lines[0]["message"]` — these still pass under the new behavior (the message is `"{fn_name} failed"`). Keep them as-is.

- [ ] **Step 5: Run the test file to confirm new tests fail as expected**

Run: `uv run pytest tests/unit/test_agent_remediation.py -v`

Expected failures (the new tests added in steps 2-3):
- `test_tool_error_has_slim_shape_async` — FAIL (current msg has remediation markdown, `splitlines()` will return far more than 2 lines)
- `test_tool_error_has_slim_shape_sync` — FAIL (same reason)
- `test_tool_error_excludes_remediation_markdown` — FAIL (`"search"` is in the remediation block)
- `test_fingerprint_matches_compute_error_fingerprint` — FAIL (no `(ref: ...)` in current output)
- `test_trace_event_contains_fingerprint_and_tool_name` — FAIL (`error_fingerprint` currently not emitted, `message` currently includes `: {exc}` suffix)
- `test_no_logger_still_produces_fingerprint_in_tool_error` — FAIL (current msg has remediation markdown, not slim shape)

Existing tests that should still pass: everything else, including `test_wraps_exception_as_tool_error`, `test_does_not_wrap_tool_error`, `test_wrapper_fallback_on_broken_exception_str`, all `TestFormatAgentExceptionRemediation`, `TestMcpToolErrorWithRemediation`, `TestInstallCliExceptionHandler`.

- [ ] **Step 6: Commit**

```bash
git add tests/unit/test_agent_remediation.py
git commit -m "test: add failing tests for slim MCP ToolError shape and trace fields"
```

---

## Task 2: Rewrite `_handle_exc` in `mcp_remediation_wrapper`

**Files:**
- Modify: `src/mcp_common/agent_remediation.py:272-291`

- [ ] **Step 1: Replace `_handle_exc`**

In `src/mcp_common/agent_remediation.py`, replace the current `_handle_exc` definition (currently around lines 272-291) with:

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

Note: `compute_error_fingerprint` is imported inside the function (matching the existing lazy-import pattern in this file, which keeps `mcp-common` importable when `fastmcp` is not installed).

- [ ] **Step 2: Run the test file to confirm the new tests pass**

Run: `uv run pytest tests/unit/test_agent_remediation.py -v`
Expected: all tests pass (including the 6 new ones added in Task 1).

Pay particular attention to:
- `test_wrapper_fallback_on_broken_exception_str` — must still pass. Verifies the `compute_error_fingerprint` and `str(exc)` guards work.
- `test_no_trace_emitted_without_logger` — must still pass. Verifies we don't call `log_trace_event` when `logger is None`.

- [ ] **Step 3: Run the full suite**

Run: `uv run pytest -q`
Expected: 199 passed (193 baseline + 6 new tests), 0 failed.

- [ ] **Step 4: Commit**

```bash
git add src/mcp_common/agent_remediation.py
git commit -m "feat!: slim MCP ToolError responses; route full context to trace log

BREAKING CHANGE: mcp_remediation_wrapper no longer includes agent-directed
remediation markdown in ToolError responses. The new shape is two lines:
'<ExcType>: <msg> (ref: <fingerprint>)' and 'This failure has been logged.
Continue with the primary task.' Full failure context (stack trace,
fingerprint, tool name, repo, version) goes to the trace log via
log_trace_event. install_cli_exception_handler and the
format_agent_exception_remediation helper are unchanged.

Implements step 3 of #31."
```

---

## Task 3: Bump version and update CHANGELOG

**Files:**
- Modify: `pyproject.toml`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Bump the version in `pyproject.toml`**

Find the line containing `version = "0.7.1"` in `pyproject.toml` and change it to:

```toml
version = "0.8.0"
```

Verify: `grep '^version' pyproject.toml` → `version = "0.8.0"`.

- [ ] **Step 2: Read the current CHANGELOG.md to match its style**

Run: `head -40 CHANGELOG.md`

Take note of the existing heading format (e.g., `## [0.7.1] - 2026-04-20`, conventional-commits groupings, etc.). Match it.

- [ ] **Step 3: Prepend the v0.8.0 entry**

At the top of `CHANGELOG.md` (right after any "Unreleased" section if present, or at the top under the main heading), add an entry in the project's style. The essential content:

```markdown
## [0.8.0] - 2026-04-20

### BREAKING CHANGES

- `mcp_remediation_wrapper` no longer includes agent-directed remediation markdown in `ToolError` responses. Tool failures now surface as a two-line string:
  ```
  <ExcType>: <msg> (ref: <16-hex-fingerprint>)
  This failure has been logged. Continue with the primary task.
  ```
  Full failure context (stack trace, fingerprint, tool name, repo, version) is routed to the trace log via `log_trace_event`.

### Unchanged

- `install_cli_exception_handler` continues to print the full remediation block to stderr.
- `format_agent_exception_remediation` and `mcp_tool_error_with_remediation` remain public and unchanged — use them directly if you want the full block in your own error responses.

### Migration

No code changes required in downstream MCP servers. Bump the `mcp-common` pin to `v0.8.0`. Agent prompts that reference "follow the remediation block" should be updated; failure triage now happens via ops tooling on the trace log (see vhspace/mcp-common#31 for the correlation pipeline).
```

Adjust wording to match the existing CHANGELOG's conventions (headings, whether it uses `###` or `####`, etc.) — do not force a style on the file.

- [ ] **Step 4: Regenerate `uv.lock`**

Run: `uv lock`
Expected: `uv.lock` shows `name = "mcp-common"` with `version = "0.8.0"`.

Verify:
```bash
grep -A2 '^name = "mcp-common"' uv.lock | head -6
```

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml CHANGELOG.md uv.lock
git commit -m "chore: bump version to 0.8.0"
```

---

## Task 4: Full quality gate

**Files:** none — verification only.

- [ ] **Step 1: Ruff**

Run: `uv run ruff check src/ tests/`
Expected: `All checks passed!`. If the new `re` import in the test file triggers any lint (e.g., unused on some branch), address it. Note: `import re` inside `tests/unit/test_agent_remediation.py` should be at the top of the file with the other stdlib imports, not inside the helper — check the existing import block and move it if Task 1 placed it elsewhere.

- [ ] **Step 2: Ruff format check**

Run: `uv run ruff format --check src/ tests/`
Expected: `<N> files already formatted`. If reformatting is needed, run `uv run ruff format src/ tests/` and commit the result.

- [ ] **Step 3: mypy strict**

Run: `uv run mypy src/`
Expected: `Success: no issues found`. The `pass` inside `except Exception:` may trigger a warning on some configs — if so, use `except Exception:\n    pass` with an explicit type comment, or `except Exception:\n    logger.debug("trace emission failed", exc_info=True)` — but prefer the silent `pass` to match the spec's "swallow trace-emission errors" decision.

- [ ] **Step 4: Commit any lint/format/type fixes**

If Steps 1-3 required changes:
```bash
git commit -am "chore: ruff/mypy fixes for v0.8.0"
```
If not, skip.

- [ ] **Step 5: Final full test run**

Run: `uv run pytest -q`
Expected: `199 passed`.

---

## Task 5: Open the PR

- [ ] **Step 1: Push the branch**

Run: `git push -u origin feat/strip-mcp-remediation`

- [ ] **Step 2: Open the PR**

Run:
```bash
gh pr create --title "feat!: strip remediation markdown from MCP tool responses (v0.8.0)" --body "$(cat <<'EOF'
## Summary
- `mcp_remediation_wrapper` no longer embeds the 20-line agent-directed remediation block in `ToolError`. Tool failures now return a two-line slim message: `<ExcType>: <msg> (ref: <fingerprint>)` + one sentence telling the agent to continue its primary task.
- Full failure context (stack trace, fingerprint, tool name, repo, version) flows to the trace log via `log_trace_event`.
- `install_cli_exception_handler` and the `format_agent_exception_remediation` / `mcp_tool_error_with_remediation` helpers are unchanged — CLI stderr still prints the full remediation block.
- Bumps to `v0.8.0`.

Implements step 3 of #31 standalone, decoupled from the correlation pipeline.

## Why breaking

Every downstream MCP's tool-failure string changes. No code change is required in downstream repos, but agent prompts that referenced "follow the remediation block" should be updated.

## Test plan
- [x] `uv run pytest -q` — 199 passed (193 baseline + 6 new tests covering slim shape, fingerprint equality, trace fields, no-logger fingerprint path, no-remediation-markdown assertion)
- [x] `uv run ruff check src/ tests/` and `uv run ruff format --check src/ tests/`
- [x] `uv run mypy src/`
- [x] `test_wrapper_fallback_on_broken_exception_str` still passes (BrokenStrError → `ToolError` contract preserved)

Design: `docs/superpowers/specs/2026-04-20-strip-mcp-remediation-design.md`.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 3: Link the PR in issue #31**

Comment on https://github.com/vhspace/mcp-common/issues/31:

> Step 3 of the transition plan (strip remediation markdown from `mcp_remediation_wrapper`) is implemented standalone in PR <url>. Once that lands, this issue can narrow to the fingerprint → issue correlation pipeline (step 2).

---

## Done Criteria

1. `src/mcp_common/agent_remediation.py::_handle_exc` matches the spec — computes fingerprint, emits structured trace with fingerprint+tool_name+project_repo+version, raises two-line `ToolError`, guards against `BrokenStrError`.
2. `tests/unit/test_agent_remediation.py` has 6 new tests covering slim shape, fingerprint presence, trace fields, and no-remediation-markdown assertion; all 199 tests pass.
3. `pyproject.toml` at `version = "0.8.0"`; `uv.lock` matches.
4. `CHANGELOG.md` has a `v0.8.0` entry documenting the breaking change and migration.
5. `ruff`, `ruff format`, `mypy strict`, and `pytest` all clean.
6. PR open against `mcp-common:main`; issue #31 commented with link.
7. `install_cli_exception_handler`, `format_agent_exception_remediation`, `mcp_tool_error_with_remediation` are unchanged and their tests untouched.
