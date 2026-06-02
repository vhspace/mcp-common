# Logging, Telemetry & Trace Adoption Guide

Practical guide for downstream MCP servers and CLI projects that depend on
`mcp-common`. Covers setup, channel helpers, timing telemetry, structured
traces, and log aggregator integration.

## MCP server logging setup

```python
from pydantic_settings import SettingsConfigDict
from mcp_common import MCPSettings, setup_logging

class MySettings(MCPSettings):
    model_config = SettingsConfigDict(env_prefix="MY_MCP_")
    api_url: str
    timeout: int = 30

settings = MySettings()
logger = setup_logging(
    name="my-mcp",
    level=settings.log_level,
    json_output=settings.log_json,
    system_log=True,
)
```

`system_log=True` (the default) attaches a `SysLogHandler` when a platform
syslog socket is available (`/dev/log` on Linux, `/var/run/syslog` on macOS).
This is silently skipped when the socket is absent, so it is safe to leave
enabled everywhere.

## CLI logging setup

```python
from mcp_common.logging import setup_logging

logger = setup_logging(name="my-cli", level="INFO", system_log=True)
```

For CLI tools the JSON mode is usually off (human-readable stderr), but
`system_log=True` still routes structured lines to journald for post-hoc
analysis.

## Using timing telemetry

### `timed_operation` context manager

Wraps any synchronous block and emits a timing event on the `access` channel:

```python
from mcp_common.logging import timed_operation

with timed_operation(logger, "deploy-node", expected_s=120.0):
    run_deploy(node)
```

The emitted JSON includes `operation`, `expected_s`, `actual_s`, `ok`, and
`timed_out`. If the block raises, `ok` is `False` and the exception propagates.

### `poll_with_progress` with timing

For async MCP polling tools, pass `logger` and `operation` to get automatic
timing events on poll completion:

```python
from mcp_common.progress import OperationStates, poll_with_progress

states = OperationStates(
    success=["complete"],
    failure=["error"],
    in_progress=["running"],
)
result = await poll_with_progress(
    ctx,
    check_fn,
    "status",
    states,
    timeout_s=300,
    interval_s=10,
    logger=logger,
    operation="commission-node",
)
```

### `log_timing_event` for manual emission

When neither context manager nor polling fits, emit directly:

```python
from mcp_common.logging import log_timing_event

log_timing_event(
    logger,
    message="firmware flash complete",
    operation="flash-bmc",
    expected_s=180.0,
    actual_s=elapsed,
    ok=True,
)
```

## Wiring remediation wrappers with logging

### CLI apps (Typer)

```python
import typer
from mcp_common.agent_remediation import install_cli_exception_handler

app = typer.Typer()
install_cli_exception_handler(
    app,
    project_repo="myorg/my-cli",
    logger=logger,
)
```

On unhandled exceptions the handler emits a `trace`-channel event via the
logger, then prints the remediation block to stderr and exits with code 1.

### MCP tool handlers (FastMCP)

```python
from mcp_common import mcp_remediation_wrapper

@mcp.tool()
@mcp_remediation_wrapper(project_repo="myorg/my-mcp", logger=logger)
async def my_tool(arg: str) -> str:
    ...
```

On exception, a `trace`-channel event is emitted before re-raising as
`ToolError` with the remediation markdown.

## Querying logs

### Linux (journald)

```bash
# All lines from the last hour
journalctl -t my-mcp --since "1 hour ago" -o json

# Filter by channel
journalctl -t my-mcp -o json | jq 'select(.log_channel == "access")'

# Trace events only (errors)
journalctl -t my-mcp -o json | jq 'select(.log_channel == "trace")'

# Timing events for a specific operation
journalctl -t my-mcp -o json | jq 'select(.operation == "commission-node")'
```

### macOS

macOS 12+ replaced traditional syslog with unified logging (`os_log`).
Messages sent to `/var/run/syslog` may not appear in `log show`. This is
best-effort — use stderr or file-based logging on macOS for reliable capture.

## Suggested smoke tests for downstream projects

Copy-paste these into your project's test suite and adapt the logger name:

```python
import io
import json
import logging

import pytest

from mcp_common.logging import JSONFormatter, setup_logging, timed_operation


def _make_test_logger(name: str) -> tuple[logging.Logger, io.StringIO]:
    """Create a JSON logger backed by a StringIO buffer for assertions."""
    buf = io.StringIO()
    h = logging.StreamHandler(buf)
    h.setFormatter(JSONFormatter())
    log = logging.getLogger(name)
    log.handlers.clear()
    log.setLevel(logging.DEBUG)
    log.addHandler(h)
    return log, buf


def test_logging_setup():
    logger = setup_logging(name="my-mcp-test", system_log=True)
    assert logger is not None


def test_timing_event():
    logger, buf = _make_test_logger("my-mcp-timing-test")
    with timed_operation(logger, "test-op"):
        pass
    data = json.loads(buf.getvalue().strip())
    assert data["operation"] == "test-op"
    assert data["ok"] is True
    assert "actual_s" in data


def test_remediation_traces():
    from mcp_common import mcp_remediation_wrapper
    from fastmcp.exceptions import ToolError

    logger, buf = _make_test_logger("my-mcp-trace-test")

    @mcp_remediation_wrapper(project_repo="myorg/my-mcp", logger=logger)
    def failing_tool():
        raise ValueError("test error")

    with pytest.raises(ToolError, match="ValueError"):
        failing_tool()

    lines = [json.loads(l) for l in buf.getvalue().strip().splitlines()]
    trace_lines = [l for l in lines if l.get("log_channel") == "trace"]
    assert len(trace_lines) >= 1
```

## Log aggregator notes

### Datadog

Datadog auto-parses JSON from syslog bodies when the syslog ident is empty
(which `JSONFormatter` sets for the syslog handler). Add pipeline remapping
rules:

| Source field | Datadog field |
|---|---|
| `level` | `status` |
| `logger` | `service` |

### Elastic / Splunk / Graylog

Raw field names (`level`, `logger`, `log_channel`, `operation`, etc.) work
without remapping.

### RFC format

`mcp-common` emits RFC 3164 (BSD syslog) via Python's `SysLogHandler`.
Upgrade to RFC 5424 with `rfc5424-logging-handler` if your aggregator
requires structured-data fields.
