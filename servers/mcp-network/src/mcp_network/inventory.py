"""Site inventory loader for mcp-network.

Reads per-site JSON inventory files under ``inventory/sites/*.json``, validates
them against ``inventory/schema/site.schema.json``, then parses them into
typed Pydantic models. Credentials are never stored in the inventory: each
site names the environment variables that hold its SSH user / password via
``credentials_env``; those values are looked up at load time from the process
environment.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr

logger = logging.getLogger(__name__)


SwitchRole = Literal["leaf", "spine", "border", "oob", "other"]


def default_inventory_dir() -> Path:
    """Return the default inventory dir bundled with this package.

    Resolves to the ``inventory/`` directory next to this file (inside the
    installed package).  Override via ``MCP_NETWORK_INVENTORY_DIR`` env var.
    """
    env_override = os.environ.get("MCP_NETWORK_INVENTORY_DIR")
    if env_override:
        return Path(env_override)
    return Path(__file__).resolve().parent / "inventory"


class SwitchEntry(BaseModel):
    """A single switch within a site."""

    model_config = ConfigDict(extra="forbid")

    name: str
    mgmt_ip: str | None = None
    alt_mgmt_ip: str | None = None
    role: SwitchRole
    model: str | None = None
    os: str | None = None
    serial: str | None = None
    rack: str | None = None
    reachable: bool = True
    notes: str | None = None

    @property
    def connect_host(self) -> str:
        """Host to use for SSH: prefer mgmt_ip, fall back to name."""
        return self.mgmt_ip or self.name


class VlanEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    subnet: str | None = None


class CredentialsEnvRef(BaseModel):
    """Names (not values) of env vars that hold the site's SSH credentials."""

    model_config = ConfigDict(extra="forbid")

    user: str
    password: str


class JumpHostConfig(BaseModel):
    """Optional SSH jump host for reaching a site's management subnet."""

    model_config = ConfigDict(extra="forbid")

    host: str
    port: int = 22
    user_env: str | None = None
    password_env: str | None = None


class UplinkPorts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    leaf: list[str] = Field(default_factory=list)
    spine: list[str] = Field(default_factory=list)
    border: list[str] = Field(default_factory=list)


class SiteInventory(BaseModel):
    """Typed view of a single ``inventory/sites/<site>.json`` file."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_: str | None = Field(default=None, alias="$schema")
    site: str
    display_name: str | None = None
    aliases: list[str] = Field(default_factory=list)
    default: bool = False
    driver: Literal["cumulus"]
    ssh_port: int = 22
    netbox_site_slug: str | None = None
    mgmt_subnets: list[str] = Field(default_factory=list)
    credentials_env: CredentialsEnvRef
    jump_host: JumpHostConfig | None = None
    vlans: dict[str, VlanEntry] = Field(default_factory=dict)
    uplink_ports: UplinkPorts = Field(default_factory=UplinkPorts)
    switches: list[SwitchEntry]

    def resolve_credentials(
        self, env: dict[str, str] | None = None
    ) -> tuple[str | None, SecretStr | None]:
        """Read user/password from the env vars named in ``credentials_env``.

        Returns ``(None, None)`` for any missing var (caller decides whether
        to treat the site as non-operational).
        """
        src = env if env is not None else os.environ
        user = src.get(self.credentials_env.user) or None
        raw_pw = src.get(self.credentials_env.password)
        password = SecretStr(raw_pw) if raw_pw else None
        return user, password

    def resolve_jump_credentials(
        self, env: dict[str, str] | None = None
    ) -> tuple[str | None, SecretStr | None]:
        """Read jump-host creds. Falls back to the site's own creds when
        ``user_env`` / ``password_env`` aren't set on the jump config.
        """
        if self.jump_host is None:
            return None, None
        src = env if env is not None else os.environ
        user_var = self.jump_host.user_env or self.credentials_env.user
        pw_var = self.jump_host.password_env or self.credentials_env.password
        user = src.get(user_var) or None
        raw_pw = src.get(pw_var)
        password = SecretStr(raw_pw) if raw_pw else None
        return user, password

    def find_switch(self, name_or_ip: str) -> SwitchEntry | None:
        """Return the switch matching ``name_or_ip`` (case-insensitive name,
        exact IP match against mgmt_ip or alt_mgmt_ip). ``None`` if not found.
        """
        key = name_or_ip.strip().lower()
        for sw in self.switches:
            if sw.name.lower() == key:
                return sw
            if sw.mgmt_ip and sw.mgmt_ip == name_or_ip:
                return sw
            if sw.alt_mgmt_ip and sw.alt_mgmt_ip == name_or_ip:
                return sw
        return None


class InventoryLoader:
    """Load and validate per-site inventory JSON files.

    Usage::

        loader = InventoryLoader()
        sites = loader.load_dir()  # uses default dir
    """

    def __init__(self, inventory_dir: Path | None = None) -> None:
        self.inventory_dir = inventory_dir or default_inventory_dir()

    @property
    def schema_path(self) -> Path:
        return self.inventory_dir / "schema" / "site.schema.json"

    @property
    def sites_dir(self) -> Path:
        return self.inventory_dir / "sites"

    def load_schema(self) -> dict[str, Any] | None:
        """Return the parsed JSON schema, or ``None`` if not present."""
        if not self.schema_path.is_file():
            logger.warning("No schema at %s; skipping JSON Schema validation", self.schema_path)
            return None
        with self.schema_path.open() as f:
            data: Any = json.load(f)
        if not isinstance(data, dict):
            logger.warning("Schema at %s is not a JSON object; ignoring", self.schema_path)
            return None
        return data

    def load_dir(self) -> list[SiteInventory]:
        """Load every ``sites/*.json`` file in sorted order.

        Invalid files emit warnings and are skipped, not raised — a single
        bad site file must not break the server.
        """
        if not self.sites_dir.is_dir():
            logger.warning("No inventory sites dir at %s", self.sites_dir)
            return []

        schema = self.load_schema()
        validator = self._build_validator(schema) if schema else None

        sites: list[SiteInventory] = []
        for path in sorted(self.sites_dir.glob("*.json")):
            try:
                sites.append(self._load_file(path, validator))
            except Exception as e:
                logger.warning("Skipping inventory %s: %s", path.name, e)
        return sites

    def _load_file(self, path: Path, validator: Any | None) -> SiteInventory:
        with path.open() as f:
            raw = json.load(f)
        if validator is not None:
            errors = sorted(validator.iter_errors(raw), key=lambda e: e.path)
            if errors:
                msgs = "; ".join(
                    f"{'/'.join(str(p) for p in e.absolute_path) or '<root>'}: {e.message}"
                    for e in errors
                )
                raise ValueError(f"schema validation failed: {msgs}")
        return SiteInventory.model_validate(raw)

    @staticmethod
    def _build_validator(schema: dict[str, Any]) -> Any | None:
        try:
            from jsonschema import Draft202012Validator  # type: ignore[import-untyped]
        except ImportError:
            logger.warning(
                "jsonschema not installed; inventory files will be parsed but not schema-validated"
            )
            return None
        return Draft202012Validator(schema)
