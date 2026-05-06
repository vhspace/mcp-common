"""Basic import and structure tests for MAAS MCP Server."""

import pytest


def test_imports():
    """Test that all main modules can be imported."""
    from maas_mcp import MaasRestClient as ExportedMaasRestClient
    from maas_mcp import Settings
    from maas_mcp.config import MaasInstanceConfig, configure_logging
    from maas_mcp.drift_auditor import compare_bios, compare_nics, compare_storage
    from maas_mcp.maas_client import MaasRestClient
    from maas_mcp.netbox_helper import (
        find_device_by_provider_id,
        fuzzy_match_machine,
        validate_link,
    )

    assert MaasRestClient is not None
    assert ExportedMaasRestClient is MaasRestClient
    assert Settings is not None
    assert MaasInstanceConfig is not None
    assert configure_logging is not None
    assert compare_bios is not None
    assert compare_nics is not None
    assert compare_storage is not None
    assert find_device_by_provider_id is not None
    assert fuzzy_match_machine is not None
    assert validate_link is not None


def test_version():
    """Test that version is defined."""
    from maas_mcp import __version__

    assert __version__ == "1.15.0"


def test_config_validation(monkeypatch):
    """Test configuration validation."""
    import os

    import maas_mcp.config as config_mod
    from maas_mcp.config import Settings

    for key in list(os.environ):
        if "MAAS" in key.upper():
            monkeypatch.delenv(key, raising=False)

    monkeypatch.setattr(config_mod, "_load_env_with_dotfiles", lambda: dict(os.environ))

    with pytest.raises(ValueError, match="No MAAS instances configured"):
        Settings(_env_file=None)


def test_drift_comparison_nics():
    """Test NIC comparison with empty data."""
    from maas_mcp.drift_auditor import compare_nics

    machine1 = {"interfaces": []}
    machine2 = {"interfaces": []}

    result = compare_nics(machine1, machine2)

    assert "matches" in result
    assert "only_in_machine1" in result
    assert "only_in_machine2" in result
    assert "differences" in result
    assert len(result["matches"]) == 0
    assert len(result["only_in_machine1"]) == 0
    assert len(result["only_in_machine2"]) == 0
    assert len(result["differences"]) == 0


def test_drift_comparison_storage():
    """Test storage comparison with empty data."""
    from maas_mcp.drift_auditor import compare_storage

    machine1 = {"block_devices": []}
    machine2 = {"block_devices": []}

    result = compare_storage(machine1, machine2)

    assert "matches" in result
    assert "only_in_machine1" in result
    assert "only_in_machine2" in result
    assert "differences" in result


def test_drift_comparison_bios():
    """Test BIOS comparison."""
    from maas_mcp.drift_auditor import compare_bios

    machine1 = {"bios_settings": {"setting1": "value1", "setting2": "value2"}}
    machine2 = {"bios_settings": {"setting1": "value1", "setting3": "value3"}}

    result = compare_bios(machine1, machine2)

    assert "matches" in result
    assert "only_in_machine1" in result
    assert "only_in_machine2" in result
    assert "differences" in result
    assert len(result["matches"]) == 1  # setting1 matches
    assert "setting2" in result["only_in_machine1"]
    assert "setting3" in result["only_in_machine2"]


def test_netbox_fuzzy_match_empty():
    """Test fuzzy matching with empty data."""
    from maas_mcp.netbox_helper import fuzzy_match_machine

    machine = {"hostname": "test-machine", "system_id": "abc123", "interfaces": []}
    devices = []

    result = fuzzy_match_machine(machine, devices)

    assert result["match"] is None
    assert result["confidence"] == "none"
    assert result["method"] is None
    assert isinstance(result["warnings"], list)


def test_netbox_validate_link_no_provider_id():
    """Test link validation when NetBox device has no Provider_Machine_ID."""
    from maas_mcp.netbox_helper import validate_link

    maas_machine = {"hostname": "test-machine", "system_id": "abc123", "interfaces": []}
    netbox_device = {"custom_fields": {}}

    result = validate_link(maas_machine, netbox_device)

    assert isinstance(result["warnings"], list)
    assert len(result["warnings"]) > 0
    assert "no Provider_Machine_ID" in result["warnings"][0]
