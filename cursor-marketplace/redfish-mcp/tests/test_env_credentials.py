"""Tests for credential resolution in agent_controller and cli."""

from unittest.mock import patch

import pytest

from redfish_mcp.agent_controller import (
    _host_site_cache,
    _lookup_site_for_host,
    _resolve_env_credentials,
    _site_slug_to_prefix,
)

# ---------------------------------------------------------------------------
# Env vars to clear so tests don't leak the real shell environment.
# ---------------------------------------------------------------------------
_ALL_KNOWN_VARS = [
    "REDFISH_USER",
    "REDFISH_PASSWORD",
    "REDFISH_SITE",
    "REDFISH_SITE_CREDENTIALS",
    "ORI_REDFISH_USER",
    "ORI_REDFISH_PASSWORD",
    "5C_REDFISH_LOGIN",
    "5C_REDFISH_PASSWORD",
    "IREN2_B200_REDFISH_USER",
    "IREN2_B200_REDFISH_PASSWORD",
    "IREN_B300_REDFISH_USER",
    "IREN_B300_REDFISH_PASSWORD",
]


def _clean_env() -> dict[str, str]:
    """Return a dict that unsets all known REDFISH_ vars."""
    return {k: "" for k in _ALL_KNOWN_VARS}


@pytest.fixture(autouse=True)
def _no_netbox_subprocess(monkeypatch):
    """Prevent real subprocess calls to netbox-cli during unit tests."""
    monkeypatch.setattr(
        "redfish_mcp.agent_controller._lookup_site_for_host",
        lambda host: None,
    )
    _host_site_cache.clear()


class TestSiteSlugToPrefix:
    """Unit tests for _site_slug_to_prefix."""

    @pytest.mark.parametrize(
        "slug,expected",
        [
            ("ori-tx", "ORI"),
            ("5c-oh1-h100", "5C"),
            ("5c-oh1-h200", "5C"),
            ("5c-md1", "5C"),
            ("5c-tn1", "5C_TN1"),
            ("iren-b200-1", "IREN2_B200"),
            ("iren-b200-1016", "IREN2_B200"),
            ("iren-b300-1", "IREN_B300"),
            ("unknown-site", None),
            ("", None),
        ],
    )
    def test_mapping(self, slug, expected):
        assert _site_slug_to_prefix(slug) == expected

    def test_case_insensitive(self):
        assert _site_slug_to_prefix("ORI-TX") == "ORI"

    def test_5c_tn1_before_5c(self):
        """5c-tn1 should match 5C_TN1, not the broader 5C prefix."""
        assert _site_slug_to_prefix("5c-tn1") == "5C_TN1"
        assert _site_slug_to_prefix("5c-oh1-h100") == "5C"


class TestLookupSiteForHost:
    """Unit tests for _lookup_site_for_host with mocked subprocess."""

    def test_successful_lookup(self, monkeypatch):
        import json
        from unittest.mock import MagicMock

        netbox_response = json.dumps(
            {
                "count": 1,
                "results": [
                    {
                        "name": "research-common-h100-097",
                        "site": {"slug": "ori-tx", "name": "ORI-TX"},
                    }
                ],
            }
        )
        mock_run = MagicMock(
            return_value=MagicMock(
                returncode=0,
                stdout=netbox_response,
                stderr="",
            )
        )
        monkeypatch.setattr("redfish_mcp.agent_controller.subprocess.run", mock_run)
        monkeypatch.setattr(
            "redfish_mcp.agent_controller._lookup_site_for_host",
            _lookup_site_for_host.__wrapped__
            if hasattr(_lookup_site_for_host, "__wrapped__")
            else _lookup_site_for_host,
        )
        _host_site_cache.clear()

        result = _lookup_site_for_host("192.168.196.97")
        assert result == "ori-tx"
        mock_run.assert_called_once()

    def test_cached_result_no_subprocess(self, monkeypatch):
        import time
        from unittest.mock import MagicMock

        _host_site_cache["192.168.196.97"] = ("ori-tx", time.time())
        mock_run = MagicMock()
        monkeypatch.setattr("redfish_mcp.agent_controller.subprocess.run", mock_run)
        monkeypatch.setattr(
            "redfish_mcp.agent_controller._lookup_site_for_host",
            _lookup_site_for_host,
        )

        result = _lookup_site_for_host("192.168.196.97")
        assert result == "ori-tx"
        mock_run.assert_not_called()

    def test_netbox_cli_not_found(self, monkeypatch):
        monkeypatch.setattr(
            "redfish_mcp.agent_controller.subprocess.run",
            lambda *a, **kw: (_ for _ in ()).throw(FileNotFoundError("netbox-cli")),
        )
        monkeypatch.setattr(
            "redfish_mcp.agent_controller._lookup_site_for_host",
            _lookup_site_for_host,
        )
        _host_site_cache.clear()
        assert _lookup_site_for_host("192.168.196.97") is None

    def test_netbox_cli_timeout(self, monkeypatch):
        import subprocess as sp

        monkeypatch.setattr(
            "redfish_mcp.agent_controller.subprocess.run",
            lambda *a, **kw: (_ for _ in ()).throw(sp.TimeoutExpired("netbox-cli", 10)),
        )
        monkeypatch.setattr(
            "redfish_mcp.agent_controller._lookup_site_for_host",
            _lookup_site_for_host,
        )
        _host_site_cache.clear()
        assert _lookup_site_for_host("192.168.196.97") is None

    def test_empty_host(self, monkeypatch):
        monkeypatch.setattr(
            "redfish_mcp.agent_controller._lookup_site_for_host",
            _lookup_site_for_host,
        )
        assert _lookup_site_for_host("") is None
        assert _lookup_site_for_host("   ") is None

    def test_no_results(self, monkeypatch):
        import json
        from unittest.mock import MagicMock

        monkeypatch.setattr(
            "redfish_mcp.agent_controller.subprocess.run",
            MagicMock(
                return_value=MagicMock(
                    returncode=0,
                    stdout=json.dumps({"count": 0, "results": []}),
                    stderr="",
                )
            ),
        )
        monkeypatch.setattr(
            "redfish_mcp.agent_controller._lookup_site_for_host",
            _lookup_site_for_host,
        )
        _host_site_cache.clear()
        assert _lookup_site_for_host("10.0.0.1") is None


class TestNetBoxCredentialResolution:
    """Tests for _resolve_env_credentials with NetBox site lookup."""

    def test_netbox_resolves_ori_site(self, monkeypatch):
        """Multiple site creds + NetBox says ORI → picks ORI creds."""
        monkeypatch.setattr(
            "redfish_mcp.agent_controller._lookup_site_for_host",
            lambda host: "ori-tx",
        )
        env = {
            **_clean_env(),
            "ORI_REDFISH_USER": "ori-test-user",
            "ORI_REDFISH_PASSWORD": "ori-test-pass",
            "5C_REDFISH_LOGIN": "5c-test-user",
            "5C_REDFISH_PASSWORD": "5c-test-pass",
        }
        with patch.dict("os.environ", env, clear=False):
            result = _resolve_env_credentials("192.168.196.97")
        assert result == ("ori-test-user", "ori-test-pass")

    def test_netbox_resolves_5c_site(self, monkeypatch):
        """Multiple site creds + NetBox says 5C → picks 5C creds."""
        monkeypatch.setattr(
            "redfish_mcp.agent_controller._lookup_site_for_host",
            lambda host: "5c-oh1-h200",
        )
        env = {
            **_clean_env(),
            "ORI_REDFISH_USER": "ori-test-user",
            "ORI_REDFISH_PASSWORD": "ori-test-pass",
            "5C_REDFISH_LOGIN": "5c-test-user",
            "5C_REDFISH_PASSWORD": "5c-test-pass",
        }
        with patch.dict("os.environ", env, clear=False):
            result = _resolve_env_credentials("10.5.0.1")
        assert result == ("5c-test-user", "5c-test-pass")

    def test_netbox_resolves_iren_b300(self, monkeypatch):
        monkeypatch.setattr(
            "redfish_mcp.agent_controller._lookup_site_for_host",
            lambda host: "iren-b300-1",
        )
        env = {
            **_clean_env(),
            "ORI_REDFISH_USER": "ori-test-user",
            "ORI_REDFISH_PASSWORD": "ori-test-pass",
            "IREN_B300_REDFISH_USER": "iren-test-user",
            "IREN_B300_REDFISH_PASSWORD": "iren-test-pass",
        }
        with patch.dict("os.environ", env, clear=False):
            result = _resolve_env_credentials("10.99.0.1")
        assert result == ("iren-test-user", "iren-test-pass")

    def test_netbox_unknown_slug_falls_to_generic(self, monkeypatch):
        """NetBox returns a site we have no prefix mapping for → fall to generic."""
        monkeypatch.setattr(
            "redfish_mcp.agent_controller._lookup_site_for_host",
            lambda host: "vultr-tx",
        )
        env = {
            **_clean_env(),
            "ORI_REDFISH_USER": "ori-test-user",
            "ORI_REDFISH_PASSWORD": "ori-test-pass",
            "5C_REDFISH_LOGIN": "5c-test-user",
            "5C_REDFISH_PASSWORD": "5c-test-pass",
            "REDFISH_USER": "generic_user",
            "REDFISH_PASSWORD": "generic_pass",
        }
        with patch.dict("os.environ", env, clear=False):
            result = _resolve_env_credentials("10.0.0.1")
        assert result == ("generic_user", "generic_pass")

    def test_netbox_failure_falls_to_generic(self, monkeypatch):
        """NetBox unreachable → fall to generic (same as before NetBox integration)."""
        monkeypatch.setattr(
            "redfish_mcp.agent_controller._lookup_site_for_host",
            lambda host: None,
        )
        env = {
            **_clean_env(),
            "ORI_REDFISH_USER": "ori-test-user",
            "ORI_REDFISH_PASSWORD": "ori-test-pass",
            "5C_REDFISH_LOGIN": "5c-test-user",
            "5C_REDFISH_PASSWORD": "5c-test-pass",
            "REDFISH_USER": "generic_user",
            "REDFISH_PASSWORD": "generic_pass",
        }
        with patch.dict("os.environ", env, clear=False):
            result = _resolve_env_credentials("10.0.0.1")
        assert result == ("generic_user", "generic_pass")

    def test_netbox_failure_no_generic_returns_none(self, monkeypatch):
        """NetBox unreachable + no generic → None (prompt user)."""
        monkeypatch.setattr(
            "redfish_mcp.agent_controller._lookup_site_for_host",
            lambda host: None,
        )
        env = {
            **_clean_env(),
            "ORI_REDFISH_USER": "ori-test-user",
            "ORI_REDFISH_PASSWORD": "ori-test-pass",
            "5C_REDFISH_LOGIN": "5c-test-user",
            "5C_REDFISH_PASSWORD": "5c-test-pass",
        }
        with patch.dict("os.environ", env, clear=False):
            result = _resolve_env_credentials("10.0.0.1")
        assert result is None


class TestResolveEnvCredentials:
    """Unit tests for the _resolve_env_credentials helper."""

    def test_generic_fallback(self):
        env = {**_clean_env(), "REDFISH_USER": "admin", "REDFISH_PASSWORD": "secret"}
        with patch.dict("os.environ", env, clear=False):
            result = _resolve_env_credentials("192.168.196.97")
        assert result == ("admin", "secret")

    def test_single_site_credential(self):
        env = {
            **_clean_env(),
            "ORI_REDFISH_USER": "ori-test-user",
            "ORI_REDFISH_PASSWORD": "ori-test-pass",
        }
        with patch.dict("os.environ", env, clear=False):
            result = _resolve_env_credentials("192.168.196.97")
        assert result == ("ori-test-user", "ori-test-pass")

    def test_no_credentials_returns_none(self):
        with patch.dict("os.environ", _clean_env(), clear=False):
            result = _resolve_env_credentials("192.168.196.97")
        assert result is None

    def test_site_hint_disambiguates(self):
        env = {
            **_clean_env(),
            "ORI_REDFISH_USER": "ori-test-user",
            "ORI_REDFISH_PASSWORD": "ori-test-pass",
            "5C_REDFISH_LOGIN": "5c-test-user",
            "5C_REDFISH_PASSWORD": "5c-test-pass",
            "REDFISH_SITE": "5C",
        }
        with patch.dict("os.environ", env, clear=False):
            result = _resolve_env_credentials("10.0.0.1")
        assert result == ("5c-test-user", "5c-test-pass")

    def test_site_hint_ori(self):
        env = {
            **_clean_env(),
            "ORI_REDFISH_USER": "ori-test-user",
            "ORI_REDFISH_PASSWORD": "ori-test-pass",
            "5C_REDFISH_LOGIN": "5c-test-user",
            "5C_REDFISH_PASSWORD": "5c-test-pass",
            "REDFISH_SITE": "ORI",
        }
        with patch.dict("os.environ", env, clear=False):
            result = _resolve_env_credentials("10.0.0.1")
        assert result == ("ori-test-user", "ori-test-pass")

    def test_multiple_sites_no_hint_returns_none(self):
        env = {
            **_clean_env(),
            "ORI_REDFISH_USER": "ori-test-user",
            "ORI_REDFISH_PASSWORD": "ori-test-pass",
            "5C_REDFISH_LOGIN": "5c-test-user",
            "5C_REDFISH_PASSWORD": "5c-test-pass",
        }
        with patch.dict("os.environ", env, clear=False):
            result = _resolve_env_credentials("10.0.0.1")
        assert result is None

    def test_multiple_sites_with_generic_uses_generic(self):
        env = {
            **_clean_env(),
            "ORI_REDFISH_USER": "ori-test-user",
            "ORI_REDFISH_PASSWORD": "ori-test-pass",
            "5C_REDFISH_LOGIN": "5c-test-user",
            "5C_REDFISH_PASSWORD": "5c-test-pass",
            "REDFISH_USER": "generic_user",
            "REDFISH_PASSWORD": "generic_pass",
        }
        with patch.dict("os.environ", env, clear=False):
            result = _resolve_env_credentials("10.0.0.1")
        assert result == ("generic_user", "generic_pass")

    def test_partial_credentials_ignored(self):
        """User without password should not match."""
        env = {**_clean_env(), "ORI_REDFISH_USER": "ori-test-user", "ORI_REDFISH_PASSWORD": ""}
        with patch.dict("os.environ", env, clear=False):
            result = _resolve_env_credentials("192.168.196.97")
        assert result is None

    def test_whitespace_only_credentials_ignored(self):
        env = {**_clean_env(), "REDFISH_USER": "  ", "REDFISH_PASSWORD": "  "}
        with patch.dict("os.environ", env, clear=False):
            result = _resolve_env_credentials("192.168.196.97")
        assert result is None

    def test_login_variant_5c(self):
        """The 5C site uses LOGIN instead of USER — verify it works."""
        env = {
            **_clean_env(),
            "5C_REDFISH_LOGIN": "5c-test-user",
            "5C_REDFISH_PASSWORD": "5c-test-pass",
        }
        with patch.dict("os.environ", env, clear=False):
            result = _resolve_env_credentials("10.0.0.1")
        assert result == ("5c-test-user", "5c-test-pass")

    def test_dynamic_site_from_env(self):
        """Unknown site patterns discovered dynamically from env."""
        env = {
            **_clean_env(),
            "NEWSITE_REDFISH_USER": "newuser",
            "NEWSITE_REDFISH_PASSWORD": "newpass",
        }
        with patch.dict("os.environ", env, clear=False):
            result = _resolve_env_credentials("10.0.0.1")
        assert result == ("newuser", "newpass")

    def test_empty_host_still_resolves(self):
        """Even with empty host, env creds should still resolve."""
        env = {**_clean_env(), "REDFISH_USER": "admin", "REDFISH_PASSWORD": "secret"}
        with patch.dict("os.environ", env, clear=False):
            result = _resolve_env_credentials("")
        assert result == ("admin", "secret")

    def test_site_hint_case_insensitive(self):
        env = {
            **_clean_env(),
            "ORI_REDFISH_USER": "ori-test-user",
            "ORI_REDFISH_PASSWORD": "ori-test-pass",
            "5C_REDFISH_LOGIN": "5c-test-user",
            "5C_REDFISH_PASSWORD": "5c-test-pass",
            "REDFISH_SITE": "ori",  # lowercase
        }
        with patch.dict("os.environ", env, clear=False):
            result = _resolve_env_credentials("10.0.0.1")
        assert result == ("ori-test-user", "ori-test-pass")


class TestCliCredsUsesResolver:
    """Verify the CLI _creds() function uses the same resolver as the MCP path."""

    def test_cli_creds_resolves_site_credentials(self, monkeypatch):
        """CLI _creds(host) should resolve site-specific env vars."""
        from redfish_mcp import cli

        monkeypatch.setattr(cli, "_cli_user", None)
        monkeypatch.setattr(cli, "_cli_password", None)
        monkeypatch.setattr(
            "redfish_mcp.agent_controller._lookup_site_for_host",
            lambda host: "ori-tx",
        )
        env = {
            **_clean_env(),
            "ORI_REDFISH_USER": "ori-test-user",
            "ORI_REDFISH_PASSWORD": "ori-test-pass",
            "5C_REDFISH_LOGIN": "5c-test-user",
            "5C_REDFISH_PASSWORD": "5c-test-pass",
        }
        with patch.dict("os.environ", env, clear=False):
            user, password = cli._creds("192.168.196.97")
        assert user == "ori-test-user"
        assert password == "ori-test-pass"

    def test_cli_creds_resolves_5c(self, monkeypatch):
        from redfish_mcp import cli

        monkeypatch.setattr(cli, "_cli_user", None)
        monkeypatch.setattr(cli, "_cli_password", None)
        monkeypatch.setattr(
            "redfish_mcp.agent_controller._lookup_site_for_host",
            lambda host: "5c-oh1-h200",
        )
        env = {
            **_clean_env(),
            "ORI_REDFISH_USER": "ori-test-user",
            "ORI_REDFISH_PASSWORD": "ori-test-pass",
            "5C_REDFISH_LOGIN": "5c-test-user",
            "5C_REDFISH_PASSWORD": "5c-test-pass",
        }
        with patch.dict("os.environ", env, clear=False):
            user, password = cli._creds("10.5.0.1")
        assert user == "5c-test-user"
        assert password == "5c-test-pass"

    def test_cli_creds_explicit_flags_override_env(self, monkeypatch):
        """--user/--password flags should take priority over env resolution."""
        from redfish_mcp import cli

        monkeypatch.setattr(cli, "_cli_user", "flag_user")
        monkeypatch.setattr(cli, "_cli_password", "flag_pass")
        env = {
            **_clean_env(),
            "ORI_REDFISH_USER": "ori-test-user",
            "ORI_REDFISH_PASSWORD": "ori-test-pass",
        }
        with patch.dict("os.environ", env, clear=False):
            user, password = cli._creds("192.168.196.97")
        assert user == "flag_user"
        assert password == "flag_pass"

    def test_cli_creds_generic_env_still_works(self, monkeypatch):
        """REDFISH_USER/REDFISH_PASSWORD should still work as before."""
        from redfish_mcp import cli

        monkeypatch.setattr(cli, "_cli_user", None)
        monkeypatch.setattr(cli, "_cli_password", None)
        env = {**_clean_env(), "REDFISH_USER": "admin", "REDFISH_PASSWORD": "secret"}
        with patch.dict("os.environ", env, clear=False):
            user, password = cli._creds("10.0.0.1")
        assert user == "admin"
        assert password == "secret"

    def test_cli_creds_no_env_exits(self, monkeypatch):
        """No credentials anywhere should raise Exit."""
        from click.exceptions import Exit

        from redfish_mcp import cli

        monkeypatch.setattr(cli, "_cli_user", None)
        monkeypatch.setattr(cli, "_cli_password", None)
        with patch.dict("os.environ", _clean_env(), clear=False):
            with pytest.raises((SystemExit, Exit)):
                cli._creds("10.0.0.1")


class TestExplicitEnvOverridesVendor:
    """Verify REDFISH_USER/REDFISH_PASSWORD take priority over vendor auto-detection.

    The fix lives in _resolve_env_credentials so both CLI and MCP server benefit.
    Regression tests for https://github.com/vhspace/redfish-mcp/issues/107
    """

    # -- Tests against _resolve_env_credentials (shared by CLI + MCP server) --

    def test_resolver_explicit_env_beats_vendor_creds(self, monkeypatch):
        """_resolve_env_credentials: REDFISH_USER/PASSWORD wins over site-specific."""
        monkeypatch.setattr(
            "redfish_mcp.agent_controller._lookup_site_for_host",
            lambda host: "ori-tx",
        )
        env = {
            **_clean_env(),
            "REDFISH_USER": "explicit_user",
            "REDFISH_PASSWORD": "explicit_pass",
            "ORI_REDFISH_USER": "ori-auto-user",
            "ORI_REDFISH_PASSWORD": "ori-auto-pass",
        }
        with patch.dict("os.environ", env, clear=False):
            result = _resolve_env_credentials("192.168.196.97")
        assert result == ("explicit_user", "explicit_pass")

    def test_resolver_explicit_env_beats_multiple_vendor_creds(self, monkeypatch):
        """_resolve_env_credentials: REDFISH_USER/PASSWORD wins with multiple vendors."""
        monkeypatch.setattr(
            "redfish_mcp.agent_controller._lookup_site_for_host",
            lambda host: "5c-oh1-h200",
        )
        env = {
            **_clean_env(),
            "REDFISH_USER": "explicit_user",
            "REDFISH_PASSWORD": "explicit_pass",
            "ORI_REDFISH_USER": "ori-auto-user",
            "ORI_REDFISH_PASSWORD": "ori-auto-pass",
            "5C_REDFISH_LOGIN": "5c-auto-user",
            "5C_REDFISH_PASSWORD": "5c-auto-pass",
        }
        with patch.dict("os.environ", env, clear=False):
            result = _resolve_env_credentials("10.5.0.1")
        assert result == ("explicit_user", "explicit_pass")

    def test_resolver_vendor_fallback_when_no_explicit_env(self, monkeypatch):
        """_resolve_env_credentials: without REDFISH_USER/PASSWORD, vendor works."""
        monkeypatch.setattr(
            "redfish_mcp.agent_controller._lookup_site_for_host",
            lambda host: "ori-tx",
        )
        env = {
            **_clean_env(),
            "ORI_REDFISH_USER": "ori-auto-user",
            "ORI_REDFISH_PASSWORD": "ori-auto-pass",
        }
        with patch.dict("os.environ", env, clear=False):
            result = _resolve_env_credentials("192.168.196.97")
        assert result == ("ori-auto-user", "ori-auto-pass")

    def test_resolver_partial_explicit_env_falls_to_vendor(self, monkeypatch):
        """_resolve_env_credentials: partial REDFISH_USER (no password) → vendor."""
        monkeypatch.setattr(
            "redfish_mcp.agent_controller._lookup_site_for_host",
            lambda host: "ori-tx",
        )
        env = {
            **_clean_env(),
            "REDFISH_USER": "explicit_user",
            "REDFISH_PASSWORD": "",
            "ORI_REDFISH_USER": "ori-auto-user",
            "ORI_REDFISH_PASSWORD": "ori-auto-pass",
        }
        with patch.dict("os.environ", env, clear=False):
            result = _resolve_env_credentials("192.168.196.97")
        assert result == ("ori-auto-user", "ori-auto-pass")

    def test_resolver_whitespace_explicit_env_falls_to_vendor(self, monkeypatch):
        """_resolve_env_credentials: whitespace REDFISH_USER/PASSWORD → vendor."""
        monkeypatch.setattr(
            "redfish_mcp.agent_controller._lookup_site_for_host",
            lambda host: "ori-tx",
        )
        env = {
            **_clean_env(),
            "REDFISH_USER": "   ",
            "REDFISH_PASSWORD": "   ",
            "ORI_REDFISH_USER": "ori-auto-user",
            "ORI_REDFISH_PASSWORD": "ori-auto-pass",
        }
        with patch.dict("os.environ", env, clear=False):
            result = _resolve_env_credentials("192.168.196.97")
        assert result == ("ori-auto-user", "ori-auto-pass")

    # -- Tests through CLI _creds() (verify CLI still uses the shared path) --

    def test_explicit_env_beats_vendor_creds(self, monkeypatch):
        """CLI _creds: REDFISH_USER/PASSWORD should win over site-specific."""
        from redfish_mcp import cli

        monkeypatch.setattr(cli, "_cli_user", None)
        monkeypatch.setattr(cli, "_cli_password", None)
        monkeypatch.setattr(
            "redfish_mcp.agent_controller._lookup_site_for_host",
            lambda host: "ori-tx",
        )
        env = {
            **_clean_env(),
            "REDFISH_USER": "explicit_user",
            "REDFISH_PASSWORD": "explicit_pass",
            "ORI_REDFISH_USER": "ori-auto-user",
            "ORI_REDFISH_PASSWORD": "ori-auto-pass",
        }
        with patch.dict("os.environ", env, clear=False):
            user, password = cli._creds("192.168.196.97")
        assert user == "explicit_user"
        assert password == "explicit_pass"

    def test_explicit_env_beats_multiple_vendor_creds(self, monkeypatch):
        """CLI _creds: REDFISH_USER/PASSWORD wins with multiple vendors."""
        from redfish_mcp import cli

        monkeypatch.setattr(cli, "_cli_user", None)
        monkeypatch.setattr(cli, "_cli_password", None)
        monkeypatch.setattr(
            "redfish_mcp.agent_controller._lookup_site_for_host",
            lambda host: "5c-oh1-h200",
        )
        env = {
            **_clean_env(),
            "REDFISH_USER": "explicit_user",
            "REDFISH_PASSWORD": "explicit_pass",
            "ORI_REDFISH_USER": "ori-auto-user",
            "ORI_REDFISH_PASSWORD": "ori-auto-pass",
            "5C_REDFISH_LOGIN": "5c-auto-user",
            "5C_REDFISH_PASSWORD": "5c-auto-pass",
        }
        with patch.dict("os.environ", env, clear=False):
            user, password = cli._creds("10.5.0.1")
        assert user == "explicit_user"
        assert password == "explicit_pass"

    def test_cli_flags_still_beat_explicit_env(self, monkeypatch):
        """--user/--password flags beat REDFISH_USER/PASSWORD env vars."""
        from redfish_mcp import cli

        monkeypatch.setattr(cli, "_cli_user", "flag_user")
        monkeypatch.setattr(cli, "_cli_password", "flag_pass")
        env = {
            **_clean_env(),
            "REDFISH_USER": "explicit_user",
            "REDFISH_PASSWORD": "explicit_pass",
            "ORI_REDFISH_USER": "ori-auto-user",
            "ORI_REDFISH_PASSWORD": "ori-auto-pass",
        }
        with patch.dict("os.environ", env, clear=False):
            user, password = cli._creds("192.168.196.97")
        assert user == "flag_user"
        assert password == "flag_pass"

    def test_vendor_fallback_when_no_explicit_env(self, monkeypatch):
        """CLI _creds: without REDFISH_USER/PASSWORD, vendor auto-detection works."""
        from redfish_mcp import cli

        monkeypatch.setattr(cli, "_cli_user", None)
        monkeypatch.setattr(cli, "_cli_password", None)
        monkeypatch.setattr(
            "redfish_mcp.agent_controller._lookup_site_for_host",
            lambda host: "ori-tx",
        )
        env = {
            **_clean_env(),
            "ORI_REDFISH_USER": "ori-auto-user",
            "ORI_REDFISH_PASSWORD": "ori-auto-pass",
        }
        with patch.dict("os.environ", env, clear=False):
            user, password = cli._creds("192.168.196.97")
        assert user == "ori-auto-user"
        assert password == "ori-auto-pass"

    def test_partial_explicit_env_falls_to_vendor(self, monkeypatch):
        """CLI _creds: if only REDFISH_USER is set (no password), fall back to vendor."""
        from redfish_mcp import cli

        monkeypatch.setattr(cli, "_cli_user", None)
        monkeypatch.setattr(cli, "_cli_password", None)
        monkeypatch.setattr(
            "redfish_mcp.agent_controller._lookup_site_for_host",
            lambda host: "ori-tx",
        )
        env = {
            **_clean_env(),
            "REDFISH_USER": "explicit_user",
            "REDFISH_PASSWORD": "",
            "ORI_REDFISH_USER": "ori-auto-user",
            "ORI_REDFISH_PASSWORD": "ori-auto-pass",
        }
        with patch.dict("os.environ", env, clear=False):
            user, password = cli._creds("192.168.196.97")
        assert user == "ori-auto-user"
        assert password == "ori-auto-pass"

    def test_whitespace_explicit_env_falls_to_vendor(self, monkeypatch):
        """CLI _creds: whitespace-only REDFISH_USER/PASSWORD should not count."""
        from redfish_mcp import cli

        monkeypatch.setattr(cli, "_cli_user", None)
        monkeypatch.setattr(cli, "_cli_password", None)
        monkeypatch.setattr(
            "redfish_mcp.agent_controller._lookup_site_for_host",
            lambda host: "ori-tx",
        )
        env = {
            **_clean_env(),
            "REDFISH_USER": "   ",
            "REDFISH_PASSWORD": "   ",
            "ORI_REDFISH_USER": "ori-auto-user",
            "ORI_REDFISH_PASSWORD": "ori-auto-pass",
        }
        with patch.dict("os.environ", env, clear=False):
            user, password = cli._creds("192.168.196.97")
        assert user == "ori-auto-user"
        assert password == "ori-auto-pass"
