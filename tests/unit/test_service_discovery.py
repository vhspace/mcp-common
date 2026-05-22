"""Tests for NetBox service discovery and endpoint models."""

import json
import os
import time
import urllib.error
from unittest.mock import MagicMock, patch

from mcp_common.service_discovery import (
    AuthType,
    NetBoxServiceDiscovery,
    ServiceEndpoint,
    SiteServices,
)
from mcp_common.sites import SiteConfig, SiteManager

# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------


class TestServiceEndpoint:
    def test_defaults(self) -> None:
        ep = ServiceEndpoint(url="https://ufm.example.com")
        assert ep.name == "default"
        assert ep.auth_type == AuthType.NONE
        assert ep.verify_ssl is True
        assert ep.timeout_seconds == 30
        assert ep.extra == {}
        assert ep.token_env is None

    def test_full_construction(self) -> None:
        ep = ServiceEndpoint(
            name="primary",
            url="https://ufm.example.com",
            api_base_path="/ufmRest",
            auth_type=AuthType.PASSWORD,
            username_env="UFM_USER",
            password_env="UFM_PASS",
            verify_ssl=False,
            timeout_seconds=60,
            extra={"version": "6.x"},
        )
        assert ep.name == "primary"
        assert ep.auth_type == AuthType.PASSWORD
        assert ep.username_env == "UFM_USER"
        assert ep.extra["version"] == "6.x"

    def test_auth_type_from_string(self) -> None:
        ep = ServiceEndpoint(url="https://x.com", auth_type="token")
        assert ep.auth_type == AuthType.TOKEN


class TestSiteServices:
    def test_empty_defaults(self) -> None:
        ss = SiteServices()
        assert ss.ufm == []
        assert ss.weka == []
        assert ss.maas == []
        assert ss.vast == []
        assert ss.topaz is None

    def test_parse_from_dict(self) -> None:
        data = {
            "ufm": [
                {"url": "https://ufm1.example.com", "auth_type": "password"},
                {"url": "https://ufm2.example.com"},
            ],
            "weka": [{"url": "https://weka.example.com", "token_env": "WEKA_TOKEN"}],
        }
        ss = SiteServices.model_validate(data)
        assert len(ss.ufm) == 2
        assert ss.ufm[0].auth_type == AuthType.PASSWORD
        assert ss.ufm[1].auth_type == AuthType.NONE
        assert len(ss.weka) == 1
        assert ss.weka[0].token_env == "WEKA_TOKEN"

    def test_topaz_dict(self) -> None:
        data = {"topaz": {"cluster": "topaz-01", "port": 8443}}
        ss = SiteServices.model_validate(data)
        assert ss.topaz == {"cluster": "topaz-01", "port": 8443}


# ---------------------------------------------------------------------------
# NetBoxServiceDiscovery tests
# ---------------------------------------------------------------------------

MOCK_NETBOX_RESPONSE = {
    "count": 2,
    "results": [
        {
            "name": "site:ori-tx",
            "data": {
                "site_services": {
                    "ufm": [
                        {
                            "url": "https://ufm.ori-tx.example.com",
                            "auth_type": "password",
                            "username_env": "UFM_ORI_USER",
                            "password_env": "UFM_ORI_PASS",
                        }
                    ],
                    "weka": [
                        {
                            "url": "https://weka.ori-tx.example.com",
                            "token_env": "WEKA_ORI_TOKEN",
                        }
                    ],
                }
            },
        },
        {
            "name": "site:5c-oh1",
            "data": {
                "site_services": {
                    "ufm": [
                        {
                            "url": "https://ufm.5c-oh1.example.com",
                            "auth_type": "token",
                            "token_env": "UFM_5C_TOKEN",
                        }
                    ],
                }
            },
        },
    ],
}


def _make_urlopen_response(data: dict) -> MagicMock:
    """Create a mock that behaves like urllib.request.urlopen context manager."""
    body = json.dumps(data).encode()
    mock_resp = MagicMock()
    mock_resp.read.return_value = body
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


class TestNetBoxServiceDiscovery:
    def test_get_services(self) -> None:
        discovery = NetBoxServiceDiscovery(
            netbox_url="https://netbox.example.com",
            netbox_token="test-token",
        )
        with patch("mcp_common.service_discovery.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_response(MOCK_NETBOX_RESPONSE)
            endpoints = discovery.get_services("ori-tx", "ufm")

        assert len(endpoints) == 1
        assert endpoints[0].url == "https://ufm.ori-tx.example.com"
        assert endpoints[0].auth_type == AuthType.PASSWORD

    def test_get_services_unknown_site(self) -> None:
        discovery = NetBoxServiceDiscovery(
            netbox_url="https://netbox.example.com",
            netbox_token="test-token",
        )
        with patch("mcp_common.service_discovery.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_response(MOCK_NETBOX_RESPONSE)
            endpoints = discovery.get_services("nonexistent", "ufm")

        assert endpoints == []

    def test_get_services_unknown_service_type(self) -> None:
        discovery = NetBoxServiceDiscovery(
            netbox_url="https://netbox.example.com",
            netbox_token="test-token",
        )
        with patch("mcp_common.service_discovery.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_response(MOCK_NETBOX_RESPONSE)
            endpoints = discovery.get_services("ori-tx", "vast")

        assert endpoints == []

    def test_get_sites_with_service(self) -> None:
        discovery = NetBoxServiceDiscovery(
            netbox_url="https://netbox.example.com",
            netbox_token="test-token",
        )
        with patch("mcp_common.service_discovery.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_response(MOCK_NETBOX_RESPONSE)
            sites = discovery.get_sites_with_service("ufm")

        assert sites == ["5c_oh1", "ori_tx"]

    def test_get_sites_with_service_weka(self) -> None:
        discovery = NetBoxServiceDiscovery(
            netbox_url="https://netbox.example.com",
            netbox_token="test-token",
        )
        with patch("mcp_common.service_discovery.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_response(MOCK_NETBOX_RESPONSE)
            sites = discovery.get_sites_with_service("weka")

        assert sites == ["ori_tx"]

    def test_cache_ttl(self) -> None:
        """Second call within TTL uses cached data without hitting NetBox."""
        discovery = NetBoxServiceDiscovery(
            netbox_url="https://netbox.example.com",
            netbox_token="test-token",
            cache_ttl=60,
        )
        with patch("mcp_common.service_discovery.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_response(MOCK_NETBOX_RESPONSE)
            discovery.get_services("ori-tx", "ufm")
            assert mock_urlopen.call_count == 1

            mock_urlopen.return_value = _make_urlopen_response(MOCK_NETBOX_RESPONSE)
            discovery.get_services("ori-tx", "ufm")
            assert mock_urlopen.call_count == 1  # still 1, cache hit

    def test_cache_expiry(self) -> None:
        """After TTL expires, NetBox is queried again."""
        discovery = NetBoxServiceDiscovery(
            netbox_url="https://netbox.example.com",
            netbox_token="test-token",
            cache_ttl=0.1,
        )
        with patch("mcp_common.service_discovery.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_response(MOCK_NETBOX_RESPONSE)
            discovery.get_services("ori-tx", "ufm")
            assert mock_urlopen.call_count == 1

            time.sleep(0.15)

            mock_urlopen.return_value = _make_urlopen_response(MOCK_NETBOX_RESPONSE)
            discovery.get_services("ori-tx", "ufm")
            assert mock_urlopen.call_count == 2

    def test_invalidate_cache(self) -> None:
        discovery = NetBoxServiceDiscovery(
            netbox_url="https://netbox.example.com",
            netbox_token="test-token",
            cache_ttl=300,
        )
        with patch("mcp_common.service_discovery.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_response(MOCK_NETBOX_RESPONSE)
            discovery.get_services("ori-tx", "ufm")
            assert mock_urlopen.call_count == 1

            discovery.invalidate_cache()

            mock_urlopen.return_value = _make_urlopen_response(MOCK_NETBOX_RESPONSE)
            discovery.get_services("ori-tx", "ufm")
            assert mock_urlopen.call_count == 2

    def test_netbox_unreachable(self) -> None:
        """When NetBox is unreachable, returns empty results without crashing."""
        discovery = NetBoxServiceDiscovery(
            netbox_url="https://netbox.example.com",
            netbox_token="test-token",
        )
        with patch("mcp_common.service_discovery.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = urllib.error.URLError("Connection refused")
            endpoints = discovery.get_services("ori-tx", "ufm")

        assert endpoints == []

    def test_netbox_timeout(self) -> None:
        discovery = NetBoxServiceDiscovery(
            netbox_url="https://netbox.example.com",
            netbox_token="test-token",
        )
        with patch("mcp_common.service_discovery.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = TimeoutError("timed out")
            endpoints = discovery.get_services("ori-tx", "ufm")

        assert endpoints == []

    def test_missing_url_and_token(self) -> None:
        """Missing config gracefully returns empty."""
        with patch.dict(os.environ, {}, clear=True):
            discovery = NetBoxServiceDiscovery()
            endpoints = discovery.get_services("ori-tx", "ufm")

        assert endpoints == []

    def test_invalid_site_services_data(self) -> None:
        """Malformed site_services are skipped."""
        response = {
            "results": [
                {
                    "name": "site:bad",
                    "data": {"site_services": "not a dict"},
                },
                {
                    "name": "site:good",
                    "data": {
                        "site_services": {
                            "ufm": [{"url": "https://ufm.good.example.com"}]
                        }
                    },
                },
            ]
        }
        discovery = NetBoxServiceDiscovery(
            netbox_url="https://netbox.example.com",
            netbox_token="test-token",
        )
        with patch("mcp_common.service_discovery.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_response(response)
            sites = discovery.get_sites_with_service("ufm")

        assert sites == ["good"]

    def test_config_context_without_site_services_key(self) -> None:
        response = {
            "results": [
                {
                    "name": "site:noservices",
                    "data": {"some_other_key": "value"},
                }
            ]
        }
        discovery = NetBoxServiceDiscovery(
            netbox_url="https://netbox.example.com",
            netbox_token="test-token",
        )
        with patch("mcp_common.service_discovery.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_response(response)
            sites = discovery.get_sites_with_service("ufm")

        assert sites == []


# ---------------------------------------------------------------------------
# SiteManager + NetBox integration tests
# ---------------------------------------------------------------------------


class UfmSiteConfig(SiteConfig):
    url: str
    username: str = "admin"
    password: str = "changeme"


class UfmSiteManager(SiteManager[UfmSiteConfig]):
    env_prefix = "UFM"
    service_type = "ufm"


class NoServiceTypeSiteManager(SiteManager[UfmSiteConfig]):
    env_prefix = "UFM"


class TestSiteManagerConfigureFromNetbox:
    def test_netbox_sites_registered(self) -> None:
        """Sites from NetBox are registered when no env vars exist."""
        discovery_mock = MagicMock()
        discovery_mock.get_sites_with_service.return_value = ["ori_tx"]
        discovery_mock.get_services.return_value = [
            ServiceEndpoint(
                url="https://ufm.ori-tx.example.com",
                auth_type=AuthType.PASSWORD,
                username_env="UFM_ORI_USER",
                password_env="UFM_ORI_PASS",
            )
        ]

        env = {
            "UFM_ORI_USER": "admin",
            "UFM_ORI_PASS": "secret",
        }
        with (
            patch.dict(os.environ, env, clear=True),
            patch(
                "mcp_common.service_discovery.NetBoxServiceDiscovery",
                return_value=discovery_mock,
            ),
        ):
            mgr = UfmSiteManager(UfmSiteConfig)
            mgr.configure_from_netbox()

        assert "ori_tx" in mgr.list_sites()
        cfg = mgr.get_site("ori_tx")
        assert cfg.url == "https://ufm.ori-tx.example.com"
        assert cfg.username == "admin"
        assert cfg.password == "secret"

    def test_env_vars_override_netbox(self) -> None:
        """Env-var discovered sites take priority over NetBox-discovered ones."""
        discovery_mock = MagicMock()
        discovery_mock.get_sites_with_service.return_value = ["prod"]
        discovery_mock.get_services.return_value = [
            ServiceEndpoint(
                url="https://ufm-netbox.example.com",
                auth_type=AuthType.PASSWORD,
                username_env="UFM_NB_USER",
                password_env="UFM_NB_PASS",
            )
        ]

        env = {
            "UFM_PROD_URL": "https://ufm-env.example.com",
            "UFM_PROD_USERNAME": "env-admin",
            "UFM_PROD_PASSWORD": "env-secret",
        }
        with (
            patch.dict(os.environ, env, clear=True),
            patch(
                "mcp_common.service_discovery.NetBoxServiceDiscovery",
                return_value=discovery_mock,
            ),
        ):
            mgr = UfmSiteManager(UfmSiteConfig)
            mgr.configure_from_netbox()

        cfg = mgr.get_site("prod")
        assert cfg.url == "https://ufm-env.example.com"
        assert cfg.username == "env-admin"

    def test_netbox_unreachable_falls_back_to_env(self) -> None:
        """If NetBox returns nothing, env-var discovery still works."""
        discovery_mock = MagicMock()
        discovery_mock.get_sites_with_service.return_value = []

        env = {
            "UFM_PROD_URL": "https://ufm-env.example.com",
        }
        with (
            patch.dict(os.environ, env, clear=True),
            patch(
                "mcp_common.service_discovery.NetBoxServiceDiscovery",
                return_value=discovery_mock,
            ),
        ):
            mgr = UfmSiteManager(UfmSiteConfig)
            mgr.configure_from_netbox()

        assert "prod" in mgr.list_sites()
        assert mgr.get_site("prod").url == "https://ufm-env.example.com"

    def test_no_service_type_skips_netbox(self) -> None:
        """Manager without service_type skips NetBox and only does env discovery."""
        env = {
            "UFM_PROD_URL": "https://ufm-env.example.com",
        }
        with patch.dict(os.environ, env, clear=True):
            mgr = NoServiceTypeSiteManager(UfmSiteConfig)
            mgr.configure_from_netbox()

        assert "prod" in mgr.list_sites()

    def test_defaults_forwarded(self) -> None:
        """Defaults dict is applied to both NetBox and env-var discovered sites."""
        discovery_mock = MagicMock()
        discovery_mock.get_sites_with_service.return_value = []

        env = {
            "UFM_ALPHA_URL": "https://alpha.example.com",
        }
        with (
            patch.dict(os.environ, env, clear=True),
            patch(
                "mcp_common.service_discovery.NetBoxServiceDiscovery",
                return_value=discovery_mock,
            ),
        ):
            mgr = UfmSiteManager(UfmSiteConfig)
            mgr.configure_from_netbox(defaults={"username": "custom-user"})

        cfg = mgr.get_site("alpha")
        assert cfg.username == "custom-user"

    def test_netbox_and_env_coexist(self) -> None:
        """Different sites from NetBox and env vars are both registered."""
        discovery_mock = MagicMock()
        discovery_mock.get_sites_with_service.return_value = ["site_a"]
        discovery_mock.get_services.return_value = [
            ServiceEndpoint(url="https://ufm-a.example.com")
        ]

        env = {
            "UFM_SITE_B_URL": "https://ufm-b.example.com",
        }
        with (
            patch.dict(os.environ, env, clear=True),
            patch(
                "mcp_common.service_discovery.NetBoxServiceDiscovery",
                return_value=discovery_mock,
            ),
        ):
            mgr = UfmSiteManager(UfmSiteConfig)
            mgr.configure_from_netbox()

        sites = mgr.list_sites()
        assert "site_a" in sites
        assert "site_b" in sites
        assert sites["site_a"].url == "https://ufm-a.example.com"
        assert sites["site_b"].url == "https://ufm-b.example.com"
