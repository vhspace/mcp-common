"""Tests for tool description quality checks."""

from __future__ import annotations

import json
import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest
from fastmcp import FastMCP

from mcp_common.testing.eval.description_qa import (
    DescriptionIssue,
    LLMDescriptionScore,
    SimilarityConflict,
    _build_llm_prompt,
    _check_tool,
    _llm_evaluate_description,
    check_description_quality,
    check_description_quality_llm,
    check_similarity_conflicts,
)


def _make_server(name: str = "test") -> FastMCP:
    return FastMCP(name)


def _register_server_module(server: FastMCP, module_name: str) -> None:
    """Insert a fake module into ``sys.modules`` so import machinery finds it."""
    mod = types.ModuleType(module_name)
    mod.mcp = server  # type: ignore[attr-defined]
    sys.modules[module_name] = mod


def _cleanup_module(module_name: str) -> None:
    sys.modules.pop(module_name, None)


# ---------------------------------------------------------------------------
# _check_tool heuristic tests
# ---------------------------------------------------------------------------


@pytest.mark.eval
class TestCheckTool:
    def test_good_description_no_issues(self) -> None:
        desc = (
            "Look up a device by hostname and return its rack location. "
            "Accepts the hostname parameter. "
            "Returns a JSON object with rack, unit, and site fields. "
            "Raises an error if the device is not found."
        )
        issues = _check_tool("srv", "lookup_device", desc, ["hostname"])
        assert issues == []

    def test_too_vague_short_description(self) -> None:
        issues = _check_tool("srv", "do_thing", "Does stuff.", ["x"])
        types = {i.issue_type for i in issues}
        assert "too_vague" in types

    def test_too_long_description(self) -> None:
        desc = "x " * 300
        issues = _check_tool("srv", "wordy", desc, [])
        types = {i.issue_type for i in issues}
        assert "too_long" in types

    def test_missing_parameters(self) -> None:
        desc = (
            "Fetch all records from the database and return them as JSON. "
            "Raises an error if connection fails."
        )
        issues = _check_tool("srv", "fetch", desc, ["table_name", "limit"])
        types = {i.issue_type for i in issues}
        assert "missing_parameters" in types

    def test_parameter_mentioned_case_insensitive(self) -> None:
        desc = (
            "Fetch records from TABLE_NAME and return them as JSON. "
            "Raises an error if the table is not found."
        )
        issues = _check_tool("srv", "fetch", desc, ["table_name"])
        types = {i.issue_type for i in issues}
        assert "missing_parameters" not in types

    def test_no_params_skips_parameter_check(self) -> None:
        desc = (
            "Return the current server time as an ISO-8601 string. "
            "Raises an error if the clock is unavailable."
        )
        issues = _check_tool("srv", "now", desc, [])
        types = {i.issue_type for i in issues}
        assert "missing_parameters" not in types

    def test_missing_error_info(self) -> None:
        desc = "Look up a hostname and return its IP address. Accepts the hostname parameter."
        issues = _check_tool("srv", "resolve", desc, ["hostname"])
        types = {i.issue_type for i in issues}
        assert "missing_error_info" in types

    def test_missing_return_info(self) -> None:
        desc = (
            "Delete the specified device from inventory. "
            "Accepts device_id. Raises an error if device_id is invalid."
        )
        issues = _check_tool("srv", "delete_device", desc, ["device_id"])
        types = {i.issue_type for i in issues}
        assert "missing_return_info" in types

    def test_fully_qualified_name(self) -> None:
        issues = _check_tool("MyServer", "my_tool", "hi", [])
        assert all(i.tool_name == "MyServer.my_tool" for i in issues)

    def test_score_between_zero_and_one(self) -> None:
        issues = _check_tool("s", "t", "", ["x"])
        for issue in issues:
            assert 0.0 <= issue.score <= 1.0


# ---------------------------------------------------------------------------
# check_description_quality integration tests
# ---------------------------------------------------------------------------

_GOOD_MODULE = "_test_desc_qa_good"
_BAD_MODULE = "_test_desc_qa_bad"


@pytest.fixture(autouse=False)
def good_server() -> FastMCP:
    server = _make_server("GoodServer")

    @server.tool()
    def healthy_tool(hostname: str) -> str:
        """Look up a device by hostname and return its rack location.

        Accepts the hostname parameter.
        Returns a JSON object with rack, unit, and site fields.
        Raises an error if the device is not found.
        """
        return hostname

    _register_server_module(server, _GOOD_MODULE)
    yield server  # type: ignore[misc]
    _cleanup_module(_GOOD_MODULE)


@pytest.fixture(autouse=False)
def bad_server() -> FastMCP:
    server = _make_server("BadServer")

    @server.tool()
    def bad_tool(x: int) -> str:
        """Does stuff."""
        return str(x)

    _register_server_module(server, _BAD_MODULE)
    yield server  # type: ignore[misc]
    _cleanup_module(_BAD_MODULE)


@pytest.mark.eval
class TestCheckDescriptionQuality:
    def test_good_server_no_issues(self, good_server: FastMCP) -> None:
        issues = check_description_quality(_GOOD_MODULE)
        assert issues == []

    def test_bad_server_returns_issues(self, bad_server: FastMCP) -> None:
        issues = check_description_quality(_BAD_MODULE)
        assert len(issues) > 0
        types = {i.issue_type for i in issues}
        assert "too_vague" in types

    def test_returns_description_issue_models(self, bad_server: FastMCP) -> None:
        issues = check_description_quality(_BAD_MODULE)
        assert all(isinstance(i, DescriptionIssue) for i in issues)


# ---------------------------------------------------------------------------
# check_similarity_conflicts tests
# ---------------------------------------------------------------------------

_SIM_A = "_test_desc_qa_sim_a"
_SIM_B = "_test_desc_qa_sim_b"
_SIM_C = "_test_desc_qa_sim_c"


@pytest.fixture()
def similar_servers() -> tuple[FastMCP, FastMCP]:
    """Two servers with near-identical tool descriptions."""
    a = _make_server("ServerA")
    b = _make_server("ServerB")

    @a.tool()
    def list_devices(site: str) -> str:
        """List all devices at a given site and return them as JSON.

        Accepts the site parameter.
        Returns a list of device objects.
        Raises an error if the site is not found.
        """
        return site

    @b.tool()
    def list_devices_b(site: str) -> str:
        """List all devices at a given site and return them as JSON.

        Accepts the site parameter.
        Returns a list of device objects.
        Raises an error if the site is not found.
        """
        return site

    _register_server_module(a, _SIM_A)
    _register_server_module(b, _SIM_B)
    yield a, b  # type: ignore[misc]
    _cleanup_module(_SIM_A)
    _cleanup_module(_SIM_B)


@pytest.fixture()
def dissimilar_servers() -> tuple[FastMCP, FastMCP]:
    """Two servers with completely different tool descriptions."""
    a = _make_server("Alpha")
    b = _make_server("Beta")

    @a.tool()
    def reboot_machine(hostname: str) -> str:
        """Power-cycle a bare-metal machine via its BMC.

        Accepts the hostname parameter.
        Returns confirmation with the new power state.
        Raises an error if the BMC is unreachable.
        """
        return hostname

    @b.tool()
    def create_filesystem(name: str) -> str:
        """Provision a new Weka filesystem with the given name.

        Accepts the name parameter.
        Returns the filesystem ID and mount path.
        Raises an error if a filesystem with that name already exists.
        """
        return name

    _register_server_module(a, _SIM_A)
    _register_server_module(b, _SIM_C)
    yield a, b  # type: ignore[misc]
    _cleanup_module(_SIM_A)
    _cleanup_module(_SIM_C)


@pytest.mark.eval
class TestCheckSimilarityConflicts:
    def test_identical_descriptions_flagged(self, similar_servers: tuple[FastMCP, FastMCP]) -> None:
        conflicts = check_similarity_conflicts([_SIM_A, _SIM_B])
        assert len(conflicts) >= 1
        assert all(isinstance(c, SimilarityConflict) for c in conflicts)
        assert conflicts[0].similarity > 0.6

    def test_dissimilar_descriptions_no_conflict(
        self, dissimilar_servers: tuple[FastMCP, FastMCP]
    ) -> None:
        conflicts = check_similarity_conflicts([_SIM_A, _SIM_C])
        assert conflicts == []

    def test_single_server_no_conflicts(self, good_server: FastMCP) -> None:
        conflicts = check_similarity_conflicts([_GOOD_MODULE])
        assert conflicts == []

    def test_conflict_has_score_field(self, similar_servers: tuple[FastMCP, FastMCP]) -> None:
        conflicts = check_similarity_conflicts([_SIM_A, _SIM_B])
        assert len(conflicts) >= 1
        for c in conflicts:
            assert 0.0 <= c.score <= 1.0
            assert c.score == c.similarity

    def test_empty_input(self) -> None:
        conflicts = check_similarity_conflicts([])
        assert conflicts == []


# ---------------------------------------------------------------------------
# LLM scoring helpers
# ---------------------------------------------------------------------------

_MOCK_LLM_RESPONSE = {
    "tool_name": "srv.lookup_device",
    "clarity": 9,
    "completeness": 8,
    "conciseness": 7,
    "disambiguity": 8,
    "overall_score": 8.0,
    "verdict": "good",
    "suggested_improvement": "",
    "explanation": "Clear, well-structured description with good parameter coverage.",
}


def _make_openai_response(payload: dict[str, object]) -> MagicMock:
    """Build a mock that mimics ``openai.ChatCompletion`` response shape."""
    message = MagicMock()
    message.content = json.dumps(payload)
    choice = MagicMock()
    choice.message = message
    response = MagicMock()
    response.choices = [choice]
    return response


# ---------------------------------------------------------------------------
# _build_llm_prompt tests
# ---------------------------------------------------------------------------


@pytest.mark.eval
class TestBuildLLMPrompt:
    def test_contains_tool_name(self) -> None:
        prompt = _build_llm_prompt("my_tool", "Does things", {"properties": {"x": {"type": "int"}}})
        assert "my_tool" in prompt

    def test_contains_description(self) -> None:
        prompt = _build_llm_prompt("t", "Fetches records from DB", {})
        assert "Fetches records from DB" in prompt

    def test_empty_description_placeholder(self) -> None:
        prompt = _build_llm_prompt("t", "", {})
        assert "(empty)" in prompt

    def test_empty_schema_placeholder(self) -> None:
        prompt = _build_llm_prompt("t", "desc", {})
        assert "(no parameters)" in prompt

    def test_schema_serialized_as_json(self) -> None:
        schema = {"properties": {"hostname": {"type": "string"}}}
        prompt = _build_llm_prompt("t", "desc", schema)
        assert '"hostname"' in prompt


# ---------------------------------------------------------------------------
# _llm_evaluate_description tests
# ---------------------------------------------------------------------------


@pytest.mark.eval
class TestLLMEvaluateDescription:
    def test_returns_valid_score(self) -> None:
        mock_response = _make_openai_response(_MOCK_LLM_RESPONSE)
        with patch("openai.OpenAI") as mock_cls:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response
            mock_cls.return_value = mock_client

            score = _llm_evaluate_description(
                "srv.lookup_device",
                "Look up a device by hostname.",
                {"properties": {"hostname": {"type": "string"}}},
                api_key="fake-key",
            )

        assert isinstance(score, LLMDescriptionScore)
        assert score.tool_name == "srv.lookup_device"
        assert score.clarity == 9
        assert score.verdict == "good"

    def test_passes_model_to_client(self) -> None:
        mock_response = _make_openai_response(_MOCK_LLM_RESPONSE)
        with patch("openai.OpenAI") as mock_cls:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response
            mock_cls.return_value = mock_client

            _llm_evaluate_description("t", "desc", {}, api_key="fake-key", model="test-model")

            call_kwargs = mock_client.chat.completions.create.call_args.kwargs
            assert call_kwargs["model"] == "test-model"

    def test_raises_without_api_key(self) -> None:
        with patch.dict("os.environ", {}, clear=False):
            os.environ.pop("TOGETHER_API_KEY", None)
            with pytest.raises(RuntimeError, match="TOGETHER_API_KEY"):
                _llm_evaluate_description("t", "desc", {})

    def test_uses_json_response_format(self) -> None:
        mock_response = _make_openai_response(_MOCK_LLM_RESPONSE)
        with patch("openai.OpenAI") as mock_cls:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response
            mock_cls.return_value = mock_client

            _llm_evaluate_description("t", "desc", {}, api_key="fake-key")

            call_kwargs = mock_client.chat.completions.create.call_args.kwargs
            assert call_kwargs["response_format"] == {"type": "json_object"}

    def test_tool_name_forced_from_argument(self) -> None:
        """Even if the LLM returns a different tool_name, the argument wins."""
        altered = {**_MOCK_LLM_RESPONSE, "tool_name": "wrong.name"}
        mock_response = _make_openai_response(altered)
        with patch("openai.OpenAI") as mock_cls:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response
            mock_cls.return_value = mock_client

            score = _llm_evaluate_description("correct.name", "desc", {}, api_key="fake-key")
        assert score.tool_name == "correct.name"


# ---------------------------------------------------------------------------
# check_description_quality_llm integration tests
# ---------------------------------------------------------------------------


@pytest.mark.eval
class TestCheckDescriptionQualityLLM:
    def test_returns_scores_for_each_tool(self, good_server: FastMCP) -> None:
        mock_response = _make_openai_response(_MOCK_LLM_RESPONSE)
        with patch("openai.OpenAI") as mock_cls:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response
            mock_cls.return_value = mock_client

            scores = check_description_quality_llm(_GOOD_MODULE, api_key="fake-key")

        assert len(scores) == 1
        assert isinstance(scores[0], LLMDescriptionScore)

    def test_skips_when_no_api_key(self, good_server: FastMCP) -> None:
        with patch.dict("os.environ", {}, clear=False):
            os.environ.pop("TOGETHER_API_KEY", None)
            scores = check_description_quality_llm(_GOOD_MODULE)
        assert scores == []

    def test_passes_custom_model(self, good_server: FastMCP) -> None:
        mock_response = _make_openai_response(_MOCK_LLM_RESPONSE)
        with patch("openai.OpenAI") as mock_cls:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response
            mock_cls.return_value = mock_client

            check_description_quality_llm(_GOOD_MODULE, api_key="fake-key", model="custom/model")

            call_kwargs = mock_client.chat.completions.create.call_args.kwargs
            assert call_kwargs["model"] == "custom/model"

    def test_scores_have_valid_ranges(self, bad_server: FastMCP) -> None:
        poor_response = {
            **_MOCK_LLM_RESPONSE,
            "clarity": 2,
            "completeness": 1,
            "conciseness": 3,
            "disambiguity": 2,
            "overall_score": 2.0,
            "verdict": "poor",
            "tool_name": "BadServer.bad_tool",
        }
        mock_response = _make_openai_response(poor_response)
        with patch("openai.OpenAI") as mock_cls:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response
            mock_cls.return_value = mock_client

            scores = check_description_quality_llm(_BAD_MODULE, api_key="fake-key")

        assert len(scores) == 1
        s = scores[0]
        assert 0 <= s.clarity <= 10
        assert 0 <= s.completeness <= 10
        assert 0 <= s.conciseness <= 10
        assert 0 <= s.disambiguity <= 10
        assert 0.0 <= s.overall_score <= 10.0
