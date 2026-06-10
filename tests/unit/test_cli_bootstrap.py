"""Tests for create_cli_app and run_cli bootstrap factory."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
import typer
from typer.core import TyperGroup
from typer.testing import CliRunner

from mcpanvil.cli import create_cli_app, run_cli
from mcpanvil.env import reset_env_state


@pytest.fixture(autouse=True)
def _reset_env() -> Any:
    reset_env_state()
    yield
    reset_env_state()


class TestCreateCliApp:
    def test_returns_typer_instance(self) -> None:
        app = create_cli_app("my-cli", project_repo="your-org/my-mcp")
        assert isinstance(app, typer.Typer)

    def test_no_args_is_help_default(self) -> None:
        app = create_cli_app("my-cli", project_repo="your-org/my-mcp")
        assert app.info.no_args_is_help is True

    def test_no_args_invocation_prints_help(self) -> None:
        app = create_cli_app("my-cli", project_repo="your-org/my-mcp", help="My CLI.")

        @app.command()
        def sub() -> None:
            pass

        @app.command()
        def other() -> None:
            pass

        runner = CliRunner(mix_stderr=False)
        result = runner.invoke(app, [])
        combined = (result.stdout or "") + (result.stderr or "")
        assert "Usage" in combined
        assert "sub" in combined

    def test_help_text_propagated(self) -> None:
        app = create_cli_app(
            "my-cli",
            project_repo="your-org/my-mcp",
            help="Hello from create_cli_app.",
        )
        assert app.info.help == "Hello from create_cli_app."

    def test_default_group_class_is_suggesting(self) -> None:
        app = create_cli_app("my-cli", project_repo="your-org/my-mcp")

        @app.command()
        def lookup() -> None:
            pass

        @app.command()
        def search() -> None:
            pass

        runner = CliRunner(mix_stderr=False)
        result = runner.invoke(app, ["lookpu"])
        assert result.exit_code != 0
        combined = (result.stdout or "") + (result.stderr or "")
        assert "Did you mean: 'lookup'?" in combined

    def test_cls_override_respected(self) -> None:
        class CustomGroup(TyperGroup):
            pass

        app = create_cli_app("my-cli", project_repo="your-org/my-mcp", cls=CustomGroup)
        assert app.info.cls is CustomGroup

    def test_exception_handler_wires_project_repo(self, monkeypatch: pytest.MonkeyPatch) -> None:
        install_mock = MagicMock()
        monkeypatch.setattr("mcpanvil.cli._bootstrap.install_cli_exception_handler", install_mock)

        app = create_cli_app("my-cli", project_repo="your-org/my-mcp")

        install_mock.assert_called_once()
        args, kwargs = install_mock.call_args
        assert args[0] is app
        assert kwargs["project_repo"] == "your-org/my-mcp"

    def test_exception_handler_prints_terse_error_on_app_call(
        self,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Direct ``app()`` invocation goes through the patched ``__call__``.

        ``install_cli_exception_handler`` patches ``Typer.__call__``
        class-wide and the patches stack across tests. To keep this
        end-to-end check independent of suite ordering, monkeypatch
        ``Typer.__call__`` back to a clean baseline that captures the
        underlying behavior without any earlier wrappers in the chain.

        Post-#115: the caller (stderr) gets a terse error only; the
        remediation block (and the ``project_repo`` it references) is routed
        to the trace log, never to stderr.
        """

        def _clean_typer_call(self: typer.Typer, *args: object, **kwargs: object) -> object:
            import typer.main as _typer_main

            return _typer_main.get_command(self)(*args, **kwargs)

        monkeypatch.setattr(typer.Typer, "__call__", _clean_typer_call)

        app = create_cli_app("my-cli", project_repo="your-org/my-mcp")

        @app.command()
        def boom() -> None:
            raise RuntimeError("explode")

        monkeypatch.setattr("sys.argv", ["my-cli", "boom"])
        with pytest.raises(SystemExit) as exc_info:
            app()
        assert exc_info.value.code == 1
        err = capsys.readouterr().err
        # Terse caller-facing error.
        assert "RuntimeError" in err
        assert "explode" in err
        assert "(ref: " in err
        assert "This failure has been logged." in err
        # Remediation block / repo / traceback must NOT leak to the caller.
        assert "Agent remediation" not in err
        assert "your-org/my-mcp" not in err
        assert "open a new issue" not in err.lower()
        assert "Traceback" not in err

    def test_extra_typer_kwargs_forwarded(self) -> None:
        app = create_cli_app(
            "my-cli",
            project_repo="your-org/my-mcp",
            subcommand_metavar="THING",
        )
        assert app.info.subcommand_metavar == "THING"


class TestRunCli:
    def test_chains_load_env_setup_logging_then_app(self, monkeypatch: pytest.MonkeyPatch) -> None:
        call_order: list[str] = []
        load_env_mock = MagicMock(side_effect=lambda: call_order.append("load_env"))
        setup_logging_mock = MagicMock(
            side_effect=lambda *a, **kw: call_order.append("setup_logging")
        )
        app = MagicMock(side_effect=lambda: call_order.append("app"))

        monkeypatch.setattr("mcpanvil.cli._bootstrap.load_env", load_env_mock)
        monkeypatch.setattr("mcpanvil.cli._bootstrap.setup_logging", setup_logging_mock)

        run_cli(app, log_name="my_cli")

        assert call_order == ["load_env", "setup_logging", "app"]
        load_env_mock.assert_called_once_with()
        app.assert_called_once_with()

    def test_passes_log_name_through(self, monkeypatch: pytest.MonkeyPatch) -> None:
        setup_logging_mock = MagicMock()
        monkeypatch.setattr("mcpanvil.cli._bootstrap.load_env", MagicMock())
        monkeypatch.setattr("mcpanvil.cli._bootstrap.setup_logging", setup_logging_mock)

        run_cli(MagicMock(), log_name="netbox_cli")

        setup_logging_mock.assert_called_once()
        kwargs = setup_logging_mock.call_args.kwargs
        assert kwargs["name"] == "netbox_cli"

    def test_log_level_override_passed_through(self, monkeypatch: pytest.MonkeyPatch) -> None:
        setup_logging_mock = MagicMock()
        monkeypatch.setattr("mcpanvil.cli._bootstrap.load_env", MagicMock())
        monkeypatch.setattr("mcpanvil.cli._bootstrap.setup_logging", setup_logging_mock)

        run_cli(MagicMock(), log_name="netbox_cli", log_level="DEBUG")

        setup_logging_mock.assert_called_once()
        kwargs = setup_logging_mock.call_args.kwargs
        assert kwargs["name"] == "netbox_cli"
        assert kwargs["level"] == "DEBUG"

    def test_default_log_level_is_info(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When log_level is None, ``"INFO"`` is passed explicitly — matching
        :func:`setup_logging`'s own default level so observable behavior is
        unchanged whether or not the caller specifies a level."""
        setup_logging_mock = MagicMock()
        monkeypatch.setattr("mcpanvil.cli._bootstrap.load_env", MagicMock())
        monkeypatch.setattr("mcpanvil.cli._bootstrap.setup_logging", setup_logging_mock)

        run_cli(MagicMock(), log_name="x")

        kwargs = setup_logging_mock.call_args.kwargs
        assert kwargs["level"] == "INFO"
