"""Tests for top-level package exports."""

import mcp_common


class TestPublicAPI:
    def test_exports_mcp_settings(self) -> None:
        assert hasattr(mcp_common, "MCPSettings")

    def test_exports_setup_logging(self) -> None:
        assert callable(mcp_common.setup_logging)

    def test_exports_health_resource(self) -> None:
        assert callable(mcp_common.health_resource)

    def test_exports_get_version(self) -> None:
        assert callable(mcp_common.get_version)

    def test_exports_format_agent_exception_remediation(self) -> None:
        assert callable(mcp_common.format_agent_exception_remediation)

    def test_exports_mcp_tool_error_with_remediation(self) -> None:
        assert callable(mcp_common.mcp_tool_error_with_remediation)

    def test_exports_mcp_remediation_wrapper(self) -> None:
        assert callable(mcp_common.mcp_remediation_wrapper)

    def test_exports_site_config(self) -> None:
        assert mcp_common.SiteConfig is not None

    def test_exports_site_manager(self) -> None:
        assert mcp_common.SiteManager is not None

    def test_exports_poll_with_progress(self) -> None:
        assert callable(mcp_common.poll_with_progress)

    def test_exports_operation_states(self) -> None:
        assert mcp_common.OperationStates is not None

    def test_exports_poll_result(self) -> None:
        assert mcp_common.PollResult is not None

    def test_exports_create_cli_app(self) -> None:
        assert callable(mcp_common.create_cli_app)

    def test_exports_run_cli(self) -> None:
        assert callable(mcp_common.run_cli)

    def test_exports_echo_result(self) -> None:
        assert callable(mcp_common.echo_result)

    def test_exports_json_option(self) -> None:
        assert mcp_common.JsonOption is not None

    def test_exports_poll_until(self) -> None:
        assert callable(mcp_common.poll_until)

    def test_exports_suggesting_typer_group(self) -> None:
        assert mcp_common.SuggestingTyperGroup is not None

    def test_exports_poll_timeout(self) -> None:
        assert issubclass(mcp_common.PollTimeout, Exception)

    def test_exports_dual_mode_tool(self) -> None:
        assert callable(mcp_common.dual_mode_tool)

    def test_exports_build_cli_from_mcp(self) -> None:
        assert callable(mcp_common.build_cli_from_mcp)

    def test_exports_cli_context(self) -> None:
        assert mcp_common.CliContext is not None

    def test_exports_lookup_cache(self) -> None:
        assert mcp_common.LookupCache is not None

    def test_exports_name_id_resolver(self) -> None:
        assert mcp_common.NameIdResolver is not None

    def test_all_matches_exports(self) -> None:
        for name in mcp_common.__all__:
            assert hasattr(mcp_common, name), f"{name} in __all__ but not importable"
