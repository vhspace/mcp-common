"""
Pytest configuration and shared fixtures.
"""

import os
import pickle
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from dc_support_mcp.vendors.ori import OriVendorHandler


def _backdate_mtime(path: Path, age: timedelta) -> None:
    """Set ``path``'s atime/mtime to ``now - age``.

    Used to test the mtime-based ``cookie_age_seconds`` helper without
    touching the actual file contents.
    """
    backdate = (datetime.now() - age).timestamp()
    os.utime(path, (backdate, backdate))


@pytest.fixture(autouse=True)
def _isolate_credential_chain():
    """Keep tests off the real kernel keyring and 1Password.

    ``CachedResolver`` caches resolved secrets in the Linux keyring and
    ``EnvResolver`` shells out to ``op`` for ``op://`` references.  Neutralize
    both so unit tests are deterministic, never leak values between tests via a
    shared keyring, and never require a real 1Password session — regardless of
    whether ``keyctl`` / ``op`` happen to exist on the host.  Individual tests
    may re-patch ``_read_op_reference`` to simulate a resolved ``op://`` ref.
    """
    from mcp_common import credential_chain as _cc

    with (
        patch.object(_cc.CachedResolver, "_keyring_read", return_value=None),
        patch.object(_cc.CachedResolver, "_keyring_store", return_value=None),
        patch.object(_cc, "_read_op_reference", return_value=None),
    ):
        yield


@pytest.fixture
def mock_credentials():
    """Mock credentials for testing."""
    return {"email": "test@example.com", "password": "testpassword123"}


@pytest.fixture
def ori_handler(mock_credentials, tmp_path):
    """Create an OriVendorHandler with mocked browser auth and temp cookie path.

    Reset post-construction auth state so tests start from a known-clean
    baseline regardless of host environment.  Without this, the patched
    ``_authenticate_with_browser`` returns a truthy ``MagicMock`` during
    ``__init__`` → ``_guarded_authenticate``, leaving
    ``_last_auth_succeeded`` truthy when no cached cookies exist (e.g. on
    a fresh CI runner), which silently disables the cooldown guard in
    several tests.
    """
    with patch.object(OriVendorHandler, "_authenticate_with_browser"):
        handler = OriVendorHandler(
            email=mock_credentials["email"],
            password=mock_credentials["password"],
            use_cached_cookies=False,
        )
    handler.cookie_file = tmp_path / "cookies.pkl"
    handler._last_auth_attempt = None
    handler._last_auth_succeeded = False
    handler.last_error = None
    return handler


@pytest.fixture
def sample_ticket_data():
    """Sample ticket data for testing."""
    return {
        "id": "SUPP-1556",
        "summary": "Slow connectivity to AS40475",
        "status": "Awaiting Customer",
        "reporter": "tsparks@together.ai",
        "assignee": "Joey Halliday",
        "created": "24/Jan/26 6:26 AM",
        "comments": [
            {
                "author": "tsparks@together.ai",
                "date": "24/Jan/26 6:26 AM",
                "comment": "Slow connectivity to AS40475",
                "type": "requester-comment",
            },
            {
                "author": "Joey Halliday",
                "date": "24/Jan/26 7:10 AM",
                "comment": "Thanks for raising this issue.",
                "type": "worker-comment",
            },
        ],
        "url": "https://oriindustries.atlassian.net/servicedesk/customer/portal/3/SUPP-1556",
    }


@pytest.fixture
def sample_api_response():
    """Sample API response from Ori portal."""
    return {
        "reqDetails": {
            "issue": {
                "key": "SUPP-1556",
                "summary": "Slow connectivity to AS40475",
                "status": "Awaiting Customer",
                "reporter": {
                    "displayName": "tsparks@together.ai",
                    "accountId": "qm:ab584c09-d338-4b3b-8d37-64c431fdd3b7:3fbe8163-963f-41da-89fb-cb731f2f6ff0",
                },
                "assignee": {
                    "displayName": "Joey Halliday",
                    "accountId": "712020:2b14c24b-dac1-41b4-8117-d60a4493a16b",
                },
                "friendlyDate": "24/Jan/26 6:26 AM",
                "organisations": [{"id": 2, "name": "TogetherAI"}],
                "requestTypeName": "Infrastructure Support",
                "activityStream": [
                    {
                        "type": "requester-comment",
                        "author": "tsparks@together.ai",
                        "friendlyDate": "24/Jan/26 6:26 AM",
                        "rawComment": "Slow connectivity to AS40475",
                    },
                    {
                        "type": "worker-comment",
                        "author": "Joey Halliday",
                        "friendlyDate": "24/Jan/26 7:10 AM",
                        "rawComment": "Thanks for raising this issue.",
                    },
                ],
            }
        },
        "xsrfToken": "test-token-123",
    }


@pytest.fixture
def mock_cookies():
    """Mock cookies for testing."""
    return [
        {
            "name": "customer.account.session.token",
            "value": "test-session-token",
            "domain": ".atlassian.net",
            "path": "/",
        },
        {
            "name": "atlassian.xsrf.token",
            "value": "test-xsrf-token",
            "domain": ".atlassian.net",
            "path": "/",
        },
    ]


@pytest.fixture
def temp_cookie_cache(tmp_path):
    """Temporary cookie cache file for testing."""
    cache_file = tmp_path / "test_cookies.pkl"

    data = {
        "cookies": [
            {"name": "test-cookie", "value": "test-value", "domain": ".atlassian.net", "path": "/"}
        ],
        "timestamp": datetime.now(),
    }

    with open(cache_file, "wb") as f:
        pickle.dump(data, f)

    return cache_file


@pytest.fixture
def expired_cookie_cache(tmp_path):
    """Expired cookie cache for testing refresh logic."""
    cache_file = tmp_path / "expired_cookies.pkl"

    data = {
        "cookies": [
            {
                "name": "expired-cookie",
                "value": "old-value",
                "domain": ".atlassian.net",
                "path": "/",
            }
        ],
        "timestamp": datetime.now() - timedelta(hours=9),
    }

    with open(cache_file, "wb") as f:
        pickle.dump(data, f)

    return cache_file


@pytest.fixture
def real_credentials():
    """
    Real credentials from environment (for E2E tests).
    Skips test if credentials not available.
    """
    email = os.getenv("ORI_PORTAL_USERNAME")
    password = os.getenv("ORI_PORTAL_PASSWORD")

    if not email or not password:
        pytest.skip(
            "Real credentials not available (set ORI_PORTAL_USERNAME and ORI_PORTAL_PASSWORD)"
        )

    return {"email": email, "password": password}
