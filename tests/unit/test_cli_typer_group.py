"""Tests for SuggestingTyperGroup typo suggestions."""

from __future__ import annotations

import json

import typer
from typer.core import TyperGroup
from typer.testing import CliRunner

from mcp_common.cli import SuggestingTyperGroup
from mcp_common.testing.dual_mode import make_cli_runner


def _make_app(*, cls: type[TyperGroup] = SuggestingTyperGroup) -> typer.Typer:
    app = typer.Typer(cls=cls)

    @app.command()
    def lookup() -> None:
        typer.echo("lookup ok")

    @app.command()
    def search() -> None:
        typer.echo("search ok")

    @app.command()
    def list_things() -> None:
        typer.echo("list ok")

    return app


class TestSuggestingTyperGroup:
    def test_known_command_runs_normally(self) -> None:
        app = _make_app()
        runner = CliRunner()
        result = runner.invoke(app, ["lookup"])
        assert result.exit_code == 0
        assert "lookup ok" in result.stdout

    def test_unknown_command_with_close_match_suggests(self) -> None:
        app = _make_app()
        runner = make_cli_runner()
        result = runner.invoke(app, ["lookpu"])
        assert result.exit_code != 0
        combined = (result.stdout or "") + (result.stderr or "")
        assert "Did you mean" in combined
        assert "'lookup'" in combined
        assert "No such command 'lookpu'" in combined

    def test_unknown_command_with_no_close_match_falls_back(self) -> None:
        app = _make_app()
        runner = make_cli_runner()
        result = runner.invoke(app, ["xyzzyplugh"])
        assert result.exit_code != 0
        combined = (result.stdout or "") + (result.stderr or "")
        assert "Did you mean" not in combined
        assert "No such command 'xyzzyplugh'" in combined

    def test_max_suggestions_respected(self) -> None:
        app = typer.Typer(
            cls=SuggestingTyperGroup.with_options(cutoff=0.1, max_suggestions=2),
        )

        @app.command()
        def alpha() -> None:
            pass

        @app.command()
        def alpha_two() -> None:
            pass

        @app.command()
        def alpha_three() -> None:
            pass

        @app.command()
        def alpha_four() -> None:
            pass

        runner = make_cli_runner()
        result = runner.invoke(app, ["alpha-zzz"])
        assert result.exit_code != 0
        combined = (result.stdout or "") + (result.stderr or "")
        line = next(
            (s for s in combined.splitlines() if s.startswith("Did you mean")),
            None,
        )
        assert line is not None, f"no Did you mean line in: {combined!r}"
        assert line.count("'") == 2 * 2

    def test_cutoff_filters_distant_matches(self) -> None:
        strict = typer.Typer(cls=SuggestingTyperGroup.with_options(cutoff=0.99))

        @strict.command()
        def hello() -> None:
            pass

        @strict.command()
        def world() -> None:
            pass

        runner = make_cli_runner()
        result = runner.invoke(strict, ["help"])
        assert result.exit_code != 0
        combined = (result.stdout or "") + (result.stderr or "")
        assert "Did you mean" not in combined

        loose = typer.Typer(cls=SuggestingTyperGroup.with_options(cutoff=0.3))

        @loose.command()
        def hello2() -> None:
            pass

        @loose.command()
        def world2() -> None:
            pass

        result2 = runner.invoke(loose, ["help"])
        assert result2.exit_code != 0
        combined2 = (result2.stdout or "") + (result2.stderr or "")
        assert "Did you mean" in combined2
        assert "'hello2'" in combined2

    def test_with_options_returns_subclass(self) -> None:
        sub = SuggestingTyperGroup.with_options(cutoff=0.4, max_suggestions=7)
        assert issubclass(sub, SuggestingTyperGroup)
        assert sub.cutoff == 0.4
        assert sub.max_suggestions == 7
        assert SuggestingTyperGroup.cutoff == 0.6
        assert SuggestingTyperGroup.max_suggestions == 3

    def test_with_options_no_args_returns_subclass_with_defaults(self) -> None:
        sub = SuggestingTyperGroup.with_options()
        assert issubclass(sub, SuggestingTyperGroup)
        assert sub.cutoff == SuggestingTyperGroup.cutoff
        assert sub.max_suggestions == SuggestingTyperGroup.max_suggestions

    def test_empty_args_falls_through(self) -> None:
        """When no command name is given, the original error propagates."""
        app = _make_app()
        runner = make_cli_runner()
        result = runner.invoke(app, [])
        combined = (result.stdout or "") + (result.stderr or "")
        assert "Did you mean" not in combined


class TestJsonErrorMode:
    """Issue #100: unknown command + ``--json``/``-j`` → structured JSON error."""

    def test_unknown_command_with_json_flag_emits_structured_error(self) -> None:
        app = _make_app()
        runner = make_cli_runner()
        result = runner.invoke(app, ["lookpu", "--json"])

        assert result.exit_code != 0
        # Single JSON document on stderr; no human "Did you mean" prose, and no
        # Click "Error:" text polluting it.
        payload = json.loads(result.stderr)
        assert payload["error"] == "No such command 'lookpu'."
        assert payload["suggestions"] == ["lookup"]
        assert "lookup" in payload["available_commands"]
        assert "search" in payload["available_commands"]
        assert "Did you mean" not in result.stderr

    def test_unknown_command_with_short_j_flag(self) -> None:
        app = _make_app()
        runner = make_cli_runner()
        result = runner.invoke(app, ["lookpu", "-j"])

        assert result.exit_code != 0
        payload = json.loads(result.stderr)
        assert payload["error"] == "No such command 'lookpu'."
        assert payload["suggestions"] == ["lookup"]

    def test_json_error_exit_code_is_two(self) -> None:
        app = _make_app()
        runner = make_cli_runner()
        result = runner.invoke(app, ["lookpu", "--json"])
        assert result.exit_code == 2

    def test_json_error_has_all_three_keys(self) -> None:
        app = _make_app()
        runner = make_cli_runner()
        result = runner.invoke(app, ["totallybogus", "--json"])

        payload = json.loads(result.stderr)
        assert set(payload) == {"error", "suggestions", "available_commands"}
        # No close match → empty suggestions, but the full command list is still
        # offered so an agent can recover.
        assert payload["suggestions"] == []
        assert sorted(payload["available_commands"]) == payload["available_commands"]
        assert payload["available_commands"]  # non-empty

    def test_unknown_command_without_json_keeps_human_behavior(self) -> None:
        """Regression: no json flag → existing ``Did you mean`` prose, no JSON."""
        app = _make_app()
        runner = make_cli_runner()
        result = runner.invoke(app, ["lookpu"])

        assert result.exit_code != 0
        combined = (result.stdout or "") + (result.stderr or "")
        assert "Did you mean" in combined
        assert "'lookup'" in combined
        assert "No such command 'lookpu'" in combined
        # Stderr must NOT be JSON in human mode.
        try:
            json.loads(result.stderr)
            is_json = True
        except (json.JSONDecodeError, ValueError):
            is_json = False
        assert not is_json

    def test_known_command_with_json_passes_through(self) -> None:
        """A valid command is not intercepted, even with ``--json`` present."""
        app = typer.Typer(cls=SuggestingTyperGroup)

        @app.command()
        def run(json_out: bool = typer.Option(False, "--json", "-j")) -> None:
            typer.echo("ran-json" if json_out else "ran")

        # A second command so the app is a real multi-command group (Typer
        # treats a single-command app as argument-only).
        @app.command()
        def other() -> None:
            typer.echo("other")

        runner = make_cli_runner()
        result = runner.invoke(app, ["run", "--json"])

        assert result.exit_code == 0, f"stderr: {result.stderr}"
        assert "ran-json" in result.stdout
        # No command-not-found JSON error was emitted.
        assert "available_commands" not in (result.stderr or "")
