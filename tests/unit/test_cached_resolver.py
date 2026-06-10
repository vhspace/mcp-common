"""Unit tests for CachedResolver."""

from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import pytest

from mcpanvil.credential_chain import CachedResolver, StaticResolver


@pytest.fixture()
def mock_run():
    with patch("mcpanvil.credential_chain.subprocess.run") as m:
        yield m


class TestCacheMiss:
    def test_cache_miss_calls_inner(self, mock_run):
        """When keyring is empty, calls inner resolver."""
        # keyctl request returns non-zero (key not found)
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="")
        inner = StaticResolver("secret-value")
        resolver = CachedResolver(inner=inner, key_name="mcp:test:miss")

        result = resolver.resolve()

        assert result == "secret-value"

    def test_stores_in_keyring(self, mock_run):
        """After resolving, value is stored in keyring with keyctl add."""
        request_fail = MagicMock(returncode=1, stdout="", stderr="")
        add_ok = MagicMock(returncode=0, stdout="123456789\n", stderr="")
        setperm_ok = MagicMock(returncode=0)
        timeout_ok = MagicMock(returncode=0)
        mock_run.side_effect = [request_fail, add_ok, setperm_ok, timeout_ok]

        inner = StaticResolver("my-secret")
        resolver = CachedResolver(inner=inner, key_name="mcp:test:store", ttl_seconds=600)

        resolver.resolve()

        # Verify keyctl add was called with the secret and @s
        add_call = mock_run.call_args_list[1]
        assert add_call == call(
            ["keyctl", "add", "user", "mcp:test:store", "my-secret", "@s"],
            capture_output=True,
            text=True,
            timeout=5,
        )

    def test_ttl_set(self, mock_run):
        """keyctl timeout is called with the configured ttl_seconds."""
        request_fail = MagicMock(returncode=1, stdout="", stderr="")
        add_ok = MagicMock(returncode=0, stdout="999\n", stderr="")
        setperm_ok = MagicMock(returncode=0)
        timeout_ok = MagicMock(returncode=0)
        mock_run.side_effect = [request_fail, add_ok, setperm_ok, timeout_ok]

        inner = StaticResolver("ttl-test")
        resolver = CachedResolver(inner=inner, key_name="mcp:test:ttl", ttl_seconds=900)

        resolver.resolve()

        timeout_call = mock_run.call_args_list[3]
        assert timeout_call == call(
            ["keyctl", "timeout", "999", "900"],
            capture_output=True,
            timeout=5,
        )


class TestCacheHit:
    def test_cache_hit_skips_inner(self, mock_run):
        """When keyring has value, doesn't call inner resolver."""
        request_ok = MagicMock(returncode=0, stdout="12345\n", stderr="")
        pipe_ok = MagicMock(returncode=0, stdout="cached-secret", stderr="")
        mock_run.side_effect = [request_ok, pipe_ok]

        inner = MagicMock(spec=StaticResolver)
        inner.resolve = MagicMock(return_value="should-not-be-called")
        resolver = CachedResolver(inner=inner, key_name="mcp:test:hit")

        result = resolver.resolve()

        assert result == "cached-secret"
        inner.resolve.assert_not_called()


class TestEdgeCases:
    def test_inner_returns_none(self, mock_run):
        """When inner returns None, nothing is stored in keyring."""
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="")

        inner = StaticResolver("")  # returns None for empty string
        resolver = CachedResolver(inner=inner, key_name="mcp:test:none")

        result = resolver.resolve()

        assert result is None
        # Only the initial keyctl request call, no add call
        assert mock_run.call_count == 1

    def test_keyctl_unavailable_falls_through(self):
        """When keyctl not found, calls inner directly."""
        with patch(
            "mcpanvil.credential_chain.subprocess.run",
            side_effect=FileNotFoundError("keyctl not found"),
        ):
            inner = StaticResolver("fallback-value")
            resolver = CachedResolver(inner=inner, key_name="mcp:test:nobin")

            result = resolver.resolve()

            assert result == "fallback-value"

    def test_custom_key_name(self, mock_run):
        """Uses the provided key_name in keyctl commands."""
        request_fail = MagicMock(returncode=1, stdout="", stderr="")
        add_ok = MagicMock(returncode=0, stdout="777\n", stderr="")
        setperm_ok = MagicMock(returncode=0)
        timeout_ok = MagicMock(returncode=0)
        mock_run.side_effect = [request_fail, add_ok, setperm_ok, timeout_ok]

        inner = StaticResolver("val")
        resolver = CachedResolver(inner=inner, key_name="custom:my:key")

        resolver.resolve()

        request_call = mock_run.call_args_list[0]
        assert request_call == call(
            ["keyctl", "request", "user", "custom:my:key"],
            capture_output=True,
            text=True,
            timeout=5,
        )


class TestInvalidate:
    def test_invalidate(self, mock_run):
        """Revokes the key from keyring."""
        request_ok = MagicMock(returncode=0, stdout="55555\n", stderr="")
        revoke_ok = MagicMock(returncode=0)
        mock_run.side_effect = [request_ok, revoke_ok]

        resolver = CachedResolver(inner=StaticResolver("x"), key_name="mcp:test:revoke")
        resolver.invalidate()

        assert mock_run.call_args_list[1] == call(
            ["keyctl", "revoke", "55555"],
            capture_output=True,
            timeout=5,
        )
