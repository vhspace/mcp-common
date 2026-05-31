"""Integration tests against real Redfish endpoints.

Set environment variables to run:
  export REDFISH_IP=192.168.196.54
  export REDFISH_USER=admin
  export REDFISH_PASSWORD=yourpassword

Run with: pytest -m integration -v
"""

import os

import pytest

from redfish_mcp.mcp_server import create_mcp_app


@pytest.fixture
def integration_config():
    """Get integration test config from environment."""
    config = {
        "host": os.getenv("REDFISH_IP"),
        "user": os.getenv("REDFISH_USER"),
        "password": os.getenv("REDFISH_PASSWORD"),
    }
    if not all(config.values()):
        pytest.skip("Integration tests require REDFISH_IP, REDFISH_USER, REDFISH_PASSWORD")
    return config


@pytest.fixture
def mcp_tools():
    _, tools = create_mcp_app()
    return tools


@pytest.mark.integration
@pytest.mark.anyio
async def test_system_info_real(mcp_tools, integration_config):
    """Test getting system info from real hardware."""
    result = await mcp_tools["redfish_get_info"](
        host=integration_config["host"],
        user=integration_config["user"],
        password=integration_config["password"],
        info_types=["system"],
        verify_tls=False,
        timeout_s=30,
    )

    print(f"\nSystem Info Result: {result}")
    assert result["ok"] is True, f"Failed: {result.get('error')}"
    assert "system" in result
    assert result["system"]["Manufacturer"] is not None


@pytest.mark.integration
@pytest.mark.anyio
async def test_drives_inventory_real(mcp_tools, integration_config):
    """Test drive inventory from real hardware."""
    result = await mcp_tools["redfish_get_info"](
        host=integration_config["host"],
        user=integration_config["user"],
        password=integration_config["password"],
        info_types=["drives"],
        verify_tls=False,
        timeout_s=60,
    )

    print(f"\nDrive Inventory Result: drives={result.get('drives', {}).get('count')}")
    assert result["ok"] is True
    assert "drives" in result
    assert "count" in result["drives"]
    # Print drive info if any found
    for drive in result.get("drives", {}).get("drives", [])[:3]:  # First 3 drives
        print(
            f"  Drive: {drive.get('Model')} SN:{drive.get('SerialNumber')} Cap:{drive.get('CapacityBytes')}"
        )


@pytest.mark.integration
@pytest.mark.anyio
async def test_comprehensive_info_real(mcp_tools, integration_config):
    """Test comprehensive info report from real hardware."""
    result = await mcp_tools["redfish_get_info"](
        host=integration_config["host"],
        user=integration_config["user"],
        password=integration_config["password"],
        info_types=["system", "boot", "drives"],
        verify_tls=False,
        timeout_s=60,
    )

    print("\nComprehensive Info Report:")
    print(
        f"  System: {result.get('system', {}).get('Manufacturer')} {result.get('system', {}).get('Model')}"
    )
    print(f"  Boot Target: {result.get('boot', {}).get('BootSourceOverrideTarget')}")
    print(f"  Drives Found: {result.get('drives', {}).get('count')}")

    assert result["ok"] is True
    assert "system" in result
    assert "boot" in result
    assert "drives" in result


@pytest.mark.integration
@pytest.mark.anyio
async def test_get_bios_for_comparison_real(integration_config):
    """Test reading BIOS attributes (for later comparison tests)."""
    from redfish_mcp.bios_diff import get_bios_attributes
    from redfish_mcp.redfish import RedfishClient

    c = RedfishClient(
        host=integration_config["host"],
        user=integration_config["user"],
        password=integration_config["password"],
        verify_tls=False,
        timeout_s=30,
    )

    ep = c.discover_system()
    attrs, bios_url, error = get_bios_attributes(c, ep)

    print(f"\nBIOS URL: {bios_url}")
    print(f"Attributes found: {len(attrs) if attrs else 0}")

    assert error is None, f"Failed to get BIOS attributes: {error}"
    assert attrs is not None
    assert isinstance(attrs, dict)
    assert len(attrs) > 0

    # Print sample attributes
    sample_keys = list(attrs.keys())[:5]
    for key in sample_keys:
        print(f"  {key}: {attrs[key]}")


@pytest.mark.integration
@pytest.mark.anyio
@pytest.mark.skipif(
    os.getenv("ENABLE_WRITE_TESTS") != "true",
    reason=("Write tests disabled by default. Set ENABLE_WRITE_TESTS=true to enable."),
)
async def test_set_nextboot_real(mcp_tools, integration_config):
    """Test setting next boot target (requires ENABLE_WRITE_TESTS=true).

    WARNING: This will modify the BMC boot settings!
    """
    # Set to BIOS setup once (safe test)
    result = await mcp_tools["redfish_set_nextboot"](
        host=integration_config["host"],
        user=integration_config["user"],
        password=integration_config["password"],
        target="bios",
        enabled="Once",
        reboot=False,  # Don't actually reboot
        allow_write=True,
        # Unit/integration tests call tools directly (no MCP session).
        async_mode=False,
    )

    print(f"\nSet Nextboot Result: {result}")
    assert result["ok"] is True
    assert "chosen_target" in result


@pytest.mark.integration
@pytest.mark.anyio
async def test_render_curl_modes(mcp_tools, integration_config):
    """Test that render_curl mode works for all tools."""
    tools_to_test = [
        (
            "redfish_get_info",
            {
                "host": "test",
                "user": "test",
                "password": "test",
                "info_types": ["system"],
            },
        ),
        (
            "redfish_query",
            {
                "host": "test",
                "user": "test",
                "password": "test",
                "query_type": "power_state",
            },
        ),
        (
            "redfish_diff_bios_settings",
            {
                "host_a": "test1",
                "host_b": "test2",
                "user": "test",
                "password": "test",
            },
        ),
        (
            "redfish_set_nextboot",
            {
                "host": "test",
                "user": "test",
                "password": "test",
                "allow_write": True,
            },
        ),
        (
            "redfish_set_bios_attributes",
            {
                "host": "test",
                "user": "test",
                "password": "test",
                "attributes": {"key": "value"},
                "allow_write": True,
            },
        ),
        (
            "redfish_update_firmware",
            {
                "host": "test",
                "user": "test",
                "password": "test",
                "image_path": "/tmp/fw.bin",
                "allow_write": True,
            },
        ),
    ]

    for tool_name, base_args in tools_to_test:
        result = await mcp_tools[tool_name](**base_args, execution_mode="render_curl")
        print(f"\n{tool_name} curl mode: {len(result.get('curl', []))} commands")
        assert result["ok"] is True
        assert result["execution_mode"] == "render_curl"
        assert "curl" in result
        assert len(result["curl"]) > 0
