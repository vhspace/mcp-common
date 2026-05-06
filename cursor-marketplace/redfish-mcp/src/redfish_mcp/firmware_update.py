"""Firmware update helpers for Redfish UpdateService upload/task flow."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from fastmcp import Context
from mcp_common import OperationStates, PollResult, poll_with_progress

from .redfish import RedfishClient

logger = logging.getLogger("redfish_mcp.firmware_update")


def normalize_task_url(base_url: str, location: str | None) -> str | None:
    if not location:
        return None
    location = location.strip()
    if not location:
        return None
    if location.startswith(("http://", "https://")):
        return location
    if not location.startswith("/"):
        location = "/" + location
    return f"{base_url}{location}"


def extract_task_url_from_upload(response, base_url: str) -> str | None:
    # Preferred Redfish header.
    task_url = normalize_task_url(base_url, response.headers.get("Location"))
    if task_url:
        return task_url

    # Some implementations return @odata.id in body.
    try:
        body = response.json()
        task_url = normalize_task_url(base_url, body.get("@odata.id"))
        if task_url:
            return task_url
        task_url = normalize_task_url(base_url, body.get("TaskMonitor"))
        if task_url:
            return task_url
    except Exception:
        pass
    return None


def upload_firmware_image(
    c: RedfishClient,
    image_path: str,
    targets: list[str] | None = None,
    apply_time: str = "Immediate",
    update_parameters_oem: dict[str, Any] | None = None,
    request_timeout_s: int = 300,
) -> dict[str, Any]:
    """Upload firmware binary to UpdateService/upload.

    Returns upload metadata, task URL (if present), and raw HTTP status.
    """
    update_parameters: dict[str, Any] = {"@Redfish.OperationApplyTime": apply_time}
    if targets:
        update_parameters["Targets"] = targets
    if update_parameters_oem:
        update_parameters["Oem"] = update_parameters_oem

    try:
        logger.info(
            "Uploading firmware image=%s targets=%s apply_time=%s", image_path, targets, apply_time
        )
        with open(image_path, "rb") as f:
            files = {
                "UpdateFile": f,
                "UpdateParameters": (None, json.dumps(update_parameters)),
            }
            resp = c.session.post(
                f"{c.base_url}/redfish/v1/UpdateService/upload",
                files=files,
                timeout=request_timeout_s,
            )
    except Exception as e:
        logger.error("Firmware upload failed: %s", e)
        return {
            "ok": False,
            "error": f"Failed to upload firmware image: {str(e)[:300]}",
            "task_url": None,
            "update_parameters": update_parameters,
        }

    task_url = extract_task_url_from_upload(resp, c.base_url)
    logger.info("Firmware upload completed status=%d task_url=%s", resp.status_code, task_url)
    return {
        "ok": resp.status_code in (100, 200, 201, 202, 204),
        "http_status": resp.status_code,
        "task_url": task_url,
        "response_text": (resp.text or "")[:2000],
        "update_parameters": update_parameters,
    }


def wait_for_task_completion(
    c: RedfishClient,
    task_url: str,
    timeout_s: int = 3600,
    poll_interval_s: int = 10,
) -> dict[str, Any]:
    """Poll a Task or TaskMonitor URL until completion or timeout."""
    logger.info(
        "Polling task_url=%s timeout=%ds poll_interval=%ds", task_url, timeout_s, poll_interval_s
    )
    deadline = time.time() + timeout_s
    history: list[dict[str, Any]] = []
    saw_successful_poll = False
    is_task_monitor = "taskmonitor" in task_url.lower()

    while time.time() < deadline:
        try:
            r = c.session.get(task_url, timeout=c.timeout_s)

            # Some TaskMonitor endpoints return 404 after completion.
            if r.status_code == 404:
                history.append(
                    {
                        "state": "NotFound",
                        "message": "Task endpoint returned 404",
                    }
                )
                if is_task_monitor and saw_successful_poll:
                    return {
                        "ok": True,
                        "task_state": "Completed",
                        "history": history,
                        "note": "TaskMonitor disappeared after prior successful poll",
                    }
                return {
                    "ok": False,
                    "task_state": "NotFound",
                    "history": history,
                    "note": "Task endpoint disappeared before completion was observed",
                }

            if r.status_code >= 400:
                history.append(
                    {
                        "state": f"HTTP_{r.status_code}",
                        "message": (r.text or "")[:300],
                    }
                )
                time.sleep(poll_interval_s)
                continue

            saw_successful_poll = True
            state = "Unknown"
            message = ""
            try:
                task_body = r.json()
                state = task_body.get("TaskState", "Unknown")
                msgs = task_body.get("Messages") or []
                if msgs and isinstance(msgs, list) and isinstance(msgs[0], dict):
                    message = msgs[0].get("Message", "")
            except Exception:
                # Non-JSON task monitor content; keep polling briefly.
                pass

            history.append({"state": state, "message": message})
            if state == "Completed":
                return {"ok": True, "task_state": state, "history": history}
            if state in ("Exception", "Killed", "Cancelled"):
                return {"ok": False, "task_state": state, "history": history}
        except Exception as e:
            history.append({"state": "PollError", "message": str(e)[:300]})
        time.sleep(poll_interval_s)

    return {"ok": False, "task_state": "Timeout", "history": history}


_FIRMWARE_TASK_STATES = OperationStates(
    success=["Completed"],
    failure=["Exception", "Killed", "Cancelled"],
    in_progress=["Running", "New", "Pending", "Starting", "Stopping", "Suspended"],
)


def _check_redfish_task(c: RedfishClient, task_url: str) -> dict[str, Any]:
    """Single sync poll of a Redfish task URL. Returns a state dict."""
    try:
        r = c.session.get(task_url, timeout=c.timeout_s)
        if r.status_code == 404:
            return {"status": "NotFound", "http_status": 404}
        if r.status_code >= 400:
            return {"status": f"HTTP_{r.status_code}", "http_status": r.status_code}
        try:
            body = r.json()
            state = body.get("TaskState", "Unknown")
            msgs = body.get("Messages") or []
            message = ""
            if msgs and isinstance(msgs, list) and isinstance(msgs[0], dict):
                message = msgs[0].get("Message", "")
            return {"status": state, "message": message, "http_status": r.status_code}
        except Exception:
            return {"status": "Unknown", "http_status": r.status_code}
    except Exception as e:
        return {"status": "PollError", "error": str(e)[:300]}


async def poll_firmware_task(
    ctx: Context,
    c: RedfishClient,
    task_url: str,
    timeout_s: int = 3600,
    poll_interval_s: int = 10,
) -> dict[str, Any]:
    """Async firmware task polling with MCP progress notifications."""
    import asyncio

    def check_fn() -> dict[str, Any]:
        return _check_redfish_task(c, task_url)

    def fmt(state: dict[str, Any], elapsed: float) -> str:
        s = state.get("status", "unknown")
        msg = state.get("message", "")
        if msg:
            return f"Firmware task: {s} - {msg} ({elapsed:.0f}s)"
        return f"Firmware task: {s} ({elapsed:.0f}s)"

    result: PollResult = await poll_with_progress(
        ctx,
        lambda: asyncio.get_event_loop().run_in_executor(None, check_fn),
        "status",
        _FIRMWARE_TASK_STATES,
        timeout_s=float(timeout_s),
        interval_s=float(poll_interval_s),
        format_message=fmt,
    )

    return {
        "ok": result.ok,
        "task_state": result.final_state,
        "elapsed_s": result.elapsed_s,
        "timed_out": result.timed_out,
        "last_poll": result.extra,
    }
