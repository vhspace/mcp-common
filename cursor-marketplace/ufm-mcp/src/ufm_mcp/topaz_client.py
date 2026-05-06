"""Client for the Topaz fabric health gRPC service.

All gRPC and protobuf imports are deferred to first use so the MCP server
can start and serve non-Topaz tools even when grpcio / protobuf are missing
or at an incompatible version.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _load_grpc():  # type: ignore[no-untyped-def]
    """Lazily import grpc and the generated proto stubs."""
    import grpc
    from google.protobuf.json_format import MessageToDict

    from ufm_mcp.proto import health_pb2, health_pb2_grpc

    return grpc, MessageToDict, health_pb2, health_pb2_grpc


def _msg_to_dict(response: Any) -> dict[str, Any]:
    from google.protobuf.json_format import MessageToDict

    return MessageToDict(response, preserving_proto_field_name=True)


class TopazClient:
    """Thin wrapper around the Topaz HealthService gRPC stub."""

    def __init__(self, endpoint: str) -> None:
        grpc, _, _, health_pb2_grpc = _load_grpc()
        self._endpoint = endpoint
        self._channel = grpc.insecure_channel(
            endpoint,
            options=[
                ("grpc.max_send_message_length", 64 * 1024 * 1024),
                ("grpc.max_receive_message_length", 64 * 1024 * 1024),
            ],
        )
        self._stub = health_pb2_grpc.HealthServiceStub(self._channel)
        self._grpc = grpc

    def __enter__(self) -> TopazClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def close(self) -> None:
        self._channel.close()

    def get_fabric_health(self, az_id: str) -> dict[str, Any]:
        _, _, health_pb2, _ = _load_grpc()
        try:
            request = health_pb2.GetFabricHealthRequest(az_id=az_id)
            response = self._stub.GetFabricHealth(request, timeout=30)
            return _msg_to_dict(response)
        except self._grpc.RpcError as exc:
            return _grpc_error_dict("GetFabricHealth", exc)

    def list_switches(self, az_id: str, errors_only: bool = False) -> dict[str, Any]:
        _, _, health_pb2, _ = _load_grpc()
        try:
            request = health_pb2.ListSwitchesRequest(az_id=az_id, errors_only=errors_only)
            response = self._stub.ListSwitches(request, timeout=30)
            return _msg_to_dict(response)
        except self._grpc.RpcError as exc:
            return _grpc_error_dict("ListSwitches", exc)

    def list_port_counters(
        self,
        az_id: str,
        errors_only: bool = False,
        guid_filter: str | None = None,
    ) -> dict[str, Any]:
        _, _, health_pb2, _ = _load_grpc()
        try:
            request = health_pb2.ListPortCountersRequest(
                az_id=az_id,
                errors_only=errors_only,
                guid_filter=guid_filter or "",
            )
            response = self._stub.ListPortCounters(request, timeout=30)
            return _msg_to_dict(response)
        except self._grpc.RpcError as exc:
            return _grpc_error_dict("ListPortCounters", exc)

    def list_cables(self, az_id: str, alarms_only: bool = False) -> dict[str, Any]:
        _, _, health_pb2, _ = _load_grpc()
        try:
            request = health_pb2.ListCablesRequest(az_id=az_id, alarms_only=alarms_only)
            response = self._stub.ListCables(request, timeout=30)
            return _msg_to_dict(response)
        except self._grpc.RpcError as exc:
            return _grpc_error_dict("ListCables", exc)

    def upload_ibdiagnet(
        self,
        az_id: str,
        tarball_data: bytes,
        filename: str = "",
    ) -> dict[str, Any]:
        _, _, health_pb2, _ = _load_grpc()
        try:
            request = health_pb2.UploadIbdiagnetRequest(
                tarball_data=tarball_data,
                az_id=az_id,
                filename=filename,
            )
            response = self._stub.UploadIbdiagnet(request, timeout=120)
            return _msg_to_dict(response)
        except self._grpc.RpcError as exc:
            return _grpc_error_dict("UploadIbdiagnet", exc)


def _grpc_error_dict(method: str, exc: object) -> dict[str, Any]:
    code = exc.code() if hasattr(exc, "code") else "UNKNOWN"
    details = exc.details() if hasattr(exc, "details") else str(exc)
    return {
        "ok": False,
        "error": f"Topaz gRPC error in {method}",
        "grpc_code": str(code),
        "grpc_details": details,
    }
