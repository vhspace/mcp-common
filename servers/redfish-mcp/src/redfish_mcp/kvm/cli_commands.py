"""typer subcommands for ``redfish-cli kvm ...`` (phase 1 stubs)."""

from __future__ import annotations

import typer

app = typer.Typer(name="kvm", help="KVM console — read screen and send keyboard input.")

_NOT_IMPL_MSG = "not_implemented — phase 1 scaffolding only; see docs/KVM_CONSOLE_FEATURE.md"


@app.command("screen")
def screen(
    host: str = typer.Argument(..., help="BMC host or IP"),
    mode: str = typer.Option(
        "text_only", "--mode", help="image|text_only|both|summary|analysis|diagnosis"
    ),
    detach: bool = typer.Option(False, "--detach", help="Return task id and exit"),
) -> None:
    typer.echo(_NOT_IMPL_MSG, err=True)
    raise typer.Exit(code=2)


@app.command("send")
def send(
    host: str = typer.Argument(..., help="BMC host or IP"),
    keys_or_text: str = typer.Argument(
        ..., help="A single key (e.g. Enter, F2, Ctrl+Alt+Del) or text"
    ),
    enter: bool = typer.Option(False, "--enter", help="Press Enter after text"),
) -> None:
    typer.echo(_NOT_IMPL_MSG, err=True)
    raise typer.Exit(code=2)


@app.command("type-and-read")
def type_and_read(
    host: str = typer.Argument(..., help="BMC host or IP"),
    text: str = typer.Argument(..., help="Text to type"),
    wait_ms: int = typer.Option(500, "--wait-ms"),
    mode: str = typer.Option("text_only", "--mode"),
) -> None:
    typer.echo(_NOT_IMPL_MSG, err=True)
    raise typer.Exit(code=2)


@app.command("close")
def close(host: str = typer.Argument(..., help="BMC host or IP")) -> None:
    typer.echo(_NOT_IMPL_MSG, err=True)
    raise typer.Exit(code=2)


@app.command("status")
def status(
    task_id: str | None = typer.Argument(None, help="Optional task id to poll"),
) -> None:
    typer.echo(_NOT_IMPL_MSG, err=True)
    raise typer.Exit(code=2)
