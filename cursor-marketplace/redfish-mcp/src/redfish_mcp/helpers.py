"""Common helpers for DRY code in MCP server tools."""

from __future__ import annotations

from typing import Any


class ResponseBuilder:
    """Helper for building consistent tool responses."""

    @staticmethod
    def success(data: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
        """Build a success response."""
        result: dict[str, Any] = {"ok": True}
        if data:
            result.update(data)
        result.update(kwargs)
        return result

    @staticmethod
    def error(message: str, **kwargs: Any) -> dict[str, Any]:
        """Build an error response."""
        result: dict[str, Any] = {"ok": False, "error": message}
        result.update(kwargs)
        return result


class CurlCommandBuilder:
    """Helper for generating curl command examples."""

    def __init__(self, verify_tls: bool = False) -> None:
        self.verify_tls = verify_tls

    def _base(self) -> str:
        """Return base curl flags."""
        return "curl -sS" if self.verify_tls else "curl -sSk"

    @staticmethod
    def _auth() -> str:
        """Return authentication string using env vars."""
        return '-u "$REDFISH_USER:$REDFISH_PASSWORD"'

    @staticmethod
    def _url(path: str) -> str:
        """Build full URL with host from env var."""
        if not path.startswith("/"):
            path = "/" + path
        return f"https://$REDFISH_IP{path}"

    def get(self, path: str) -> str:
        """Generate a GET curl command."""
        return " \\\n  ".join([self._base(), self._auth(), f'"{self._url(path)}"'])

    def request(self, method: str, path: str, body_json: str | None = None) -> str:
        """Generate a curl command for any HTTP method."""
        parts = [self._base(), self._auth(), f"-X {method.upper()}"]
        if body_json is not None:
            parts.append("-H 'Content-Type: application/json'")
            parts.append(f"-d '{body_json}'")
        parts.append(f'"{self._url(path)}"')
        return " \\\n  ".join(parts)

    def patch(self, path: str, body_json: str) -> str:
        """Generate a PATCH curl command."""
        return self.request("PATCH", path, body_json)

    def post(self, path: str, body_json: str) -> str:
        """Generate a POST curl command."""
        return self.request("POST", path, body_json)


def execution_mode_handler(verify_tls: bool, curl_commands: list[str]) -> dict[str, Any]:
    """Handle execution_mode='render_curl' responses consistently."""
    return ResponseBuilder.success(execution_mode="render_curl", curl=curl_commands)


class SystemFetcher:
    """Helper for fetching and caching system information."""

    def __init__(self, client: Any, endpoint: Any) -> None:
        self.client = client
        self.endpoint = endpoint
        self._system_cache: dict[str, Any] | None = None
        self._system_error: str | None = None

    def get_system(self) -> tuple[dict[str, Any] | None, str | None]:
        """Get system info (cached after first call)."""
        if self._system_cache is not None or self._system_error is not None:
            return self._system_cache, self._system_error

        self._system_cache, self._system_error = self.client.get_json_maybe(
            self.endpoint.system_url
        )
        return self._system_cache, self._system_error

    def get_system_or_error_response(
        self, host: str
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        """Get system or return error response dict. Returns (system_dict, error_response_dict)."""
        system, error = self.get_system()
        if error or not system:
            return None, ResponseBuilder.error(
                error or "Failed to get system info", host=host, system_url=self.endpoint.system_url
            )
        return system, None
