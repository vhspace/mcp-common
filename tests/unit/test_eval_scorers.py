"""Tests for eval scorers: tool_use_scorer, combined_scorer, parity_scorer."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from inspect_ai.model import ChatMessageAssistant, ChatMessageUser
from inspect_ai.model._chat_message import ToolCall
from inspect_ai.scorer import CORRECT, INCORRECT, PARTIAL, Target
from inspect_ai.solver import TaskState

from mcp_common.testing.eval.scorers import (
    _classify,
    _compute_tool_selection_score,
    _extract_tool_calls,
    _get_final_response,
    _parse_expected_tools,
    combined_scorer,
    parity_scorer,
    tool_use_scorer,
)

# ---------------------------------------------------------------------------
# Helpers to build mock TaskState objects
# ---------------------------------------------------------------------------


def _make_state(
    messages: list[Any],
    metadata: dict[str, Any] | None = None,
) -> TaskState:
    """Construct a minimal TaskState for testing."""
    return TaskState(
        model="test/model",
        sample_id=1,
        epoch=1,
        input="test input",
        messages=messages,
        metadata=metadata,
    )


def _make_tool_call(function: str, arguments: dict[str, Any] | None = None) -> ToolCall:
    return ToolCall(
        id=f"call_{function}",
        function=function,
        arguments=arguments or {},
    )


def _make_llm_response(content: str) -> MagicMock:
    """Fake OpenAI chat completion response."""
    choice = MagicMock()
    choice.message.content = content
    resp = MagicMock()
    resp.choices = [choice]
    return resp


# ---------------------------------------------------------------------------
# Unit tests for helpers
# ---------------------------------------------------------------------------


@pytest.mark.eval
class TestExtractToolCalls:
    def test_empty_messages(self) -> None:
        state = _make_state([])
        assert _extract_tool_calls(state) == []

    def test_no_tool_calls(self) -> None:
        state = _make_state(
            [
                ChatMessageUser(content="hello"),
                ChatMessageAssistant(content="hi there"),
            ]
        )
        assert _extract_tool_calls(state) == []

    def test_extracts_tool_calls(self) -> None:
        tc = _make_tool_call("get_device", {"name": "srv1"})
        state = _make_state(
            [
                ChatMessageUser(content="find srv1"),
                ChatMessageAssistant(content="Looking up…", tool_calls=[tc]),
            ]
        )
        result = _extract_tool_calls(state)
        assert len(result) == 1
        assert result[0]["function"] == "get_device"
        assert result[0]["arguments"] == {"name": "srv1"}

    def test_multiple_calls_across_messages(self) -> None:
        tc1 = _make_tool_call("list_devices")
        tc2 = _make_tool_call("get_device", {"id": "42"})
        state = _make_state(
            [
                ChatMessageAssistant(content="step 1", tool_calls=[tc1]),
                ChatMessageUser(content="next"),
                ChatMessageAssistant(content="step 2", tool_calls=[tc2]),
            ]
        )
        result = _extract_tool_calls(state)
        assert [r["function"] for r in result] == ["list_devices", "get_device"]


@pytest.mark.eval
class TestGetFinalResponse:
    def test_returns_last_assistant_text(self) -> None:
        state = _make_state(
            [
                ChatMessageAssistant(content="first"),
                ChatMessageAssistant(content="final answer"),
            ]
        )
        assert _get_final_response(state) == "final answer"

    def test_skips_empty_content(self) -> None:
        state = _make_state(
            [
                ChatMessageAssistant(content="real answer"),
                ChatMessageAssistant(content=""),
            ]
        )
        assert _get_final_response(state) == "real answer"

    def test_empty_messages(self) -> None:
        state = _make_state([])
        assert _get_final_response(state) == ""


@pytest.mark.eval
class TestComputeToolSelectionScore:
    def test_perfect_match(self) -> None:
        assert _compute_tool_selection_score(["a", "b"], ["a", "b"]) == 1.0

    def test_no_match(self) -> None:
        assert _compute_tool_selection_score(["c"], ["a", "b"]) == 0.0

    def test_partial_match(self) -> None:
        assert _compute_tool_selection_score(["a", "c"], ["a", "b"]) == 0.5

    def test_empty_expected(self) -> None:
        assert _compute_tool_selection_score(["a"], []) == 1.0

    def test_extra_tools_dont_penalize(self) -> None:
        assert _compute_tool_selection_score(["a", "b", "c"], ["a", "b"]) == 1.0


@pytest.mark.eval
class TestParseExpectedTools:
    def test_csv(self) -> None:
        assert _parse_expected_tools(Target("get_device,list_ips")) == [
            "get_device",
            "list_ips",
        ]

    def test_single(self) -> None:
        assert _parse_expected_tools(Target("get_device")) == ["get_device"]

    def test_empty(self) -> None:
        assert _parse_expected_tools(Target("")) == []

    def test_whitespace(self) -> None:
        assert _parse_expected_tools(Target(" a , b ")) == ["a", "b"]


@pytest.mark.eval
class TestClassify:
    def test_correct(self) -> None:
        assert _classify(0.8, 0.7) == CORRECT
        assert _classify(1.0, 1.0) == CORRECT

    def test_partial_tool(self) -> None:
        assert _classify(0.5, 0.3) == PARTIAL

    def test_partial_completion(self) -> None:
        assert _classify(0.3, 0.5) == PARTIAL

    def test_incorrect(self) -> None:
        assert _classify(0.0, 0.0) == INCORRECT
        assert _classify(0.4, 0.4) == INCORRECT


# ---------------------------------------------------------------------------
# Scorer integration tests (LLM mocked)
# ---------------------------------------------------------------------------


def _patch_llm_client(completion_score: float = 0.9, interface_score: float = 0.8):
    """Return a patch that replaces _get_llm_client with a mock."""
    mock_client = MagicMock()

    def fake_create(**kwargs: Any) -> MagicMock:
        prompt_text = kwargs.get("messages", [{}])[0].get("content", "")
        if "interface" in prompt_text.lower():
            body = json.dumps({"score": interface_score, "explanation": "mock interface"})
        elif "semantically equivalent" in prompt_text.lower():
            body = json.dumps({"equivalent": True, "score": 0.9, "explanation": "mock parity"})
        else:
            body = json.dumps({"score": completion_score, "explanation": "mock completion"})
        return _make_llm_response(body)

    mock_client.chat.completions.create = MagicMock(side_effect=fake_create)
    return patch(
        "mcp_common.testing.eval.scorers._get_llm_client",
        return_value=(mock_client, "test-model"),
    )


@pytest.mark.eval
class TestToolUseScorer:
    @pytest.mark.anyio
    async def test_correct_score(self) -> None:
        tc = _make_tool_call("get_device")
        state = _make_state(
            messages=[
                ChatMessageUser(content="find device"),
                ChatMessageAssistant(content="calling tool", tool_calls=[tc]),
                ChatMessageAssistant(content="Device found: srv1"),
            ],
            metadata={"input": "find device", "expected_behavior": "return device info"},
        )
        target = Target("get_device")

        with _patch_llm_client(completion_score=0.9):
            scorer_fn = tool_use_scorer()
            result = await scorer_fn(state, target)

        assert result.value == CORRECT
        assert result.metadata["tool_selection_score"] == 1.0
        assert result.metadata["task_completion_score"] == 0.9
        assert result.metadata["tools_called"] == ["get_device"]
        assert result.metadata["expected_tools"] == ["get_device"]
        assert result.answer == "Device found: srv1"

    @pytest.mark.anyio
    async def test_incorrect_no_tools(self) -> None:
        state = _make_state(
            messages=[
                ChatMessageUser(content="find device"),
                ChatMessageAssistant(content="I don't know"),
            ],
            metadata={"input": "find device", "expected_behavior": "return device info"},
        )
        target = Target("get_device")

        with _patch_llm_client(completion_score=0.1):
            scorer_fn = tool_use_scorer()
            result = await scorer_fn(state, target)

        assert result.value == INCORRECT
        assert result.metadata["tool_selection_score"] == 0.0

    @pytest.mark.anyio
    async def test_partial_score(self) -> None:
        tc = _make_tool_call("get_device")
        state = _make_state(
            messages=[
                ChatMessageAssistant(content="partial", tool_calls=[tc]),
            ],
            metadata={"input": "find device and list IPs"},
        )
        target = Target("get_device,list_ips")

        with _patch_llm_client(completion_score=0.6):
            scorer_fn = tool_use_scorer()
            result = await scorer_fn(state, target)

        assert result.value == PARTIAL
        assert result.metadata["tool_selection_score"] == 0.5

    @pytest.mark.anyio
    async def test_no_api_key(self) -> None:
        tc = _make_tool_call("get_device")
        state = _make_state(
            messages=[ChatMessageAssistant(content="done", tool_calls=[tc])],
            metadata={"input": "test"},
        )
        target = Target("get_device")

        with patch(
            "mcp_common.testing.eval.scorers._get_llm_client",
            return_value=None,
        ):
            scorer_fn = tool_use_scorer()
            result = await scorer_fn(state, target)

        assert result.metadata["task_completion_score"] == 0.0
        assert "TOGETHER_API_KEY" in result.explanation


@pytest.mark.eval
class TestCombinedScorer:
    @pytest.mark.anyio
    async def test_includes_interface_choice(self) -> None:
        tc = _make_tool_call("bash", {"command": "netbox-cli devices list"})
        state = _make_state(
            messages=[
                ChatMessageUser(content="list devices"),
                ChatMessageAssistant(content="Using CLI", tool_calls=[tc]),
                ChatMessageAssistant(content="Devices: srv1, srv2"),
            ],
            metadata={"input": "list devices", "expected_behavior": "return device list"},
        )
        target = Target("bash")

        with _patch_llm_client(completion_score=0.9, interface_score=1.0):
            scorer_fn = combined_scorer()
            result = await scorer_fn(state, target)

        assert result.value == CORRECT
        assert "interface_choice_score" in result.metadata
        assert result.metadata["interface_choice_score"] == 1.0

    @pytest.mark.anyio
    async def test_no_api_key_combined(self) -> None:
        state = _make_state(
            messages=[ChatMessageAssistant(content="done")],
            metadata={"input": "test"},
        )
        target = Target("")

        with patch(
            "mcp_common.testing.eval.scorers._get_llm_client",
            return_value=None,
        ):
            scorer_fn = combined_scorer()
            result = await scorer_fn(state, target)

        assert result.metadata["interface_choice_score"] == 0.0
        assert "TOGETHER_API_KEY" in result.explanation


@pytest.mark.eval
class TestParityScorer:
    @pytest.mark.anyio
    async def test_no_reference_log(self) -> None:
        state = _make_state(
            messages=[ChatMessageAssistant(content="done")],
            metadata={"input": "test"},
        )
        target = Target("")

        scorer_fn = parity_scorer(reference_log=None)
        result = await scorer_fn(state, target)

        assert result.value == INCORRECT
        assert "skipped" in result.explanation.lower()

    @pytest.mark.anyio
    async def test_missing_log_file(self, tmp_path: Path) -> None:
        state = _make_state(
            messages=[ChatMessageAssistant(content="done")],
            metadata={"input": "test"},
        )
        target = Target("")

        scorer_fn = parity_scorer(reference_log=str(tmp_path / "nonexistent.eval"))
        result = await scorer_fn(state, target)

        assert result.value == INCORRECT
        assert "No matching sample" in result.explanation

    @pytest.mark.anyio
    async def test_with_matching_reference(self, tmp_path: Path) -> None:
        log_file = tmp_path / "ref.eval"
        log_file.write_text(
            json.dumps({"input": "list devices", "response": "Devices: srv1, srv2"}) + "\n"
        )

        state = _make_state(
            messages=[
                ChatMessageUser(content="list devices"),
                ChatMessageAssistant(content="Found: srv1, srv2"),
            ],
            metadata={"input": "list devices"},
        )
        target = Target("")

        with _patch_llm_client():
            scorer_fn = parity_scorer(reference_log=str(log_file))
            result = await scorer_fn(state, target)

        assert result.value == CORRECT
        assert result.metadata["parity_score"] == 0.9

    @pytest.mark.anyio
    async def test_no_api_key_parity(self, tmp_path: Path) -> None:
        log_file = tmp_path / "ref.eval"
        log_file.write_text(json.dumps({"input": "test", "response": "ref answer"}) + "\n")

        state = _make_state(
            messages=[ChatMessageAssistant(content="my answer")],
            metadata={"input": "test"},
        )
        target = Target("")

        with patch(
            "mcp_common.testing.eval.scorers._get_llm_client",
            return_value=None,
        ):
            scorer_fn = parity_scorer(reference_log=str(log_file))
            result = await scorer_fn(state, target)

        assert result.metadata["parity_score"] == 0.0
        assert "TOGETHER_API_KEY" in result.explanation
