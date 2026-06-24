"""typer subcommands for ``redfish-cli kvm ...`` (phase 1 stubs).

The write subcommands (``send``, ``type-and-read``, ``close``) carry the shared
``@enforce_read_only_cli(read_only=False)`` gate so they match their MCP-side
classification (``redfish_kvm_sendkey`` / ``redfish_kvm_sendkeys`` /
``redfish_kvm_type_and_read`` / ``redfish_kvm_close`` are all readOnlyHint=False)
and are refused under ``MCP_ENFORCE_READONLY``. ``screen`` and ``status`` are
read-only and stay ungated.
"""

from __future__ import annotations

import typer
from mcp_common.dual_mode import enforce_read_only_cli

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
@enforce_read_only_cli(read_only=False)
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
@enforce_read_only_cli(read_only=False)
def type_and_read(
    host: str = typer.Argument(..., help="BMC host or IP"),
    text: str = typer.Argument(..., help="Text to type"),
    wait_ms: int = typer.Option(500, "--wait-ms"),
    mode: str = typer.Option("text_only", "--mode"),
) -> None:
    typer.echo(_NOT_IMPL_MSG, err=True)
    raise typer.Exit(code=2)


@app.command("close")
@enforce_read_only_cli(read_only=False)
def close(host: str = typer.Argument(..., help="BMC host or IP")) -> None:
    typer.echo(_NOT_IMPL_MSG, err=True)
    raise typer.Exit(code=2)


@app.command("status")
def status(
    task_id: str | None = typer.Argument(None, help="Optional task id to poll"),
) -> None:
    typer.echo(_NOT_IMPL_MSG, err=True)
    raise typer.Exit(code=2)
