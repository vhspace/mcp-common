"""Inventory loader + JSON Schema tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mcp_network.inventory import (
    InventoryLoader,
    SiteInventory,
    default_inventory_dir,
)


def test_default_inventory_dir_exists() -> None:
    d = default_inventory_dir()
    assert d.is_dir()
    assert (d / "sites").is_dir()
    assert (d / "schema" / "site.schema.json").is_file()


def test_shipped_inventory_validates_and_loads() -> None:
    loader = InventoryLoader()
    sites = loader.load_dir()
    assert len(sites) >= 1
    ori = next(s for s in sites if s.site == "ori")
    assert ori.display_name
    assert ori.driver == "cumulus"
    assert ori.default is True
    # Fleet shape from switch_port_mapping.md
    assert len(ori.switches) == 6
    roles = {s.role for s in ori.switches}
    assert roles == {"leaf", "spine"}
    leaves = [s for s in ori.switches if s.role == "leaf"]
    spines = [s for s in ori.switches if s.role == "spine"]
    assert len(leaves) == 4
    assert len(spines) == 2
    # credentials are named, not embedded
    assert ori.credentials_env.user == "ORI_NETWORK_USER"
    assert ori.credentials_env.password == "ORI_NETWORK_PASSWORD"


def test_schema_rejects_bad_role(tmp_path: Path) -> None:
    good = default_inventory_dir() / "sites" / "ori.json"
    data = json.loads(good.read_text())
    data["switches"][0]["role"] = "router"  # not in enum
    _write_tmp_inventory(tmp_path, "bad", data)
    loader = InventoryLoader(tmp_path)
    assert loader.load_dir() == []


def test_schema_rejects_missing_credentials_env(tmp_path: Path) -> None:
    good = default_inventory_dir() / "sites" / "ori.json"
    data = json.loads(good.read_text())
    del data["credentials_env"]
    _write_tmp_inventory(tmp_path, "bad", data)
    loader = InventoryLoader(tmp_path)
    assert loader.load_dir() == []


def test_resolve_credentials_with_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    good = default_inventory_dir() / "sites" / "ori.json"
    data = json.loads(good.read_text())
    _write_tmp_inventory(tmp_path, "ori", data)
    loader = InventoryLoader(tmp_path)
    inv = loader.load_dir()[0]
    monkeypatch.setenv("ORI_NETWORK_USER", "alice")
    monkeypatch.setenv("ORI_NETWORK_PASSWORD", "s3cret")
    user, pw = inv.resolve_credentials()
    assert user == "alice"
    assert pw is not None
    assert pw.get_secret_value() == "s3cret"


def test_resolve_credentials_missing_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    good = default_inventory_dir() / "sites" / "ori.json"
    data = json.loads(good.read_text())
    _write_tmp_inventory(tmp_path, "ori", data)
    loader = InventoryLoader(tmp_path)
    inv = loader.load_dir()[0]
    monkeypatch.delenv("ORI_NETWORK_USER", raising=False)
    monkeypatch.delenv("ORI_NETWORK_PASSWORD", raising=False)
    user, pw = inv.resolve_credentials()
    assert user is None
    assert pw is None


def test_find_switch_by_name_and_ip() -> None:
    inv: SiteInventory = InventoryLoader().load_dir()[0]
    sw = inv.find_switch("dfw01-inb-sw-lea-03")
    assert sw is not None
    assert sw.mgmt_ip == "192.168.199.205"
    assert inv.find_switch("192.168.199.205") is not None
    assert inv.find_switch("192.168.229.252") is not None  # spine alt mgmt
    assert inv.find_switch("not-a-switch") is None


def _write_tmp_inventory(tmp_path: Path, name: str, data: dict) -> None:
    """Helper: write a site JSON to a tmp inventory dir (with schema copy)."""
    sites = tmp_path / "sites"
    sites.mkdir(exist_ok=True)
    (sites / f"{name}.json").write_text(json.dumps(data))
    schema_src = default_inventory_dir() / "schema" / "site.schema.json"
    schema_dst = tmp_path / "schema"
    schema_dst.mkdir(exist_ok=True)
    (schema_dst / "site.schema.json").write_text(schema_src.read_text())
