"""Tests for host-URL resolution — default-but-overridable config.

Host URLs are non-secret config with a built-in default that may be overridden
via an environment variable (a literal *or* an ``op://`` reference resolved
through the mcp-common credential chain).  These tests assert, for each URL:

* the built-in default is returned when the env var is unset (the common case),
* a literal env value overrides the default, and
* an ``op://`` value is passed through the resolver (and resolves when ``op``
  can read it; falls back to the default when it cannot).

Like ``test_secrets.py`` these never need a real 1Password session: the autouse
``_isolate_credential_chain`` fixture (tests/conftest.py) neutralizes keyctl
caching and stubs ``op`` resolution; individual tests re-patch
``_read_op_reference`` to simulate a resolved ``op://`` ref.
"""

from unittest.mock import patch

import pytest

from dc_support_mcp.constants import (
    GRAFANA_AM_PROXY_BASE,
    IREN_BASE_URL,
    IREN_FRESHDESK_URL,
    NETBOX_URL,
    ORI_BASE_URL,
    RTB_BASE_URL,
    grafana_am_proxy_base,
    iren_base_url,
    iren_freshdesk_url,
    netbox_url,
    rtb_base_url,
)
from dc_support_mcp.secrets import host_url


@pytest.mark.unit
class TestHostUrlHelper:
    @patch.dict("os.environ", {}, clear=True)
    def test_returns_default_when_unset(self):
        assert host_url("RTB_BASE_URL", "https://default.example") == "https://default.example"

    @patch.dict("os.environ", {"RTB_BASE_URL": "   "})
    def test_returns_default_when_blank(self):
        assert host_url("RTB_BASE_URL", "https://default.example") == "https://default.example"

    @patch.dict("os.environ", {"RTB_BASE_URL": "https://override.example"})
    def test_literal_env_overrides_default(self):
        assert host_url("RTB_BASE_URL", "https://default.example") == "https://override.example"

    @patch.dict("os.environ", {"RTB_BASE_URL": "op://Vault/RTB/url"})
    def test_op_reference_is_resolved(self):
        from mcp_common import credential_chain as cc

        with patch.object(cc, "_read_op_reference", return_value="https://from-1p.example"):
            assert host_url("RTB_BASE_URL", "https://default.example") == "https://from-1p.example"

    @patch.dict("os.environ", {"RTB_BASE_URL": "op://Vault/RTB/url"})
    def test_unresolvable_op_reference_falls_back_to_default(self):
        # Default fixture stub makes op resolution return None → default wins.
        assert host_url("RTB_BASE_URL", "https://default.example") == "https://default.example"


@pytest.mark.unit
class TestUrlResolvers:
    """Each resolver returns its default when unset and honors an env override."""

    @patch.dict("os.environ", {}, clear=True)
    def test_defaults_when_unset(self):
        assert rtb_base_url() == RTB_BASE_URL == "https://rtb.together.ai"
        assert netbox_url() == NETBOX_URL == "https://i.together.ai"
        assert grafana_am_proxy_base() == GRAFANA_AM_PROXY_BASE
        assert iren_base_url() == IREN_BASE_URL == "https://support.iren.com"
        assert iren_freshdesk_url() == IREN_FRESHDESK_URL == "https://iren.freshdesk.com"

    @patch.dict("os.environ", {"RTB_BASE_URL": "https://rtb.staging.test"})
    def test_rtb_env_override(self):
        assert rtb_base_url() == "https://rtb.staging.test"

    @patch.dict("os.environ", {"NETBOX_URL": "https://netbox.staging.test"})
    def test_netbox_env_override(self):
        assert netbox_url() == "https://netbox.staging.test"

    @patch.dict("os.environ", {"GRAFANA_AM_PROXY_BASE": "https://grafana.together.xyz/api/am"})
    def test_grafana_env_override(self):
        assert grafana_am_proxy_base() == "https://grafana.together.xyz/api/am"

    @patch.dict("os.environ", {"IREN_BASE_URL": "https://iren.staging.test"})
    def test_iren_base_env_override(self):
        assert iren_base_url() == "https://iren.staging.test"

    @patch.dict("os.environ", {"IREN_FRESHDESK_URL": "https://iren.fd.staging.test"})
    def test_iren_freshdesk_env_override(self):
        assert iren_freshdesk_url() == "https://iren.fd.staging.test"

    @patch.dict("os.environ", {"NETBOX_URL": "op://Vault/NetBox/url"})
    def test_op_reference_passed_to_resolver(self):
        from mcp_common import credential_chain as cc

        with patch.object(cc, "_read_op_reference", return_value="https://netbox.from-1p.test"):
            assert netbox_url() == "https://netbox.from-1p.test"


@pytest.mark.unit
class TestAtlassianHandlerBaseUrl:
    """The Atlassian vendor handlers expose the resolved base URL as ``BASE_URL``."""

    def test_default_when_unset(self, ori_handler):
        with patch.dict("os.environ", {}, clear=True):
            assert ori_handler.BASE_URL == ORI_BASE_URL

    def test_literal_env_override(self, ori_handler, monkeypatch):
        monkeypatch.setenv("ORI_BASE_URL", "https://ori.staging.test")
        assert ori_handler.BASE_URL == "https://ori.staging.test"

    def test_op_reference_resolved(self, ori_handler, monkeypatch):
        from mcp_common import credential_chain as cc

        monkeypatch.setenv("ORI_BASE_URL", "op://Vault/Ori/url")
        with patch.object(cc, "_read_op_reference", return_value="https://ori.from-1p.test"):
            assert ori_handler.BASE_URL == "https://ori.from-1p.test"
