"""Entry point for ``python -m redfish_mcp.kvm.daemon``."""

from __future__ import annotations

import asyncio

from redfish_mcp.kvm.daemon.server import main


def run() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    run()
