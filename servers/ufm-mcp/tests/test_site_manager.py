from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastmcp.exceptions import ToolError
from mcp_common.service_discovery import ServiceEndpoint

from ufm_mcp.config import Settings
from ufm_mcp.site_manager import SiteManager


def test_site_manager_configure_default_site() -> None:
    sm = SiteManager()
    settings = Settings(ufm_url="https://ufm.example.com/", verify_ssl=False, timeout_seconds=10)
    sm.configure(settings)

    assert sm.active_key == "default"
    assert "default" in sm.sites
    client = sm.get_client()
    assert client is not None


def test_site_manager_resolve_unknown_raises() -> None:
    sm = SiteManager()
    settings = Settings(ufm_url="https://ufm.example.com/", verify_ssl=False, timeout_seconds=10)
    sm.configure(settings)

    with pytest.raises(ToolError, match="Unknown site"):
        sm.resolve("nonexistent")


def test_site_manager_set_active() -> None:
    sm = SiteManager()
    settings = Settings(ufm_url="https://ufm.example.com/", verify_ssl=False, timeout_seconds=10)
    sm.configure(settings)

    cfg = sm.set_active("default")
    assert cfg.site == "default"
    assert sm.active_key == "default"


def test_site_manager_resolve_none_returns_active() -> None:
    sm = SiteManager()
    settings = Settings(ufm_url="https://ufm.example.com/", verify_ssl=False, timeout_seconds=10)
    sm.configure(settings)

    assert sm.resolve(None) == "default"
    assert sm.resolve("") == "default"


def test_site_manager_get_config() -> None:
    sm = SiteManager()
    settings = Settings(ufm_url="https://ufm.example.com/", verify_ssl=False, timeout_seconds=10)
    sm.configure(settings)

    cfg = sm.get_config()
    assert cfg.ufm_url == "https://ufm.example.com/"
    assert cfg.verify_ssl is False


def test_site_manager_list_sites() -> None:
    sm = SiteManager()
    settings = Settings(ufm_url="https://ufm.example.com/", verify_ssl=False, timeout_seconds=10)
    sm.configure(settings)

    sites_list = sm.list_sites()
    assert len(sites_list) >= 1
    assert any(s["active"] for s in sites_list)


def test_site_manager_close_all() -> None:
    sm = SiteManager()
    settings = Settings(ufm_url="https://ufm.example.com/", verify_ssl=False, timeout_seconds=10)
    sm.configure(settings)
    sm.close_all()


def test_site_manager_env_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UFM_TESTSITE_URL", "https://test-ufm.example.com/")
    monkeypatch.setenv("UFM_TESTSITE_TOKEN", "test-token")

    sm = SiteManager()
    settings = Settings(ufm_url="https://ufm.example.com/", verify_ssl=False, timeout_seconds=10)
    sm.configure(settings)

    cfg = sm.get_config("testsite")
    assert cfg.ufm_url == "https://test-ufm.example.com/"
    assert cfg.ufm_token == "test-token"
    sm.close_all()


def test_site_manager_alias_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UFM_SITE_ALIASES_JSON", '{"myalias": "default"}')

    sm = SiteManager()
    settings = Settings(ufm_url="https://ufm.example.com/", verify_ssl=False, timeout_seconds=10)
    sm.configure(settings)

    assert sm.resolve("myalias") == "default"
    sm.close_all()


def test_site_manager_invalid_alias_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UFM_SITE_ALIASES_JSON", "not-valid-json")

    sm = SiteManager()
    settings = Settings(ufm_url="https://ufm.example.com/", verify_ssl=False, timeout_seconds=10)
    sm.configure(settings)

    assert sm.active_key == "default"
    sm.close_all()


def test_site_manager_empty_alias_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UFM_SITE_ALIASES_JSON", "")

    sm = SiteManager()
    settings = Settings(ufm_url="https://ufm.example.com/", verify_ssl=False, timeout_seconds=10)
    sm.configure(settings)

    assert sm.active_key == "default"
    sm.close_all()


def test_site_config_path_normalization() -> None:
    from ufm_mcp.site_manager import UfmSiteConfig

    cfg = UfmSiteConfig(
        site="test",
        url="https://ufm.example.com",
        token=None,
        verify_ssl=False,
        timeout_seconds=10,
        ufm_api_base_path="/ufmRestV3/",
        ufm_resources_base_path="/ufmRestV3/",
        ufm_logs_base_path="/ufmRestV3/",
        ufm_web_base_path="/ufm_web/",
        ufm_backup_base_path="/ufmRestV3/",
        ufm_jobs_base_path="/ufmRestV3/",
    )
    assert cfg.ufm_api_base_path == "/ufmRestV3"
    assert cfg.ufm_resources_base_path == "/ufmRestV3"
    assert cfg.ufm_web_base_path == "/ufm_web"


def test_site_manager_effective_summary() -> None:
    sm = SiteManager()
    settings = Settings(ufm_url="https://ufm.example.com/", verify_ssl=False, timeout_seconds=10)
    sm.configure(settings)

    summary = sm.get_effective_summary()
    assert summary["active_site"] == "default"
    assert "default" in summary["sites"]
    assert summary["ufm_token"] in (None, "***REDACTED***")
    assert summary["verify_ssl"] is False
    sm.close_all()


def test_site_manager_resolve_no_active_raises() -> None:
    sm = SiteManager()
    with pytest.raises(ToolError, match="No active UFM site"):
        sm.resolve(None)


# ---- NetBox service discovery tests ----


def _mock_discovery(endpoints_by_slug: dict[str, list[ServiceEndpoint]]) -> MagicMock:
    """Create a mock NetBoxServiceDiscovery that returns canned endpoints."""
    mock = MagicMock()
    mock.get_sites_with_service.return_value = sorted(endpoints_by_slug.keys())
    mock.get_services.side_effect = lambda slug, svc: endpoints_by_slug.get(slug, [])
    return mock


@patch("mcp_common.service_discovery.NetBoxServiceDiscovery")
def test_netbox_discovery_registers_new_site(
    mock_cls: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A site in NetBox but not in env vars gets registered."""
    monkeypatch.setenv("UFM_ORI_TOKEN", "tok-ori-secret")

    ep = ServiceEndpoint(
        url="https://192.168.230.201",
        auth_type="token",
        token_env="UFM_ORI_TOKEN",
        verify_ssl=False,
        timeout_seconds=30,
        extra={"site_key": "ori", "topaz_az_id": "us-south-2a"},
    )
    mock_cls.return_value = _mock_discovery({"ori_tx": [ep]})

    sm = SiteManager()
    settings = Settings(
        ufm_url="https://default.example.com/", verify_ssl=False, timeout_seconds=10
    )
    sm.configure(settings)

    cfg = sm.get_config("ori")
    assert cfg.url == "https://192.168.230.201"
    assert cfg.token == "tok-ori-secret"
    assert cfg.verify_ssl is False
    assert sm.topaz_az_overrides == {"ori": "us-south-2a"}
    sm.close_all()


@patch("mcp_common.service_discovery.NetBoxServiceDiscovery")
def test_netbox_discovery_env_var_wins(
    mock_cls: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Env-var-discovered site is not overwritten by NetBox."""
    monkeypatch.setenv("UFM_ORI_URL", "https://env-ori.example.com/")
    monkeypatch.setenv("UFM_ORI_TOKEN", "env-token")

    ep = ServiceEndpoint(
        url="https://192.168.230.201",
        auth_type="token",
        token_env="UFM_ORI_TOKEN",
        extra={"site_key": "ori", "topaz_az_id": "us-south-2a"},
    )
    mock_cls.return_value = _mock_discovery({"ori_tx": [ep]})

    sm = SiteManager()
    settings = Settings(
        ufm_url="https://default.example.com/", verify_ssl=False, timeout_seconds=10
    )
    sm.configure(settings)

    cfg = sm.get_config("ori")
    assert cfg.url == "https://env-ori.example.com/"
    # Topaz AZ still harvested even though env var won
    assert sm.topaz_az_overrides == {"ori": "us-south-2a"}
    sm.close_all()


@patch("mcp_common.service_discovery.NetBoxServiceDiscovery")
def test_netbox_discovery_no_token_env(
    mock_cls: MagicMock,
) -> None:
    """Site registered with token=None when token_env is unset."""
    ep = ServiceEndpoint(
        url="https://192.168.230.201",
        auth_type="token",
        token_env="UFM_MISSING_TOKEN",
        extra={"site_key": "ori"},
    )
    mock_cls.return_value = _mock_discovery({"ori_tx": [ep]})

    sm = SiteManager()
    settings = Settings(
        ufm_url="https://default.example.com/", verify_ssl=False, timeout_seconds=10
    )
    sm.configure(settings)

    cfg = sm.get_config("ori")
    assert cfg.url == "https://192.168.230.201"
    assert cfg.token is None
    sm.close_all()


@patch("mcp_common.service_discovery.NetBoxServiceDiscovery")
def test_netbox_discovery_graceful_failure(
    mock_cls: MagicMock,
) -> None:
    """NetBox unreachable doesn't break initialization."""
    mock_cls.return_value.get_sites_with_service.side_effect = Exception("connection refused")

    sm = SiteManager()
    settings = Settings(
        ufm_url="https://default.example.com/", verify_ssl=False, timeout_seconds=10
    )
    sm.configure(settings)

    assert sm.active_key == "default"
    assert sm.topaz_az_overrides == {}
    sm.close_all()


@patch("mcp_common.service_discovery.NetBoxServiceDiscovery")
def test_netbox_discovery_uses_slug_when_no_site_key(
    mock_cls: MagicMock,
) -> None:
    """When extra.site_key is absent, the NetBox slug is used as site key."""
    ep = ServiceEndpoint(
        url="https://10.0.0.1",
        auth_type="none",
        extra={},
    )
    mock_cls.return_value = _mock_discovery({"new_site": [ep]})

    sm = SiteManager()
    settings = Settings(
        ufm_url="https://default.example.com/", verify_ssl=False, timeout_seconds=10
    )
    sm.configure(settings)

    cfg = sm.get_config("new_site")
    assert cfg.url == "https://10.0.0.1"
    sm.close_all()


@patch("mcp_common.service_discovery.NetBoxServiceDiscovery")
def test_netbox_discovery_topaz_az_not_set(
    mock_cls: MagicMock,
) -> None:
    """No topaz_az_id in extra means no override entry."""
    ep = ServiceEndpoint(
        url="https://10.0.0.1",
        auth_type="none",
        extra={"site_key": "mysite"},
    )
    mock_cls.return_value = _mock_discovery({"mysite": [ep]})

    sm = SiteManager()
    settings = Settings(
        ufm_url="https://default.example.com/", verify_ssl=False, timeout_seconds=10
    )
    sm.configure(settings)

    assert sm.topaz_az_overrides == {}
    sm.close_all()
