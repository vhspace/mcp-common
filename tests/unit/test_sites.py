"""Tests for the generic multi-site SiteManager pattern."""

import os
from unittest.mock import patch

import pytest

from mcp_common.sites import SiteConfig, SiteManager, _auto_aliases, _normalize_key


class WekaSiteConfig(SiteConfig):
    url: str
    username: str = "admin"
    password: str = "changeme"
    org: str | None = None


class WekaSiteManager(SiteManager[WekaSiteConfig]):
    env_prefix = "WEKA"


class TestNormalizeKey:
    def test_basic(self) -> None:
        assert _normalize_key("PROD_US_EAST") == "prod_us_east"

    def test_strips_special_chars(self) -> None:
        assert _normalize_key("  My--Site  ") == "my_site"

    def test_empty(self) -> None:
        assert _normalize_key("") == ""


class TestAutoAliases:
    def test_single_word(self) -> None:
        aliases = _auto_aliases("prod")
        assert "prod" in aliases

    def test_two_parts(self) -> None:
        aliases = _auto_aliases("us_east")
        assert "us_east" in aliases
        assert "east" in aliases

    def test_three_parts(self) -> None:
        aliases = _auto_aliases("us_east_1")
        assert "us_east_1" in aliases
        assert "1" in aliases
        assert "east_1" in aliases


class TestSiteManagerDiscovery:
    def test_discovers_sites_from_env(self) -> None:
        env = {
            "WEKA_PROD_URL": "https://weka-prod.example.com",
            "WEKA_PROD_USERNAME": "weka-admin",
            "WEKA_PROD_PASSWORD": "secret123",
            "WEKA_STAGING_URL": "https://weka-staging.example.com",
        }
        with patch.dict(os.environ, env, clear=True):
            mgr = WekaSiteManager(WekaSiteConfig)
            mgr.discover()

        sites = mgr.list_sites()
        assert "prod" in sites
        assert "staging" in sites
        assert sites["prod"].url == "https://weka-prod.example.com"
        assert sites["prod"].username == "weka-admin"
        assert sites["prod"].password == "secret123"
        assert sites["staging"].username == "admin"

    def test_default_site_from_env(self) -> None:
        env = {
            "WEKA_PROD_URL": "https://weka-prod.example.com",
            "WEKA_STAGING_URL": "https://weka-staging.example.com",
            "WEKA_DEFAULT_SITE": "staging",
        }
        with patch.dict(os.environ, env, clear=True):
            mgr = WekaSiteManager(WekaSiteConfig)
            mgr.discover()

        assert mgr.default_site == "staging"
        cfg = mgr.get_site()
        assert cfg.site == "staging"

    def test_default_site_first_discovered(self) -> None:
        env = {
            "WEKA_ALPHA_URL": "https://alpha.example.com",
        }
        with patch.dict(os.environ, env, clear=True):
            mgr = WekaSiteManager(WekaSiteConfig)
            mgr.discover()

        assert mgr.default_site == "alpha"

    def test_alias_json(self) -> None:
        env = {
            "WEKA_PROD_US_URL": "https://us.example.com",
            "WEKA_SITE_ALIASES_JSON": '{"production": "prod_us"}',
        }
        with patch.dict(os.environ, env, clear=True):
            mgr = WekaSiteManager(WekaSiteConfig)
            mgr.discover()

        cfg = mgr.get_site("production")
        assert cfg.url == "https://us.example.com"

    def test_invalid_alias_json_ignored(self) -> None:
        env = {
            "WEKA_PROD_URL": "https://prod.example.com",
            "WEKA_SITE_ALIASES_JSON": "not-json!!!",
        }
        with patch.dict(os.environ, env, clear=True):
            mgr = WekaSiteManager(WekaSiteConfig)
            mgr.discover()

        assert "prod" in mgr.list_sites()

    def test_defaults_dict(self) -> None:
        env = {
            "WEKA_PROD_URL": "https://prod.example.com",
        }
        with patch.dict(os.environ, env, clear=True):
            mgr = WekaSiteManager(WekaSiteConfig)
            mgr.discover(defaults={"org": "default-org"})

        cfg = mgr.get_site("prod")
        assert cfg.org == "default-org"

    def test_no_sites_raises_on_get(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            mgr = WekaSiteManager(WekaSiteConfig)
            mgr.discover()

        with pytest.raises(KeyError, match="No sites configured"):
            mgr.get_site()

    def test_unknown_site_raises(self) -> None:
        env = {
            "WEKA_PROD_URL": "https://prod.example.com",
        }
        with patch.dict(os.environ, env, clear=True):
            mgr = WekaSiteManager(WekaSiteConfig)
            mgr.discover()

        with pytest.raises(KeyError, match="Unknown site"):
            mgr.get_site("nonexistent")


class TestSiteManagerResolve:
    def test_resolve_canonical(self) -> None:
        env = {"WEKA_PROD_URL": "https://prod.example.com"}
        with patch.dict(os.environ, env, clear=True):
            mgr = WekaSiteManager(WekaSiteConfig)
            mgr.discover()

        assert mgr.resolve("prod") == "prod"

    def test_resolve_alias(self) -> None:
        env = {
            "WEKA_PROD_US_URL": "https://us.example.com",
        }
        with patch.dict(os.environ, env, clear=True):
            mgr = WekaSiteManager(WekaSiteConfig)
            mgr.discover()

        assert mgr.resolve("us") == "prod_us"

    def test_resolve_none_returns_default(self) -> None:
        env = {
            "WEKA_PROD_URL": "https://prod.example.com",
            "WEKA_DEFAULT_SITE": "prod",
        }
        with patch.dict(os.environ, env, clear=True):
            mgr = WekaSiteManager(WekaSiteConfig)
            mgr.discover()

        assert mgr.resolve(None) == "prod"
        assert mgr.resolve("") == "prod"


class TestAutoAliasCollision:
    def test_conflicting_auto_alias_keeps_first(self) -> None:
        """When two sites produce the same auto-alias, the first registration wins."""
        env = {
            "WEKA_US_EAST_URL": "https://us-east.example.com",
            "WEKA_EU_EAST_URL": "https://eu-east.example.com",
        }
        with patch.dict(os.environ, env, clear=True):
            mgr = WekaSiteManager(WekaSiteConfig)
            mgr.discover()

        assert "us_east" in mgr.list_sites()
        assert "eu_east" in mgr.list_sites()
        resolved = mgr.resolve("east")
        assert resolved in ("us_east", "eu_east")
        first_resolved = resolved
        mgr2 = WekaSiteManager(WekaSiteConfig)
        with patch.dict(os.environ, env, clear=True):
            mgr2.discover()
        assert mgr2.resolve("east") == first_resolved


class TestSiteManagerManualRegistration:
    def test_register_site(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            mgr = WekaSiteManager(WekaSiteConfig)
            mgr.discover()

        cfg = WekaSiteConfig(
            site="manual",
            url="https://manual.example.com",
            username="u",
            password="p",
        )
        mgr.register_site(cfg)
        assert "manual" in mgr.list_sites()
        assert mgr.get_site("manual").url == "https://manual.example.com"


class TestSiteManagerProperties:
    def test_sites_returns_copy(self) -> None:
        env = {"WEKA_PROD_URL": "https://prod.example.com"}
        with patch.dict(os.environ, env, clear=True):
            mgr = WekaSiteManager(WekaSiteConfig)
            mgr.discover()

        s1 = mgr.sites
        s2 = mgr.sites
        assert s1 is not s2
        assert s1 == s2

    def test_aliases_returns_copy(self) -> None:
        env = {"WEKA_PROD_URL": "https://prod.example.com"}
        with patch.dict(os.environ, env, clear=True):
            mgr = WekaSiteManager(WekaSiteConfig)
            mgr.discover()

        a1 = mgr.aliases
        a2 = mgr.aliases
        assert a1 is not a2
