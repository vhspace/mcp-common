from __future__ import annotations

import pytest
from fastmcp.exceptions import ToolError

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
    from ufm_mcp.site_manager import SiteConfig

    cfg = SiteConfig(
        site="test",
        ufm_url="https://ufm.example.com",
        ufm_token=None,
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
