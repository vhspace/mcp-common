"""Package-import smoke test for the kvm subpackage."""

from __future__ import annotations


def test_kvm_package_importable():
    import redfish_mcp.kvm
    import redfish_mcp.kvm.daemon

    assert hasattr(redfish_mcp.kvm, "__name__")
    assert redfish_mcp.kvm.__name__ == "redfish_mcp.kvm"
    assert redfish_mcp.kvm.daemon.__name__ == "redfish_mcp.kvm.daemon"
