"""Tests for progress-aware polling utilities."""

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
        assert ctx.report_progress.await_count == 3

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
