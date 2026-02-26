# mcp-common

Shared utilities and testing infrastructure for Python MCP server projects.

## Install

```bash
uv add mcp-common
```

For testing utilities:

```bash
uv add "mcp-common[testing]"
```

Or from source:

```bash
uv add git+https://github.com/org/mcp-common
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

@mcp.resource("health://status")
def health() -> dict:
    return health_resource("my-server", "1.0.0", checks={"db": True}).to_dict()
```

### Version (`mcp_common.version`)

Runtime version introspection:

```python
from mcp_common import get_version

version = get_version("my-mcp-server")  # "1.2.3" or "0.0.0-dev"
```

### Testing (`mcp_common.testing`)

Shared pytest fixtures and assertions:

```python
from mcp_common.testing import mcp_client, assert_tool_exists

@pytest.fixture
async def client():
    async for c in mcp_client(app):
        yield c

@pytest.mark.anyio
async def test_tools_registered(client):
    await assert_tool_exists(client, "my_tool")
```

## Development

```bash
uv sync --all-groups
uv run ruff check src/ tests/
uv run mypy src/
uv run pytest
```

## License

Apache-2.0
