"""Tests for the JSON line-framed socket protocol."""

from __future__ import annotations

import json

import pytest

from redfish_mcp.kvm.protocol import (
    ErrorPayload,
    ProtocolError,
    Request,
    Response,
    decode_message,
    encode_message,
)


class TestRequestEncoding:
    def test_request_roundtrip(self):
        req = Request(id=1, method="open", params={"host": "x"})
        line = encode_message(req)
        assert line.endswith(b"\n")
        assert b"\n" not in line[:-1]
        back = decode_message(line)
        assert isinstance(back, Request)
        assert back.id == 1
        assert back.method == "open"
        assert back.params == {"host": "x"}


class TestResponseEncoding:
    def test_success_result(self):
        resp = Response(id=2, result={"png_b64": "AAA"})
        line = encode_message(resp)
        back = decode_message(line)
        assert isinstance(back, Response)
        assert back.id == 2
        assert back.result == {"png_b64": "AAA"}
        assert back.error is None
        assert back.progress is None

    def test_error_payload(self):
        resp = Response(
            id=3,
            error=ErrorPayload(code="auth_failed", message="bad creds", stage="authenticating"),
        )
        line = encode_message(resp)
        back = decode_message(line)
        assert isinstance(back, Response)
        assert back.error is not None
        assert back.error.code == "auth_failed"
        assert back.error.stage == "authenticating"

    def test_progress_envelope(self):
        resp = Response(id=4, progress={"stage": "starting_vnc", "detail": ""})
        line = encode_message(resp)
        back = decode_message(line)
        assert isinstance(back, Response)
        assert back.progress == {"stage": "starting_vnc", "detail": ""}


class TestDecodeErrors:
    def test_malformed_json_raises_protocol_error(self):
        with pytest.raises(ProtocolError):
            decode_message(b"not-json\n")

    def test_missing_id_raises(self):
        with pytest.raises(ProtocolError):
            decode_message(json.dumps({"method": "x"}).encode() + b"\n")

    def test_request_and_response_shape_conflict(self):
        with pytest.raises(ProtocolError):
            decode_message(json.dumps({"id": 1, "method": "x", "result": {}}).encode() + b"\n")
