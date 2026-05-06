from __future__ import annotations

import asyncio
import os
from typing import Any

import requests


class TogetherChatClient:
    """Minimal Together Chat Completions client (OpenAI-compatible)."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout_s: int = 8,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.timeout_s = timeout_s
        self.base_url = os.getenv(
            "TOGETHER_API_BASE",
            "https://api.together.xyz",
        ).rstrip("/")

    async def chat(self, prompt: str) -> str:
        def _run() -> str:
            url = f"{self.base_url}/v1/chat/completions"
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }
            payload: dict[str, Any] = {
                "model": self.model,
                "messages": [
                    {
                        "role": "system",
                        "content": "You are a concise, accurate assistant.",
                    },
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": 120,
                "temperature": 0.2,
            }
            resp = requests.post(
                url,
                headers=headers,
                json=payload,
                timeout=self.timeout_s,
            )
            resp.raise_for_status()
            data = resp.json()
            choices = data.get("choices") or []
            if not choices:
                return ""
            msg = choices[0].get("message") or {}
            content = msg.get("content")
            return content if isinstance(content, str) else ""

        return await asyncio.to_thread(_run)
