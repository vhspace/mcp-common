"""Tests for CLI output helpers."""

from __future__ import annotations

import json
import re
import sys
from typing import Any

import pytest
import typer
from typer.testing import CliRunner

from mcp_common.cli import JsonOption, PaginatedFormatter, echo_result, should_emit_json

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(s: str) -> str:
    """Remove ANSI SGR escape sequences so substring assertions work
    regardless of whether Typer's Rich help renderer split a token
    (e.g. ``--json``) into separately-styled runs under a color-capable
    ``TERM`` such as CI's ``xterm-256color``.
    """
    return _ANSI_RE.sub("", s)


class TestShouldEmitJson:
    def test_explicit_json_always_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
        assert should_emit_json(True) is True

    def test_tty_without_flag_is_human(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
        assert should_emit_json(False) is False

    def test_piped_without_flag_is_json(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
        assert should_emit_json(False) is True

    def test_piped_with_explicit_flag_is_json(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
        assert should_emit_json(True) is True


class TestEchoResultJsonMode:
    def test_json_output_is_valid_and_round_trips(self, capsys: pytest.CaptureFixture[str]) -> None:
        data = {"name": "foo", "count": 3, "nested": {"a": [1, 2, 3]}}
        echo_result(data, as_json=True)
        out = capsys.readouterr().out
        assert json.loads(out) == data

    def test_json_output_pretty_indented(self, capsys: pytest.CaptureFixture[str]) -> None:
        echo_result({"a": 1}, as_json=True)
        out = capsys.readouterr().out
        assert '"a": 1' in out
        assert "\n" in out

    def test_json_mode_ignores_human_formatter(self, capsys: pytest.CaptureFixture[str]) -> None:
        echo_result(
            {"x": 1},
            as_json=True,
            human_formatter=lambda d: "HUMAN FORMAT WINS",
        )
        out = capsys.readouterr().out
        assert "HUMAN FORMAT" not in out
        assert json.loads(out) == {"x": 1}

    def test_json_mode_ignores_title(self, capsys: pytest.CaptureFixture[str]) -> None:
        echo_result({"x": 1}, as_json=True, title="MY TITLE")
        out = capsys.readouterr().out
        assert "MY TITLE" not in out

    def test_json_mode_serializes_non_json_via_default_str(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from pathlib import Path

        echo_result({"path": Path("/tmp/foo")}, as_json=True)
        out = capsys.readouterr().out
        parsed = json.loads(out)
        assert parsed["path"] == "/tmp/foo"

    def test_json_mode_keys_are_sorted(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Agents pattern-match on field order; output must be deterministic."""
        echo_result({"z": 1, "a": 2, "m": 3}, as_json=True)
        out = capsys.readouterr().out
        keys_in_order = [
            line.strip().split(":", 1)[0].strip('"') for line in out.splitlines() if ":" in line
        ]
        assert keys_in_order == ["a", "m", "z"]


class TestEchoResultPydanticSerialization:
    """``echo_result(as_json=True)`` must serialize Pydantic models as JSON objects.

    FastMCP tools commonly return Pydantic models, so the JSON-mode rendering
    has to dump them via ``model_dump(mode="json")`` rather than ``str()``
    (which would emit the ``"name='foo' field='bar'"`` repr form).
    """

    def test_pydantic_v2_model_serializes_as_json_object(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        import pydantic

        class Device(pydantic.BaseModel):
            name: str
            ports: int

        echo_result(Device(name="sw01", ports=48), as_json=True)
        out = capsys.readouterr().out
        parsed = json.loads(out)
        assert parsed == {"name": "sw01", "ports": 48}

    def test_nested_pydantic_models_serialize_recursively(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        import pydantic

        class Site(pydantic.BaseModel):
            slug: str

        class Device(pydantic.BaseModel):
            name: str
            site: Site

        echo_result(Device(name="sw01", site=Site(slug="dc1")), as_json=True)
        out = capsys.readouterr().out
        parsed = json.loads(out)
        assert parsed == {"name": "sw01", "site": {"slug": "dc1"}}

    def test_pydantic_model_inside_container_serializes(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        import pydantic

        class Device(pydantic.BaseModel):
            name: str

        echo_result({"results": [Device(name="a"), Device(name="b")]}, as_json=True)
        out = capsys.readouterr().out
        parsed = json.loads(out)
        assert parsed == {"results": [{"name": "a"}, {"name": "b"}]}

    def test_non_pydantic_object_falls_back_to_str(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        class Plain:
            def __str__(self) -> str:
                return "plain-repr"

        echo_result({"obj": Plain()}, as_json=True)
        out = capsys.readouterr().out
        parsed = json.loads(out)
        assert parsed == {"obj": "plain-repr"}

    def test_non_callable_model_dump_attr_does_not_crash(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Edge case: object exposes ``model_dump`` as data, not a method."""

        class Decoy:
            model_dump = "not-callable"

            def __str__(self) -> str:
                return "decoy-str"

        echo_result({"obj": Decoy()}, as_json=True)
        out = capsys.readouterr().out
        parsed = json.loads(out)
        assert parsed == {"obj": "decoy-str"}


class TestEchoResultHumanMode:
    def test_human_formatter_used(self, capsys: pytest.CaptureFixture[str]) -> None:
        echo_result(
            {"name": "device-1"},
            as_json=False,
            human_formatter=lambda d: f"Name: {d['name']}",
        )
        out = capsys.readouterr().out
        assert "Name: device-1" in out

    def test_no_formatter_falls_back_to_str(self, capsys: pytest.CaptureFixture[str]) -> None:
        echo_result({"a": 1}, as_json=False)
        out = capsys.readouterr().out
        assert "{'a': 1}" in out

    def test_title_rendered_above_body(self, capsys: pytest.CaptureFixture[str]) -> None:
        echo_result(
            {"name": "x"},
            as_json=False,
            title="Devices",
            human_formatter=lambda d: d["name"],
        )
        out = capsys.readouterr().out
        lines = [line for line in out.splitlines() if line]
        assert len(lines) == 2
        assert "Devices" in lines[0]
        assert lines[1] == "x"

    def test_title_omitted_when_no_title(self, capsys: pytest.CaptureFixture[str]) -> None:
        echo_result("body-only", as_json=False)
        out = capsys.readouterr().out
        lines = [line for line in out.splitlines() if line]
        assert lines == ["body-only"]


class TestEchoResultTruncate:
    """Truncation is a HUMAN-mode-only readability affordance (#113).

    JSON mode is exercised by :class:`TestEchoResultJsonNeverTruncates`;
    these cases pin the human-mode behavior that must stay unchanged.
    """

    def test_truncate_long_human_string(self, capsys: pytest.CaptureFixture[str]) -> None:
        long_payload = {"data": "x" * 10000}
        echo_result(long_payload, as_json=False, truncate=200)
        out = capsys.readouterr().out
        assert "more chars" in out
        assert "…" in out

    def test_default_truncate_applies_in_human_mode(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        body = "y" * 5000
        echo_result(body, as_json=False)  # default truncate=4096
        out = capsys.readouterr().out
        assert "… (904 more chars)" in out

    def test_truncate_zero_disables(self, capsys: pytest.CaptureFixture[str]) -> None:
        long_payload = {"data": "x" * 10000}
        echo_result(long_payload, as_json=False, truncate=0)
        out = capsys.readouterr().out
        assert "more chars" not in out
        assert "…" not in out

    def test_truncate_does_not_affect_short_output(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        echo_result({"a": 1}, as_json=True, truncate=4096)
        out = capsys.readouterr().out
        assert json.loads(out) == {"a": 1}
        assert "more chars" not in out

    def test_truncate_drops_correct_count(self, capsys: pytest.CaptureFixture[str]) -> None:
        body = "abc" * 100
        echo_result(body, as_json=False, truncate=10)
        out = capsys.readouterr().out.rstrip("\n")
        assert "more chars" in out
        dropped = len(body) - 10
        assert f"({dropped} more chars)" in out


class TestEchoResultJsonNeverTruncates:
    """JSON output is always complete and ``json.loads``-able (#113).

    Truncating JSON would clip it mid-structure and corrupt it, breaking
    ``--json`` for every ``@dual_mode_tool`` synthesized command whose
    output exceeds ``truncate``. JSON completeness always wins over the
    ``truncate`` argument.
    """

    def test_large_json_payload_is_complete_and_round_trips(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # >4096 bytes of JSON rendered under the default truncate=4096.
        big = [{"i": i, "pad": "x" * 200} for i in range(30)]
        echo_result(big, as_json=True)
        out = capsys.readouterr().out
        assert len(out) > 4096
        assert "more chars" not in out
        assert "…" not in out
        assert json.loads(out) == big

    def test_explicit_small_truncate_ignored_in_json_mode(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # An explicit tiny truncate must NOT clip JSON — completeness wins.
        data = {"name": "foo", "count": 3, "nested": {"a": [1, 2, 3]}}
        echo_result(data, as_json=True, truncate=10)
        out = capsys.readouterr().out
        assert "more chars" not in out
        assert json.loads(out) == data


class TestPaginatedFormatter:
    def test_formats_count_and_lines(self) -> None:
        formatter = PaginatedFormatter(lambda d: f"- {d['name']}")
        out = formatter({"count": 2, "results": [{"name": "a"}, {"name": "b"}]})
        assert out == "2 results\n- a\n- b"

    def test_singular_count_label(self) -> None:
        formatter = PaginatedFormatter(lambda d: d["name"])
        out = formatter({"count": 1, "results": [{"name": "only"}]})
        assert out.startswith("1 result\n")
        assert " results" not in out.splitlines()[0]

    def test_zero_count(self) -> None:
        formatter = PaginatedFormatter(lambda d: d["name"])
        out = formatter({"count": 0, "results": []})
        assert out == "0 results"

    def test_show_count_false_omits_count_line(self) -> None:
        formatter = PaginatedFormatter(lambda d: f"- {d['name']}", show_count=False)
        out = formatter({"count": 2, "results": [{"name": "a"}, {"name": "b"}]})
        assert out == "- a\n- b"

    def test_missing_count_infers_from_results(self) -> None:
        formatter = PaginatedFormatter(lambda d: d["name"])
        out = formatter({"results": [{"name": "a"}, {"name": "b"}, {"name": "c"}]})
        assert out.startswith("3 results\n")

    def test_non_dict_input_fallback(self) -> None:
        formatter = PaginatedFormatter(lambda d: d["name"])
        out = formatter([{"name": "a"}])
        assert out == str([{"name": "a"}])

    def test_results_skip_non_dict_items(self) -> None:
        formatter = PaginatedFormatter(lambda d: d["name"], show_count=False)
        out = formatter({"results": [{"name": "a"}, "bogus", {"name": "b"}, None]})
        assert out == "a\nb"

    def test_compatible_with_echo_result(self, capsys: pytest.CaptureFixture[str]) -> None:
        formatter = PaginatedFormatter(lambda d: d["name"])
        data: dict[str, Any] = {
            "count": 2,
            "results": [{"name": "alpha"}, {"name": "beta"}],
        }
        echo_result(data, as_json=False, human_formatter=formatter)
        out = capsys.readouterr().out
        assert "2 results" in out
        assert "alpha" in out
        assert "beta" in out


class TestJsonOptionTyperIntegration:
    def test_json_option_works_as_typer_flag(self) -> None:
        app = typer.Typer()

        @app.command()
        def show(json: JsonOption = False) -> None:
            typer.echo(f"json={json}")

        runner = CliRunner()
        result_default = runner.invoke(app, [])
        assert "json=False" in result_default.stdout

        result_long = runner.invoke(app, ["--json"])
        assert "json=True" in result_long.stdout

        result_short = runner.invoke(app, ["-j"])
        assert "json=True" in result_short.stdout

    def test_json_option_in_help_text(self) -> None:
        app = typer.Typer()

        @app.command()
        def show(json: JsonOption = False) -> None:
            pass

        runner = CliRunner()
        result = runner.invoke(app, ["--help"])
        clean = _strip_ansi(result.stdout)
        assert "--json" in clean
        assert "raw JSON" in clean or "JSON" in clean
