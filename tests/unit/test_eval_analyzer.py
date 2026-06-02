"""Tests for eval log analyzer: EvalFailure model, analyze_eval_log, analyze_eval_dir."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from inspect_ai.model import ChatMessageAssistant, ChatMessageUser
from inspect_ai.scorer import CORRECT, INCORRECT, PARTIAL, Score
from inspect_ai.tool import ToolCall

from mcp_common.testing.eval.analyzer import (
    EvalFailure,
    _build_trace_excerpt,
    _extract_input_text,
    _extract_tool_call_names,
    _server_from_task_name,
    analyze_eval_dir,
    analyze_eval_log,
)

# ---------------------------------------------------------------------------
# Helpers to build mock Inspect AI objects
# ---------------------------------------------------------------------------


def _make_tool_call(function: str, arguments: dict[str, Any] | None = None) -> ToolCall:
    return ToolCall(
        id=f"call_{function}",
        function=function,
        arguments=arguments or {},
    )


def _make_sample(
    *,
    sample_id: int | str = 1,
    input_text: str = "test input",
    scores: dict[str, Score] | None = None,
    messages: list[Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> MagicMock:
    """Build a mock EvalSample."""
    sample = MagicMock()
    sample.id = sample_id
    sample.input = input_text
    sample.scores = scores
    sample.messages = messages or []
    sample.metadata = metadata or {}
    return sample


def _make_eval_log(
    *,
    task: str = "netbox_mcp_eval",
    samples: list[Any] | None = None,
    location: str = "/tmp/test.eval",
) -> MagicMock:
    """Build a mock EvalLog."""
    log = MagicMock()
    log.eval.task = task
    log.samples = samples
    log.location = location
    return log


# ---------------------------------------------------------------------------
# EvalFailure model tests
# ---------------------------------------------------------------------------


@pytest.mark.eval
class TestEvalFailure:
    def test_construction_minimal(self) -> None:
        f = EvalFailure(server="netbox-mcp", scenario="list all devices")
        assert f.server == "netbox-mcp"
        assert f.scenario == "list all devices"
        assert f.tool_calls == []
        assert f.error == ""
        assert f.score == ""
        assert f.trace_excerpt == ""

    def test_construction_full(self) -> None:
        f = EvalFailure(
            server="netbox-mcp",
            scenario="find device by name",
            tool_calls=["get_device", "list_ips"],
            error="Tool selection: 0.50",
            score="I",
            trace_excerpt="Called get_device...",
        )
        assert f.server == "netbox-mcp"
        assert f.tool_calls == ["get_device", "list_ips"]
        assert f.score == "I"

    def test_serialization_roundtrip(self) -> None:
        f = EvalFailure(
            server="test-server",
            scenario="test scenario",
            score="P",
        )
        data = f.model_dump()
        restored = EvalFailure.model_validate(data)
        assert restored == f


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------


@pytest.mark.eval
class TestServerFromTaskName:
    def test_standard_convention(self) -> None:
        assert _server_from_task_name("netbox_mcp_eval") == "netbox-mcp"

    def test_no_eval_suffix(self) -> None:
        assert _server_from_task_name("netbox_mcp") == "netbox-mcp"

    def test_simple_name(self) -> None:
        assert _server_from_task_name("maas_eval") == "maas"

    def test_already_hyphenated(self) -> None:
        assert _server_from_task_name("my-server") == "my-server"


@pytest.mark.eval
class TestExtractInputText:
    def test_string_input(self) -> None:
        sample = _make_sample(input_text="list all devices")
        assert _extract_input_text(sample) == "list all devices"

    def test_message_list_input(self) -> None:
        sample = _make_sample()
        sample.input = [
            ChatMessageUser(content="find device srv1"),
        ]
        assert _extract_input_text(sample) == "find device srv1"

    def test_fallback_to_id(self) -> None:
        sample = _make_sample(sample_id=42)
        sample.input = []
        assert _extract_input_text(sample) == "42"


@pytest.mark.eval
class TestExtractToolCallNames:
    def test_no_messages(self) -> None:
        sample = _make_sample(messages=[])
        assert _extract_tool_call_names(sample) == []

    def test_extracts_names(self) -> None:
        tc1 = _make_tool_call("get_device")
        tc2 = _make_tool_call("list_ips")
        sample = _make_sample(
            messages=[
                ChatMessageAssistant(content="step 1", tool_calls=[tc1]),
                ChatMessageUser(content="ok"),
                ChatMessageAssistant(content="step 2", tool_calls=[tc2]),
            ]
        )
        assert _extract_tool_call_names(sample) == ["get_device", "list_ips"]


@pytest.mark.eval
class TestBuildTraceExcerpt:
    def test_collects_last_messages(self) -> None:
        sample = _make_sample(
            messages=[
                ChatMessageAssistant(content="Looking up device..."),
                ChatMessageUser(content="ok"),
                ChatMessageAssistant(content="Found device srv1 in rack A1."),
            ]
        )
        excerpt = _build_trace_excerpt(sample)
        assert "Found device srv1" in excerpt
        assert "Looking up device" in excerpt

    def test_truncates_long_excerpts(self) -> None:
        long_msg = "x" * 600
        sample = _make_sample(messages=[ChatMessageAssistant(content=long_msg)])
        excerpt = _build_trace_excerpt(sample, max_chars=100)
        assert len(excerpt) <= 101  # 100 + ellipsis char
        assert excerpt.endswith("…")

    def test_empty_messages(self) -> None:
        sample = _make_sample(messages=[])
        assert _build_trace_excerpt(sample) == ""


# ---------------------------------------------------------------------------
# analyze_eval_log tests (with mocked read_eval_log)
# ---------------------------------------------------------------------------


@pytest.mark.eval
class TestAnalyzeEvalLog:
    def test_extracts_failures(self) -> None:
        tc = _make_tool_call("get_device")
        sample_fail = _make_sample(
            input_text="find device srv1",
            scores={"tool_use": Score(value=INCORRECT, explanation="Wrong tool selected")},
            messages=[
                ChatMessageAssistant(content="I'll look that up", tool_calls=[tc]),
                ChatMessageAssistant(content="Device not found"),
            ],
        )
        sample_pass = _make_sample(
            input_text="list devices",
            scores={"tool_use": Score(value=CORRECT, explanation="All correct")},
            messages=[ChatMessageAssistant(content="Here are the devices")],
        )
        eval_log = _make_eval_log(
            task="netbox_mcp_eval",
            samples=[sample_fail, sample_pass],
        )

        with patch("mcp_common.testing.eval.analyzer.read_eval_log", return_value=eval_log):
            failures = analyze_eval_log("/tmp/test.eval")

        assert len(failures) == 1
        f = failures[0]
        assert f.server == "netbox-mcp"
        assert f.scenario == "find device srv1"
        assert f.score == INCORRECT
        assert f.error == "Wrong tool selected"
        assert "get_device" in f.tool_calls

    def test_no_samples(self) -> None:
        eval_log = _make_eval_log(samples=None)
        with patch("mcp_common.testing.eval.analyzer.read_eval_log", return_value=eval_log):
            failures = analyze_eval_log("/tmp/test.eval")
        assert failures == []

    def test_all_passing(self) -> None:
        sample = _make_sample(
            scores={"tool_use": Score(value=CORRECT, explanation="OK")},
            messages=[],
        )
        eval_log = _make_eval_log(samples=[sample])
        with patch("mcp_common.testing.eval.analyzer.read_eval_log", return_value=eval_log):
            failures = analyze_eval_log("/tmp/test.eval")
        assert failures == []

    def test_partial_is_failure(self) -> None:
        sample = _make_sample(
            input_text="partial scenario",
            scores={"tool_use": Score(value=PARTIAL, explanation="Partially correct")},
            messages=[ChatMessageAssistant(content="partial answer")],
        )
        eval_log = _make_eval_log(samples=[sample])
        with patch("mcp_common.testing.eval.analyzer.read_eval_log", return_value=eval_log):
            failures = analyze_eval_log("/tmp/test.eval")
        assert len(failures) == 1
        assert failures[0].score == PARTIAL

    def test_multiple_scorers(self) -> None:
        """A sample with two scorers, one passing and one failing, yields one failure."""
        sample = _make_sample(
            input_text="multi-scorer test",
            scores={
                "tool_use": Score(value=CORRECT, explanation="OK"),
                "combined": Score(value=INCORRECT, explanation="Interface wrong"),
            },
            messages=[ChatMessageAssistant(content="result")],
        )
        eval_log = _make_eval_log(samples=[sample])
        with patch("mcp_common.testing.eval.analyzer.read_eval_log", return_value=eval_log):
            failures = analyze_eval_log("/tmp/test.eval")
        assert len(failures) == 1
        assert failures[0].error == "Interface wrong"


# ---------------------------------------------------------------------------
# analyze_eval_dir tests
# ---------------------------------------------------------------------------


@pytest.mark.eval
class TestAnalyzeEvalDir:
    def test_reads_all_eval_files(self, tmp_path: Path) -> None:
        (tmp_path / "log1.eval").touch()
        (tmp_path / "log2.eval").touch()
        (tmp_path / "other.json").touch()

        failure = EvalFailure(server="test", scenario="test scenario", score="I")
        with patch(
            "mcp_common.testing.eval.analyzer.analyze_eval_log",
            return_value=[failure],
        ):
            results = analyze_eval_dir(tmp_path)

        assert len(results) == 2

    def test_nonexistent_dir(self, tmp_path: Path) -> None:
        results = analyze_eval_dir(tmp_path / "nonexistent")
        assert results == []

    def test_empty_dir(self, tmp_path: Path) -> None:
        results = analyze_eval_dir(tmp_path)
        assert results == []

    def test_handles_read_errors(self, tmp_path: Path) -> None:
        (tmp_path / "bad.eval").touch()
        (tmp_path / "good.eval").touch()

        failure = EvalFailure(server="test", scenario="good one", score="I")

        call_count = 0

        def mock_analyze(path: Any) -> list[EvalFailure]:
            nonlocal call_count
            call_count += 1
            if "bad" in str(path):
                raise ValueError("corrupt file")
            return [failure]

        with patch(
            "mcp_common.testing.eval.analyzer.analyze_eval_log",
            side_effect=mock_analyze,
        ):
            results = analyze_eval_dir(tmp_path)

        assert len(results) == 1
        assert results[0].scenario == "good one"
