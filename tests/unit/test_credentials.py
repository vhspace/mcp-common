"""Tests for mcp_common.credentials."""

from __future__ import annotations

from unittest.mock import patch

from mcp_common.credentials import (
    CredentialCandidate,
    UsernamePasswordCredentialProvider,
)


def _provider() -> UsernamePasswordCredentialProvider:
    return UsernamePasswordCredentialProvider(
        candidates=[
            CredentialCandidate(
                name="ORI",
                user_env="REDFISH_ORI_USER",
                password_env="REDFISH_ORI_PASSWORD",
                user_ref_env="REDFISH_ORI_USER_REF",
                password_ref_env="REDFISH_ORI_PASSWORD_REF",
            ),
            CredentialCandidate(
                name="5C",
                user_env="REDFISH_5C_LOGIN",
                password_env="REDFISH_5C_PASSWORD",
                user_ref_env="REDFISH_5C_LOGIN_REF",
                password_ref_env="REDFISH_5C_PASSWORD_REF",
            ),
        ],
        generic_candidate=CredentialCandidate(
            name="GENERIC",
            user_env="REDFISH_USER",
            password_env="REDFISH_PASSWORD",
            user_ref_env="REDFISH_USER_REF",
            password_ref_env="REDFISH_PASSWORD_REF",
        ),
        site_hint_env="REDFISH_SITE",
    )


def test_explicit_wins() -> None:
    provider = _provider()
    result = provider.resolve(explicit_user="x", explicit_password="y")
    assert result is not None
    assert result.credentials.user == "x"
    assert result.credentials.password == "y"
    assert result.audit.source == "explicit"


def test_site_hint_chooses_candidate() -> None:
    provider = _provider()
    env = {
        "REDFISH_SITE": "ORI",
        "REDFISH_ORI_USER": "taiuser",
        "REDFISH_ORI_PASSWORD": "secret",
        "REDFISH_5C_LOGIN": "other",
        "REDFISH_5C_PASSWORD": "othersecret",
    }
    with patch.dict("os.environ", env, clear=True):
        result = provider.resolve(host="10.0.0.1")
    assert result is not None
    assert result.credentials.user == "taiuser"
    assert result.audit.candidate == "ORI"


def test_unambiguous_single_candidate_used() -> None:
    provider = _provider()
    env = {
        "REDFISH_ORI_USER": "taiuser",
        "REDFISH_ORI_PASSWORD": "secret",
    }
    with patch.dict("os.environ", env, clear=True):
        result = provider.resolve()
    assert result is not None
    assert result.credentials.user == "taiuser"


def test_ambiguous_candidates_fall_back_to_generic() -> None:
    provider = _provider()
    env = {
        "REDFISH_ORI_USER": "taiuser",
        "REDFISH_ORI_PASSWORD": "secret",
        "REDFISH_5C_LOGIN": "tai",
        "REDFISH_5C_PASSWORD": "secret2",
        "REDFISH_USER": "generic",
        "REDFISH_PASSWORD": "genericsecret",
    }
    with patch.dict("os.environ", env, clear=True):
        result = provider.resolve()
    assert result is not None
    assert result.credentials.user == "generic"
    assert result.audit.candidate == "GENERIC"


def test_reads_1password_references() -> None:
    provider = _provider()
    env = {
        "REDFISH_USER_REF": "op://shared/redfish/user",
        "REDFISH_PASSWORD_REF": "op://shared/redfish/pass",
    }
    with patch.dict("os.environ", env, clear=True):
        with patch(
            "mcp_common.credentials._read_1password_reference"
        ) as read_ref:
            read_ref.side_effect = ["op-user", "op-pass"]
            result = provider.resolve()
    assert result is not None
    assert result.credentials.user == "op-user"
    assert result.credentials.password == "op-pass"
    assert result.audit.source == "1password_ref"
    assert result.audit.used_1password_refs is True
    fields = result.audit.as_log_fields()
    assert "password" not in fields
    assert "user" not in fields
