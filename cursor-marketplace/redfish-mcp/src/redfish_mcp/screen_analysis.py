"""LLM-powered screen analysis for BMC console screenshots.

Uses Together AI vision models to extract structured information
(screen type, boot stage, errors, diagnostics) from VGA framebuffer captures.
"""

from __future__ import annotations

import base64
import json
import logging
import re
from typing import Any

import requests

from redfish_mcp.vision import DEFAULT_MODEL, TOGETHER_API_URL, _get_api_key

logger = logging.getLogger("redfish_mcp.screen_analysis")

SCREEN_TYPES = (
    "blank_screen",
    "bios_splash",
    "bios_post",
    "bios_setup",
    "boot_menu",
    "pxe_boot",
    "grub_menu",
    "linux_boot",
    "login_prompt",
    "kernel_panic",
    "firmware_update",
    "error_screen",
    "unknown",
)

BOOT_STAGES = (
    "power_off",
    "bios_init",
    "post",
    "post_complete",
    "bootloader",
    "pxe",
    "os_loading",
    "os_booted",
    "login_ready",
    "kernel_panic",
    "firmware_update",
    "bios_setup",
    "stuck",
    "unknown",
)

SEVERITIES = ("info", "warning", "critical")

_KVM_OVERLAY_NOTE = (
    "NOTE: BMC KVM overlays like 'HID init...', 'Initializing USB device', "
    "or 'KVM session' are artifacts of the KVM-over-IP connection — they are NOT "
    "host console output. Ignore them when analyzing screen content. "
)

_SUMMARY_SYSTEM = (
    "You are a server BMC console screenshot analyzer. "
    + _KVM_OVERLAY_NOTE
    + "Return ONLY a JSON object with these fields: "
    '"summary" (string, one-sentence description of what the screen shows), '
    '"screen_type" (one of: ' + ", ".join(SCREEN_TYPES) + "), "
    '"is_interactive" (boolean, true if the screen expects '
    "user input), "
    '"needs_attention" (boolean, true if the screen shows errors or is stuck).'
)

_ANALYSIS_SYSTEM = (
    "You are a server BMC console screenshot analyzer. "
    + _KVM_OVERLAY_NOTE
    + "Return ONLY a JSON object with these fields: "
    '"summary" (string, one-sentence description), '
    '"screen_type" (one of: ' + ", ".join(SCREEN_TYPES) + "), "
    '"is_interactive" (boolean), '
    '"needs_attention" (boolean), '
    '"boot_stage" (one of: ' + ", ".join(BOOT_STAGES) + "), "
    '"errors" (list of strings — deduplicate repeated messages, max 5 unique entries), '
    '"warnings" (list of strings — deduplicate repeated messages, max 5 unique entries), '
    '"key_values" (object, notable key-value pairs shown on screen), '
    '"menu_selection" (string or null, currently highlighted menu item if any), '
    '"progress_pct" (integer 0-100 or null, progress percentage if visible).'
)

_DIAGNOSIS_SYSTEM = (
    "You are a server BMC console screenshot analyzer and diagnostician. "
    + _KVM_OVERLAY_NOTE
    + "Return ONLY a JSON object with these fields: "
    '"summary" (string, one-sentence description), '
    '"screen_type" (one of: ' + ", ".join(SCREEN_TYPES) + "), "
    '"is_interactive" (boolean), '
    '"needs_attention" (boolean), '
    '"boot_stage" (one of: ' + ", ".join(BOOT_STAGES) + "), "
    '"errors" (list of strings — deduplicate repeated messages, max 5 unique entries), '
    '"warnings" (list of strings — deduplicate repeated messages, max 5 unique entries), '
    '"key_values" (object), '
    '"menu_selection" (string or null), '
    '"progress_pct" (integer 0-100 or null), '
    '"diagnosis" (string, explanation of what is happening and why), '
    '"suggested_actions" (list of strings, recommended next steps), '
    '"severity" (one of: ' + ", ".join(SEVERITIES) + ")."
)

_USER_PROMPT = "Analyze this BMC VGA console screenshot."

_MODES: dict[str, tuple[str, str, int, int]] = {
    "summary": (_SUMMARY_SYSTEM, _USER_PROMPT, 150, 90),
    "analysis": (_ANALYSIS_SYSTEM, _USER_PROMPT, 500, 120),
    "diagnosis": (_DIAGNOSIS_SYSTEM, _USER_PROMPT, 800, 180),
}

_FENCE_RE = re.compile(r"^```(?:json)?\s*\n?(.*?)\n?\s*```$", re.DOTALL | re.IGNORECASE)


def _strip_markdown_fences(text: str) -> str:
    """Strip ```json ... ``` or ``` ... ``` fences from model output."""
    text = text.strip()
    m = _FENCE_RE.match(text)
    return m.group(1).strip() if m else text


def _repair_truncated_json(text: str) -> str | None:
    """Try to close a JSON object truncated by max_tokens.

    When the LLM hits the token limit mid-JSON, we often get valid JSON
    up to a point with unclosed brackets/strings.  This attempts a
    best-effort repair by:
      1. Stripping a trailing partial value (incomplete string/number)
      2. Closing any unbalanced brackets/braces
    Returns the repaired string, or None if repair seems hopeless.
    """
    s = text.rstrip()
    if not s.startswith("{"):
        return None

    s = re.sub(r",\s*$", "", s)
    s = re.sub(r':\s*"[^"]*$', ": null", s)
    s = re.sub(r":\s*$", ": null", s)
    s = re.sub(r'"[^"]*$', '"truncated"', s)

    opens = 0
    in_str = False
    escape = False
    for ch in s:
        if escape:
            escape = False
            continue
        if ch == "\\" and in_str:
            escape = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch in ("{", "["):
            opens += 1
        elif ch in ("}", "]"):
            opens -= 1

    if opens <= 0:
        return None

    closers = {"{": "}", "[": "]"}
    stack: list[str] = []
    in_str = False
    escape = False
    for ch in s:
        if escape:
            escape = False
            continue
        if ch == "\\" and in_str:
            escape = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch in closers:
            stack.append(closers[ch])
        elif ch in ("}", "]"):
            if stack and stack[-1] == ch:
                stack.pop()

    s += "".join(reversed(stack))
    return s


def analyze_screenshot(
    image_bytes: bytes,
    mime_type: str = "image/jpeg",
    mode: str = "summary",
    *,
    model: str | None = None,
    max_tokens: int | None = None,
    timeout_s: int | None = None,
) -> dict[str, Any]:
    """Analyze a BMC console screenshot and return structured data.

    Parameters
    ----------
    image_bytes:
        Raw image data (JPEG/PNG).
    mime_type:
        MIME type of the image.
    mode:
        Analysis depth — ``"summary"``, ``"analysis"``, or ``"diagnosis"``.
    model:
        Together AI vision model. Defaults to ``DEFAULT_MODEL``.
    max_tokens:
        Override the per-mode default max_tokens.
    timeout_s:
        HTTP request timeout in seconds.  ``None`` uses the per-mode
        default (90 s for summary, 120 s for analysis, 180 s for diagnosis)
        which accounts for thinking-model reasoning overhead on vision requests.

    Returns
    -------
    dict
        Parsed JSON from the model. Contains ``_parse_error: True`` if the
        model output could not be parsed as JSON.
    """
    if mode not in _MODES:
        raise ValueError(f"Invalid mode {mode!r}, expected one of {list(_MODES)}")

    system_prompt, user_prompt, default_max_tokens, default_timeout = _MODES[mode]
    model = model or DEFAULT_MODEL
    max_tokens = max_tokens or default_max_tokens
    timeout_s = timeout_s if timeout_s is not None else default_timeout

    api_key = _get_api_key()
    b64 = base64.b64encode(image_bytes).decode("ascii")
    data_uri = f"data:{mime_type};base64,{b64}"

    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_prompt},
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
    raw = msg.get("content", "").strip()
    usage = body.get("usage", {})
    logger.info(
        "Screen analysis completed: mode=%s, model=%s, prompt_tokens=%s, completion_tokens=%s",
        mode,
        model,
        usage.get("prompt_tokens"),
        usage.get("completion_tokens"),
    )

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    stripped = _strip_markdown_fences(raw)
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass

    repaired = _repair_truncated_json(stripped or raw)
    if repaired:
        try:
            result = json.loads(repaired)
            result["_repaired"] = True
            logger.info("Repaired truncated JSON from model output")
            return result
        except json.JSONDecodeError:
            pass

    logger.warning("Failed to parse model output as JSON: %.200s", raw)
    return {"_raw": raw, "_parse_error": True}
