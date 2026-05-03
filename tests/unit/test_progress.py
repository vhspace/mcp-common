"""Tests for progress-aware polling utilities."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from mcp_common.progress import OperationStates, PollResult, poll_with_progress


class TestPollResult:
    def test_defaults(self) -> None:
        result = PollResult(ok=True, final_state="done", elapsed_s=10.0)
        assert result.ok is True
        assert result.final_state == "done"
        assert result.elapsed_s == 10.0
        assert result.timed_out is False
        assert result.extra == {}

    def test_with_extra(self) -> None:
        extra = {"id": 42, "name": "job"}
        result = PollResult(ok=False, final_state="failed", elapsed_s=5.0, extra=extra)
        assert result.ok is False
        assert result.extra == extra

    def test_timed_out(self) -> None:
        result = PollResult(ok=False, final_state="running", elapsed_s=600.0, timed_out=True)
        assert result.timed_out is True


class TestOperationStates:
    def test_defaults(self) -> None:
        states = OperationStates()
        assert states.success == []
        assert states.failure == []
        assert states.in_progress == []

    def test_custom_states(self) -> None:
        states = OperationStates(
            success=["deployed", "ready"],
            failure=["error", "broken"],
            in_progress=["deploying"],
        )
        assert "deployed" in states.success
        assert "error" in states.failure
        assert "deploying" in states.in_progress


class TestPollWithProgress:
    @pytest.mark.anyio
    async def test_success_after_n_polls(self) -> None:
        call_count = 0

        async def check_fn() -> dict:
            nonlocal call_count
            call_count += 1
            if call_count >= 3:
                return {"status": "complete"}
            return {"status": "running"}

        ctx = AsyncMock()
        states = OperationStates(success=["complete"], failure=["error"], in_progress=["running"])

        result = await poll_with_progress(
            ctx, check_fn, "status", states, timeout_s=60, interval_s=0.01
        )

        assert result.ok is True
        assert result.final_state == "complete"
        assert result.timed_out is False
        assert call_count == 3

    @pytest.mark.anyio
    async def test_failure_detection(self) -> None:
        async def check_fn() -> dict:
            return {"status": "error", "message": "something broke"}

        ctx = AsyncMock()
        states = OperationStates(success=["complete"], failure=["error"])

        result = await poll_with_progress(
            ctx, check_fn, "status", states, timeout_s=60, interval_s=0.01
        )

        assert result.ok is False
        assert result.final_state == "error"
        assert result.timed_out is False
        assert result.extra["message"] == "something broke"

    @pytest.mark.anyio
    async def test_timeout(self) -> None:
        async def check_fn() -> dict:
            return {"status": "running"}

        ctx = AsyncMock()
        states = OperationStates(success=["complete"], failure=["error"])

        result = await poll_with_progress(
            ctx, check_fn, "status", states, timeout_s=0.03, interval_s=0.01
        )

        assert result.ok is False
        assert result.timed_out is True
        assert result.final_state == "running"

    @pytest.mark.anyio
    async def test_sync_check_fn(self) -> None:
        """Sync callables that return a dict (not a coroutine) should also work."""
        call_count = 0

        def check_fn() -> dict:
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                return {"state": "done"}
            return {"state": "pending"}

        ctx = AsyncMock()
        states = OperationStates(success=["done"])

        result = await poll_with_progress(
            ctx, check_fn, "state", states, timeout_s=60, interval_s=0.01
        )

        assert result.ok is True
        assert result.final_state == "done"
        assert call_count == 2

    @pytest.mark.anyio
    async def test_custom_format_message(self) -> None:
        call_count = 0

        async def check_fn() -> dict:
            nonlocal call_count
            call_count += 1
            return {"status": "complete", "pct": 100}

        ctx = AsyncMock()
        states = OperationStates(success=["complete"])

        def fmt(state: dict, elapsed: float) -> str:
            return f"{state['pct']}% after {elapsed:.0f}s"

        result = await poll_with_progress(
            ctx, check_fn, "status", states, timeout_s=60, interval_s=0.01, format_message=fmt
        )

        assert result.ok is True
        ctx.report_progress.assert_awaited_once()
        call_args = ctx.report_progress.call_args
        assert "100%" in call_args.kwargs.get("message", call_args[2] if len(call_args) > 2 else "")

    @pytest.mark.anyio
    async def test_missing_state_key_defaults_to_unknown(self) -> None:
        async def check_fn() -> dict:
            return {"other_key": "value"}

        ctx = AsyncMock()
        states = OperationStates(success=["complete"])

        result = await poll_with_progress(
            ctx, check_fn, "status", states, timeout_s=0.03, interval_s=0.01
        )

        assert result.ok is False
        assert result.timed_out is True
        assert result.final_state == "unknown"

    @pytest.mark.anyio
    async def test_progress_report_failure_does_not_crash(self) -> None:
        """If ctx.report_progress raises, poll still returns a valid result."""
        ctx = AsyncMock()
        ctx.report_progress = AsyncMock(side_effect=Exception("transport closed"))

        call_count = 0

        async def check_fn() -> dict:
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                return {"status": "successful"}
            return {"status": "running"}

        states = OperationStates(
            success=["successful"], failure=["error"], in_progress=["running"]
        )

        result = await poll_with_progress(
            ctx, check_fn, "status", states, timeout_s=30, interval_s=0.01
        )

        assert result.ok is True
        assert result.final_state == "successful"

    @pytest.mark.anyio
    async def test_hard_timeout_prevents_infinite_hang(self) -> None:
        """Even if check_fn blocks, poll returns within timeout_s + buffer."""
        ctx = AsyncMock()

        async def blocking_check() -> dict:
            await asyncio.sleep(100)
            return {"status": "running"}

        states = OperationStates(
            success=["complete"], failure=["error"], in_progress=["running"]
        )

        start = asyncio.get_event_loop().time()
        result = await poll_with_progress(
            ctx, blocking_check, "status", states, timeout_s=2, interval_s=0.1
        )
        elapsed = asyncio.get_event_loop().time() - start

        assert result.timed_out is True
        assert elapsed < 15  # should be ~7s (2s + 5s buffer), definitely not 100s

    @pytest.mark.anyio
    async def test_wall_clock_elapsed_tracks_real_time(self) -> None:
        """Elapsed should reflect wall-clock time, not just sleep intervals."""
        ctx = AsyncMock()

        call_count = 0

        async def slow_check() -> dict:
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(0.3)
            if call_count >= 3:
                return {"status": "successful"}
            return {"status": "running"}

        states = OperationStates(
            success=["successful"], failure=["error"], in_progress=["running"]
        )

        result = await poll_with_progress(
            ctx, slow_check, "status", states, timeout_s=30, interval_s=0.01
        )

        assert result.ok is True
        assert result.elapsed_s >= 0.5  # at least 3 * 0.3s check time minus some scheduling
