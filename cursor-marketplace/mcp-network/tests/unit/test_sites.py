"""Tests for ``NetworkSiteManager`` — registration, aliases, resolution."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mcp_network.inventory import default_inventory_dir
from mcp_network.sites import NetworkSiteManager


@pytest.fixture
def two_site_inventory(tmp_path: Path) -> Path:
    """Build a tmp inventory dir containing two sites and a schema."""
    schema_src = default_inventory_dir() / "schema" / "site.schema.json"
    (tmp_path / "schema").mkdir()
    (tmp_path / "schema" / "site.schema.json").write_text(schema_src.read_text())

    sites = tmp_path / "sites"
    sites.mkdir()

    ori = json.loads((default_inventory_dir() / "sites" / "ori.json").read_text())
    (sites / "ori.json").write_text(json.dumps(ori))

    second = json.loads(json.dumps(ori))
    second["site"] = "fake"
    second["display_name"] = "Fake DC"
    second["aliases"] = ["fake-dc"]
    second["default"] = False
    second["netbox_site_slug"] = None
    second["switches"] = [
        {
            "name": "fake-inb-sw-lea-01",
            "mgmt_ip": "10.1.1.1",
            "role": "leaf",
        }
    ]
    second["credentials_env"] = {
        "user": "FAKE_NETWORK_USER",
        "password": "FAKE_NETWORK_PASSWORD",
    }
    (sites / "fake.json").write_text(json.dumps(second))
    return tmp_path


def test_loads_sites_and_picks_json_default(
    two_site_inventory: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("NETWORK_DEFAULT_SITE", raising=False)
    m = NetworkSiteManager()
    m.load(two_site_inventory)
    assert set(m.sites.keys()) == {"ori", "fake"}
    assert m.default_site == "ori"
    # inventory-file aliases present
    assert m.aliases.get("ori_tx") == "ori"
    assert m.aliases.get("dfw01") == "ori"
    assert m.aliases.get("fake_dc") == "fake"


def test_env_var_overrides_json_default(
    two_site_inventory: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NETWORK_DEFAULT_SITE", "fake")
    m = NetworkSiteManager()
    m.load(two_site_inventory)
    assert m.default_site == "fake"


def test_missing_creds_marks_non_operational(
    two_site_inventory: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("FAKE_NETWORK_USER", raising=False)
    monkeypatch.delenv("FAKE_NETWORK_PASSWORD", raising=False)
    m = NetworkSiteManager()
    m.load(two_site_inventory)
    fake = m.get_site("fake")
    assert fake.operational is False
    assert fake.reason is not None
    assert "FAKE_NETWORK_USER" in fake.reason


def test_resolve_switch_by_name(two_site_inventory: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NETWORK_DEFAULT_SITE", raising=False)
    m = NetworkSiteManager()
    m.load(two_site_inventory)
    cfg, sw = m.resolve_switch("dfw01-inb-sw-lea-03")
    assert cfg.site == "ori"
    assert sw.mgmt_ip == "192.168.199.205"


def test_resolve_switch_by_ip(two_site_inventory: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NETWORK_DEFAULT_SITE", raising=False)
    m = NetworkSiteManager()
    m.load(two_site_inventory)
    cfg, sw = m.resolve_switch("10.1.1.1")
    assert cfg.site == "fake"
    assert sw.name == "fake-inb-sw-lea-01"


def test_resolve_switch_not_found(two_site_inventory: Path) -> None:
    m = NetworkSiteManager()
    m.load(two_site_inventory)
    with pytest.raises(KeyError):
        m.resolve_switch("no-such-switch")


def test_resolve_switch_respects_site_hint(two_site_inventory: Path) -> None:
    m = NetworkSiteManager()
    m.load(two_site_inventory)
    # site hint narrows lookup; wrong site -> not found even if exists elsewhere
    with pytest.raises(KeyError):
        m.resolve_switch("dfw01-inb-sw-lea-03", site="fake")
