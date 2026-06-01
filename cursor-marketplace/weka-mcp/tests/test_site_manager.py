"""Tests for multi-site Weka client management."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from fastmcp.exceptions import ToolError

from weka_mcp.site_manager import WekaSiteManager, _site_key


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


class TestDefaultSiteConfig:
    @patch.dict("os.environ", {"WEKA_DEFAULT_SITE": "ori"}, clear=False)
    @patch("weka_mcp.site_manager.WekaRestClient")
    def test_default_site_from_env(self, mock_client_cls, base_settings):
        mgr = WekaSiteManager()
        mgr.configure(base_settings)
        assert mgr.active_key == "ori"
        assert "ori" in mgr.sites

    @patch.dict("os.environ", {}, clear=True)
    @patch("weka_mcp.site_manager.WekaRestClient")
    def test_default_site_fallback(self, mock_client_cls, base_settings):
        mgr = WekaSiteManager()
        mgr.configure(base_settings)
        assert mgr.active_key == "default"
        assert "default" in mgr.sites

    @patch.dict("os.environ", {}, clear=True)
    @patch("weka_mcp.site_manager.WekaRestClient")
    def test_default_site_config_values(self, mock_client_cls, base_settings):
        mgr = WekaSiteManager()
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
        mgr = WekaSiteManager()
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
        mgr = WekaSiteManager()
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
        mgr = WekaSiteManager()
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
        mgr = WekaSiteManager()
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
        mgr = WekaSiteManager()
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
        mgr = WekaSiteManager()
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
        mgr = WekaSiteManager()
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
        mgr = WekaSiteManager()
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
        mgr = WekaSiteManager()
        mgr.configure(base_settings)
        assert mgr.active_key == "default"
        cfg = mgr.set_active("ori")
        assert cfg.site == "ori"
        assert mgr.active_key == "ori"

    @patch.dict("os.environ", {}, clear=True)
    @patch("weka_mcp.site_manager.WekaRestClient")
    def test_resolve_none_returns_active(self, mock_client_cls, base_settings):
        mgr = WekaSiteManager()
        mgr.configure(base_settings)
        assert mgr.resolve(None) == mgr.active_key

    @patch.dict("os.environ", {}, clear=True)
    @patch("weka_mcp.site_manager.WekaRestClient")
    def test_resolve_empty_returns_active(self, mock_client_cls, base_settings):
        mgr = WekaSiteManager()
        mgr.configure(base_settings)
        assert mgr.resolve("") == mgr.active_key


class TestUnknownSite:
    @patch.dict("os.environ", {}, clear=True)
    @patch("weka_mcp.site_manager.WekaRestClient")
    def test_unknown_site_raises(self, mock_client_cls, base_settings):
        mgr = WekaSiteManager()
        mgr.configure(base_settings)
        with pytest.raises(ToolError, match="Unknown site"):
            mgr.resolve("nonexistent")

    @patch.dict("os.environ", {}, clear=True)
    @patch("weka_mcp.site_manager.WekaRestClient")
    def test_get_client_unknown_raises(self, mock_client_cls, base_settings):
        mgr = WekaSiteManager()
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
        mgr = WekaSiteManager()
        mgr.configure(base_settings)
        assert "ori" not in mgr._clients
        mgr.get_client("ori")
        assert "ori" in mgr._clients

    @patch.dict("os.environ", {}, clear=True)
    @patch("weka_mcp.site_manager.WekaRestClient")
    def test_client_reused(self, mock_client_cls, base_settings):
        mgr = WekaSiteManager()
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
        mgr = WekaSiteManager()
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
        mgr = WekaSiteManager()
        mgr.configure(base_settings)
        mgr.get_client()
        mgr.close_all()
        for c in mgr._clients.values():
            c.close.assert_called_once()


class TestOptionalBaseCredentials:
    """Tests for issue #27: site-only config without base WEKA_HOST/WEKA_PASSWORD."""

    @pytest.fixture()
    def no_base_settings(self):
        """Settings mock with weka_host=None, weka_password=None."""
        s = MagicMock()
        s.weka_host = None
        s.weka_username = "admin"
        s.weka_password = None
        s.weka_org = None
        s.verify_ssl = True
        s.timeout_seconds = 30.0
        s.api_base_path = "/api/v2"
        return s

    @patch.dict(
        "os.environ",
        {
            "WEKA_ORI_URL": "https://192.168.231.211:14000",
            "WEKA_ORI_ADMIN": "together",
            "WEKA_ORI_ADMIN_PASSWORD": "oripass",
        },
        clear=True,
    )
    @patch("weka_mcp.site_manager.WekaRestClient")
    def test_site_only_config(self, mock_client_cls, no_base_settings):
        """When no base credentials, site-specific env vars should still work."""
        mgr = WekaSiteManager()
        mgr.configure(no_base_settings)
        assert "ori" in mgr.sites
        assert mgr.active_key == "ori"
        assert "default" not in mgr.sites
        cfg = mgr.get_config("ori")
        assert cfg.weka_host == "https://192.168.231.211:14000"
        assert cfg.username == "together"
        assert cfg.password == "oripass"

    @patch.dict(
        "os.environ",
        {
            "WEKA_ORI_URL": "https://ori:14000",
            "WEKA_ORI_ADMIN_PASSWORD": "oripass",
            "WEKA_5C_OH1_URL": "https://oh1:14000",
            "WEKA_5C_OH1_ADMIN_PASSWORD": "oh1pass",
        },
        clear=True,
    )
    @patch("weka_mcp.site_manager.WekaRestClient")
    def test_multiple_sites_without_base(self, mock_client_cls, no_base_settings):
        """Multiple site-specific env vars work without base credentials."""
        mgr = WekaSiteManager()
        mgr.configure(no_base_settings)
        assert "ori" in mgr.sites
        assert "5c_oh1" in mgr.sites
        assert mgr.active_key is not None

    @patch.dict("os.environ", {}, clear=True)
    @patch("weka_mcp.site_manager.WekaRestClient")
    def test_no_sites_at_all_raises(self, mock_client_cls, no_base_settings):
        """Clear error when no base credentials AND no site env vars."""
        mgr = WekaSiteManager()
        with pytest.raises(ToolError, match="No Weka sites configured"):
            mgr.configure(no_base_settings)

    @patch.dict(
        "os.environ",
        {
            "WEKA_ORI_URL": "https://ori:14000",
        },
        clear=True,
    )
    @patch("weka_mcp.site_manager.WekaRestClient")
    def test_site_without_password_skipped(self, mock_client_cls, no_base_settings):
        """A site URL without any password source should be skipped."""
        mgr = WekaSiteManager()
        with pytest.raises(ToolError, match="No Weka sites configured"):
            mgr.configure(no_base_settings)


_SENTINEL = object()


def _mock_endpoint(
    url="https://192.168.231.211:14000",
    username_env="WEKA_ORI_ADMIN",
    password_env="WEKA_ORI_ADMIN_PASSWORD",
    verify_ssl=False,
    timeout_seconds=30,
    api_base_path="/api/v2",
    extra=_SENTINEL,
):
    """Create a mock ServiceEndpoint."""
    ep = MagicMock()
    ep.url = url
    ep.username_env = username_env
    ep.password_env = password_env
    ep.verify_ssl = verify_ssl
    ep.timeout_seconds = timeout_seconds
    ep.api_base_path = api_base_path
    ep.extra = {"org": "root"} if extra is _SENTINEL else extra
    return ep


class TestNetBoxDiscovery:
    """Tests for NetBox service discovery integration."""

    @pytest.fixture()
    def no_base_settings(self):
        s = MagicMock()
        s.weka_host = None
        s.weka_username = "admin"
        s.weka_password = None
        s.weka_org = None
        s.verify_ssl = True
        s.timeout_seconds = 30.0
        s.api_base_path = "/api/v2"
        return s

    @patch.dict(
        "os.environ",
        {
            "WEKA_ORI_ADMIN": "together",
            "WEKA_ORI_ADMIN_PASSWORD": "oripass",
        },
        clear=True,
    )
    @patch("weka_mcp.site_manager.WekaRestClient")
    @patch("mcp_common.service_discovery.NetBoxServiceDiscovery")
    def test_discovers_site_from_netbox(self, mock_nb_cls, mock_client_cls, no_base_settings):
        """Sites are backfilled from NetBox when not configured via env vars."""
        mock_nb = mock_nb_cls.return_value
        mock_nb.get_sites_with_service.return_value = ["ori_tx"]
        mock_nb.get_services.return_value = [_mock_endpoint()]

        mgr = WekaSiteManager()
        mgr.configure(no_base_settings)
        assert "ori_tx" in mgr.sites
        cfg = mgr.get_config("ori_tx")
        assert cfg.weka_host == "https://192.168.231.211:14000"
        assert cfg.username == "together"
        assert cfg.password == "oripass"
        assert cfg.org == "root"
        assert cfg.verify_ssl is False
        assert cfg.api_base_path == "/api/v2"

    @patch.dict(
        "os.environ",
        {
            "WEKA_ORI_URL": "https://ori-env:14000",
            "WEKA_ORI_ADMIN": "admin",
            "WEKA_ORI_ADMIN_PASSWORD": "envpass",
            "WEKA_ORI_ADMIN2": "netbox_user",
            "WEKA_ORI_ADMIN2_PASSWORD": "netbox_pass",
        },
        clear=True,
    )
    @patch("weka_mcp.site_manager.WekaRestClient")
    @patch("mcp_common.service_discovery.NetBoxServiceDiscovery")
    def test_env_vars_take_priority(self, mock_nb_cls, mock_client_cls, no_base_settings):
        """Env-var discovered sites are NOT overwritten by NetBox."""
        mock_nb = mock_nb_cls.return_value
        mock_nb.get_sites_with_service.return_value = ["ori"]
        mock_nb.get_services.return_value = [_mock_endpoint(url="https://ori-netbox:14000")]

        mgr = WekaSiteManager()
        mgr.configure(no_base_settings)
        cfg = mgr.get_config("ori")
        assert cfg.weka_host == "https://ori-env:14000"
        assert cfg.password == "envpass"

    @patch.dict("os.environ", {}, clear=True)
    @patch("weka_mcp.site_manager.WekaRestClient")
    @patch("mcp_common.service_discovery.NetBoxServiceDiscovery")
    def test_netbox_site_without_password_skipped(
        self, mock_nb_cls, mock_client_cls, no_base_settings
    ):
        """NetBox sites with missing password env vars are skipped."""
        mock_nb = mock_nb_cls.return_value
        mock_nb.get_sites_with_service.return_value = ["ori_tx"]
        mock_nb.get_services.return_value = [_mock_endpoint()]

        mgr = WekaSiteManager()
        with pytest.raises(ToolError, match="No Weka sites configured"):
            mgr.configure(no_base_settings)

    @patch.dict("os.environ", {}, clear=True)
    @patch("weka_mcp.site_manager.WekaRestClient")
    @patch("mcp_common.service_discovery.NetBoxServiceDiscovery")
    def test_netbox_failure_is_non_fatal(self, mock_nb_cls, mock_client_cls, base_settings):
        """NetBox init failure is logged and non-fatal if other sites exist."""
        mock_nb_cls.side_effect = RuntimeError("NetBox unreachable")

        mgr = WekaSiteManager()
        mgr.configure(base_settings)
        assert "default" in mgr.sites

    @patch.dict(
        "os.environ",
        {
            "WEKA_ORI_ADMIN": "together",
            "WEKA_ORI_ADMIN_PASSWORD": "oripass",
        },
        clear=True,
    )
    @patch("weka_mcp.site_manager.WekaRestClient")
    @patch("mcp_common.service_discovery.NetBoxServiceDiscovery")
    def test_extra_site_key_override(self, mock_nb_cls, mock_client_cls, no_base_settings):
        """extra.site_key overrides the NetBox slug for the site key."""
        mock_nb = mock_nb_cls.return_value
        mock_nb.get_sites_with_service.return_value = ["ori_tx"]
        mock_nb.get_services.return_value = [
            _mock_endpoint(extra={"site_key": "ori", "org": "root"})
        ]

        mgr = WekaSiteManager()
        mgr.configure(no_base_settings)
        assert "ori" in mgr.sites
        assert "ori_tx" not in mgr.sites

    @patch.dict(
        "os.environ",
        {
            "WEKA_ORI_ADMIN": "together",
            "WEKA_ORI_ADMIN_PASSWORD": "oripass",
        },
        clear=True,
    )
    @patch("weka_mcp.site_manager.WekaRestClient")
    @patch("mcp_common.service_discovery.NetBoxServiceDiscovery")
    def test_org_from_extra(self, mock_nb_cls, mock_client_cls, no_base_settings):
        """Org is read from the ServiceEndpoint extra dict."""
        mock_nb = mock_nb_cls.return_value
        mock_nb.get_sites_with_service.return_value = ["ori_tx"]
        mock_nb.get_services.return_value = [_mock_endpoint(extra={"org": "production"})]

        mgr = WekaSiteManager()
        mgr.configure(no_base_settings)
        cfg = mgr.get_config("ori_tx")
        assert cfg.org == "production"

    @patch.dict(
        "os.environ",
        {
            "WEKA_ORI_ADMIN": "together",
            "WEKA_ORI_ADMIN_PASSWORD": "oripass",
        },
        clear=True,
    )
    @patch("weka_mcp.site_manager.WekaRestClient")
    @patch("mcp_common.service_discovery.NetBoxServiceDiscovery")
    def test_org_falls_back_to_base(self, mock_nb_cls, mock_client_cls, no_base_settings):
        """When extra has no org, falls back to base settings org."""
        no_base_settings.weka_org = "fallback_org"
        mock_nb = mock_nb_cls.return_value
        mock_nb.get_sites_with_service.return_value = ["ori_tx"]
        mock_nb.get_services.return_value = [_mock_endpoint(extra={})]

        mgr = WekaSiteManager()
        mgr.configure(no_base_settings)
        cfg = mgr.get_config("ori_tx")
        assert cfg.org == "fallback_org"

    @patch.dict(
        "os.environ",
        {
            "WEKA_ORI_ADMIN": "together",
            "WEKA_ORI_ADMIN_PASSWORD": "oripass",
            "WEKA_5C_ADMIN": "user5c",
            "WEKA_5C_ADMIN_PASSWORD": "pass5c",
        },
        clear=True,
    )
    @patch("weka_mcp.site_manager.WekaRestClient")
    @patch("mcp_common.service_discovery.NetBoxServiceDiscovery")
    def test_discovers_multiple_netbox_sites(self, mock_nb_cls, mock_client_cls, no_base_settings):
        """Multiple sites from NetBox are registered."""
        ep1 = _mock_endpoint()
        ep2 = _mock_endpoint(
            url="https://10.0.0.1:14000",
            username_env="WEKA_5C_ADMIN",
            password_env="WEKA_5C_ADMIN_PASSWORD",
            extra={"org": "test"},
        )
        mock_nb = mock_nb_cls.return_value
        mock_nb.get_sites_with_service.return_value = ["ori_tx", "5c_oh1"]
        mock_nb.get_services.side_effect = lambda slug, svc: [ep1] if slug == "ori_tx" else [ep2]

        mgr = WekaSiteManager()
        mgr.configure(no_base_settings)
        assert "ori_tx" in mgr.sites
        assert "5c_oh1" in mgr.sites
