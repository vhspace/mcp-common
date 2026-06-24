"""CLI-layer integration test against the seeded NetBox simulator.

The other integration modules call the ``netbox_mcp.server`` tool functions
directly; this one drives the real ``netbox-cli`` Typer app so a CLI-only
behaviour -- ``search`` auto-expanding a matched cluster to its member devices
(the README's headline feature) -- is actually exercised end to end against a
live NetBox. The ``netbox_client`` fixture sets ``server.netbox``, which the
CLI's ``_client()`` reuses, so the command talks to the sim.
"""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from netbox_mcp.cli import app

pytestmark = pytest.mark.integration

try:  # Click >= 8.2 removed ``mix_stderr`` (stdout/stderr are always separate).
    _runner = CliRunner(mix_stderr=False)
except TypeError:
    _runner = CliRunner()


def test_cli_search_expands_cluster(netbox_client: object) -> None:
    """``search cartesia5 --json`` auto-expands the cluster to its 2 members."""
    result = _runner.invoke(app, ["search", "cartesia5", "--json"])

    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)

    expanded = data["cluster_devices"]["cartesia5"]
    assert expanded["count"] == 2
    member_names = {d["name"] for d in expanded["results"]}
    assert {"sim-gpu-01", "sim-gpu-02"} <= member_names
