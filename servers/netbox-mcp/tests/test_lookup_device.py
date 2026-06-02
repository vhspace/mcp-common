"""Tests for the netbox_lookup_device consolidated tool."""

from unittest.mock import patch

from netbox_mcp.server import (
    ESSENTIAL_DEVICE_FIELDS,
    _trim_device,
    netbox_get_objects,
    netbox_lookup_device,
)

MOCK_DEVICE = {
    "id": 42,
    "name": "gpu-node-01.dc1.together.ai",
    "status": {"value": "active", "label": "Active"},
    "site": {"id": 1, "name": "DC1", "slug": "dc1"},
    "rack": {"id": 10, "name": "R01"},
    "device_role": {"id": 2, "name": "GPU Node"},
    "device_type": {"id": 5, "name": "DGX H100"},
    "serial": "SN123456",
    "primary_ip4": {"id": 100, "address": "10.20.30.40/24", "family": 4},
    "primary_ip6": None,
    "oob_ip": {"id": 200, "address": "192.168.196.12/24", "family": 4},
}


@patch("netbox_mcp.server.netbox")
def test_lookup_returns_device_with_flat_ips(mock_netbox):
    """Should return device with convenience *_address fields (bare IPs)."""
    mock_netbox.get.return_value = {
        "count": 1,
        "next": None,
        "previous": None,
        "results": [MOCK_DEVICE.copy()],
    }

    result = netbox_lookup_device(hostname="gpu-node-01")

    assert result["count"] == 1
    device = result["results"][0]
    assert device["primary_ip4_address"] == "10.20.30.40"
    assert device["oob_ip_address"] == "192.168.196.12"
    assert device["primary_ip6_address"] is None


@patch("netbox_mcp.server.netbox")
def test_lookup_returns_empty_for_no_match(mock_netbox):
    """Should return count=0 and empty results when neither name nor Provider_Machine_ID matches."""
    mock_netbox.get.return_value = {
        "count": 0,
        "next": None,
        "previous": None,
        "results": [],
    }

    result = netbox_lookup_device(hostname="nonexistent-host")

    assert result["count"] == 0
    assert result["results"] == []
    assert result["query"] == "nonexistent-host"
    assert mock_netbox.get.call_count == 2


@patch("netbox_mcp.server.netbox")
def test_lookup_falls_back_to_provider_machine_id(mock_netbox):
    """Should fall back to cf_Provider_Machine_ID when name search returns nothing."""
    empty = {"count": 0, "next": None, "previous": None, "results": []}
    fallback_device = MOCK_DEVICE.copy()
    fallback_device["name"] = "f30409c5-342"
    fallback_hit = {"count": 1, "next": None, "previous": None, "results": [fallback_device]}

    mock_netbox.get.side_effect = [empty, fallback_hit]

    result = netbox_lookup_device(hostname="PG22A-6-3-HPC")

    assert result["count"] == 1
    assert result["results"][0]["name"] == "f30409c5-342"
    assert mock_netbox.get.call_count == 2
    fallback_call = mock_netbox.get.call_args_list[1]
    assert fallback_call[1]["params"]["cf_Provider_Machine_ID"] == "PG22A-6-3-HPC"


@patch("netbox_mcp.server.netbox")
def test_lookup_skips_fallback_when_name_matches(mock_netbox):
    """Should NOT issue a fallback query when the name search already found results."""
    mock_netbox.get.return_value = {
        "count": 1,
        "next": None,
        "previous": None,
        "results": [MOCK_DEVICE.copy()],
    }

    result = netbox_lookup_device(hostname="gpu-node-01")

    assert result["count"] == 1
    mock_netbox.get.assert_called_once()


@patch("netbox_mcp.server.netbox")
def test_lookup_uses_case_insensitive_search(mock_netbox):
    """Should pass name__ic filter for case-insensitive partial matching."""
    mock_netbox.get.return_value = {"count": 0, "results": [], "next": None, "previous": None}

    netbox_lookup_device(hostname="GPU-NODE")

    first_call = mock_netbox.get.call_args_list[0]
    assert first_call[1]["params"]["name__ic"] == "GPU-NODE"


@patch("netbox_mcp.server.netbox")
def test_lookup_passes_fields_parameter(mock_netbox):
    """Should forward the fields parameter to the API query."""
    mock_netbox.get.return_value = {"count": 0, "results": [], "next": None, "previous": None}

    netbox_lookup_device(hostname="test", fields=["id", "name", "oob_ip"])

    call_args = mock_netbox.get.call_args
    assert call_args[1]["params"]["fields"] == "id,name,oob_ip"


@patch("netbox_mcp.server.netbox")
def test_lookup_limits_results_to_5(mock_netbox):
    """Should request at most 5 results from the API."""
    mock_netbox.get.return_value = {"count": 0, "results": [], "next": None, "previous": None}

    netbox_lookup_device(hostname="node")

    call_args = mock_netbox.get.call_args
    assert call_args[1]["params"]["limit"] == 5


@patch("netbox_mcp.server.netbox")
def test_lookup_handles_device_without_oob_ip(mock_netbox):
    """Should handle devices that have no oob_ip set."""
    device = MOCK_DEVICE.copy()
    device["oob_ip"] = None

    mock_netbox.get.return_value = {
        "count": 1,
        "results": [device],
        "next": None,
        "previous": None,
    }

    result = netbox_lookup_device(hostname="gpu-node-01")

    device_result = result["results"][0]
    assert device_result["oob_ip_address"] is None
    assert device_result["primary_ip4_address"] == "10.20.30.40"


@patch("netbox_mcp.server.netbox")
def test_lookup_multiple_matches(mock_netbox):
    """Should return all matching devices (up to limit)."""
    device1 = MOCK_DEVICE.copy()
    device2 = MOCK_DEVICE.copy()
    device2["id"] = 43
    device2["name"] = "gpu-node-02.dc1.together.ai"

    mock_netbox.get.return_value = {
        "count": 2,
        "results": [device1, device2],
        "next": None,
        "previous": None,
    }

    result = netbox_lookup_device(hostname="gpu-node")

    assert result["count"] == 2
    assert len(result["results"]) == 2


@patch("netbox_mcp.server.netbox")
def test_lookup_fallback_discards_excessive_results(mock_netbox):
    """Guard: if the cf_ filter is silently ignored, discard the unfiltered result set."""
    empty = {"count": 0, "next": None, "previous": None, "results": []}
    huge = {"count": 5000, "next": None, "previous": None, "results": [MOCK_DEVICE.copy()] * 5}

    mock_netbox.get.side_effect = [empty, huge]

    result = netbox_lookup_device(hostname="PG22A-6-3-HPC")

    assert result["count"] == 0
    assert result["results"] == []


@patch("netbox_mcp.server.netbox")
def test_lookup_fallback_propagates_fields(mock_netbox):
    """Fields parameter should be forwarded to the fallback query."""
    empty = {"count": 0, "next": None, "previous": None, "results": []}
    fallback_device = MOCK_DEVICE.copy()
    fallback_hit = {"count": 1, "next": None, "previous": None, "results": [fallback_device]}

    mock_netbox.get.side_effect = [empty, fallback_hit]

    netbox_lookup_device(hostname="PG22A-6-3-HPC", fields=["id", "name", "oob_ip"])

    fallback_call = mock_netbox.get.call_args_list[1]
    assert fallback_call[1]["params"]["fields"] == "id,name,oob_ip"
    assert fallback_call[1]["params"]["cf_Provider_Machine_ID"] == "PG22A-6-3-HPC"


# ── Site filter tests ────────────────────────────────────────────────

SITE_LOOKUP = {"count": 1, "results": [{"id": 5, "name": "ORI-TX"}]}


@patch("netbox_mcp.server.netbox")
def test_lookup_site_filter_adds_site_id(mock_netbox):
    """When site is provided, site_id should be included in the device query."""
    mock_netbox.get.side_effect = [
        SITE_LOOKUP,
        {"count": 1, "results": [MOCK_DEVICE.copy()], "next": None, "previous": None},
    ]

    result = netbox_lookup_device(hostname="gpu-node-01", site="ORI-TX")

    assert result["count"] == 1
    device_call = mock_netbox.get.call_args_list[1]
    assert device_call[1]["params"]["site_id"] == 5


@patch("netbox_mcp.server.netbox")
def test_lookup_site_filter_propagates_to_fallback(mock_netbox):
    """Site filter should also be applied to the Provider_Machine_ID fallback."""
    empty = {"count": 0, "results": [], "next": None, "previous": None}
    fallback_device = MOCK_DEVICE.copy()
    fallback_hit = {"count": 1, "results": [fallback_device], "next": None, "previous": None}

    mock_netbox.get.side_effect = [SITE_LOOKUP, empty, fallback_hit]

    result = netbox_lookup_device(hostname="PG22A-6-3-HPC", site="ORI-TX")

    assert result["count"] == 1
    fallback_call = mock_netbox.get.call_args_list[2]
    assert fallback_call[1]["params"]["site_id"] == 5
    assert fallback_call[1]["params"]["cf_Provider_Machine_ID"] == "PG22A-6-3-HPC"


@patch("netbox_mcp.server.netbox")
def test_lookup_without_site_has_no_site_id(mock_netbox):
    """When no site is provided, site_id should not appear in params."""
    mock_netbox.get.return_value = {
        "count": 1,
        "results": [MOCK_DEVICE.copy()],
        "next": None,
        "previous": None,
    }

    netbox_lookup_device(hostname="gpu-node-01")

    call_args = mock_netbox.get.call_args
    assert "site_id" not in call_args[1]["params"]


@patch("netbox_mcp.server.netbox")
def test_lookup_site_not_found_returns_hint(mock_netbox):
    """Invalid site should return count=0 with a helpful _hint."""
    mock_netbox.get.return_value = {"count": 0, "results": []}

    result = netbox_lookup_device(hostname="gpu-node-01", site="NO-SUCH-SITE")

    assert result["count"] == 0
    assert "_hint" in result
    assert "NO-SUCH-SITE" in result["_hint"]


@patch("netbox_mcp.server.netbox")
def test_lookup_multiple_matches_includes_hint(mock_netbox):
    """When multiple devices match, result should include a disambiguation hint."""
    device2 = MOCK_DEVICE.copy()
    device2["id"] = 2
    device2["name"] = "gpu-node-02"
    mock_netbox.get.return_value = {
        "count": 2,
        "results": [MOCK_DEVICE.copy(), device2],
        "next": None,
        "previous": None,
    }

    result = netbox_lookup_device(hostname="gpu-node")

    assert result["count"] == 2
    assert "_hint" in result
    assert "Multiple" in result["_hint"]


# ── _trim_device tests ────────────────────────────────────────────────


FULL_DEVICE = {
    "id": 42,
    "url": "https://netbox.example.com/api/dcim/devices/42/",
    "display": "gpu-node-01",
    "name": "gpu-node-01",
    "status": {"value": "active", "label": "Active"},
    "serial": "SN123",
    "site": {"id": 1, "name": "ORI-TX", "slug": "ori-tx", "url": "https://..."},
    "rack": {"id": 10, "name": "R01", "display": "R01", "url": "https://..."},
    "position": 12,
    "device_role": {"id": 2, "name": "GPU Node", "slug": "gpu-node", "url": "https://..."},
    "device_type": {"id": 5, "model": "H100-80GB-SXM-8x", "manufacturer": {"name": "NVIDIA"}},
    "cluster": {"id": 7, "name": "research-common-h100", "url": "https://..."},
    "primary_ip4": {"id": 100, "address": "10.0.0.1/24", "family": 4},
    "primary_ip6": None,
    "oob_ip": {"id": 200, "address": "192.168.196.12/24", "family": 4},
    "primary_ip4_address": "10.0.0.1",
    "primary_ip6_address": None,
    "oob_ip_address": "192.168.196.12",
    "custom_fields": {
        "Provider_Machine_ID": "GPU-39",
        "some_internal_field": "irrelevant",
        "another_field": 123,
    },
    "tags": [{"name": "gpu"}],
    "config_context": {"huge": "blob"},
    "tenant": {"id": 1, "name": "team-a"},
    "comments": "some notes",
    "local_context_data": None,
}


def test_trim_device_keeps_essential_fields():
    """Trimmed result should contain only ESSENTIAL_DEVICE_FIELDS keys."""
    trimmed = _trim_device(FULL_DEVICE)
    assert set(trimmed.keys()).issubset(ESSENTIAL_DEVICE_FIELDS)


def test_trim_device_strips_non_essential_fields():
    """Fields like url, display, config_context, tenant should be removed."""
    trimmed = _trim_device(FULL_DEVICE)
    for key in ("url", "display", "config_context", "tenant", "comments", "local_context_data"):
        assert key not in trimmed


def test_trim_device_extracts_nested_object_names():
    """Nested dicts (site, rack, cluster, etc.) should be simplified to id+name."""
    trimmed = _trim_device(FULL_DEVICE)
    assert trimmed["site"] == {"id": 1, "name": "ORI-TX"}
    assert trimmed["rack"] == {"id": 10, "name": "R01"}
    assert trimmed["cluster"] == {"id": 7, "name": "research-common-h100"}
    assert trimmed["device_role"] == {"id": 2, "name": "GPU Node"}


def test_trim_device_extracts_device_type_model():
    """device_type should use 'model' field when 'name' is absent."""
    trimmed = _trim_device(FULL_DEVICE)
    assert trimmed["device_type"] == {"id": 5, "name": "H100-80GB-SXM-8x"}


def test_trim_device_keeps_only_provider_machine_id():
    """custom_fields should only retain Provider_Machine_ID."""
    trimmed = _trim_device(FULL_DEVICE)
    assert trimmed["custom_fields"] == {"Provider_Machine_ID": "GPU-39"}


def test_trim_device_omits_custom_fields_when_no_provider_id():
    """custom_fields should be omitted entirely when Provider_Machine_ID is absent."""
    device = FULL_DEVICE.copy()
    device["custom_fields"] = {"some_other": "value"}
    trimmed = _trim_device(device)
    assert "custom_fields" not in trimmed


def test_trim_device_preserves_status_as_string():
    """Status dict should be flattened to its value string."""
    trimmed = _trim_device(FULL_DEVICE)
    assert trimmed["status"] == "active"


def test_trim_device_preserves_scalar_status():
    """If status is already a string, keep it as-is."""
    device = FULL_DEVICE.copy()
    device["status"] = "planned"
    trimmed = _trim_device(device)
    assert trimmed["status"] == "planned"


@patch("netbox_mcp.server.netbox")
def test_lookup_trims_when_fields_is_none(mock_netbox):
    """When fields=None, the response should be trimmed to essential fields."""
    full_device = FULL_DEVICE.copy()
    mock_netbox.get.return_value = {
        "count": 1,
        "results": [full_device],
        "next": None,
        "previous": None,
    }

    result = netbox_lookup_device(hostname="gpu-node-01")

    device = result["results"][0]
    assert "url" not in device
    assert "config_context" not in device
    assert "tenant" not in device
    assert device["site"] == {"id": 1, "name": "ORI-TX"}


@patch("netbox_mcp.server.netbox")
def test_get_objects_default_limit_is_20(mock_netbox):
    """netbox_get_objects should default to limit=20."""
    mock_netbox.get.return_value = {"count": 0, "results": [], "next": None, "previous": None}
    netbox_get_objects(object_type="dcim.device", filters={"status": "active"})
    params = mock_netbox.get.call_args[1]["params"]
    assert params["limit"] == 20


def test_trim_device_promotes_provider_machine_id():
    """provider_machine_id should appear as a top-level field when Provider_Machine_ID is set."""
    device = FULL_DEVICE.copy()
    device["provider_machine_id"] = device["custom_fields"]["Provider_Machine_ID"]
    trimmed = _trim_device(device)
    assert trimmed["provider_machine_id"] == "GPU-39"
    assert "provider_machine_id" in ESSENTIAL_DEVICE_FIELDS


def test_trim_device_omits_provider_machine_id_when_absent():
    """provider_machine_id should not appear when not set on the device."""
    device = FULL_DEVICE.copy()
    device.pop("provider_machine_id", None)
    device["custom_fields"] = {"some_other": "value"}
    trimmed = _trim_device(device)
    assert "provider_machine_id" not in trimmed


@patch("netbox_mcp.server.netbox")
def test_lookup_enriches_provider_machine_id(mock_netbox):
    """netbox_lookup_device should promote Provider_Machine_ID to a top-level field."""
    device = FULL_DEVICE.copy()
    mock_netbox.get.return_value = {
        "count": 1,
        "results": [device],
        "next": None,
        "previous": None,
    }

    result = netbox_lookup_device(hostname="gpu-node-01")

    dev = result["results"][0]
    assert dev["provider_machine_id"] == "GPU-39"


@patch("netbox_mcp.server.netbox")
def test_lookup_no_provider_machine_id_when_absent(mock_netbox):
    """provider_machine_id should not be set when custom_fields lacks it."""
    device = MOCK_DEVICE.copy()
    device["custom_fields"] = {"other_field": "val"}
    mock_netbox.get.return_value = {
        "count": 1,
        "results": [device],
        "next": None,
        "previous": None,
    }

    result = netbox_lookup_device(hostname="gpu-node-01")

    dev = result["results"][0]
    assert "provider_machine_id" not in dev


@patch("netbox_mcp.server.netbox")
def test_lookup_does_not_trim_when_fields_provided(mock_netbox):
    """When fields are explicitly provided, no trimming should occur."""
    full_device = FULL_DEVICE.copy()
    mock_netbox.get.return_value = {
        "count": 1,
        "results": [full_device],
        "next": None,
        "previous": None,
    }

    result = netbox_lookup_device(hostname="gpu-node-01", fields=["id", "name", "site"])

    device = result["results"][0]
    # Fields are passed to API; full response is preserved as-is
    assert "url" in device
    assert "config_context" in device
