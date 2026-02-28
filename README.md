# mcp-common

[![CI](https://github.com/vhspace/mcp-common/actions/workflows/ci.yml/badge.svg)](https://github.com/vhspace/mcp-common/actions/workflows/ci.yml)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-green.svg)](https://opensource.org/licenses/Apache-2.0)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

Shared utilities and testing infrastructure for Python MCP server projects.

## Install

```bash
uv add git+https://github.com/vhspace/mcp-common
```

For testing utilities:

```bash
uv add "mcp-common[testing] @ git+https://github.com/vhspace/mcp-common"
```

## What's Included

### Configuration (`mcp_common.config`)

Base settings class built on pydantic-settings with `.env` file support:

```python
from mcp_common import MCPSettings
from pydantic_settings import SettingsConfigDict

class MySettings(MCPSettings):
    model_config = SettingsConfigDict(env_prefix="MY_SERVER_")
    api_url: str
    api_token: str
    timeout: int = 30
```

Built-in fields: `debug`, `log_level`, `log_json`.

### Logging (`mcp_common.logging`)

Structured logging with JSON mode for containers:

```python
from mcp_common import setup_logging

logger = setup_logging(level="DEBUG", json_output=True, name="my-server")
```

### Health Checks (`mcp_common.health`)

Standard health check responses:

```python
from mcp_common import health_resource

result = health_resource("my-server", "1.0.0", checks={"db": True})
result.to_dict()
# {"name": "my-server", "version": "1.0.0", "status": "healthy", ...}
```

### Version (`mcp_common.version`)

Runtime version introspection:

```python
from mcp_common import get_version

version = get_version("my-mcp-server")  # "1.2.3" or "0.0.0-dev"
```

### Progress Polling (`mcp_common.progress`)

Poll long-running operations with MCP progress notifications:

```python
from mcp_common import OperationStates, poll_with_progress

states = OperationStates(success=["complete"], failure=["error"], in_progress=["running"])
result = await poll_with_progress(ctx, check_fn, "status", states, timeout_s=300)
```

### Testing (`mcp_common.testing`)

Shared pytest fixtures and assertions for MCP servers:

```python
from mcp_common.testing import mcp_client, assert_tool_exists, assert_tool_success

@pytest.fixture
async def client():
    async for c in mcp_client(app):
        yield c

@pytest.mark.anyio
async def test_tools(client):
    await assert_tool_exists(client, "my_tool")
    result = await assert_tool_success(client, "my_tool", {"arg": "value"})
```

## Development

```bash
uv sync --all-groups
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run mypy src/
uv run pytest -v
```

## License

Apache-2.0
