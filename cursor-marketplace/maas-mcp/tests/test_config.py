"""Tests for configuration management: Settings, multi-instance, env discovery."""

import os

import pytest

import maas_mcp.config as config_mod
from maas_mcp.config import Settings, _discover_prefixed_instances, _ensure_scheme


def _isolate_env(monkeypatch):
    """Remove all MAAS/NETBOX env vars and patch discovery to use clean env."""
    for key in list(os.environ):
        upper = key.upper()
        if "MAAS" in upper or "NETBOX" in upper:
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(config_mod, "_load_env_with_dotfiles", lambda: dict(os.environ))
    monkeypatch.setattr(config_mod, "_discover_prefixed_instances", lambda env=None: {})


class TestEnsureScheme:
    def test_adds_http(self):
        assert _ensure_scheme("maas.example.com") == "http://maas.example.com"

    def test_preserves_http(self):
        assert _ensure_scheme("http://maas.example.com") == "http://maas.example.com"

    def test_preserves_https(self):
        assert _ensure_scheme("https://maas.example.com") == "https://maas.example.com"

    def test_strips_whitespace(self):
        assert _ensure_scheme("  http://maas.example.com  ") == "http://maas.example.com"


class TestDiscoverPrefixedInstances:
    def test_discovers_site_pair(self):
        env = {
            "MAAS_ORI_URL": "http://maas-ori.example.com",
            "MAAS_ORI_API_KEY": "key:tok:sec",
        }
        result = _discover_prefixed_instances(env)
        assert "ori" in result
        assert result["ori"]["url"] == "http://maas-ori.example.com"

    def test_discovers_api_suffix_variant(self):
        env = {
            "MAAS_CENTRAL_URL": "http://maas-central.example.com",
            "MAAS_CENTRAL_API": "key:tok:sec",
        }
        result = _discover_prefixed_instances(env)
        assert "central" in result

    def test_ignores_url_without_key(self):
        env = {"MAAS_ORPHAN_URL": "http://maas-orphan.example.com"}
        result = _discover_prefixed_instances(env)
        assert "orphan" not in result

    def test_ignores_empty_url(self):
        env = {
            "MAAS_EMPTY_URL": "",
            "MAAS_EMPTY_API_KEY": "key:tok:sec",
        }
        result = _discover_prefixed_instances(env)
        assert "empty" not in result

    def test_site_name_with_leading_digit(self):
        env = {
            "MAAS_5C_OH1_URL": "http://maas-5c-oh1.example.com",
            "MAAS_5C_OH1_API_KEY": "key:tok:sec",
        }
        result = _discover_prefixed_instances(env)
        assert "5c_oh1" in result
        assert result["5c_oh1"]["url"] == "http://maas-5c-oh1.example.com"

    def test_does_not_match_old_format(self):
        env = {
            "ORI_MAAS_URL": "http://maas-ori.example.com",
            "ORI_MAAS_API_KEY": "key:tok:sec",
        }
        result = _discover_prefixed_instances(env)
        assert len(result) == 0


class TestSettingsValidation:
    def test_no_instances_raises(self, monkeypatch):
        _isolate_env(monkeypatch)
        with pytest.raises(ValueError, match="No MAAS instances configured"):
            Settings(_env_file=None)

    def test_single_instance(self, monkeypatch):
        _isolate_env(monkeypatch)
        monkeypatch.setenv("MAAS_URL", "http://maas.example.com/MAAS")
        monkeypatch.setenv("MAAS_API_KEY", "key:tok:sec")
        s = Settings(_env_file=None)
        instances = s.get_maas_instances()
        assert "default" in instances
        assert str(instances["default"].url).rstrip("/").endswith("/MAAS")

    def test_invalid_api_key_format(self, monkeypatch):
        _isolate_env(monkeypatch)
        monkeypatch.setenv("MAAS_URL", "http://maas.example.com")
        monkeypatch.setenv("MAAS_API_KEY", "bad-key")
        with pytest.raises(ValueError, match="consumer_key:consumer_token:secret"):
            Settings(_env_file=None)

    def test_maas_instances_json_string(self, monkeypatch):
        _isolate_env(monkeypatch)
        monkeypatch.setenv(
            "MAAS_INSTANCES",
            '{"prod": {"url": "http://maas-prod.example.com", "api_key": "k:t:s"}}',
        )
        s = Settings(_env_file=None)
        instances = s.get_maas_instances()
        assert "prod" in instances

    def test_site_aliases(self, monkeypatch):
        _isolate_env(monkeypatch)
        monkeypatch.setenv(
            "MAAS_INSTANCES",
            '{"central": {"url": "http://maas.example.com", "api_key": "k:t:s"}}',
        )
        monkeypatch.setenv("MAAS_SITE_ALIASES_JSON", '{"prod": "central"}')
        s = Settings(_env_file=None)
        instances = s.get_maas_instances()
        assert "prod" in instances
        assert str(instances["prod"].url) == str(instances["central"].url)

    def test_default_site(self, monkeypatch):
        _isolate_env(monkeypatch)
        monkeypatch.setenv(
            "MAAS_INSTANCES",
            '{"central": {"url": "http://maas.example.com", "api_key": "k:t:s"}}',
        )
        monkeypatch.setenv("MAAS_DEFAULT_SITE", "central")
        s = Settings(_env_file=None)
        instances = s.get_maas_instances()
        assert "default" in instances

    def test_port_validation(self, monkeypatch):
        _isolate_env(monkeypatch)
        monkeypatch.setenv("MAAS_URL", "http://maas.example.com")
        monkeypatch.setenv("MAAS_API_KEY", "k:t:s")
        monkeypatch.setenv("PORT", "99999")
        with pytest.raises(ValueError, match="Port must be"):
            Settings(_env_file=None)

    def test_timeout_validation(self, monkeypatch):
        _isolate_env(monkeypatch)
        monkeypatch.setenv("MAAS_URL", "http://maas.example.com")
        monkeypatch.setenv("MAAS_API_KEY", "k:t:s")
        monkeypatch.setenv("TIMEOUT_SECONDS", "-1")
        with pytest.raises(ValueError, match="TIMEOUT_SECONDS must be > 0"):
            Settings(_env_file=None)


class TestEffectiveConfigSummary:
    def test_secrets_redacted(self, monkeypatch):
        _isolate_env(monkeypatch)
        monkeypatch.setenv("MAAS_URL", "http://maas.example.com/MAAS")
        monkeypatch.setenv("MAAS_API_KEY", "key:tok:sec")
        monkeypatch.setenv("NETBOX_TOKEN", "super-secret-token")
        s = Settings(_env_file=None)
        summary = s.get_effective_config_summary()
        assert summary["maas_instances"]["default"]["api_key"] == "***REDACTED***"
        assert summary["netbox_token"] == "***REDACTED***"
        assert "super-secret-token" not in str(summary)
        assert "key:tok:sec" not in str(summary)
