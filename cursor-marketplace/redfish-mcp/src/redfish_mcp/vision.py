"""OCR / text extraction from BMC screenshots via Together AI vision models.

Uses the Together Inference API (OpenAI-compatible) with a lightweight
vision model to extract on-screen text from VGA framebuffer captures.
No additional dependencies beyond ``requests`` (already a project dep).
"""

from __future__ import annotations

import base64
import logging
import os

import requests

logger = logging.getLogger("redfish_mcp.vision")

DEFAULT_MODEL = "google/gemma-4-31B-it"
TOGETHER_API_URL = "https://api.together.xyz/v1/chat/completions"

OCR_SYSTEM_PROMPT = (
    "You are a precise OCR assistant for server BMC (Baseboard Management Controller) "
    "VGA console screenshots. Extract ALL visible text from the screen exactly as shown, "
    "preserving layout structure (line breaks, indentation, columns). "
    "Include BIOS menus, POST messages, boot logs, error messages, and any status text. "
    "Do not add commentary or interpretation—only output the raw text content."
)

OCR_USER_PROMPT = (
    "Extract all text visible on this BMC VGA console screenshot. "
    "Preserve the original layout as closely as possible."
)


def _get_api_key() -> str:
    key = os.getenv("TOGETHER_API_KEY") or os.getenv("TOGETHER_INFERENCE_KEY") or ""
    if not key:
        raise RuntimeError(
            "Together API key not found. Set TOGETHER_API_KEY or TOGETHER_INFERENCE_KEY."
        )
    return key


def extract_text_from_screenshot(
    image_bytes: bytes,
    mime_type: str = "image/jpeg",
    *,
    model: str = DEFAULT_MODEL,
    max_tokens: int = 4096,
    timeout_s: int = 120,
) -> str:
    """Send a screenshot to Together AI vision model and return extracted text.

    Uses the chat completions API with an inline base64 image.
    The default model (Gemma 4 31B) is serverless, reasonably priced
    ($0.20/1M input tokens), and well-suited for OCR of structured
    BIOS/console screens.

    The default timeout is 120 s to accommodate thinking-model reasoning
    overhead on vision requests (the model generates hidden reasoning
    tokens before producing visible OCR output).
    """
    api_key = _get_api_key()
    b64 = base64.b64encode(image_bytes).decode("ascii")
    data_uri = f"data:{mime_type};base64,{b64}"

    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": OCR_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": OCR_USER_PROMPT},
                    {"type": "image_url", "image_url": {"url": data_uri}},
                ],
            },
        ],
    }

    resp = requests.post(
        TOGETHER_API_URL,
        json=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        timeout=timeout_s,
    )
    resp.raise_for_status()

    body = resp.json()
    choices = body.get("choices", [])
    if not choices:
        raise RuntimeError(f"Together API returned no choices: {body}")

    msg = choices[0].get("message", {})
    text = msg.get("content") or msg.get("reasoning") or ""
    usage = body.get("usage", {})
    logger.info(
        "Vision OCR completed: model=%s, prompt_tokens=%s, completion_tokens=%s",
        model,
        usage.get("prompt_tokens"),
        usage.get("completion_tokens"),
    )
    return text.strip()
