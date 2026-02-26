"""Tests for health check resource."""

from mcp_common.health import health_resource


class TestHealthResource:
    def test_healthy_with_no_checks(self) -> None:
        result = health_resource("test-server", "1.0.0")
        assert result.status == "healthy"
        assert result.name == "test-server"
        assert result.version == "1.0.0"
        assert result.uptime_seconds >= 0

    def test_healthy_with_passing_checks(self) -> None:
        result = health_resource("test-server", "1.0.0", checks={"db": True})
        assert result.status == "healthy"
        assert result.checks == {"db": True}

    def test_degraded_with_failing_check(self) -> None:
        result = health_resource("test-server", "1.0.0", checks={"db": True, "cache": False})
        assert result.status == "degraded"

    def test_to_dict(self) -> None:
        result = health_resource("test-server", "1.0.0")
        d = result.to_dict()
        assert isinstance(d, dict)
        assert d["name"] == "test-server"
        assert "uptime_seconds" in d
