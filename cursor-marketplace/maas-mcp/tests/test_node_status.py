"""Tests for MAAS NodeStatus coercion (machines list filters)."""

from maas_mcp.node_status import (
    apply_status_coercion_to_machine_params,
    coerce_machines_list_status_value,
)


def test_coerce_integer_to_alias() -> None:
    assert coerce_machines_list_status_value(4) == "ready"


def test_coerce_numeric_string() -> None:
    assert coerce_machines_list_status_value("6") == "deployed"


def test_coerce_lowercase_alias() -> None:
    assert coerce_machines_list_status_value("ready") == "ready"
    assert coerce_machines_list_status_value("deployed") == "deployed"


def test_coerce_uppercase_alias() -> None:
    assert coerce_machines_list_status_value("Ready") == "ready"
    assert coerce_machines_list_status_value("DEPLOYED") == "deployed"


def test_coerce_ui_label_with_spaces() -> None:
    assert coerce_machines_list_status_value("Failed commissioning") == "failed_commissioning"


def test_coerce_unknown_string_unchanged() -> None:
    assert coerce_machines_list_status_value("not-a-status") == "not-a-status"


def test_apply_params_coerces_status_only() -> None:
    p = apply_status_coercion_to_machine_params(
        {"hostname": "gpu001", "status": "ready", "zone": "az1"}
    )
    assert p["hostname"] == "gpu001"
    assert p["status"] == "ready"
    assert p["zone"] == "az1"


def test_apply_params_status_list() -> None:
    p = apply_status_coercion_to_machine_params({"status": ["ready", "6"]})
    assert p["status"] == ["ready", "deployed"]


def test_apply_empty() -> None:
    assert apply_status_coercion_to_machine_params({}) == {}
