"""Abstract driver interface for talking to a single switch.

A driver owns the command vocabulary for one switch OS (e.g. Cumulus Linux).
Future drivers (Arista EOS, Junos, SONiC) plug in here by implementing the
same async surface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Any

from pydantic import SecretStr


@dataclass(frozen=True, slots=True)
class ConnectionInfo:
    """Everything needed to SSH into one switch."""

    host: str
    user: str
    password: SecretStr
    port: int = 22
    jump_host: str | None = None
    jump_port: int = 22
    jump_user: str | None = None
    jump_password: SecretStr | None = None


class NetworkDriverError(RuntimeError):
    """Raised on SSH/connection/parse failures. Includes host + hint."""

    def __init__(self, message: str, *, host: str | None = None, hint: str | None = None):
        parts: list[str] = []
        if host:
            parts.append(f"[{host}]")
        parts.append(message)
        if hint:
            parts.append(f"(hint: {hint})")
        super().__init__(" ".join(parts))
        self.host = host
        self.hint = hint


class SwitchDriver(ABC):
    """Async driver contract. Each method wraps one ``show`` style command.

    Use ``async with drv.session()`` to hold a single SSH connection open
    for multiple commands within one tool call. Without a session, each
    command opens its own connection (backwards-compatible default).
    """

    def __init__(self, conn: ConnectionInfo):
        self.conn = conn

    @abstractmethod
    def session(self) -> AbstractAsyncContextManager[None]:
        """Context manager that holds an SSH connection open for reuse.

        Within the block, all commands multiplex as channels on the same
        connection. On exit the connection is closed.
        """
        ...

    @abstractmethod
    async def system_info(self) -> dict[str, Any]: ...

    @abstractmethod
    async def interfaces_brief(self) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def interface(self, port: str) -> dict[str, Any]: ...

    @abstractmethod
    async def interface_counters(self, port: str) -> dict[str, Any]: ...

    @abstractmethod
    async def lldp(self) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def bgp_summary(self) -> dict[str, Any]: ...

    @abstractmethod
    async def mac_table(self) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def logs(
        self,
        lines: int = 75,
        since: str | None = None,
        until: str | None = None,
        unit: str | None = None,
        identifier: str | None = None,
        priority: str | None = None,
        grep: str | None = None,
        boot: bool = False,
        kernel: bool = False,
    ) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def wjh(self) -> list[dict[str, Any]]: ...
