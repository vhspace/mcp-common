"""Tests for SuggestingTyperGroup typo suggestions."""

from __future__ import annotations

import typer
from typer.core import TyperGroup
from typer.testing import CliRunner

from mcp_common.cli import SuggestingTyperGroup


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
        runner = CliRunner(mix_stderr=False)
        result = runner.invoke(app, ["lookpu"])
        assert result.exit_code != 0
        combined = (result.stdout or "") + (result.stderr or "")
        assert "Did you mean" in combined
        assert "'lookup'" in combined
        assert "No such command 'lookpu'" in combined

    def test_unknown_command_with_no_close_match_falls_back(self) -> None:
        app = _make_app()
        runner = CliRunner(mix_stderr=False)
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

        runner = CliRunner(mix_stderr=False)
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

        runner = CliRunner(mix_stderr=False)
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
        runner = CliRunner(mix_stderr=False)
        result = runner.invoke(app, [])
        combined = (result.stdout or "") + (result.stderr or "")
        assert "Did you mean" not in combined
