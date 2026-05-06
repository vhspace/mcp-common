"""Tests for multi-site Weka client management."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from fastmcp.exceptions import ToolError

from weka_mcp.site_manager import SiteManager, _aliases_for_site, _site_key


@pytest.fixture()
def base_settings():
    """Create a minimal mock Settings object."""
    s = MagicMock()
    s.weka_host = "https://weka.example.com:14000"
    s.weka_username = "admin"
    s.weka_password = MagicMock()
    s.weka_password.get_secret_value.return_value = "secret123"
    s.weka_org = "root"
    s.verify_ssl = False
    s.timeout_seconds = 30.0
    s.api_base_path = "/api/v2"
    return s


class TestSiteKey:
    def test_lowercases(self):
        assert _site_key("ORI") == "ori"

    def test_replaces_dashes(self):
        assert _site_key("5C-OH1") == "5c_oh1"

    def test_strips_whitespace(self):
        assert _site_key("  ori  ") == "ori"

    def test_empty(self):
        assert _site_key("") == ""


class TestAliasesForSite:
    def test_single_part(self):
        aliases = _aliases_for_site("ori")
        assert "ori" in aliases

    def test_multi_part(self):
        aliases = _aliases_for_site("5c_oh1")
        assert "5c_oh1" in aliases
        assert "oh1" in aliases

    def test_three_parts(self):
        aliases = _aliases_for_site("us_east_1")
        assert "us_east_1" in aliases
        assert "1" in aliases
        assert "east_1" in aliases


class TestDefaultSiteConfig:
    @patch.dict("os.environ", {"WEKA_DEFAULT_SITE": "ori"}, clear=False)
    @patch("weka_mcp.site_manager.WekaRestClient")
    def test_default_site_from_env(self, mock_client_cls, base_settings):
        mgr = SiteManager()
        mgr.configure(base_settings)
        assert mgr.active_key == "ori"
        assert "ori" in mgr.sites

    @patch.dict("os.environ", {}, clear=True)
    @patch("weka_mcp.site_manager.WekaRestClient")
    def test_default_site_fallback(self, mock_client_cls, base_settings):
        mgr = SiteManager()
        mgr.configure(base_settings)
        assert mgr.active_key == "default"
        assert "default" in mgr.sites

    @patch.dict("os.environ", {}, clear=True)
    @patch("weka_mcp.site_manager.WekaRestClient")
    def test_default_site_config_values(self, mock_client_cls, base_settings):
        mgr = SiteManager()
        mgr.configure(base_settings)
        cfg = mgr.get_config("default")
        assert cfg.weka_host == "https://weka.example.com:14000"
        assert cfg.username == "admin"
        assert cfg.password == "secret123"
        assert cfg.org == "root"
        assert cfg.verify_ssl is False

    @patch.dict("os.environ", {}, clear=True)
    @patch("weka_mcp.site_manager.WekaRestClient")
    def test_default_alias_registered(self, mock_client_cls, base_settings):
        mgr = SiteManager()
        mgr.configure(base_settings)
        assert mgr.resolve("default") == "default"


class TestMultiSiteDiscovery:
    @patch.dict(
        "os.environ",
        {
            "WEKA_ORI_URL": "https://192.168.231.211:14000",
            "WEKA_ORI_ADMIN": "together",
            "WEKA_ORI_ADMIN_PASSWORD": "oripass",
            "WEKA_ORI_ORG": "root",
        },
        clear=True,
    )
    @patch("weka_mcp.site_manager.WekaRestClient")
    def test_discovers_site_from_env(self, mock_client_cls, base_settings):
        mgr = SiteManager()
        mgr.configure(base_settings)
        assert "ori" in mgr.sites
        cfg = mgr.get_config("ori")
        assert cfg.weka_host == "https://192.168.231.211:14000"
        assert cfg.username == "together"
        assert cfg.password == "oripass"
        assert cfg.org == "root"

    @patch.dict(
        "os.environ",
        {
            "WEKA_ORI_URL": "https://weka-ori:14000/ui",
            "WEKA_ORI_ADMIN": "admin",
            "WEKA_ORI_ADMIN_PASSWORD": "pass",
        },
        clear=True,
    )
    @patch("weka_mcp.site_manager.WekaRestClient")
    def test_strips_ui_suffix(self, mock_client_cls, base_settings):
        mgr = SiteManager()
        mgr.configure(base_settings)
        cfg = mgr.get_config("ori")
        assert cfg.weka_host == "https://weka-ori:14000"

    @patch.dict(
        "os.environ",
        {
            "WEKA_5C_OH1_URL": "https://weka-oh1:14000",
            "WEKA_5C_OH1_USERNAME": "userx",
            "WEKA_5C_OH1_PASSWORD": "passx",
        },
        clear=True,
    )
    @patch("weka_mcp.site_manager.WekaRestClient")
    def test_username_password_aliases(self, mock_client_cls, base_settings):
        mgr = SiteManager()
        mgr.configure(base_settings)
        cfg = mgr.get_config("5c_oh1")
        assert cfg.username == "userx"
        assert cfg.password == "passx"

    @patch.dict(
        "os.environ",
        {
            "WEKA_ORI_URL": "https://ori:14000",
            "WEKA_ORI_ADMIN": "admin",
            "WEKA_ORI_ADMIN_PASSWORD": "pass",
            "WEKA_5C_OH1_URL": "https://oh1:14000",
            "WEKA_5C_OH1_ADMIN": "admin2",
            "WEKA_5C_OH1_ADMIN_PASSWORD": "pass2",
        },
        clear=True,
    )
    @patch("weka_mcp.site_manager.WekaRestClient")
    def test_discovers_multiple_sites(self, mock_client_cls, base_settings):
        mgr = SiteManager()
        mgr.configure(base_settings)
        assert "ori" in mgr.sites
        assert "5c_oh1" in mgr.sites


class TestAliasResolution:
    @patch.dict(
        "os.environ",
        {
            "WEKA_SITE_ALIASES_JSON": json.dumps({"production": "ori"}),
            "WEKA_ORI_URL": "https://ori:14000",
            "WEKA_ORI_ADMIN": "admin",
            "WEKA_ORI_ADMIN_PASSWORD": "pass",
        },
        clear=True,
    )
    @patch("weka_mcp.site_manager.WekaRestClient")
    def test_alias_from_json(self, mock_client_cls, base_settings):
        mgr = SiteManager()
        mgr.configure(base_settings)
        assert mgr.resolve("production") == "ori"

    @patch.dict(
        "os.environ",
        {
            "WEKA_SITE_ALIASES_JSON": "not-valid-json",
        },
        clear=True,
    )
    @patch("weka_mcp.site_manager.WekaRestClient")
    def test_invalid_alias_json_ignored(self, mock_client_cls, base_settings):
        mgr = SiteManager()
        mgr.configure(base_settings)

    @patch.dict(
        "os.environ",
        {
            "WEKA_5C_OH1_URL": "https://oh1:14000",
            "WEKA_5C_OH1_ADMIN": "admin",
            "WEKA_5C_OH1_ADMIN_PASSWORD": "pass",
        },
        clear=True,
    )
    @patch("weka_mcp.site_manager.WekaRestClient")
    def test_auto_aliases(self, mock_client_cls, base_settings):
        mgr = SiteManager()
        mgr.configure(base_settings)
        assert mgr.resolve("oh1") == "5c_oh1"


class TestSiteSwitching:
    @patch.dict(
        "os.environ",
        {
            "WEKA_DEFAULT_SITE": "default",
            "WEKA_ORI_URL": "https://ori:14000",
            "WEKA_ORI_ADMIN": "admin",
            "WEKA_ORI_ADMIN_PASSWORD": "pass",
        },
        clear=True,
    )
    @patch("weka_mcp.site_manager.WekaRestClient")
    def test_set_active(self, mock_client_cls, base_settings):
        mgr = SiteManager()
        mgr.configure(base_settings)
        assert mgr.active_key == "default"
        cfg = mgr.set_active("ori")
        assert cfg.site == "ori"
        assert mgr.active_key == "ori"

    @patch.dict("os.environ", {}, clear=True)
    @patch("weka_mcp.site_manager.WekaRestClient")
    def test_resolve_none_returns_active(self, mock_client_cls, base_settings):
        mgr = SiteManager()
        mgr.configure(base_settings)
        assert mgr.resolve(None) == mgr.active_key

    @patch.dict("os.environ", {}, clear=True)
    @patch("weka_mcp.site_manager.WekaRestClient")
    def test_resolve_empty_returns_active(self, mock_client_cls, base_settings):
        mgr = SiteManager()
        mgr.configure(base_settings)
        assert mgr.resolve("") == mgr.active_key


class TestUnknownSite:
    @patch.dict("os.environ", {}, clear=True)
    @patch("weka_mcp.site_manager.WekaRestClient")
    def test_unknown_site_raises(self, mock_client_cls, base_settings):
        mgr = SiteManager()
        mgr.configure(base_settings)
        with pytest.raises(ToolError, match="Unknown site"):
            mgr.resolve("nonexistent")

    @patch.dict("os.environ", {}, clear=True)
    @patch("weka_mcp.site_manager.WekaRestClient")
    def test_get_client_unknown_raises(self, mock_client_cls, base_settings):
        mgr = SiteManager()
        mgr.configure(base_settings)
        with pytest.raises(ToolError, match="Unknown site"):
            mgr.get_client("nonexistent")


class TestLazyClientCreation:
    @patch.dict(
        "os.environ",
        {
            "WEKA_ORI_URL": "https://ori:14000",
            "WEKA_ORI_ADMIN": "admin",
            "WEKA_ORI_ADMIN_PASSWORD": "pass",
        },
        clear=True,
    )
    @patch("weka_mcp.site_manager.WekaRestClient")
    def test_client_created_lazily(self, mock_client_cls, base_settings):
        mgr = SiteManager()
        mgr.configure(base_settings)
        assert "ori" not in mgr._clients
        mgr.get_client("ori")
        assert "ori" in mgr._clients

    @patch.dict("os.environ", {}, clear=True)
    @patch("weka_mcp.site_manager.WekaRestClient")
    def test_client_reused(self, mock_client_cls, base_settings):
        mgr = SiteManager()
        mgr.configure(base_settings)
        c1 = mgr.get_client()
        c2 = mgr.get_client()
        assert c1 is c2


class TestListSites:
    @patch.dict(
        "os.environ",
        {
            "WEKA_DEFAULT_SITE": "default",
            "WEKA_ORI_URL": "https://ori:14000",
            "WEKA_ORI_ADMIN": "admin",
            "WEKA_ORI_ADMIN_PASSWORD": "pass",
        },
        clear=True,
    )
    @patch("weka_mcp.site_manager.WekaRestClient")
    def test_list_sites(self, mock_client_cls, base_settings):
        mgr = SiteManager()
        mgr.configure(base_settings)
        site_list = mgr.list_sites()
        names = [s["site"] for s in site_list]
        assert "default" in names
        assert "ori" in names
        active = [s for s in site_list if s["active"]]
        assert len(active) == 1
        assert active[0]["site"] == "default"


class TestCloseAll:
    @patch.dict("os.environ", {}, clear=True)
    @patch("weka_mcp.site_manager.WekaRestClient")
    def test_close_all(self, mock_client_cls, base_settings):
        mgr = SiteManager()
        mgr.configure(base_settings)
        mgr.get_client()
        mgr.close_all()
        for c in mgr._clients.values():
            c.close.assert_called_once()
