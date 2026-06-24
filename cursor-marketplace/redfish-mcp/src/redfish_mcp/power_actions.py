"""Canonical power-control action mapping and normalization."""

from __future__ import annotations

from typing import Literal

PowerAction = Literal["on", "off", "force_off", "restart", "force_restart", "nmi"]

ACTION_TO_RESET_TYPE: dict[str, str] = {
    "on": "On",
    "off": "GracefulShutdown",
    "force_off": "ForceOff",
    "restart": "GracefulRestart",
    "force_restart": "ForceRestart",
    "nmi": "Nmi",
}

_ALIASES: dict[str, str] = {canonical: canonical for canonical in ACTION_TO_RESET_TYPE}
_ALIASES.update({v.lower(): canonical for canonical, v in ACTION_TO_RESET_TYPE.items()})

_VALID_CSV = ", ".join(ACTION_TO_RESET_TYPE)


class InvalidActionError(ValueError):
    """Raised when a power-control action cannot be resolved."""

    def __init__(self, action: str) -> None:
        self.action = action
        self.message = f"Invalid action '{action}'. Valid actions: {_VALID_CSV}."
        super().__init__(self.message)


def resolve_reset_type(action: str) -> tuple[str, str]:
    """Return ``(canonical_action, redfish_reset_type)`` for *action*.

    Accepts canonical snake_case, Redfish PascalCase, and any case variation.
    Raises :class:`InvalidActionError` for unrecognised input.
    """
    if not isinstance(action, str) or not action:
        raise InvalidActionError(str(action))

    key = action.lower().replace("-", "_").replace("_", "")
    canonical = _ALIASES.get(key)
    if canonical is None:
        key_with_underscores = action.lower().replace("-", "_")
        canonical = _ALIASES.get(key_with_underscores)
    if canonical is None:
        raise InvalidActionError(action)
    return canonical, ACTION_TO_RESET_TYPE[canonical]
