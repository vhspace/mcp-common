from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from .agent_state_store import AgentStateStore
from .together_llm import TogetherChatClient


def _env_bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "y", "on"}


@dataclass(frozen=True)
class Hint:
    hint_type: str
    message: str
    confidence: float | None = None


class HintingEngine:
    """Generate sparse, high-confidence hints using an LLM (Together/Qwen)."""

    def __init__(self, *, site: str) -> None:
        self.site = site
        self.enabled = _env_bool("REDFISH_HINTING_ENABLED", default=False)

        self.model = os.getenv(
            "REDFISH_HINTING_MODEL",
            "Qwen/Qwen3-235B-A22B-Instruct-2507-tput",
        )
        self.key = os.getenv("TOGETHER_INFERENCE_KEY", "").strip()
        self.timeout_s = int(os.getenv("REDFISH_HINTING_TIMEOUT_S", "8") or "8")

        # Gating thresholds (cheap checks, not hint selection).
        self.window_minutes = int(os.getenv("REDFISH_HINTING_WINDOW_MINUTES", "10") or "10")
        self.min_calls = int(os.getenv("REDFISH_HINTING_MIN_CALLS", "6") or "6")
        self.min_errors = int(os.getenv("REDFISH_HINTING_MIN_ERRORS", "3") or "3")
        self.cooldown_s = int(os.getenv("REDFISH_HINTING_COOLDOWN_S", "3600") or "3600")

        self._llm: TogetherChatClient | None = None

    def available(self) -> bool:
        return bool(self.enabled and self.key)

    def _client(self) -> TogetherChatClient:
        if self._llm is None:
            self._llm = TogetherChatClient(
                api_key=self.key,
                model=self.model,
                timeout_s=self.timeout_s,
            )
        return self._llm

    async def maybe_generate_hint(
        self,
        *,
        store: AgentStateStore,
        tool_name: str,
        host: str,
        client_id: str | None,
        request_meta: dict[str, Any],
    ) -> Hint | None:
        if not self.available():
            return None

        host_key = host.lower()

        # Cheap gating: focus and/or trouble, plus cooldown.
        stats = store.get_host_stats(
            host_key=host_key,
            window_minutes=self.window_minutes,
        )
        if stats.calls_total < self.min_calls and stats.calls_error < self.min_errors:
            return None

        hint_type = "suggest_report_observation"
        if store.hint_in_cooldown(
            host_key=host_key,
            hint_type=hint_type,
            client_id=client_id,
        ):
            return None

        existing_obs = store.list_observations(
            host_key=host_key,
            limit=1,
            include_expired=False,
        )
        if existing_obs:
            # For now, we only hint when the store is missing observations for this host.
            return None

        # Only pass safe, non-secret context to the LLM.
        agent_signal = None
        try:
            agent_signal = (request_meta or {}).get("together.ai/redfish-mcp")
        except Exception:
            agent_signal = None

        prompt = (
            "You are the hinting component inside a Redfish MCP server. "
            "Your goal is to emit ONE short, high-signal hint that helps an agent "
            "reduce redundant tool calls and capture reusable knowledge. "
            "Never ask for secrets. Keep it under 200 characters.\n\n"
            f"Site: {self.site}\n"
            f"Host: {host_key}\n"
            f"Tool being called now: {tool_name}\n"
            f"Recent calls to this host (last {stats.window_minutes}m): "
            f"{stats.calls_total}\n"
            f"Recent errors to this host (last {stats.window_minutes}m): "
            f"{stats.calls_error}\n"
            f"Existing observations stored: 0\n"
            f"Caller agent meta (optional): {agent_signal}\n\n"
            "Return a single sentence hint that suggests using "
            "`redfish_agent_report_observation` with a brief summary of what they've learned."
        )

        msg = await self._client().chat(prompt)
        msg = (msg or "").strip().replace("\n", " ")
        if not msg:
            return None
        if len(msg) > 220:
            msg = msg[:220].rstrip()

        # Set cooldown after we successfully produce a hint.
        store.set_hint_cooldown(
            host_key=host_key,
            hint_type=hint_type,
            client_id=client_id,
            cooldown_seconds=self.cooldown_s,
        )

        return Hint(hint_type=hint_type, message=msg, confidence=0.7)
