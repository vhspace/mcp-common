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

    def test_exports_poll_with_progress(self) -> None:
        assert callable(mcp_common.poll_with_progress)

    def test_exports_operation_states(self) -> None:
        assert mcp_common.OperationStates is not None

    def test_exports_poll_result(self) -> None:
        assert mcp_common.PollResult is not None

    def test_all_matches_exports(self) -> None:
        for name in mcp_common.__all__:
            assert hasattr(mcp_common, name), f"{name} in __all__ but not importable"
