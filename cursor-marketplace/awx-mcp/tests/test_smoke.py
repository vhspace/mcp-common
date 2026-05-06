"""Smoke tests for awx-cli and awx-mcp against a live AWX instance.

Read-only tests that verify CLI and MCP tool behavior matches the skill
documentation in skills/awx-automation/SKILL.md. Safe to run against
production — no jobs are launched, no resources are modified.

Run manually:
    uv run pytest tests/test_smoke.py -v -s

Requires AWX_HOST and AWX_TOKEN in environment or .env file.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# .env loading — secrets are expected in .env when running locally
# ---------------------------------------------------------------------------


def _load_dotenv() -> None:
    """Source AWX_HOST / AWX_TOKEN from .env if not already set."""
    for candidate in [REPO_ROOT / ".env", REPO_ROOT.parent / ".env"]:
        if candidate.exists():
            for line in candidate.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip("'\"")
                if key and key not in os.environ:
                    os.environ[key] = value
            break


_load_dotenv()

# ---------------------------------------------------------------------------
# Skip entire module when AWX credentials are absent
# ---------------------------------------------------------------------------

_has_creds = bool(os.getenv("AWX_HOST")) and bool(os.getenv("AWX_TOKEN"))

pytestmark = [
    pytest.mark.smoke,
    pytest.mark.skipif(not _has_creds, reason="AWX_HOST / AWX_TOKEN not set"),
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _awx_cli(*args: str) -> subprocess.CompletedProcess[str]:
    """Run awx-cli, preferring the PATH binary, falling back to uv run."""
    cli = shutil.which("awx-cli")
    cmd = [cli, *args] if cli else ["uv", "run", "awx-cli", *args]
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(REPO_ROOT),
    )


def _cli_json(*args: str) -> dict:
    """Run awx-cli with --json and return parsed output."""
    result = _awx_cli(*args, "--json")
    assert result.returncode == 0, f"awx-cli {' '.join(args)} failed: {result.stderr}"
    return json.loads(result.stdout)


# ===================================================================
# CLI smoke tests
# ===================================================================


class TestCLISmoke:
    """Verify awx-cli works against the live AWX instance."""

    def test_ping(self) -> None:
        result = _awx_cli("ping")
        assert result.returncode == 0
        assert "version" in result.stdout.lower() or "pong" in result.stdout.lower()

    def test_templates_list(self) -> None:
        data = _cli_json("templates", "--fields", "id,name,playbook")
        assert data.get("count", 0) > 0, "No templates found"
        assert "results" in data

    def test_inventories_list(self) -> None:
        data = _cli_json("inventories")
        assert data.get("count", 0) > 0, "No inventories found"

    def test_me(self) -> None:
        result = _awx_cli("me")
        assert result.returncode == 0


class TestCLIHostnameGotcha:
    """Validate the skill's hostname warning: AWX inventories use short names,
    not FQDNs.  Uses inventory 256 (research-common-h100) as the test target.
    If that inventory doesn't exist, the tests are skipped gracefully."""

    INVENTORY_ID = "256"
    SHORT_HOST = "research-common-h100-013"
    FQDN_HOST = "research-common-h100-013.cloud.together.ai"

    @pytest.fixture(autouse=True)
    def _check_inventory(self) -> None:
        result = _awx_cli("hosts", self.INVENTORY_ID)
        if result.returncode != 0 or "not found" in result.stderr.lower():
            pytest.skip(f"Inventory {self.INVENTORY_ID} not reachable")

    def test_short_hostname_matches(self) -> None:
        """The skill says to use short hostnames — they should match."""
        data = _cli_json("hosts", self.INVENTORY_ID, "--search", self.SHORT_HOST)
        assert data.get("count", 0) >= 1, (
            f"Short hostname '{self.SHORT_HOST}' should match at least one host"
        )

    def test_fqdn_does_not_match(self) -> None:
        """The skill warns FQDNs will match zero hosts — verify that."""
        data = _cli_json("hosts", self.INVENTORY_ID, "--search", self.FQDN_HOST)
        assert data.get("count", 0) == 0, (
            f"FQDN '{self.FQDN_HOST}' should NOT match any host (skill warning is correct)"
        )

    def test_hosts_use_short_names(self) -> None:
        """Verify the naming convention: no host should contain a domain suffix."""
        data = _cli_json("hosts", self.INVENTORY_ID)
        for host in (data.get("results") or [])[:10]:
            name = host.get("name", "")
            assert ".cloud.together.ai" not in name, (
                f"Host '{name}' uses FQDN — expected short hostname"
            )


# ===================================================================
# MCP smoke tests (in-process via fastmcp.Client)
# ===================================================================


class TestMCPSmoke:
    """Verify MCP tools work in-process against the live AWX instance."""

    @pytest.fixture
    async def client(self):
        from mcp_common.testing import mcp_client

        import awx_mcp.server as srv
        from awx_mcp.awx_client import AwxRestClient
        from awx_mcp.config import Settings

        if srv.awx is None:
            settings = Settings()
            srv.awx = AwxRestClient(
                host=str(settings.awx_host),
                token=settings.awx_token.get_secret_value(),
                api_base_path=settings.api_base_path,
                verify_ssl=settings.verify_ssl,
                timeout_seconds=settings.timeout_seconds,
            )
        async for c in mcp_client(srv.mcp):
            yield c

    @pytest.mark.anyio
    async def test_ping_tool_exists(self, client) -> None:
        from mcp_common.testing import assert_tool_exists

        await assert_tool_exists(client, "awx_ping")

    @pytest.mark.anyio
    async def test_ping(self, client) -> None:
        from mcp_common.testing import assert_tool_success

        result = await assert_tool_success(client, "awx_ping")
        assert result is not None

    @pytest.mark.anyio
    async def test_list_templates(self, client) -> None:
        from mcp_common.testing import assert_tool_success

        result = await assert_tool_success(
            client,
            "awx_list_resources",
            {
                "resource_type": "job_templates",
                "fields": ["id", "name"],
                "page_size": 5,
            },
        )
        assert result is not None

    @pytest.mark.anyio
    async def test_list_inventories(self, client) -> None:
        from mcp_common.testing import assert_tool_success

        result = await assert_tool_success(
            client,
            "awx_list_resources",
            {
                "resource_type": "inventories",
                "fields": ["id", "name"],
                "page_size": 5,
            },
        )
        assert result is not None
