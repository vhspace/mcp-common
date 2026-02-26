"""Standard health check resource for MCP servers."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class HealthStatus:
    """Health check result for an MCP server."""

    name: str
    version: str
    status: str = "healthy"
    uptime_seconds: float = 0.0
    checks: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "status": self.status,
            "uptime_seconds": round(self.uptime_seconds, 2),
            "checks": self.checks,
        }


_start_time: float = time.monotonic()


def health_resource(
    name: str,
    version: str,
    checks: dict[str, Any] | None = None,
) -> HealthStatus:
    """Generate a health check response.

    Args:
        name: Server name.
        version: Server version string.
        checks: Optional dict of named health checks and their results.

    Returns:
        HealthStatus with uptime and check results.
    """
    uptime = time.monotonic() - _start_time
    all_checks = checks or {}
    status = "healthy" if all(v for v in all_checks.values()) else "degraded"

    return HealthStatus(
        name=name,
        version=version,
        status=status if all_checks else "healthy",
        uptime_seconds=uptime,
        checks=all_checks,
    )
