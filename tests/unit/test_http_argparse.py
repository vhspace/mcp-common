"""Tests for the shared server-side argparse helpers (#89)."""

from __future__ import annotations

import pytest
from pydantic_settings import SettingsConfigDict

from mcp_common.config import MCPSettings
from mcp_common.http import ArgSpec, build_arg_parser, settings_from_args


class _SrvSettings(MCPSettings):
    model_config = SettingsConfigDict(env_prefix="TESTSRV_", extra="ignore")

    netbox_url: str = "https://default.example"


def _parser(**kwargs):
    return build_arg_parser(description="test server", settings_cls=_SrvSettings, **kwargs)


class TestBuildArgParser:
    def test_standard_flags_present(self) -> None:
        ns = _parser().parse_args(
            [
                "--transport",
                "http",
                "--host",
                "0.0.0.0",
                "--port",
                "9000",
                "--log-level",
                "debug",
                "--log-format",
                "json",
            ]
        )
        assert ns.transport == "http"
        assert ns.host == "0.0.0.0"
        assert ns.port == 9000  # argparse coerces to int
        assert ns.log_level == "debug"
        assert ns.log_format == "json"

    def test_unset_flags_are_absent_from_namespace(self) -> None:
        # SUPPRESS defaults => only explicitly-passed flags appear.
        ns = _parser().parse_args([])
        assert vars(ns) == {}

    def test_invalid_choice_rejected(self) -> None:
        with pytest.raises(SystemExit):
            _parser().parse_args(["--transport", "carrier-pigeon"])

    def test_epilog_mentions_env_prefix(self) -> None:
        assert "TESTSRV_" in (_parser().epilog or "")

    def test_extra_args_added(self) -> None:
        parser = _parser(extra_args=[ArgSpec("--netbox-url", help="Override NETBOX_URL")])
        ns = parser.parse_args(["--netbox-url", "https://nb.test"])
        assert ns.netbox_url == "https://nb.test"

    def test_extra_arg_store_true(self) -> None:
        parser = _parser(extra_args=[ArgSpec("--verbose", action="store_true")])
        assert vars(parser.parse_args([])) == {}  # absent unless passed
        assert parser.parse_args(["--verbose"]).verbose is True


class TestSettingsFromArgs:
    def test_defaults_when_nothing_passed(self) -> None:
        ns = _parser().parse_args([])
        settings = settings_from_args(_SrvSettings, ns)
        assert settings.transport == "stdio"
        assert settings.host == "127.0.0.1"
        assert settings.port == 8000
        assert settings.log_json is False

    def test_cli_overrides_defaults(self) -> None:
        ns = _parser().parse_args(["--transport", "http", "--port", "9001"])
        settings = settings_from_args(_SrvSettings, ns)
        assert settings.transport == "http"
        assert settings.port == 9001

    def test_env_used_when_no_cli(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TESTSRV_PORT", "5000")
        ns = _parser().parse_args([])
        settings = settings_from_args(_SrvSettings, ns)
        assert settings.port == 5000  # env > default

    def test_cli_beats_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TESTSRV_PORT", "5000")
        ns = _parser().parse_args(["--port", "9002"])
        settings = settings_from_args(_SrvSettings, ns)
        assert settings.port == 9002  # CLI > env

    def test_passing_one_flag_does_not_reset_env_others(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TESTSRV_HOST", "10.0.0.1")
        ns = _parser().parse_args(["--port", "9003"])
        settings = settings_from_args(_SrvSettings, ns)
        assert settings.port == 9003  # from CLI
        assert settings.host == "10.0.0.1"  # untouched env value preserved

    def test_log_format_json_sets_log_json(self) -> None:
        ns = _parser().parse_args(["--log-format", "json"])
        assert settings_from_args(_SrvSettings, ns).log_json is True

    def test_log_format_text_clears_log_json(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TESTSRV_LOG_JSON", "true")
        ns = _parser().parse_args(["--log-format", "text"])
        assert settings_from_args(_SrvSettings, ns).log_json is False  # CLI text overrides env

    def test_log_level_normalized(self) -> None:
        ns = _parser().parse_args(["--log-level", "debug"])
        assert settings_from_args(_SrvSettings, ns).log_level == "DEBUG"

    def test_extra_arg_overlaid_on_settings(self) -> None:
        parser = _parser(extra_args=[ArgSpec("--netbox-url", help="Override NETBOX_URL")])
        ns = parser.parse_args(["--netbox-url", "https://nb.test"])
        assert settings_from_args(_SrvSettings, ns).netbox_url == "https://nb.test"

    def test_unknown_extra_arg_is_ignored(self) -> None:
        parser = _parser(extra_args=[ArgSpec("--mystery")])
        ns = parser.parse_args(["--mystery", "x"])
        settings = settings_from_args(_SrvSettings, ns)
        assert not hasattr(settings, "mystery")  # extra="ignore"

    def test_extra_kwarg_has_highest_precedence(self) -> None:
        ns = _parser().parse_args(["--port", "9004"])
        settings = settings_from_args(_SrvSettings, ns, extra={"port": 7000})
        assert settings.port == 7000
