"""Tests for the sync poll_until helper."""

from __future__ import annotations

from typing import Any

import pytest

from mcpanvil.cli import PollTimeout, poll_until


class FakeClock:
    """Test double for time.monotonic + time.sleep.

    ``monotonic`` returns the current logical time; ``sleep`` advances
    it without blocking. Use via :func:`install_fake_clock` to patch
    :mod:`mcpanvil.cli.poll`'s ``time`` references.
    """

    def __init__(self) -> None:
        self.now: float = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def fake_clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    clock = FakeClock()
    monkeypatch.setattr("mcpanvil.cli.poll.time.monotonic", clock.monotonic)
    monkeypatch.setattr("mcpanvil.cli.poll.time.sleep", clock.sleep)
    return clock


class TestPollUntilHappyPath:
    def test_terminal_on_first_fetch_returns_immediately(self, fake_clock: FakeClock) -> None:
        calls = 0

        def fetch() -> dict[str, Any]:
            nonlocal calls
            calls += 1
            return {"status": "done"}

        result = poll_until(
            fetch,
            lambda v: v["status"] == "done",
            timeout_s=60,
            interval_s=1,
        )

        assert result == {"status": "done"}
        assert calls == 1
        assert fake_clock.sleeps == []

    def test_terminal_after_n_fetches_returns_final_value(self, fake_clock: FakeClock) -> None:
        states = ["pending", "running", "running", "done"]

        def fetch() -> str:
            return states.pop(0)

        result = poll_until(
            fetch,
            lambda v: v == "done",
            timeout_s=60,
            interval_s=1,
        )

        assert result == "done"
        assert fake_clock.sleeps == [1, 1, 1]

    def test_returns_actual_terminal_value_not_predicate_input(self, fake_clock: FakeClock) -> None:
        values = [{"id": 1, "n": 1}, {"id": 2, "n": 4}, {"id": 3, "n": 9}]

        def fetch() -> dict[str, int]:
            return values.pop(0)

        result = poll_until(fetch, lambda v: v["n"] >= 9, timeout_s=10, interval_s=0.5)
        assert result == {"id": 3, "n": 9}


class TestPollUntilTimeout:
    def test_timeout_raises_with_elapsed_and_last_value(self, fake_clock: FakeClock) -> None:
        def fetch() -> dict[str, str]:
            return {"status": "still-running"}

        with pytest.raises(PollTimeout) as exc_info:
            poll_until(
                fetch,
                lambda v: v["status"] == "done",
                timeout_s=5,
                interval_s=2,
            )

        err = exc_info.value
        assert err.elapsed_s >= 5
        assert err.last_value == {"status": "still-running"}
        assert "timed out" in str(err)
        assert "still-running" in str(err)

    def test_timeout_message_includes_last_value_repr(self, fake_clock: FakeClock) -> None:
        def fetch() -> str:
            return "queued"

        with pytest.raises(PollTimeout) as exc_info:
            poll_until(
                fetch,
                lambda v: v == "done",
                timeout_s=1,
                interval_s=0.5,
            )

        assert "'queued'" in str(exc_info.value)


class TestPollUntilOnTick:
    def test_on_tick_called_per_non_terminal_fetch(self, fake_clock: FakeClock) -> None:
        ticks: list[tuple[float, str]] = []
        states = ["a", "b", "c", "DONE"]

        def fetch() -> str:
            return states.pop(0)

        result = poll_until(
            fetch,
            lambda v: v == "DONE",
            timeout_s=60,
            interval_s=1,
            on_tick=lambda elapsed, snapshot: ticks.append((elapsed, snapshot)),
        )

        assert result == "DONE"
        assert len(ticks) == 3
        assert [snap for _, snap in ticks] == ["a", "b", "c"]
        assert ticks[0][0] == pytest.approx(0.0)
        assert ticks[1][0] == pytest.approx(1.0)
        assert ticks[2][0] == pytest.approx(2.0)

    def test_on_tick_not_called_when_first_fetch_is_terminal(self, fake_clock: FakeClock) -> None:
        ticks: list[tuple[float, Any]] = []

        def fetch() -> str:
            return "done"

        poll_until(
            fetch,
            lambda v: v == "done",
            timeout_s=60,
            interval_s=1,
            on_tick=lambda elapsed, snap: ticks.append((elapsed, snap)),
        )

        assert ticks == []

    def test_on_tick_optional(self, fake_clock: FakeClock) -> None:
        """Missing on_tick should not raise."""
        states = ["x", "y", "DONE"]

        result = poll_until(
            lambda: states.pop(0),
            lambda v: v == "DONE",
            timeout_s=10,
            interval_s=0.5,
        )
        assert result == "DONE"


class TestPollUntilIntervalRespected:
    def test_interval_passed_to_sleep_each_iteration(self, fake_clock: FakeClock) -> None:
        states = ["wait", "wait", "wait", "done"]

        poll_until(
            lambda: states.pop(0),
            lambda v: v == "done",
            timeout_s=60,
            interval_s=2.5,
        )

        assert fake_clock.sleeps == [2.5, 2.5, 2.5]

    def test_interval_zero_still_advances_via_real_monotonic_skew(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        clock = FakeClock()
        monkeypatch.setattr("mcpanvil.cli.poll.time.monotonic", clock.monotonic)
        monkeypatch.setattr("mcpanvil.cli.poll.time.sleep", clock.sleep)

        states = ["a", "b", "done"]
        poll_until(
            lambda: states.pop(0),
            lambda v: v == "done",
            timeout_s=10,
            interval_s=0,
        )
        assert fake_clock_sleep_count(clock) == 2

    def test_sleep_skipped_after_terminal(self, fake_clock: FakeClock) -> None:
        """Once terminal is observed, no extra sleep happens."""
        poll_until(
            lambda: "done",
            lambda v: v == "done",
            timeout_s=10,
            interval_s=5,
        )
        assert fake_clock.sleeps == []


def fake_clock_sleep_count(clock: FakeClock) -> int:
    return len(clock.sleeps)


class TestPollTimeoutAttributes:
    def test_construct_directly(self) -> None:
        err = PollTimeout(elapsed_s=42.0, last_value={"x": 1})
        assert err.elapsed_s == 42.0
        assert err.last_value == {"x": 1}
        assert isinstance(err, Exception)
