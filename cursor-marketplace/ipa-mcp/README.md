# IPA MCP Server

[![CI](https://github.com/vhspace/ipa-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/vhspace/ipa-mcp/actions/workflows/ci.yml)
[![Release](.github/badges/release.svg)](https://github.com/vhspace/ipa-mcp/releases)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-green.svg)](https://opensource.org/licenses/Apache-2.0)

MCP server and CLI for [FreeIPA](https://www.freeipa.org/) — manages user groups, host groups, HBAC rules, and sudo rules via the FreeIPA JSON-RPC API. Designed for forge cluster bringup and access control automation in the Together AI SRE stack.

## Quick Start

### Cursor IDE

Add to `.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "ipa-mcp": {
      "command": "uvx",
      "args": ["--from", "ipa-mcp", "ipa-mcp"],
      "env": {
        "IPA_HOST": "ipa.example.com",
        "IPA_USERNAME": "admin",
        "IPA_PASSWORD": "your-password"
      }
    }
  }
}
```

### From Source

```bash
cd ipa-mcp
uv sync --all-groups
uv run ipa-mcp
```

## Tools

### Read Tools (6)

| Tool | Description |
|------|-------------|
| `ipa_list_groups` | List user groups |
| `ipa_list_hostgroups` | List host groups |
| `ipa_list_hbac_rules` | List HBAC rules |
| `ipa_list_sudo_rules` | List sudo rules |
| `ipa_list_users` | List users |
| `ipa_list_hosts` | List hosts |

### Write Tools (10)

| Tool | Description |
|------|-------------|
| `ipa_create_group` | Create user group |
| `ipa_add_group_members` | Add users to group |
| `ipa_create_hostgroup` | Create host group |
| `ipa_add_hostgroup_members` | Add hosts to host group |
| `ipa_create_hbac_rule` | Create HBAC rule |
| `ipa_add_hbac_rule_members` | Add members to HBAC rule |
| `ipa_create_sudo_rule` | Create sudo rule |
| `ipa_add_sudo_rule_members` | Add members to sudo rule |
| `ipa_add_sudo_option` | Add sudo option |
| `ipa_setup_forge` | One-shot forge cluster setup (groups + HBAC + sudo) |

## CLI

The companion `ipa-cli` provides the same capabilities via shell commands — use when token budget matters or shell access is available.

| Task | Command |
|------|---------|
| List user groups | `ipa-cli groups` |
| List host groups | `ipa-cli hostgroups` |
| List HBAC rules | `ipa-cli hbac-rules` |
| List sudo rules | `ipa-cli sudo-rules` |
| List users | `ipa-cli users` |
| List hosts | `ipa-cli hosts` |
| Create user group | `ipa-cli create-group <name> --desc "description"` |
| Create host group | `ipa-cli create-hostgroup <name>` |
| Full forge setup | `ipa-cli setup-forge <cluster> --hosts "host1,host2" --users "alice,bob"` |

Install CLI: `uvx --from ipa-mcp ipa-cli` or run from repo with `uv run ipa-cli`.

## Cross-MCP Integration

This server works alongside other MCP servers in the SRE stack:

- **NetBox MCP** — Look up host FQDNs before adding them to IPA host groups. NetBox is the source of truth for device inventory.
- **AWX MCP** — Trigger Ansible playbooks for IPA enrollment or host provisioning after forge setup.
- **MAAS MCP** — Coordinate with MAAS when commissioning nodes that will be enrolled in IPA.

## Installation

Requires Python 3.12+ and a FreeIPA server with JSON-RPC API enabled.

```bash
uv add ipa-mcp
# or
pip install ipa-mcp
```

For development from source:

```bash
cd ipa-mcp
uv sync --all-groups
```

## Configuration

### Environment Variables

Create a `.env` file (see `env.example`):

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `IPA_HOST` | Yes | — | FreeIPA server hostname or URL |
| `IPA_USERNAME` | No | `admin` | IPA API username |
| `IPA_PASSWORD` | Yes | — | IPA admin password |
| `IPA_VERIFY_SSL` | No | `false` | SSL certificate verification (typically false for self-signed) |

Aliases: `IPA_URL` for `IPA_HOST`, `IPA_USER` for `IPA_USERNAME`, `IPA_PASS` for `IPA_PASSWORD`.

### Command Line

```bash
ipa-mcp                    # stdio (default)
ipa-cli groups             # CLI
ipa-cli setup-forge cartesia5 --hosts "host1.cloud.together.ai" --users "alice"
```

## Cursor / Claude Code Integration

### Cursor (`.cursor/mcp.json` or `.mcp.json`)

```json
{
  "mcpServers": {
    "ipa-mcp": {
      "command": "uv",
      "args": ["--directory", "/path/to/ipa-mcp", "run", "ipa-mcp"],
      "env": {
        "IPA_HOST": "ipa.example.com",
        "IPA_USERNAME": "admin",
        "IPA_PASSWORD": "your-password"
      }
    }
  }
}
```

### Claude Code

```bash
claude mcp add ipa-mcp -- uv --directory /path/to/ipa-mcp run ipa-mcp
```

## Development

```bash
uv sync --all-groups
uv run ruff check src/ tests/
uv run ruff format src/ tests/
uv run pytest -v
uv run mypy src/
```

### Project Structure

```
src/ipa_mcp/
├── config.py       # Pydantic Settings
├── ipa_client.py   # FreeIPA JSON-RPC client
├── server.py       # FastMCP tools and entrypoint
└── cli.py          # Typer CLI
```

## Security

- Credentials are `SecretStr` and redacted in logs
- Never commit `.env` files with real credentials
- FreeIPA servers often use self-signed certs — `IPA_VERIFY_SSL=false` is typical

## License

Apache License 2.0
