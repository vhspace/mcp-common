"""Tests for eval dataset loading and the Scenario model."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from mcp_common.testing.eval.datasets import (
    Scenario,
    load_scenarios,
    scenario_to_sample,
    scenarios_to_dataset,
)


@pytest.mark.eval
class TestScenarioModel:
    def test_minimal_scenario(self) -> None:
        s = Scenario(input="List all devices")
        assert s.input == "List all devices"

    def test_defaults(self) -> None:
        s = Scenario(input="test prompt")
        assert s.expected_tools == []
        assert s.expected_commands == []
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


@pytest.mark.eval
class TestScenarioToSample:
    def test_input_and_target(self) -> None:
        s = Scenario(input="find srv1", expected_tools=["get_device", "list_ips"])
        sample = scenario_to_sample(s)
        assert sample.input == "find srv1"
        # target is the comma-separated form `_parse_expected_tools` consumes
        assert sample.target == "get_device,list_ips"

    def test_target_empty_when_no_tools(self) -> None:
        sample = scenario_to_sample(Scenario(input="x"))
        assert sample.target == ""

    def test_metadata_forwards_all_fields(self) -> None:
        s = Scenario(
            input="list active devices",
            expected_tools=["netbox_get_objects"],
            expected_commands=["netbox-cli list", "netbox-cli search"],
            expected_behavior="lists them",
            mode="cli",
            tags=["inventory"],
        )
        sample = scenario_to_sample(s)
        assert sample.metadata == {
            "input": "list active devices",
            "expected_tools": ["netbox_get_objects"],
            "expected_commands": ["netbox-cli list", "netbox-cli search"],
            "expected_behavior": "lists them",
            "mode": "cli",
            "tags": ["inventory"],
        }

    def test_metadata_carries_expected_commands(self) -> None:
        # The exact field a hand-rolled loader dropped (#133): the scorer reads
        # state.metadata["expected_commands"], so it must survive the conversion.
        s = Scenario(
            input="list devices in cluster",
            expected_commands=["netbox-cli devices --cluster X"],
        )
        sample = scenario_to_sample(s)
        assert sample.metadata["expected_commands"] == ["netbox-cli devices --cluster X"]

    def test_metadata_has_input_for_judge(self) -> None:
        # scorers read state.metadata["input"] for the LLM judge prompt
        sample = scenario_to_sample(Scenario(input="the prompt"))
        assert sample.metadata["input"] == "the prompt"


@pytest.mark.eval
class TestScenariosToDataset:
    def test_returns_memory_dataset_with_all_samples(self) -> None:
        from inspect_ai.dataset import MemoryDataset

        scenarios = [Scenario(input="a"), Scenario(input="b")]
        ds = scenarios_to_dataset(scenarios)
        assert isinstance(ds, MemoryDataset)
        assert [s.input for s in ds.samples] == ["a", "b"]

    def test_mode_filter_keeps_matching(self) -> None:
        scenarios = [
            Scenario(input="cli-only", mode="cli"),
            Scenario(input="mcp-only", mode="mcp"),
            Scenario(input="both", mode="both"),
        ]
        ds = scenarios_to_dataset(scenarios, mode_filter={"cli", "both"})
        assert [s.input for s in ds.samples] == ["cli-only", "both"]

    def test_no_filter_includes_everything(self) -> None:
        scenarios = [Scenario(input="cli", mode="cli"), Scenario(input="mcp", mode="mcp")]
        ds = scenarios_to_dataset(scenarios)
        assert len(ds.samples) == 2

    def test_empty(self) -> None:
        ds = scenarios_to_dataset([])
        assert list(ds.samples) == []

    def test_name_forwarded(self) -> None:
        ds = scenarios_to_dataset([Scenario(input="a")], name="netbox-cli")
        assert ds.name == "netbox-cli"

    def test_samples_forward_expected_commands(self) -> None:
        scenarios = [
            Scenario(input="list", expected_commands=["netbox-cli list"], mode="cli"),
        ]
        ds = scenarios_to_dataset(scenarios, mode_filter={"cli", "both"})
        assert ds.samples[0].metadata["expected_commands"] == ["netbox-cli list"]
