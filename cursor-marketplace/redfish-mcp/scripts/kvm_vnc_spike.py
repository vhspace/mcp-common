"""Benchmark asyncvnc vs vncdotool against a local Xvfb + x11vnc target.

Usage:
    uv run python scripts/kvm_vnc_spike.py

Both libraries must already be installed (uv add --dev asyncvnc vncdotool).

Prints a table comparing:
  - connect + screenshot round-trip latency (3 samples, median)
  - screenshot size (bytes)
  - anomalies / errors

Writes a decision template to scripts/kvm_vnc_spike_result.md.
"""

from __future__ import annotations

import asyncio
import statistics
import time
from pathlib import Path

from redfish_mcp.kvm.backends._subprocess import SessionSubprocesses

GEOMETRY = "800x600x24"


async def bench_asyncvnc(vnc_port: int, password: str) -> dict:
    import io

    import asyncvnc

    latencies: list[float] = []
    png_size = 0
    error = None
    try:
        for _ in range(3):
            t0 = time.perf_counter()
            async with asyncvnc.connect("127.0.0.1", vnc_port, password=password) as client:
                img = await client.screenshot()
            latencies.append(time.perf_counter() - t0)

        # One capture for size
        async with asyncvnc.connect("127.0.0.1", vnc_port, password=password) as client:
            img = await client.screenshot()
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            png_size = len(buf.getvalue())
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"

    return {
        "lib": "asyncvnc",
        "median_s": statistics.median(latencies) if latencies else None,
        "samples": latencies,
        "png_bytes": png_size,
        "error": error,
    }


def bench_vncdotool_sync(vnc_port: int, password: str) -> dict:
    import io

    from vncdotool import api

    latencies: list[float] = []
    png_size = 0
    error = None
    try:
        for _ in range(3):
            t0 = time.perf_counter()
            client = api.connect(f"127.0.0.1::{vnc_port}", password=password)
            try:
                client.refreshScreen()
            finally:
                client.disconnect()
            latencies.append(time.perf_counter() - t0)

        client = api.connect(f"127.0.0.1::{vnc_port}", password=password)
        try:
            client.refreshScreen()
            buf = io.BytesIO()
            client.screen.save(buf, format="PNG")
            png_size = len(buf.getvalue())
        finally:
            client.disconnect()
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"

    return {
        "lib": "vncdotool",
        "median_s": statistics.median(latencies) if latencies else None,
        "samples": latencies,
        "png_bytes": png_size,
        "error": error,
    }


async def bench_vncdotool(vnc_port: int, password: str) -> dict:
    return await asyncio.to_thread(bench_vncdotool_sync, vnc_port, password)


async def main() -> None:
    print(f"Starting Xvfb + x11vnc @ {GEOMETRY}")
    async with SessionSubprocesses.for_x11_only(geometry=GEOMETRY) as spawned:
        password = spawned.vnc_secret_path.read_text().strip()
        print(f"VNC on 127.0.0.1:{spawned.vnc_port}")

        results: list[dict] = []
        results.append(await bench_asyncvnc(spawned.vnc_port, password))
        results.append(await bench_vncdotool(spawned.vnc_port, password))

        lines = [
            "# VNC library spike results",
            "",
            f"Target: Xvfb {GEOMETRY} + x11vnc on localhost",
            "Samples: 3 screenshots per library",
            "",
            "| Library   | Median (s) | Samples | PNG bytes | Error |",
            "|-----------|------------|---------|-----------|-------|",
        ]
        for r in results:
            samples = ", ".join(f"{s:.3f}" for s in r["samples"]) if r["samples"] else "-"
            median = f"{r['median_s']:.3f}" if r["median_s"] is not None else "-"
            lines.append(
                f"| {r['lib']:9s} | {median:>10} | {samples} | "
                f"{r['png_bytes']} | {r['error'] or ''} |"
            )

        decision_path = Path(__file__).parent / "kvm_vnc_spike_result.md"
        decision_path.write_text("\n".join(lines) + "\n")
        print("\n".join(lines))
        print(f"\nWrote {decision_path}")


if __name__ == "__main__":
    asyncio.run(main())
