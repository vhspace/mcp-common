from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

try:
    from mcp_common.credentials import (  # pyright: ignore[reportMissingImports]
        CredentialCandidate,
        UsernamePasswordCredentialProvider,
    )
except ModuleNotFoundError:
    # Compatibility shim for older mcp-common revisions pinned in downstream repos.
    @dataclass(frozen=True)
    class CredentialCandidate:
        name: str
        user_env: str
        password_env: str
        user_ref_env: str | None = None
        password_ref_env: str | None = None

    @dataclass(frozen=True)
    class _CompatResolvedCreds:
        user: str
        password: str

    @dataclass(frozen=True)
    class _CompatAudit:
        source: str
        candidate: str | None = None
        host: str | None = None
        site_hint: str | None = None
        used_1password_refs: bool = False

        def as_log_fields(self) -> dict[str, str | bool]:
            out: dict[str, str | bool] = {"source": self.source}
            if self.candidate:
                out["candidate"] = self.candidate
            if self.host:
                out["host"] = self.host
            if self.site_hint:
                out["site_hint"] = self.site_hint
            out["used_1password_refs"] = self.used_1password_refs
            return out

    @dataclass(frozen=True)
    class _CompatResult:
        credentials: _CompatResolvedCreds
        audit: _CompatAudit

    def _compat_read_1p(ref: str) -> str | None:
        try:
            proc = subprocess.run(
                ["op", "read", ref],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return None
        if proc.returncode != 0:
            return None
        return proc.stdout.strip() or None

    class UsernamePasswordCredentialProvider:
        def __init__(
            self,
            *,
            candidates: list[CredentialCandidate],
            generic_candidate: CredentialCandidate | None = None,
            site_hint_env: str | None = None,
            site_selector=None,
        ) -> None:
            self._candidates = candidates
            self._generic = generic_candidate
            self._site_hint_env = site_hint_env
            self._site_selector = site_selector

        @staticmethod
        def _val(key: str | None) -> str:
            return os.getenv(key, "").strip() if key else ""

        def _resolve(self, c: CredentialCandidate) -> _CompatResult | None:
            user_ref = self._val(c.user_ref_env)
            pass_ref = self._val(c.password_ref_env)
            used_refs = False
            user = ""
            password = ""
            if user_ref and pass_ref:
                u = _compat_read_1p(user_ref)
                p = _compat_read_1p(pass_ref)
                if u and p:
                    user, password, used_refs = u, p, True
            if not user or not password:
                user = self._val(c.user_env)
                password = self._val(c.password_env)
            if not user or not password:
                return None
            return _CompatResult(
                credentials=_CompatResolvedCreds(user=user, password=password),
                audit=_CompatAudit(
                    source="1password_ref" if used_refs else "env",
                    candidate=c.name,
                    used_1password_refs=used_refs,
                ),
            )

        def resolve(
            self,
            *,
            host: str = "",
            explicit_user: str | None = None,
            explicit_password: str | None = None,
        ):
            user = (explicit_user or "").strip()
            password = (explicit_password or "").strip()
            if user and password:
                return _CompatResult(
                    credentials=_CompatResolvedCreds(user=user, password=password),
                    audit=_CompatAudit(source="explicit", host=host or None),
                )
            by_name = {c.name.upper(): c for c in self._candidates}
            hint = self._val(self._site_hint_env).upper() if self._site_hint_env else ""
            if hint and hint in by_name:
                r = self._resolve(by_name[hint])
                if r is not None:
                    return r
            if host and self._site_selector is not None:
                selected = (self._site_selector(host) or "").strip().upper()
                if selected and selected in by_name:
                    r = self._resolve(by_name[selected])
                    if r is not None:
                        return r
            resolved = [r for c in self._candidates if (r := self._resolve(c)) is not None]
            if len(resolved) == 1:
                return resolved[0]
            if self._generic is not None:
                return self._resolve(self._generic)
            return None


from mcp.server.fastmcp import Context
from mcp.types import CallToolResult, ContentBlock, CreateTaskResult, TextContent
from pydantic import BaseModel, Field

from ._util import _json_text
from .agent_state_store import AgentStateStore
from .hinting_engine import Hint, HintingEngine

logger = logging.getLogger("redfish_mcp.agent_controller")

WRITE_TOOLS = frozenset(
    {"redfish_set_nextboot", "redfish_set_bios_attributes", "redfish_update_firmware"}
)


class _Creds(BaseModel):
    user: str
    password: str = Field(json_schema_extra={"writeOnly": True})


def extract_hosts(arguments: dict[str, Any] | None) -> list[str]:
    """Best-effort host extractor for Redfish tools.

    We intentionally keep this conservative and only look at common host keys.
    """
    if not arguments:
        return []

    out: list[str] = []

    def _add(v: Any) -> None:
        if isinstance(v, str) and v.strip():
            out.append(v.strip())

    for k in ("host", "host_a", "host_b"):
        if k in arguments:
            _add(arguments.get(k))

    # De-duplicate while keeping order.
    seen: set[str] = set()
    uniq: list[str] = []
    for h in out:
        key = h.lower()
        if key in seen:
            continue
        seen.add(key)
        uniq.append(h)
    return uniq


@dataclass(frozen=True)
class ToolCallContext:
    tool_name: str
    arguments: dict[str, Any]
    hosts: list[str]
    request_id: str | None
    client_id: str | None
    request_meta: dict[str, Any]
    started_at_monotonic: float


@dataclass
class _CachedCreds:
    user: str
    password: str
    cached_at_s: float = 0.0


@dataclass
class _ClientCache:
    creds_by_host: dict[str, _CachedCreds] = field(default_factory=dict)
    last_host: str | None = None
    last_creds: _CachedCreds | None = None


def _norm_host(host: str) -> str:
    return (host or "").strip().lower()


_SITE_CREDENTIAL_PATTERNS: list[tuple[str, str]] = [
    ("ORI_REDFISH_USER", "ORI_REDFISH_PASSWORD"),
    ("5C_REDFISH_LOGIN", "5C_REDFISH_PASSWORD"),
    ("5C_TN1_REDFISH_LOGIN", "5C_TN1_REDFISH_PASSWORD"),
    ("IREN2_B200_REDFISH_USER", "IREN2_B200_REDFISH_PASSWORD"),
    ("IREN_B300_REDFISH_USER", "IREN_B300_REDFISH_PASSWORD"),
]

_ENV_SKIP = frozenset(
    {
        "REDFISH_USER",
        "REDFISH_PASSWORD",
        "REDFISH_SITE",
        "REDFISH_SITE_CREDENTIALS",
        "REDFISH_ELICIT_CACHE_TTL_S",
    }
)

# ── NetBox site lookup for credential resolution ─────────────────────

_host_site_cache: dict[str, tuple[str, float]] = {}
_HOST_SITE_TTL_S = 900  # 15 minutes

_SITE_SLUG_PREFIX_RULES: list[tuple[str, str]] = [
    ("ori-", "ORI"),
    ("5c-tn1", "5C_TN1"),
    ("5c-", "5C"),
    ("iren-b200", "IREN2_B200"),
    ("iren-b300", "IREN_B300"),
]


def _site_slug_to_prefix(slug: str) -> str | None:
    """Map a NetBox site slug to the env var credential prefix.

    Uses ordered prefix rules so more-specific slugs (``5c-tn1``) match
    before broader ones (``5c-``).
    """
    slug_lower = slug.lower()
    for pattern, prefix in _SITE_SLUG_PREFIX_RULES:
        if slug_lower.startswith(pattern):
            return prefix
    return None


def _lookup_site_for_host(host: str) -> str | None:
    """Resolve a BMC host IP to a NetBox site slug via ``netbox-cli``.

    Results are cached for 15 minutes.  Returns ``None`` on any failure
    so the caller can fall through gracefully.
    """
    if not host or not host.strip():
        return None

    host = host.strip()
    now = time.time()
    cached = _host_site_cache.get(host)
    if cached and (now - cached[1]) < _HOST_SITE_TTL_S:
        return cached[0]

    try:
        result = subprocess.run(
            ["netbox-cli", "lookup", host, "--json"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return None
        data = json.loads(result.stdout)
        results = data.get("results", [])
        if not results:
            return None
        site = results[0].get("site", {})
        slug = site.get("slug", "") if isinstance(site, dict) else ""
        if slug:
            _host_site_cache[host] = (slug, now)
        return slug or None
    except Exception:
        return None


def _resolve_env_credentials(host: str) -> tuple[str, str] | None:
    """Try to resolve BMC credentials from environment variables.

    Supports both direct env pairs and 1Password references (`*_REF`), e.g.
    `REDFISH_ORI_USER_REF=op://...`.
    """
    candidates: list[CredentialCandidate] = []
    seen_names: set[str] = set()

    def _add_candidate(base: str, user_suffix: str) -> None:
        name = base.upper()
        if not name or name in seen_names:
            return
        seen_names.add(name)
        user_env = f"{base}_REDFISH_{user_suffix}"
        user_ref_suffix = "USER_REF" if user_suffix == "USER" else "LOGIN_REF"
        candidates.append(
            CredentialCandidate(
                name=name,
                user_env=user_env,
                password_env=f"{base}_REDFISH_PASSWORD",
                user_ref_env=f"{base}_REDFISH_{user_ref_suffix}",
                password_ref_env=f"{base}_REDFISH_PASSWORD_REF",
            )
        )

    for user_env, _pass_env in _SITE_CREDENTIAL_PATTERNS:
        if "_REDFISH_" in user_env:
            parts = user_env.split("_REDFISH_", 1)
            base = parts[0]
            suffix = parts[1]  # USER or LOGIN
            _add_candidate(base, suffix)

    for key in os.environ:
        if "_REDFISH_" not in key:
            continue
        if key in _ENV_SKIP:
            continue
        parts = key.rsplit("_REDFISH_", 1)
        if len(parts) != 2:
            continue
        base, suffix = parts
        if suffix == "USER":
            _add_candidate(base, "USER")
        elif suffix == "LOGIN":
            _add_candidate(base, "LOGIN")
        elif suffix == "USER_REF":
            _add_candidate(base, "USER")
        elif suffix == "LOGIN_REF":
            _add_candidate(base, "LOGIN")

    def _site_selector(host_ip: str) -> str | None:
        site_slug = _lookup_site_for_host(host_ip)
        if not site_slug:
            return None
        target_prefix = _site_slug_to_prefix(site_slug)
        if target_prefix:
            logger.debug(
                "Resolved host %s -> site %s -> prefix %s",
                host_ip,
                site_slug,
                target_prefix,
            )
        return target_prefix

    provider = UsernamePasswordCredentialProvider(
        candidates=candidates,
        generic_candidate=CredentialCandidate(
            name="GENERIC",
            user_env="REDFISH_USER",
            password_env="REDFISH_PASSWORD",
            user_ref_env="REDFISH_USER_REF",
            password_ref_env="REDFISH_PASSWORD_REF",
        ),
        site_hint_env="REDFISH_SITE",
        site_selector=_site_selector,
    )
    # Explicit REDFISH_USER/REDFISH_PASSWORD env vars take priority over
    # vendor auto-detection (e.g. ORI_REDFISH_USER). This ensures
    # `REDFISH_USER=x REDFISH_PASSWORD=y redfish-cli ...` always wins.
    explicit_user = os.environ.get("REDFISH_USER", "").strip()
    explicit_pass = os.environ.get("REDFISH_PASSWORD", "").strip()
    resolved = provider.resolve(
        host=host or "",
        explicit_user=explicit_user if explicit_user and explicit_pass else None,
        explicit_password=explicit_pass if explicit_user and explicit_pass else None,
    )
    if resolved is None:
        return None
    logger.debug("Resolved Redfish credentials: %s", resolved.audit.as_log_fields())
    return (resolved.credentials.user, resolved.credentials.password)


class AgentController:
    """Central hook point for observing tool calls and attaching response meta.

    This will be extended to:
    - record stats to on-disk storage
    - accept agent-provided context via request `_meta`
    - optionally call an LLM for hint generation behind a feature flag
    """

    @staticmethod
    async def _ctx_log(ctx: Context, level: str, message: str) -> None:
        """Best-effort MCP log to the client. Silently ignored if transport lacks support."""
        try:
            await ctx.log(level, message, logger_name="redfish-mcp")  # type: ignore[arg-type]
        except Exception:
            pass

    def __init__(self) -> None:
        site = os.getenv("REDFISH_SITE", "default") or "default"
        self._store: AgentStateStore | None = None
        try:
            self._store = AgentStateStore(site=site)
        except Exception:
            logger.warning(
                "Failed to initialize AgentStateStore; recording disabled", exc_info=True
            )
            self._store = None
        self._hinting = HintingEngine(site=site)
        # Per-client ephemeral cache for creds collected via elicitation.
        # IMPORTANT: This is process-memory only (not persisted). We keep password in-memory
        # to avoid re-prompting, but never write it to disk.
        self._cache_by_client: dict[str, _ClientCache] = {}
        # Fallback cache in case client_id is missing/unstable across calls.
        self._global_cache: _ClientCache = _ClientCache()

    def _client_cache(self, ctx: Context) -> _ClientCache:
        key = "default"
        try:
            if isinstance(ctx.client_id, str) and ctx.client_id.strip():
                key = ctx.client_id.strip()
        except Exception:
            key = "default"
        cache = self._cache_by_client.get(key)
        if cache is None:
            cache = _ClientCache()
            self._cache_by_client[key] = cache
        return cache

    def report_observation(
        self,
        *,
        host: str,
        kind: str,
        summary: str,
        details: dict[str, Any] | None,
        tags: list[str] | None,
        confidence: float | None,
        reporter_id: str | None,
        ttl_hours: int | None,
    ) -> dict[str, Any]:
        if self._store is None:
            return {"ok": False, "error": "state store unavailable"}
        obs_id = self._store.add_observation(
            host_key=host,
            kind=kind,
            summary=summary,
            details=details,
            tags=tags,
            confidence=confidence,
            reporter_id=reporter_id,
            ttl_hours=ttl_hours,
        )
        return {"ok": True, "observation_id": obs_id}

    def list_observations(
        self,
        *,
        host: str,
        limit: int = 20,
        include_expired: bool = False,
    ) -> dict[str, Any]:
        if self._store is None:
            return {"ok": False, "error": "state store unavailable"}
        obs = self._store.list_observations(
            host_key=host,
            limit=limit,
            include_expired=include_expired,
        )
        return {"ok": True, "host": host, "observations": obs, "count": len(obs)}

    def get_host_stats(
        self,
        *,
        host: str,
        window_minutes: int = 60,
    ) -> dict[str, Any]:
        if self._store is None:
            return {"ok": False, "error": "state store unavailable"}
        stats = self._store.get_host_stats(host_key=host, window_minutes=window_minutes)
        return {
            "ok": True,
            "host": host,
            "window_minutes": stats.window_minutes,
            "calls_total": stats.calls_total,
            "calls_error": stats.calls_error,
            "tools_top": stats.tools_top,
            "last_called_at_ms": stats.last_called_at_ms,
        }

    async def on_tool_call(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        context: Context,
        tool_executor,
    ) -> CallToolResult | Any:
        """Execute a tool call with centralized observation and meta support.

        Returns:
          - `CallToolResult` (preferred, to allow `_meta` hints)
          - or any other tool return shape (e.g., CreateTaskResult) to be passed through
        """
        started = time.monotonic()

        hosts = extract_hosts(arguments)
        host_label = ", ".join(hosts) if hosts else "no host"
        await self._ctx_log(context, "info", f"{tool_name} starting ({host_label})")

        # -------------------- Centralized preflight elicitation --------------------
        # This happens before ToolManager validation, so we can fill missing required args.
        preflight = await self._maybe_pre_elicit(
            tool_name=tool_name, arguments=arguments, context=context
        )
        if isinstance(preflight, (CallToolResult, CreateTaskResult)):
            return preflight
        arguments = preflight

        # Request-scoped identifiers (may be None for stdio transports).
        request_id: str | None = None
        client_id: str | None = None
        request_meta: dict[str, Any] = {}
        try:
            request_id = context.request_id
        except Exception:
            request_id = None
        try:
            client_id = context.client_id
        except Exception:
            client_id = None
        try:
            meta_obj = context.request_context.meta
            if meta_obj is not None:
                # `meta_obj` is a Pydantic model with extra fields allowed.
                request_meta = dict(meta_obj.model_dump(by_alias=True))
        except Exception:
            request_meta = {}

        tctx = ToolCallContext(
            tool_name=tool_name,
            arguments=arguments,
            hosts=extract_hosts(arguments),
            request_id=request_id,
            client_id=client_id,
            request_meta=request_meta,
            started_at_monotonic=started,
        )

        ok = True
        results: Any
        hint: Hint | None = None
        try:
            results = await tool_executor(tool_name, arguments, context)
        except Exception as e:
            # Keep error content compact.
            err = str(e)[:2000]
            # Special-case: if auth failed, elicit credentials and retry once.
            if (
                tool_name.startswith("redfish_")
                and not tool_name.startswith("redfish_agent_")
                and ("401" in err and "Unauthorized" in err)
            ):
                try:
                    r = await context.elicit(
                        message="Authentication failed (401). Provide correct Redfish user + password to retry.",
                        schema=_Creds,
                    )
                except Exception:
                    r = None

                if r is not None and getattr(r, "action", None) == "accept":
                    arguments = dict(arguments or {})
                    arguments["user"] = r.data.user
                    arguments["password"] = r.data.password
                    # Update per-host cache so subsequent calls don't re-prompt.
                    now_s = time.time()
                    host = arguments.get("host")
                    if isinstance(host, str) and host.strip():
                        host_key = _norm_host(host)
                        creds = _CachedCreds(
                            user=r.data.user.strip(), password=r.data.password, cached_at_s=now_s
                        )
                        cc = self._client_cache(context)
                        cc.creds_by_host[host_key] = creds
                        cc.last_host = host_key
                        cc.last_creds = creds
                        self._global_cache.creds_by_host[host_key] = creds
                        self._global_cache.last_host = host_key
                        self._global_cache.last_creds = creds
                    try:
                        results = await tool_executor(tool_name, arguments, context)
                        ok = True
                    except Exception as e2:
                        err = str(e2)[:2000]
                        ok = False
                else:
                    ok = False
            else:
                ok = False

            if not ok:
                duration_ms = int((time.monotonic() - started) * 1000)
                if self._store is not None:
                    self._store.record_tool_call(
                        tool_name=tool_name,
                        hosts=tctx.hosts,
                        ok=False,
                        duration_ms=duration_ms,
                        request_id=tctx.request_id,
                        client_id=tctx.client_id,
                        request_meta=tctx.request_meta,
                    )
                return CallToolResult(
                    content=[TextContent(type="text", text=err)],
                    isError=True,
                    structuredContent={
                        "ok": False,
                        "error": err,
                        "tool": tool_name,
                    },
                    _meta={
                        "together.ai/redfish-mcp": {
                            "request_id": tctx.request_id,
                            "client_id": tctx.client_id,
                            "hosts": tctx.hosts,
                        }
                    },
                )
        duration_ms = int((time.monotonic() - started) * 1000)
        await self._ctx_log(context, "info", f"{tool_name} completed in {duration_ms}ms")
        if self._store is not None:
            self._store.record_tool_call(
                tool_name=tool_name,
                hosts=tctx.hosts,
                ok=ok,
                duration_ms=duration_ms,
                request_id=tctx.request_id,
                client_id=tctx.client_id,
                request_meta=tctx.request_meta,
            )

        # Potentially generate a sparse hint after observing the completed call.
        # We only do this when it can genuinely help, and only behind a server flag.
        if self._store is not None and tctx.hosts:
            try:
                hint = await self._hinting.maybe_generate_hint(
                    store=self._store,
                    tool_name=tool_name,
                    host=tctx.hosts[0],
                    client_id=tctx.client_id,
                    request_meta=tctx.request_meta,
                )
            except Exception:
                hint = None

        # -------------------- Centralized postflight elicitation --------------------
        # When hinting suggests capturing reusable knowledge, optionally ask the user
        # whether to store an observation. This is gated by server env to avoid surprises.
        if hint is not None:
            results = await self._maybe_post_elicit_observation(
                tool_name=tool_name,
                results=results,
                hint=hint,
                tctx=tctx,
                context=context,
            )

        # Normalize return into CallToolResult so we can attach `_meta`.
        # FastMCP's ToolManager already returns one of:
        # - Iterable[ContentBlock] OR
        # - (Iterable[ContentBlock], dict[str, Any]) OR
        # - CallToolResult OR
        # - CreateTaskResult (pass-through)
        hint_meta = None
        if hint is not None:
            hint_meta = {
                "hints": [
                    {
                        "type": hint.hint_type,
                        "message": hint.message,
                        "confidence": hint.confidence,
                    }
                ]
            }

        # Pass-through for task responses (task payload is delivered via tasks/result).
        if isinstance(results, CreateTaskResult):
            existing = results.meta or {}
            merged = {
                **(existing if isinstance(existing, dict) else {}),
                "together.ai/redfish-mcp": {
                    "request_id": tctx.request_id,
                    "client_id": tctx.client_id,
                    "hosts": tctx.hosts,
                    **(hint_meta or {}),
                },
            }
            return CreateTaskResult(task=results.task, _meta=merged)

        if isinstance(results, CallToolResult):
            existing = results.meta or {}
            existing_ns = {}
            if isinstance(existing, dict):
                v = existing.get("together.ai/redfish-mcp")
                if isinstance(v, dict):
                    existing_ns = v
            merged = {
                **existing,
                "together.ai/redfish-mcp": {
                    **existing_ns,
                    "request_id": tctx.request_id,
                    "client_id": tctx.client_id,
                    "hosts": tctx.hosts,
                    **(hint_meta or {}),
                },
            }
            return CallToolResult(
                content=results.content,
                structuredContent=results.structuredContent,
                isError=results.isError,
                _meta=merged,
            )

        structured: dict[str, Any] | None = None
        unstructured: Iterable[ContentBlock]

        if isinstance(results, tuple) and len(results) == 2:
            unstructured, structured = results
        elif isinstance(results, dict):
            # Should be rare with convert_result=True, but handle it anyway.
            structured = results
            unstructured = [TextContent(type="text", text=_json_text(results))]
        else:
            if hasattr(results, "__iter__"):
                unstructured = results
            else:
                unstructured = [TextContent(type="text", text=_json_text(results))]

        return CallToolResult(
            content=list(unstructured),
            structuredContent=structured,
            isError=False,
            _meta={
                "together.ai/redfish-mcp": {
                    "request_id": tctx.request_id,
                    "client_id": tctx.client_id,
                    "hosts": tctx.hosts,
                    **(hint_meta or {}),
                }
            },
        )

    async def _maybe_pre_elicit(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        context: Context,
    ) -> dict[str, Any] | CallToolResult | CreateTaskResult:
        """Centralized preflight elicitation for missing args + write confirmation.

        This must be conservative: only ask for clearly required, primitive fields.
        """

        args = dict(arguments or {})

        ttl_s = int(os.getenv("REDFISH_ELICIT_CACHE_TTL_S", "900") or "900")
        now_s = time.time()
        cc = self._client_cache(context)

        def _is_agent_tool() -> bool:
            return tool_name.startswith("redfish_agent_")

        def _is_redfish_tool() -> bool:
            return tool_name.startswith("redfish_")

        def _is_write_tool() -> bool:
            return tool_name in WRITE_TOOLS

        # Skip elicitation for internal coordination tools.
        if _is_agent_tool():
            return args

        # Ask for missing host fields.
        if tool_name == "redfish_diff_bios_settings":

            class _Hosts(BaseModel):
                host_a: str
                host_b: str

            if not (isinstance(args.get("host_a"), str) and args["host_a"].strip()) or not (
                isinstance(args.get("host_b"), str) and args["host_b"].strip()
            ):
                r = await context.elicit(
                    message="Which two hosts should I compare? Provide host_a and host_b.",
                    schema=_Hosts,
                )
                if r.action != "accept":
                    return CallToolResult(
                        content=[
                            TextContent(
                                type="text", text="Missing hosts; elicitation declined/cancelled."
                            )
                        ],
                        isError=True,
                        structuredContent={"ok": False, "error": "missing hosts"},
                    )
                args["host_a"] = r.data.host_a
                args["host_b"] = r.data.host_b

            # Best-effort: if both hosts have cached creds and they match, fill user/password.
            ha = args.get("host_a")
            hb = args.get("host_b")
            if isinstance(ha, str) and isinstance(hb, str):
                ca = cc.creds_by_host.get(_norm_host(ha)) or self._global_cache.creds_by_host.get(
                    _norm_host(ha)
                )
                cb = cc.creds_by_host.get(_norm_host(hb)) or self._global_cache.creds_by_host.get(
                    _norm_host(hb)
                )
                if ca and cb and ca.user == cb.user and ca.password == cb.password:
                    if not (isinstance(args.get("user"), str) and args["user"].strip()):
                        args["user"] = ca.user
                    if not (isinstance(args.get("password"), str) and args["password"].strip()):
                        args["password"] = ca.password

        else:

            class _Host(BaseModel):
                host: str

            if _is_redfish_tool() and not (
                isinstance(args.get("host"), str) and args["host"].strip()
            ):
                r = await context.elicit(
                    message="Which host should I target? Provide host (IP/hostname).",
                    schema=_Host,
                )
                if r.action != "accept":
                    return CallToolResult(
                        content=[
                            TextContent(
                                type="text", text="Missing host; elicitation declined/cancelled."
                            )
                        ],
                        isError=True,
                        structuredContent={"ok": False, "error": "missing host"},
                    )
                args["host"] = r.data.host

        # If we have a host and credentials are missing, try to fill from per-host cache.
        host = args.get("host")
        if _is_redfish_tool() and isinstance(host, str) and host.strip():
            host_key = _norm_host(host)
            cached = cc.creds_by_host.get(host_key)
            if cached is None and ttl_s > 0:
                cached = self._global_cache.creds_by_host.get(host_key)
            if cached and ttl_s > 0 and (now_s - cached.cached_at_s) <= ttl_s:
                if not (isinstance(args.get("user"), str) and args["user"].strip()):
                    args["user"] = cached.user
                if not (isinstance(args.get("password"), str) and args["password"].strip()):
                    args["password"] = cached.password

        # Ask for missing credentials for Redfish tools (but never for agent tools).
        if _is_redfish_tool() and tool_name not in {"redfish_check_bios_online"}:
            missing_user = not (isinstance(args.get("user"), str) and args["user"].strip())
            missing_pass = not (isinstance(args.get("password"), str) and args["password"].strip())

            # Try environment variable credentials before prompting.
            if missing_user or missing_pass:
                env_creds = _resolve_env_credentials(host if isinstance(host, str) else "")
                if env_creds:
                    if missing_user:
                        args["user"] = env_creds[0]
                    if missing_pass:
                        args["password"] = env_creds[1]
                    missing_user = not (isinstance(args.get("user"), str) and args["user"].strip())
                    missing_pass = not (
                        isinstance(args.get("password"), str) and args["password"].strip()
                    )

            if missing_user or missing_pass:
                # If we have a last-used credential, offer reuse first.
                last = cc.last_creds or self._global_cache.last_creds
                if last is not None and ttl_s > 0 and (now_s - last.cached_at_s) <= ttl_s:

                    class _Reuse(BaseModel):
                        reuse_last_credentials: bool

                    reuse_msg = (
                        "Redfish credentials required.\n"
                        "I have cached credentials from a previous host in this session. "
                        "If this host is in the same NetBox *site* as the previous host, "
                        "the Redfish user/password is often the same.\n\n"
                        "Reuse last credentials?"
                    )
                    rr = await context.elicit(message=reuse_msg, schema=_Reuse)
                    if rr.action == "accept" and bool(rr.data.reuse_last_credentials):
                        args["user"] = last.user
                        args["password"] = last.password
                    else:
                        r = await context.elicit(
                            message="Redfish credentials required. Provide user + password.",
                            schema=_Creds,
                        )
                        if r.action != "accept":
                            return CallToolResult(
                                content=[
                                    TextContent(
                                        type="text",
                                        text="Missing credentials; elicitation declined/cancelled.",
                                    )
                                ],
                                isError=True,
                                structuredContent={"ok": False, "error": "missing credentials"},
                            )
                        args["user"] = r.data.user
                        args["password"] = r.data.password
                else:
                    r = await context.elicit(
                        message=(
                            "Redfish credentials required. Provide user + password.\n\n"
                            "Hint: if NetBox MCP is available, hosts in the same NetBox site often share Redfish creds."
                        ),
                        schema=_Creds,
                    )
                    if r.action != "accept":
                        return CallToolResult(
                            content=[
                                TextContent(
                                    type="text",
                                    text="Missing credentials; elicitation declined/cancelled.",
                                )
                            ],
                            isError=True,
                            structuredContent={"ok": False, "error": "missing credentials"},
                        )
                    args["user"] = r.data.user
                    args["password"] = r.data.password

        # Update per-host cache once we have host/user/password.
        host = args.get("host")
        user = args.get("user")
        password = args.get("password")
        if (
            isinstance(host, str)
            and host.strip()
            and isinstance(user, str)
            and user.strip()
            and isinstance(password, str)
            and password.strip()
        ):
            host_key = _norm_host(host)
            creds = _CachedCreds(user=user.strip(), password=password, cached_at_s=now_s)
            cc.creds_by_host[host_key] = creds
            cc.last_host = host_key
            cc.last_creds = creds
            self._global_cache.creds_by_host[host_key] = creds
            self._global_cache.last_host = host_key
            self._global_cache.last_creds = creds

        # Write confirmation (safe-by-default): if allow_write isn't explicitly True, ask.
        if _is_write_tool() and args.get("execution_mode", "execute") != "render_curl":

            class _ConfirmWrite(BaseModel):
                confirm_write: bool

            if args.get("allow_write") is not True:
                r = await context.elicit(
                    message="This is a write operation. Confirm you want to proceed (confirm_write=true).",
                    schema=_ConfirmWrite,
                )
                if r.action != "accept" or not bool(r.data.confirm_write):
                    return CallToolResult(
                        content=[TextContent(type="text", text="Write not confirmed; refusing.")],
                        isError=True,
                        structuredContent={"ok": False, "error": "write not confirmed"},
                    )
                args["allow_write"] = True

        return args

    async def _maybe_post_elicit_observation(
        self,
        *,
        tool_name: str,
        results: Any,
        hint: Hint,
        tctx: ToolCallContext,
        context: Context,
    ) -> Any:
        """Optionally ask whether to store an observation after a tool call."""

        if self._store is None:
            return results

        enabled = os.getenv("REDFISH_POST_ELICIT_OBSERVATION", "0").strip().lower() in {
            "1",
            "true",
            "yes",
            "y",
            "on",
        }
        if not enabled:
            return results

        if hint.hint_type != "suggest_report_observation":
            return results

        # Only prompt when we have a host to associate.
        host = tctx.hosts[0] if tctx.hosts else None
        if not host:
            return results

        class _Obs(BaseModel):
            store_observation: bool
            summary: str | None = None
            kind: str | None = None

        msg = (
            "I can store a short observation about this host for future reuse.\n"
            "Set store_observation=true and optionally provide kind + summary.\n"
            f"Hint: {hint.message}"
        )
        try:
            r = await context.elicit(message=msg, schema=_Obs)
        except Exception:
            return results

        if r.action != "accept" or not bool(r.data.store_observation):
            return results

        summary = (r.data.summary or hint.message or "").strip()
        if not summary:
            return results

        kind = (r.data.kind or "note").strip() or "note"
        try:
            self._store.add_observation(
                host_key=host,
                kind=kind,
                summary=summary,
                details=None,
                tags=None,
                confidence=hint.confidence,
                reporter_id=tctx.client_id,
                ttl_hours=72,
            )
        except Exception:
            return results

        return results
