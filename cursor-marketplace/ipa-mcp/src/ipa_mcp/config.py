"""Configuration for FreeIPA MCP Server."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


def _find_env_files() -> list[Path]:
    """Build .env search paths: CWD, parent, walk up ancestors, workspace root."""
    paths: list[Path] = [Path(".env"), Path("../.env")]
    for parent in Path.cwd().parents:
        candidate = parent / ".env"
        if candidate.is_file():
            paths.append(candidate)
            break
    workspace = Path("/workspaces/together/.env")
    if workspace.is_file() and workspace not in paths:
        paths.append(workspace)
    return paths


def resolve_secret_value(secret: SecretStr) -> str:
    """Resolve a secret value, supporting op:// references for 1Password.

    If the value starts with ``op://``, it is resolved at runtime via
    ``op read``. Otherwise the literal value is returned unchanged.
    This enables backward-compatible static passwords alongside dynamic
    1Password references (e.g. ``IPA_PASSWORD=op://Vault/Item/field``).
    """
    raw = secret.get_secret_value()
    if not raw.startswith("op://"):
        return raw
    try:
        proc = subprocess.run(
            ["op", "read", raw],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(
            f"Failed to resolve 1Password reference: {exc}. "
            "Ensure the 'op' CLI is installed and authenticated."
        ) from exc
    if proc.returncode != 0:
        raise RuntimeError(
            f"1Password 'op read' failed (rc={proc.returncode}): {proc.stderr.strip()}"
        )
    resolved = proc.stdout.strip()
    if not resolved:
        raise RuntimeError("1Password 'op read' returned empty value")
    logger.debug("Resolved IPA password from 1Password reference")
    return resolved


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_find_env_files(),
        env_file_encoding="utf-8",
        env_prefix="IPA_",
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
    )

    host: str = Field(validation_alias=AliasChoices("IPA_HOST", "IPA_URL"))
    username: str = Field(
        default="admin",
        validation_alias=AliasChoices("IPA_USERNAME", "IPA_USER"),
    )
    password: SecretStr = Field(validation_alias=AliasChoices("IPA_PASSWORD", "IPA_PASS"))
    verify_ssl: bool = Field(default=False)
