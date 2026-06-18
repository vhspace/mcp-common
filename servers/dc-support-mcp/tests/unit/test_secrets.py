"""Tests for dc_support_mcp.secrets — credential-chain backed secret helpers.

These tests never require a real 1Password session or kernel keyring: the
autouse ``_isolate_credential_chain`` fixture (tests/conftest.py) neutralizes
``keyctl`` caching and stubs ``op`` resolution.  The ``op://`` resolution path
is exercised by re-patching ``_read_op_reference`` per test.
"""

from unittest.mock import patch

import pytest

from dc_support_mcp.secrets import (
    maybe_secret,
    portal_configured,
    portal_credentials,
    portal_source,
    secret_configured,
    secret_source,
)


@pytest.mark.unit
class TestMaybeSecret:
    @patch.dict("os.environ", {}, clear=True)
    def test_returns_none_when_unset(self):
        assert maybe_secret("RTB_API_KEY") is None

    @patch.dict("os.environ", {"RTB_API_KEY": "   "})
    def test_returns_none_when_blank(self):
        assert maybe_secret("RTB_API_KEY") is None

    @patch.dict("os.environ", {"RTB_API_KEY": "rtb-literal-123"})
    def test_returns_literal(self):
        assert maybe_secret("RTB_API_KEY") == "rtb-literal-123"

    @patch.dict("os.environ", {"RTB_API_KEY": "op://Vault/RTB/key"})
    def test_resolves_op_reference(self):
        from mcp_common import credential_chain as cc

        with patch.object(cc, "_read_op_reference", return_value="resolved-from-1p"):
            assert maybe_secret("RTB_API_KEY") == "resolved-from-1p"

    @patch.dict("os.environ", {"RTB_API_KEY": "op://Vault/RTB/key"})
    def test_op_reference_unresolvable_returns_none(self):
        # Default fixture stub makes op resolution return None.
        assert maybe_secret("RTB_API_KEY") is None


@pytest.mark.unit
class TestSecretSource:
    @patch.dict("os.environ", {}, clear=True)
    def test_none_when_unset(self):
        assert secret_source("NETBOX_TOKEN") is None
        assert secret_configured("NETBOX_TOKEN") is False

    @patch.dict("os.environ", {"NETBOX_TOKEN": "abc123"})
    def test_env_literal(self):
        assert secret_source("NETBOX_TOKEN") == "env"
        assert secret_configured("NETBOX_TOKEN") is True

    @patch.dict("os.environ", {"NETBOX_TOKEN": "op://Vault/NetBox/token"})
    def test_op_reference(self):
        assert secret_source("NETBOX_TOKEN") == "op://"

    @patch.dict("os.environ", {"NETBOX_TOKEN": "vault://secret/netbox"})
    def test_vault_reference(self):
        assert secret_source("NETBOX_TOKEN") == "vault://"


@pytest.mark.unit
class TestPortalCredentials:
    @patch.dict("os.environ", {}, clear=True)
    def test_none_when_both_missing(self):
        assert portal_credentials("ori") is None

    @patch.dict("os.environ", {"ORI_PORTAL_USERNAME": "user@together.ai"}, clear=True)
    def test_none_when_password_missing(self):
        assert portal_credentials("ori") is None

    @patch.dict(
        "os.environ",
        {"ORI_PORTAL_USERNAME": "user@together.ai", "ORI_PORTAL_PASSWORD": "pw"},
        clear=True,
    )
    def test_env_pair(self):
        result = portal_credentials("ori")
        assert result == ("user@together.ai", "pw", "env")

    @patch.dict(
        "os.environ",
        {
            "HYPERTEC_PORTAL_USERNAME": "user@together.ai",
            "HYPERTEC_PORTAL_PASSWORD": "op://Vault/Hypertec/password",
        },
        clear=True,
    )
    def test_op_reference_half_marks_source_op(self):
        from mcp_common import credential_chain as cc

        with patch.object(cc, "_read_op_reference", return_value="secret-pw"):
            result = portal_credentials("hypertec")
        assert result == ("user@together.ai", "secret-pw", "op://")

    @patch.dict(
        "os.environ",
        {
            "IREN_PORTAL_USERNAME": "user@together.ai",
            "IREN_PORTAL_PASSWORD": "op://Vault/IREN/password",
        },
        clear=True,
    )
    def test_op_reference_unresolvable_returns_none(self):
        # op stubbed to None by the autouse fixture → cannot resolve password.
        assert portal_credentials("iren") is None

    @patch.dict(
        "os.environ",
        {"ORI_PORTAL_USERNAME": "user@together.ai", "ORI_PORTAL_PASSWORD": "pw"},
        clear=True,
    )
    def test_audit_log_has_no_values(self, caplog):
        with caplog.at_level("INFO"):
            portal_credentials("ori")
        joined = " ".join(r.getMessage() for r in caplog.records)
        assert "pw" not in joined
        assert "user@together.ai" not in joined
        assert "ori" in joined
        assert "env" in joined


@pytest.mark.unit
class TestPortalSource:
    @patch.dict("os.environ", {}, clear=True)
    def test_none_when_unconfigured(self):
        assert portal_source("ori") is None
        assert portal_configured("ori") is False

    @patch.dict(
        "os.environ",
        {"ORI_PORTAL_USERNAME": "u", "ORI_PORTAL_PASSWORD": "p"},
        clear=True,
    )
    def test_env_pair(self):
        assert portal_source("ori") == "env"
        assert portal_configured("ori") is True

    @patch.dict(
        "os.environ",
        {"ORI_PORTAL_USERNAME": "op://Vault/Ori/user", "ORI_PORTAL_PASSWORD": "p"},
        clear=True,
    )
    def test_op_half_marks_op(self):
        assert portal_source("ori") == "op://"
