"""Line-delimited JSON RPC envelopes for the KVM daemon."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


class ProtocolError(Exception):
    """Raised when a wire message cannot be parsed into a valid envelope."""


@dataclass(frozen=True)
class Request:
    id: int
    method: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ErrorPayload:
    code: str
    message: str
    stage: str | None = None


@dataclass(frozen=True)
class Response:
    id: int
    result: dict[str, Any] | None = None
    error: ErrorPayload | None = None
    progress: dict[str, Any] | None = None


def encode_message(msg: Request | Response) -> bytes:
    if isinstance(msg, Request):
        payload: dict[str, Any] = {"id": msg.id, "method": msg.method, "params": msg.params}
    else:
        payload = {"id": msg.id}
        if msg.result is not None:
            payload["result"] = msg.result
        if msg.error is not None:
            payload["error"] = {
                "code": msg.error.code,
                "message": msg.error.message,
                "stage": msg.error.stage,
            }
        if msg.progress is not None:
            payload["progress"] = msg.progress
    line = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    return line.encode("utf-8") + b"\n"


def decode_message(line: bytes) -> Request | Response:
    try:
        payload = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ProtocolError(f"invalid JSON: {exc}") from exc
    if not isinstance(payload, dict) or "id" not in payload:
        raise ProtocolError("missing 'id' field")

    is_request = "method" in payload
    has_response_fields = any(k in payload for k in ("result", "error", "progress"))

    if is_request and has_response_fields:
        raise ProtocolError("message has both request and response fields")
    if is_request:
        return Request(
            id=int(payload["id"]),
            method=str(payload["method"]),
            params=dict(payload.get("params") or {}),
        )

    err_raw = payload.get("error")
    err: ErrorPayload | None = None
    if err_raw is not None:
        err = ErrorPayload(
            code=str(err_raw["code"]),
            message=str(err_raw["message"]),
            stage=err_raw.get("stage"),
        )
    return Response(
        id=int(payload["id"]),
        result=payload.get("result"),
        error=err,
        progress=payload.get("progress"),
    )
