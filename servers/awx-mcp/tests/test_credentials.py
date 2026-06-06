"""Unit tests for AWX credential resolution.

Covers :func:`awx_mcp.config.resolve_secret` (literal pass-through + ``op://``
resolution) and the CLI client factory wiring (:func:`awx_mcp.cli._build_awx_client`),
plus the new optional ``AWX_HOST`` default. The 1Password ``op`` CLI and the
kernel keyring are mocked so the tests are hermetic and never touch real
credentials or prompt for biometrics.
"""

from __future__ import annotations

import pytest

from awx_mcp import cli
from awx_mcp.config import DEFAULT_AWX_HOST, Settings, resolve_secret

_CRED_CHAIN = "mcp_common.credential_chain"

_AWX_ENV_VARS = (
    "AWX_HOST",
    "AWX_TOKEN",
    "CONTROLLER_HOST",
    "CONTROLLER_OAUTH_TOKEN",
    "VERIFY_SSL",
    "API_BASE_PATH",
    "TIMEOUT_SECONDS",
)


@pytest.fixture(autouse=True)
def _no_keyring(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force the kernel-keyring cache to miss + no-op.

    Keeps ``op://`` resolution deterministic: without this a value cached by a
    prior run under the same ``key_name`` could leak across tests.
    """
    monkeypatch.setattr(f"{_CRED_CHAIN}.CachedResolver._keyring_read", lambda self: None)
    monkeypatch.setattr(f"{_CRED_CHAIN}.CachedResolver._keyring_store", lambda self, value: None)


def _mock_op(monkeypatch: pytest.MonkeyPatch, value: str | None) -> list[str]:
    """Patch the ``op read`` reader to return *value*; record refs it was asked for."""
    calls: list[str] = []

    def _fake_read(reference: str, *, timeout_s: int = 30) -> str | None:
        calls.append(reference)
        return value

    monkeypatch.setattr(f"{_CRED_CHAIN}._read_op_reference", _fake_read)
    return calls


@pytest.fixture
def _clean_awx_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in _AWX_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


# --------------------------------------------------------------------------- #
# resolve_secret
# --------------------------------------------------------------------------- #


def test_resolve_secret_literal_passthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    """A literal token is returned unchanged and never invokes ``op``."""
    calls = _mock_op(monkeypatch, "SHOULD-NOT-BE-USED")
    assert resolve_secret("plain-literal-token", key_name="mcp:awx-token") == "plain-literal-token"
    assert calls == []


def test_resolve_secret_op_reference(monkeypatch: pytest.MonkeyPatch) -> None:
    """An ``op://`` reference is resolved via the credential chain."""
    calls = _mock_op(monkeypatch, "resolved-from-op")
    ref = "op://Employee/AWX/credential"
    assert resolve_secret(ref, key_name="mcp:awx-token") == "resolved-from-op"
    assert calls == [ref]


def test_resolve_secret_empty_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Empty input is treated as 'credential absent' and returned unchanged."""
    calls = _mock_op(monkeypatch, "unused")
    assert resolve_secret("", key_name="mcp:awx-token") == ""
    assert calls == []


def test_resolve_secret_op_failure_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed ``op://`` resolution surfaces a clear RuntimeError."""
    _mock_op(monkeypatch, None)
    with pytest.raises(RuntimeError):
        resolve_secret("op://Employee/AWX/credential", key_name="mcp:awx-token")


def test_resolve_secret_vault_not_implemented() -> None:
    """``vault://`` references are explicitly rejected (reserved)."""
    with pytest.raises(NotImplementedError):
        resolve_secret("vault://secret/data/awx", key_name="mcp:awx-token")


# --------------------------------------------------------------------------- #
# CLI client factory wiring + AWX_HOST default
# --------------------------------------------------------------------------- #


def test_build_awx_client_literal_token(
    monkeypatch: pytest.MonkeyPatch, _clean_awx_env: None
) -> None:
    """Literal AWX_TOKEN behaves exactly as before (no ``op`` call)."""
    calls = _mock_op(monkeypatch, "SHOULD-NOT-BE-USED")
    monkeypatch.setenv("AWX_HOST", "https://awx.example.com/")
    monkeypatch.setenv("AWX_TOKEN", "literal-token")
    client = cli._build_awx_client()
    assert client is not None
    try:
        assert client.token == "literal-token"
        assert client.host == "https://awx.example.com/"
    finally:
        client.close()
    assert calls == []


def test_build_awx_client_resolves_op_token(
    monkeypatch: pytest.MonkeyPatch, _clean_awx_env: None
) -> None:
    """An ``op://`` AWX_TOKEN is resolved before the client is built."""
    ref = "op://Employee/AWX/credential"
    calls = _mock_op(monkeypatch, "resolved-op-token")
    monkeypatch.setenv("AWX_HOST", "https://awx.example.com/")
    monkeypatch.setenv("AWX_TOKEN", ref)
    client = cli._build_awx_client()
    assert client is not None
    try:
        assert client.token == "resolved-op-token"
    finally:
        client.close()
    assert calls == [ref]


def test_build_awx_client_defaults_host(
    monkeypatch: pytest.MonkeyPatch, _clean_awx_env: None
) -> None:
    """With AWX_TOKEN set but AWX_HOST unset, the default host is used."""
    _mock_op(monkeypatch, "unused")
    monkeypatch.setenv("AWX_TOKEN", "literal-token")
    client = cli._build_awx_client()
    assert client is not None
    try:
        assert client.host == DEFAULT_AWX_HOST
    finally:
        client.close()


def test_build_awx_client_none_without_token(
    monkeypatch: pytest.MonkeyPatch, _clean_awx_env: None
) -> None:
    """No token (even with the default host available) yields ``None``."""
    _mock_op(monkeypatch, "unused")
    assert cli._build_awx_client() is None


# --------------------------------------------------------------------------- #
# Settings AWX_HOST default
# --------------------------------------------------------------------------- #


def test_settings_awx_host_defaults(monkeypatch: pytest.MonkeyPatch, _clean_awx_env: None) -> None:
    """AWX_HOST is optional in Settings and falls back to the default URL."""
    monkeypatch.setattr(
        "awx_mcp.config.Settings.model_config",
        {**Settings.model_config, "env_file": None},
    )
    settings = Settings(awx_token="literal-token")  # type: ignore[call-arg]
    assert str(settings.awx_host) == DEFAULT_AWX_HOST
