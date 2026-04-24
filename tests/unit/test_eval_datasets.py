"""Tests for eval dataset loading and the Scenario model."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from mcp_common.testing.eval.datasets import Scenario, load_scenarios


@pytest.mark.eval
class TestScenarioModel:
    def test_minimal_scenario(self) -> None:
        s = Scenario(input="List all devices")
        assert s.input == "List all devices"

    def test_defaults(self) -> None:
        s = Scenario(input="test prompt")
        assert s.expected_tools == []
        assert s.expected_behavior == ""
        assert s.mode == "both"
        assert s.tags == []

    def test_full_scenario(self) -> None:
        s = Scenario(
            input="Restart the server",
            expected_tools=["restart_server"],
            expected_behavior="Server restarts cleanly",
            mode="mcp",
            tags=["happy_path"],
        )
        assert s.expected_tools == ["restart_server"]
        assert s.expected_behavior == "Server restarts cleanly"
        assert s.mode == "mcp"
        assert s.tags == ["happy_path"]

    def test_missing_input_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError):
            Scenario.model_validate({})

    def test_invalid_mode_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError):
            Scenario(input="x", mode="invalid")  # type: ignore[arg-type]


@pytest.mark.eval
class TestLoadScenarios:
    def test_happy_path(self, tmp_path: Path) -> None:
        data = [
            {"input": "Check GPU health", "expected_tools": ["gpu_status"]},
            {"input": "List nodes", "tags": ["inventory"]},
        ]
        f = tmp_path / "scenarios.json"
        f.write_text(json.dumps(data))

        result = load_scenarios(f)

        assert len(result) == 2
        assert all(isinstance(s, Scenario) for s in result)
        assert result[0].input == "Check GPU health"
        assert result[0].expected_tools == ["gpu_status"]
        assert result[1].tags == ["inventory"]

    def test_accepts_str_path(self, tmp_path: Path) -> None:
        f = tmp_path / "s.json"
        f.write_text(json.dumps([{"input": "ping"}]))

        result = load_scenarios(str(f))

        assert len(result) == 1
        assert result[0].input == "ping"

    def test_accepts_path_object(self, tmp_path: Path) -> None:
        f = tmp_path / "s.json"
        f.write_text(json.dumps([{"input": "ping"}]))

        result = load_scenarios(f)

        assert len(result) == 1

    def test_empty_list(self, tmp_path: Path) -> None:
        f = tmp_path / "empty.json"
        f.write_text("[]")

        result = load_scenarios(f)

        assert result == []

    def test_missing_required_field_raises(self, tmp_path: Path) -> None:
        f = tmp_path / "bad.json"
        f.write_text(json.dumps([{"tags": ["oops"]}]))

        with pytest.raises(ValidationError):
            load_scenarios(f)
