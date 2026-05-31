"""Tests for eval scorers: tool_use_scorer, combined_scorer, parity_scorer."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from inspect_ai.model import ChatMessageAssistant, ChatMessageUser
from inspect_ai.scorer import CORRECT, INCORRECT, PARTIAL, Target
from inspect_ai.solver import TaskState
from inspect_ai.tool import ToolCall

from mcp_common.testing.eval.scorers import (
    _DEFAULT_JUDGE_MODEL,
    _TOGETHER_BASE_URL,
    _acceptable_subcommands,
    _build_expected_cli_items,
    _classify,
    _compute_cli_tool_selection_score,
    _compute_tool_selection_score,
    _derive_cli_subcommand,
    _ExpectedCliItem,
    _extract_bash_commands,
    _extract_cli_subcommands,
    _extract_tool_calls,
    _get_final_response,
    _get_llm_client,
    _infer_mcp_name,
    _judge,
    _normalize_expected_command,
    _parse_expected_tools,
    cli_tool_use_scorer,
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

    def test_duplicate_expected_tools(self) -> None:
        """Expected tools with duplicates counted correctly."""
        assert _compute_tool_selection_score(["get_device"], ["get_device", "get_device"]) == 0.5

    def test_duplicate_expected_all_matched(self) -> None:
        """All duplicates matched when called enough times."""
        assert (
            _compute_tool_selection_score(
                ["get_device", "get_device"], ["get_device", "get_device"]
            )
            == 1.0
        )


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
# Tests for unified _judge function
# ---------------------------------------------------------------------------


@pytest.mark.eval
class TestJudge:
    def test_judge_malformed_json(self) -> None:
        """LLM returns non-JSON -> returns (None, reason)."""
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _make_llm_response(
            "I can't evaluate this"
        )

        score, explanation = _judge(mock_client, "test-model", "test prompt")
        assert score is None
        assert "unparseable" in explanation.lower()

    def test_judge_out_of_range_score(self) -> None:
        """LLM returns score > 1.0 -> clamped to 1.0."""
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _make_llm_response(
            json.dumps({"score": 5.0, "explanation": "great"})
        )

        score, explanation = _judge(mock_client, "test-model", "test prompt")
        assert score == 1.0
        assert explanation == "great"

    def test_judge_negative_score(self) -> None:
        """LLM returns score < 0.0 -> clamped to 0.0."""
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _make_llm_response(
            json.dumps({"score": -1.0, "explanation": "bad"})
        )

        score, explanation = _judge(mock_client, "test-model", "test prompt")
        assert score == 0.0
        assert explanation == "bad"

    def test_judge_valid_response(self) -> None:
        """LLM returns well-formed JSON -> parsed correctly."""
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _make_llm_response(
            json.dumps({"score": 0.75, "explanation": "mostly correct"})
        )

        score, explanation = _judge(mock_client, "test-model", "test prompt")
        assert score == 0.75
        assert explanation == "mostly correct"


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
    async def test_raises_without_api_key(self) -> None:
        """Scorer raises RuntimeError when TOGETHER_API_KEY is missing."""
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
            with pytest.raises(RuntimeError, match="TOGETHER_API_KEY"):
                await scorer_fn(state, target)


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
    async def test_raises_without_api_key_combined(self) -> None:
        """Combined scorer raises RuntimeError when TOGETHER_API_KEY is missing."""
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
            with pytest.raises(RuntimeError, match="TOGETHER_API_KEY"):
                await scorer_fn(state, target)


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
        assert (
            "No matching sample" in result.explanation
            or "Reference log not found" in result.explanation
        )

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
    async def test_raises_without_api_key_parity(self, tmp_path: Path) -> None:
        """Parity scorer raises RuntimeError when TOGETHER_API_KEY is missing."""
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
            with pytest.raises(RuntimeError, match="TOGETHER_API_KEY"):
                await scorer_fn(state, target)


# ---------------------------------------------------------------------------
# CLI-aware scorer: naming/mapping helpers
# ---------------------------------------------------------------------------


@pytest.mark.eval
class TestDeriveCliSubcommand:
    def test_lookup_device(self) -> None:
        assert _derive_cli_subcommand("netbox_lookup_device", "netbox") == "lookup-device"

    def test_get_object_by_id(self) -> None:
        assert _derive_cli_subcommand("netbox_get_object_by_id", "netbox") == "get-object-by-id"

    def test_get_objects_by_ids(self) -> None:
        assert _derive_cli_subcommand("netbox_get_objects_by_ids", "netbox") == "get-objects-by-ids"

    def test_oob_summary(self) -> None:
        assert _derive_cli_subcommand("netbox_oob_summary", "netbox") == "oob-summary"

    def test_camel_case_boundary(self) -> None:
        assert _derive_cli_subcommand("netbox_getDevice", "netbox") == "get-device"

    def test_namespace_variants_stripped(self) -> None:
        # server named "netbox-mcp" still strips the namespace
        assert _derive_cli_subcommand("netbox_mcp_list_devices", "netbox-mcp") == "list-devices"

    def test_unprefixed_tool_passes_through(self) -> None:
        assert _derive_cli_subcommand("lookup_device", "redfish") == "lookup-device"

    def test_parity_with_canonical_dual_mode_naming(self) -> None:
        """The self-contained helper must agree with the canonical implementation.

        Guards against silent drift from ``mcp_common.dual_mode._naming`` (which
        the scorer deliberately does not import — see scorers.py module note).
        """
        from mcp_common.dual_mode._naming import derive_cli_name

        names = [
            "netbox_lookup_device",
            "netbox_get_object_by_id",
            "netbox_get_objects_by_ids",
            "netbox_oob_summary",
            "netbox_getDevice",
            "_private_thing",
            "already-kebab",
            "lookup_device",
        ]
        for mcp_name in ("netbox", "netbox-mcp", "redfish", ""):
            for name in names:
                assert _derive_cli_subcommand(name, mcp_name) == derive_cli_name(name, mcp_name)


@pytest.mark.eval
class TestInferMcpName:
    def test_strips_dash_cli(self) -> None:
        assert _infer_mcp_name("netbox-cli") == "netbox"

    def test_strips_underscore_cli(self) -> None:
        assert _infer_mcp_name("foo_cli") == "foo"

    def test_no_suffix(self) -> None:
        assert _infer_mcp_name("mytool") == "mytool"

    def test_path_basename(self) -> None:
        assert _infer_mcp_name("/usr/local/bin/redfish-cli") == "redfish"


# ---------------------------------------------------------------------------
# CLI-aware scorer: bash command extraction + parsing
# ---------------------------------------------------------------------------


@pytest.mark.eval
class TestExtractBashCommands:
    def test_bash_tool_command_key(self) -> None:
        tc = _make_tool_call("bash", {"command": "netbox-cli lookup-device --hostname X"})
        state = _make_state([ChatMessageAssistant(content="run", tool_calls=[tc])])
        assert _extract_bash_commands(state) == ["netbox-cli lookup-device --hostname X"]

    def test_bash_session_input_key(self) -> None:
        tc = _make_tool_call(
            "bash_session",
            {"action": "type_submit", "input": "netbox-cli search foo"},
        )
        state = _make_state([ChatMessageAssistant(content="run", tool_calls=[tc])])
        assert _extract_bash_commands(state) == ["netbox-cli search foo"]

    def test_ignores_non_bash_tools(self) -> None:
        tc = _make_tool_call("netbox_lookup_device", {"hostname": "X"})
        state = _make_state([ChatMessageAssistant(content="run", tool_calls=[tc])])
        assert _extract_bash_commands(state) == []

    def test_skips_empty_input(self) -> None:
        # e.g. a bash_session "read" action carries no command text
        tc = _make_tool_call("bash_session", {"action": "read", "input": None})
        state = _make_state([ChatMessageAssistant(content="run", tool_calls=[tc])])
        assert _extract_bash_commands(state) == []

    def test_collects_multiple_calls(self) -> None:
        tc1 = _make_tool_call("bash", {"command": "netbox-cli devices"})
        tc2 = _make_tool_call("bash", {"command": "netbox-cli search foo"})
        state = _make_state(
            [
                ChatMessageAssistant(content="a", tool_calls=[tc1]),
                ChatMessageAssistant(content="b", tool_calls=[tc2]),
            ]
        )
        assert _extract_bash_commands(state) == ["netbox-cli devices", "netbox-cli search foo"]

    def test_custom_bash_tool_name(self) -> None:
        tc = _make_tool_call("shell", {"command": "netbox-cli devices"})
        state = _make_state([ChatMessageAssistant(content="run", tool_calls=[tc])])
        assert _extract_bash_commands(state, bash_tools=("shell",)) == ["netbox-cli devices"]


@pytest.mark.eval
class TestExtractCliSubcommands:
    def test_simple(self) -> None:
        assert _extract_cli_subcommands("netbox-cli lookup-device --hostname X", "netbox-cli") == [
            "lookup-device"
        ]

    def test_uv_run_prefix(self) -> None:
        assert _extract_cli_subcommands(
            "uv run netbox-cli get-object-by-id dcim.device 4723", "netbox-cli"
        ) == ["get-object-by-id"]

    def test_env_var_prefix(self) -> None:
        assert _extract_cli_subcommands(
            "NETBOX_TOKEN=abc netbox-cli oob-summary a6177514-026", "netbox-cli"
        ) == ["oob-summary"]

    def test_absolute_path_binary(self) -> None:
        assert _extract_cli_subcommands(
            "/usr/local/bin/netbox-cli lookup-device --hostname X", "netbox-cli"
        ) == ["lookup-device"]

    def test_global_flag_before_subcommand(self) -> None:
        assert _extract_cli_subcommands(
            "netbox-cli --json get-objects-by-ids 4723 4724", "netbox-cli"
        ) == ["get-objects-by-ids"]

    def test_chained_commands(self) -> None:
        assert _extract_cli_subcommands(
            "netbox-cli devices --cluster c && netbox-cli search foo", "netbox-cli"
        ) == ["devices", "search"]

    def test_piped_command(self) -> None:
        assert _extract_cli_subcommands(
            "netbox-cli list-objects dcim.device | grep gpu", "netbox-cli"
        ) == ["list-objects"]

    def test_semicolon_separated(self) -> None:
        assert _extract_cli_subcommands(
            "netbox-cli devices; netbox-cli search foo", "netbox-cli"
        ) == ["devices", "search"]

    def test_no_binary(self) -> None:
        assert _extract_cli_subcommands("echo hi && ls -la", "netbox-cli") == []

    def test_bare_binary_no_subcommand(self) -> None:
        assert _extract_cli_subcommands("netbox-cli", "netbox-cli") == []

    def test_only_flag_no_subcommand(self) -> None:
        assert _extract_cli_subcommands("netbox-cli --help", "netbox-cli") == []

    def test_malformed_quotes_fall_back(self) -> None:
        # unbalanced quote -> shlex raises -> naive split -> still finds subcommand
        assert _extract_cli_subcommands(
            'netbox-cli lookup-device --hostname "unterminated', "netbox-cli"
        ) == ["lookup-device"]


@pytest.mark.eval
class TestNormalizeExpectedCommand:
    def test_full_invocation(self) -> None:
        assert _normalize_expected_command("netbox-cli devices --cluster X", "netbox-cli") == (
            "devices"
        )

    def test_binary_plus_subcommand(self) -> None:
        assert _normalize_expected_command("netbox-cli lookup", "netbox-cli") == "lookup"

    def test_bare_subcommand(self) -> None:
        assert _normalize_expected_command("lookup-device", "netbox-cli") == "lookup-device"

    def test_only_flags_returns_none(self) -> None:
        assert _normalize_expected_command("--json", "netbox-cli") is None


# ---------------------------------------------------------------------------
# CLI-aware scorer: expected-item building + scoring
# ---------------------------------------------------------------------------


@pytest.mark.eval
class TestAcceptableSubcommands:
    def test_falls_back_to_kebab_derivation_when_undeclared(self) -> None:
        assert _acceptable_subcommands("netbox_lookup_device", "netbox", None) == ("lookup-device",)

    def test_falls_back_when_tool_absent_from_mapping(self) -> None:
        mapping = {"netbox_get_objects": ["list", "search"]}
        assert _acceptable_subcommands("netbox_lookup_device", "netbox", mapping) == (
            "lookup-device",
        )

    def test_declared_single_alias(self) -> None:
        mapping = {"netbox_get_objects": ["list"]}
        assert _acceptable_subcommands("netbox_get_objects", "netbox", mapping) == ("list",)

    def test_declared_multiple_aliases(self) -> None:
        mapping = {"netbox_get_objects": ["list", "search", "devices"]}
        assert _acceptable_subcommands("netbox_get_objects", "netbox", mapping) == (
            "list",
            "search",
            "devices",
        )

    def test_empty_declaration_falls_back(self) -> None:
        mapping: dict[str, list[str]] = {"netbox_get_objects": []}
        assert _acceptable_subcommands("netbox_get_objects", "netbox", mapping) == ("get-objects",)


@pytest.mark.eval
class TestBuildExpectedCliItems:
    def test_derives_from_expected_tools(self) -> None:
        items = _build_expected_cli_items(
            Target("netbox_lookup_device,netbox_get_object_by_id"),
            metadata=None,
            cli_binary="netbox-cli",
            mcp_name="netbox",
        )
        assert items == [
            _ExpectedCliItem(("lookup-device",), "netbox_lookup_device"),
            _ExpectedCliItem(("get-object-by-id",), "netbox_get_object_by_id"),
        ]

    def test_explicit_commands_take_precedence(self) -> None:
        items = _build_expected_cli_items(
            Target("netbox_lookup_device"),
            metadata={"expected_commands": ["netbox-cli devices --cluster X", "search"]},
            cli_binary="netbox-cli",
            mcp_name="netbox",
        )
        assert items == [
            _ExpectedCliItem(("devices",), None),
            _ExpectedCliItem(("search",), None),
        ]

    def test_declared_subcommands_map_multiple_aliases(self) -> None:
        # netbox_get_objects derives "get-objects" but really runs list/search/devices
        items = _build_expected_cli_items(
            Target("netbox_get_objects"),
            metadata=None,
            cli_binary="netbox-cli",
            mcp_name="netbox",
            tool_subcommands={"netbox_get_objects": ["list", "search", "devices"]},
        )
        assert items == [
            _ExpectedCliItem(("list", "search", "devices"), "netbox_get_objects"),
        ]

    def test_explicit_commands_take_precedence_over_tool_subcommands(self) -> None:
        items = _build_expected_cli_items(
            Target("netbox_get_objects"),
            metadata={"expected_commands": ["netbox-cli devices"]},
            cli_binary="netbox-cli",
            mcp_name="netbox",
            tool_subcommands={"netbox_get_objects": ["list", "search"]},
        )
        assert items == [_ExpectedCliItem(("devices",), None)]

    def test_empty(self) -> None:
        assert _build_expected_cli_items(Target(""), None, "netbox-cli", "netbox") == []


@pytest.mark.eval
class TestComputeCliToolSelectionScore:
    def test_subcommand_match(self) -> None:
        items = [_ExpectedCliItem(("lookup-device",), "netbox_lookup_device")]
        assert _compute_cli_tool_selection_score(items, ["lookup-device"], [], True) == 1.0

    def test_no_match(self) -> None:
        items = [_ExpectedCliItem(("lookup-device",), "netbox_lookup_device")]
        assert _compute_cli_tool_selection_score(items, ["devices"], [], True) == 0.0

    def test_partial_match(self) -> None:
        items = [
            _ExpectedCliItem(("lookup-device",), "netbox_lookup_device"),
            _ExpectedCliItem(("get-object-by-id",), "netbox_get_object_by_id"),
        ]
        assert _compute_cli_tool_selection_score(items, ["lookup-device"], [], True) == 0.5

    def test_any_of_multiple_acceptable_subcommands_credits(self) -> None:
        # one MCP tool -> multiple acceptable CLI subcommands; running ANY counts
        items = [_ExpectedCliItem(("list", "search", "devices"), "netbox_get_objects")]
        assert _compute_cli_tool_selection_score(items, ["search"], [], False) == 1.0
        assert _compute_cli_tool_selection_score(items, ["devices"], [], False) == 1.0
        assert _compute_cli_tool_selection_score(items, ["get-objects"], [], False) == 0.0

    def test_empty_expected_is_vacuously_correct(self) -> None:
        assert _compute_cli_tool_selection_score([], [], [], True) == 1.0

    def test_accepts_mcp_name_when_enabled(self) -> None:
        items = [_ExpectedCliItem(("lookup-device",), "netbox_lookup_device")]
        # agent used the MCP tool directly, ran no CLI command
        assert _compute_cli_tool_selection_score(items, [], ["netbox_lookup_device"], True) == 1.0

    def test_rejects_mcp_name_when_disabled(self) -> None:
        # CLI-only default (accept_mcp_names=False): a hallucinated MCP call with
        # no CLI invocation must NOT be credited (vhspace/mcp-common#133).
        items = [_ExpectedCliItem(("lookup-device",), "netbox_lookup_device")]
        assert _compute_cli_tool_selection_score(items, [], ["netbox_lookup_device"], False) == 0.0


# ---------------------------------------------------------------------------
# CLI-aware scorer: integration (LLM mocked)
# ---------------------------------------------------------------------------


def _cli_state(command: str, *, tool: str = "bash", metadata: dict[str, Any] | None = None):
    """Build a TaskState whose agent ran a single bash CLI command."""
    if tool == "bash_session":
        args: dict[str, Any] = {"action": "type_submit", "input": command}
    else:
        args = {"command": command}
    tc = _make_tool_call(tool, args)
    return _make_state(
        messages=[
            ChatMessageUser(content="do the thing"),
            ChatMessageAssistant(content="running CLI", tool_calls=[tc]),
            ChatMessageAssistant(content="Here is the answer."),
        ],
        metadata=metadata or {"input": "do the thing", "expected_behavior": "answer it"},
    )


@pytest.mark.eval
class TestCliToolUseScorer:
    @pytest.mark.anyio
    async def test_credits_correct_subcommand(self) -> None:
        state = _cli_state("netbox-cli lookup-device --hostname research-common-h100-001")
        target = Target("netbox_lookup_device")

        with _patch_llm_client(completion_score=0.9):
            scorer_fn = cli_tool_use_scorer()
            result = await scorer_fn(state, target)

        assert result.value == CORRECT
        assert result.metadata["tool_selection_score"] == 1.0
        assert result.metadata["task_completion_score"] == 0.9
        assert result.metadata["cli_commands_invoked"] == ["lookup-device"]
        assert result.metadata["expected_cli_commands"] == ["lookup-device"]
        assert result.metadata["cli_binary"] == "netbox-cli"

    @pytest.mark.anyio
    async def test_credits_uv_run_get_object_by_id(self) -> None:
        state = _cli_state("uv run netbox-cli get-object-by-id dcim.device 4723")
        target = Target("netbox_get_object_by_id")

        with _patch_llm_client(completion_score=1.0):
            scorer_fn = cli_tool_use_scorer()
            result = await scorer_fn(state, target)

        assert result.value == CORRECT
        assert result.metadata["tool_selection_score"] == 1.0
        assert result.metadata["cli_commands_invoked"] == ["get-object-by-id"]

    @pytest.mark.anyio
    async def test_bash_session_tool(self) -> None:
        state = _cli_state("netbox-cli oob-summary a6177514-026", tool="bash_session")
        target = Target("netbox_oob_summary")

        with _patch_llm_client(completion_score=0.9):
            scorer_fn = cli_tool_use_scorer()
            result = await scorer_fn(state, target)

        assert result.metadata["tool_selection_score"] == 1.0
        assert result.metadata["cli_commands_invoked"] == ["oob-summary"]

    @pytest.mark.anyio
    async def test_wrong_subcommand_not_credited(self) -> None:
        state = _cli_state("netbox-cli devices --cluster reflection")
        target = Target("netbox_lookup_device")

        with _patch_llm_client(completion_score=0.9):
            scorer_fn = cli_tool_use_scorer()
            result = await scorer_fn(state, target)

        # right answer (completion 0.9) but wrong tool selection -> PARTIAL, not CORRECT
        assert result.metadata["tool_selection_score"] == 0.0
        assert result.value == PARTIAL

    @pytest.mark.anyio
    async def test_generic_bash_not_credited(self) -> None:
        state = _cli_state("echo hello && cat /etc/hosts")
        target = Target("netbox_lookup_device")

        with _patch_llm_client(completion_score=0.2):
            scorer_fn = cli_tool_use_scorer()
            result = await scorer_fn(state, target)

        assert result.metadata["tool_selection_score"] == 0.0
        assert result.metadata["cli_commands_invoked"] == []

    @pytest.mark.anyio
    async def test_multiple_chained_invocations(self) -> None:
        state = _cli_state(
            "netbox-cli lookup-device --hostname X && netbox-cli get-object-by-id dcim.device 1"
        )
        target = Target("netbox_lookup_device,netbox_get_object_by_id")

        with _patch_llm_client(completion_score=0.9):
            scorer_fn = cli_tool_use_scorer()
            result = await scorer_fn(state, target)

        assert result.value == CORRECT
        assert result.metadata["tool_selection_score"] == 1.0
        assert result.metadata["cli_commands_invoked"] == ["lookup-device", "get-object-by-id"]

    @pytest.mark.anyio
    async def test_explicit_expected_commands_metadata(self) -> None:
        state = _cli_state(
            "netbox-cli devices --cluster research-common-h100 --status active --site ORI-TX",
            metadata={
                "input": "list active devices",
                "expected_behavior": "list them",
                "expected_commands": ["netbox-cli devices --cluster"],
            },
        )
        target = Target("")  # cli_specific scenario has no expected_tools

        with _patch_llm_client(completion_score=1.0):
            scorer_fn = cli_tool_use_scorer()
            result = await scorer_fn(state, target)

        assert result.value == CORRECT
        assert result.metadata["tool_selection_score"] == 1.0
        assert result.metadata["expected_cli_commands"] == ["devices"]

    @pytest.mark.anyio
    async def test_accepts_mcp_tool_call_in_combined_mode(self) -> None:
        # agent chose the MCP tool directly instead of the CLI
        tc = _make_tool_call("netbox_lookup_device", {"hostname": "X"})
        state = _make_state(
            messages=[
                ChatMessageUser(content="find device"),
                ChatMessageAssistant(content="using MCP", tool_calls=[tc]),
                ChatMessageAssistant(content="Found it."),
            ],
            metadata={"input": "find device", "expected_behavior": "find it"},
        )
        target = Target("netbox_lookup_device")

        with _patch_llm_client(completion_score=0.9):
            scorer_fn = cli_tool_use_scorer(accept_mcp_names=True)
            result = await scorer_fn(state, target)

        assert result.metadata["tool_selection_score"] == 1.0

        with _patch_llm_client(completion_score=0.9):
            scorer_fn_strict = cli_tool_use_scorer(accept_mcp_names=False)
            result_strict = await scorer_fn_strict(state, target)

        assert result_strict.metadata["tool_selection_score"] == 0.0

    @pytest.mark.anyio
    async def test_configurable_binary(self) -> None:
        state = _cli_state("redfish-cli get-power-state --host bmc1")
        target = Target("redfish_get_power_state")

        with _patch_llm_client(completion_score=0.9):
            scorer_fn = cli_tool_use_scorer(cli_binary="redfish-cli")
            result = await scorer_fn(state, target)

        assert result.metadata["tool_selection_score"] == 1.0
        assert result.metadata["cli_commands_invoked"] == ["get-power-state"]

    @pytest.mark.anyio
    async def test_tool_subcommands_credits_declared_alias(self) -> None:
        # vhspace/netbox-mcp#121: netbox_get_objects derives "get-objects" but is
        # really run as `netbox-cli list`/`search`/`devices`. With the declared
        # mapping, running ANY of them credits tool-selection.
        target = Target("netbox_get_objects")
        mapping = {"netbox_get_objects": ["list", "search", "devices"]}
        for sub in ("list", "search", "devices"):
            state = _cli_state(f"netbox-cli {sub} --site ORI-TX")
            with _patch_llm_client(completion_score=1.0):
                scorer_fn = cli_tool_use_scorer(tool_subcommands=mapping)
                result = await scorer_fn(state, target)
            assert result.metadata["tool_selection_score"] == 1.0, sub
            assert result.value == CORRECT, sub
            assert result.metadata["cli_commands_invoked"] == [sub]
            assert result.metadata["expected_cli_commands"] == ["list", "search", "devices"]

    @pytest.mark.anyio
    async def test_without_mapping_reproduces_kebab_miss(self) -> None:
        # Same scenario WITHOUT the mapping: the derived "get-objects" never
        # matches `netbox-cli search`, so tool-selection is 0 even though the
        # answer is right (the original bug this issue fixes).
        state = _cli_state("netbox-cli search reflection-cluster")
        target = Target("netbox_get_objects")

        with _patch_llm_client(completion_score=1.0):
            scorer_fn = cli_tool_use_scorer()
            result = await scorer_fn(state, target)

        assert result.metadata["tool_selection_score"] == 0.0
        assert result.value == PARTIAL  # right answer, wrong-looking tool selection

        with _patch_llm_client(completion_score=1.0):
            scorer_fn_mapped = cli_tool_use_scorer(
                tool_subcommands={"netbox_get_objects": ["list", "search", "devices"]}
            )
            result_mapped = await scorer_fn_mapped(state, target)

        assert result_mapped.metadata["tool_selection_score"] == 1.0
        assert result_mapped.value == CORRECT

    @pytest.mark.anyio
    async def test_cli_only_default_rejects_hallucinated_mcp_call(self) -> None:
        # vhspace/mcp-common#133: a CLI-only run where the model hallucinated an
        # MCP tool call and ran NO netbox-cli must NOT be credited. With the new
        # default (accept_mcp_names=False) tool-selection is 0.
        tc = _make_tool_call("netbox_lookup_device", {"hostname": "X"})
        state = _make_state(
            messages=[
                ChatMessageUser(content="find device"),
                ChatMessageAssistant(content="calling MCP", tool_calls=[tc]),
                ChatMessageAssistant(content="(no real answer)"),
            ],
            metadata={"input": "find device", "expected_behavior": "find it"},
        )
        target = Target("netbox_lookup_device")

        with _patch_llm_client(completion_score=0.0):
            scorer_fn = cli_tool_use_scorer()  # default: accept_mcp_names=False
            result = await scorer_fn(state, target)

        assert result.metadata["tool_selection_score"] == 0.0
        assert result.metadata["cli_commands_invoked"] == []
        assert result.value == INCORRECT

        # Combined eval opts in -> the same direct MCP call IS credited.
        with _patch_llm_client(completion_score=0.9):
            scorer_fn_combined = cli_tool_use_scorer(accept_mcp_names=True)
            result_combined = await scorer_fn_combined(state, target)

        assert result_combined.metadata["tool_selection_score"] == 1.0

    @pytest.mark.anyio
    async def test_raises_without_api_key(self) -> None:
        state = _cli_state("netbox-cli lookup-device --hostname X")
        target = Target("netbox_lookup_device")

        with patch(
            "mcp_common.testing.eval.scorers._get_llm_client",
            return_value=None,
        ):
            scorer_fn = cli_tool_use_scorer()
            with pytest.raises(RuntimeError, match="TOGETHER_API_KEY"):
                await scorer_fn(state, target)


# ---------------------------------------------------------------------------
# Judge credential / endpoint decoupling (vhspace/mcp-common#132)
# ---------------------------------------------------------------------------


@pytest.mark.eval
class TestGetLlmClientCredentials:
    """The judge can run on a separate key/endpoint, falling back to the model's."""

    def _clear_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for var in (
            "TOGETHER_API_KEY",
            "EVAL_JUDGE_API_KEY",
            "EVAL_JUDGE_BASE_URL",
            "EVAL_JUDGE_MODEL",
        ):
            monkeypatch.delenv(var, raising=False)

    def test_falls_back_to_together_key_and_default_base_url(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._clear_env(monkeypatch)
        monkeypatch.setenv("TOGETHER_API_KEY", "together-key")

        with patch("openai.OpenAI") as mock_openai:
            result = _get_llm_client()

        assert result is not None
        client, model = result
        assert client is mock_openai.return_value
        assert model == _DEFAULT_JUDGE_MODEL
        kwargs = mock_openai.call_args.kwargs
        assert kwargs["api_key"] == "together-key"
        assert kwargs["base_url"] == _TOGETHER_BASE_URL

    def test_uses_judge_key_and_base_url_when_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._clear_env(monkeypatch)
        # Model-under-test creds are present, but the judge overrides take priority.
        monkeypatch.setenv("TOGETHER_API_KEY", "model-key")
        monkeypatch.setenv("EVAL_JUDGE_API_KEY", "judge-key")
        monkeypatch.setenv("EVAL_JUDGE_BASE_URL", "https://judge.internal/v1")

        with patch("openai.OpenAI") as mock_openai:
            result = _get_llm_client()

        assert result is not None
        kwargs = mock_openai.call_args.kwargs
        assert kwargs["api_key"] == "judge-key"
        assert kwargs["base_url"] == "https://judge.internal/v1"

    def test_judge_key_works_without_together_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._clear_env(monkeypatch)
        monkeypatch.setenv("EVAL_JUDGE_API_KEY", "judge-only")

        with patch("openai.OpenAI") as mock_openai:
            result = _get_llm_client()

        assert result is not None
        kwargs = mock_openai.call_args.kwargs
        assert kwargs["api_key"] == "judge-only"
        assert kwargs["base_url"] == _TOGETHER_BASE_URL

    def test_judge_base_url_alone_keeps_together_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._clear_env(monkeypatch)
        monkeypatch.setenv("TOGETHER_API_KEY", "together-key")
        monkeypatch.setenv("EVAL_JUDGE_BASE_URL", "https://judge.internal/v1")

        with patch("openai.OpenAI") as mock_openai:
            result = _get_llm_client()

        assert result is not None
        kwargs = mock_openai.call_args.kwargs
        assert kwargs["api_key"] == "together-key"
        assert kwargs["base_url"] == "https://judge.internal/v1"

    def test_eval_judge_model_override_still_applies(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._clear_env(monkeypatch)
        monkeypatch.setenv("TOGETHER_API_KEY", "together-key")
        monkeypatch.setenv("EVAL_JUDGE_MODEL", "custom/Judge-Model")

        with patch("openai.OpenAI"):
            result = _get_llm_client()

        assert result is not None
        _client, model = result
        assert model == "custom/Judge-Model"

    def test_returns_none_without_any_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._clear_env(monkeypatch)
        assert _get_llm_client() is None
