"""Unit tests for the shared handler-timing helpers (issue #90).

Covers ``cookie_age_seconds`` and ``cooldown_remaining_seconds`` which
centralize the session-timing math previously duplicated across
``cli.py``, ``vendors/atlassian_base.py``, and ``vendors/iren.py``.
"""

import os
import time
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from dc_support_mcp.constants import AUTH_COOLDOWN
from dc_support_mcp.vendors.iren import IrenVendorHandler
from dc_support_mcp.vendors.ori import OriVendorHandler


def _make_iren(mock_credentials, tmp_path):
    handler = IrenVendorHandler(
        email=mock_credentials["email"],
        password=mock_credentials["password"],
        use_cached_cookies=False,
        verbose=False,
    )
    handler.cookie_file = tmp_path / "iren_cookies.pkl"
    handler._last_auth_attempt = None
    handler._last_auth_succeeded = False
    return handler


def _make_ori(mock_credentials, tmp_path):
    with patch.object(OriVendorHandler, "_authenticate_with_browser"):
        handler = OriVendorHandler(
            email=mock_credentials["email"],
            password=mock_credentials["password"],
            use_cached_cookies=False,
            verbose=False,
        )
    handler.cookie_file = tmp_path / "cookies.pkl"
    handler._last_auth_attempt = None
    handler._last_auth_succeeded = False
    handler.last_error = None
    return handler


# ── cookie_age_seconds ───────────────────────────────────────────────


@pytest.mark.unit
class TestCookieAgeSeconds:
    """``cookie_age_seconds`` returns the on-disk cookie file age (mtime-based)."""

    def test_returns_none_when_file_missing(self, mock_credentials, tmp_path):
        handler = _make_ori(mock_credentials, tmp_path)
        assert not handler.cookie_file.exists()
        assert handler.cookie_age_seconds() is None

    def test_returns_zero_for_freshly_written_file(self, mock_credentials, tmp_path):
        handler = _make_ori(mock_credentials, tmp_path)
        handler.cookie_file.write_bytes(b"x")

        age = handler.cookie_age_seconds()
        assert age is not None
        assert age >= 0
        assert age < 5  # freshly written

    def test_matches_mtime_delta(self, mock_credentials, tmp_path):
        """Return value mirrors ``int((now - mtime).total_seconds())``."""
        handler = _make_ori(mock_credentials, tmp_path)
        handler.cookie_file.write_bytes(b"x")

        # Backdate the file's mtime by 90 minutes.
        backdate = datetime.now() - timedelta(minutes=90)
        os.utime(handler.cookie_file, (backdate.timestamp(), backdate.timestamp()))

        age = handler.cookie_age_seconds()
        assert age is not None
        # Allow a few seconds of slack between os.utime and datetime.now().
        assert 90 * 60 - 5 <= age <= 90 * 60 + 5

    def test_returns_int_not_float(self, mock_credentials, tmp_path):
        handler = _make_ori(mock_credentials, tmp_path)
        handler.cookie_file.write_bytes(b"x")
        age = handler.cookie_age_seconds()
        assert isinstance(age, int)

    def test_works_for_iren_handler(self, mock_credentials, tmp_path):
        """The same method is inherited by IREN's handler base."""
        handler = _make_iren(mock_credentials, tmp_path)
        assert handler.cookie_age_seconds() is None

        handler.cookie_file.write_bytes(b"x")
        backdate = datetime.now() - timedelta(seconds=30)
        os.utime(handler.cookie_file, (backdate.timestamp(), backdate.timestamp()))

        age = handler.cookie_age_seconds()
        assert age is not None
        assert 25 <= age <= 35

    def test_handles_unreadable_file(self, mock_credentials, tmp_path):
        """If stat fails after exists() passes, the helper degrades to None."""
        handler = _make_ori(mock_credentials, tmp_path)
        handler.cookie_file.write_bytes(b"x")

        with patch.object(type(handler.cookie_file), "stat", side_effect=OSError("boom")):
            assert handler.cookie_age_seconds() is None


# ── cooldown_remaining_seconds ───────────────────────────────────────


@pytest.mark.unit
class TestCooldownRemainingSeconds:
    """``cooldown_remaining_seconds`` reports time left in the auth cooldown."""

    def test_returns_zero_when_no_attempt_yet(self, mock_credentials, tmp_path):
        handler = _make_ori(mock_credentials, tmp_path)
        assert handler._last_auth_attempt is None
        assert handler.cooldown_remaining_seconds() == 0

    def test_returns_zero_when_last_attempt_succeeded(self, mock_credentials, tmp_path):
        """Successful auth never triggers cooldown (issue #65)."""
        handler = _make_ori(mock_credentials, tmp_path)
        handler._last_auth_attempt = datetime.now()
        handler._last_auth_succeeded = True

        assert handler.cooldown_remaining_seconds() == 0

    def test_returns_positive_within_window_after_failure(self, mock_credentials, tmp_path):
        handler = _make_ori(mock_credentials, tmp_path)
        elapsed = timedelta(seconds=30)
        handler._last_auth_attempt = datetime.now() - elapsed
        handler._last_auth_succeeded = False

        remaining = handler.cooldown_remaining_seconds()
        expected = int(AUTH_COOLDOWN.total_seconds() - elapsed.total_seconds())
        # Allow a 1-second slack for clock advancement during the call.
        assert isinstance(remaining, int)
        assert expected - 2 <= remaining <= expected

    def test_returns_zero_past_window_after_failure(self, mock_credentials, tmp_path):
        handler = _make_ori(mock_credentials, tmp_path)
        handler._last_auth_attempt = datetime.now() - AUTH_COOLDOWN - timedelta(seconds=1)
        handler._last_auth_succeeded = False

        assert handler.cooldown_remaining_seconds() == 0

    def test_full_cooldown_for_just_now_failure(self, mock_credentials, tmp_path):
        handler = _make_ori(mock_credentials, tmp_path)
        handler._last_auth_attempt = datetime.now()
        handler._last_auth_succeeded = False

        remaining = handler.cooldown_remaining_seconds()
        full = int(AUTH_COOLDOWN.total_seconds())
        # Either ``full`` or ``full - 1`` depending on int truncation timing.
        assert full - 1 <= remaining <= full

    def test_returns_int_not_float(self, mock_credentials, tmp_path):
        handler = _make_ori(mock_credentials, tmp_path)
        handler._last_auth_attempt = datetime.now() - timedelta(seconds=10)
        handler._last_auth_succeeded = False

        remaining = handler.cooldown_remaining_seconds()
        assert isinstance(remaining, int)

    def test_works_for_iren_handler(self, mock_credentials, tmp_path):
        handler = _make_iren(mock_credentials, tmp_path)
        assert handler.cooldown_remaining_seconds() == 0

        handler._last_auth_attempt = datetime.now() - timedelta(seconds=10)
        handler._last_auth_succeeded = False
        remaining = handler.cooldown_remaining_seconds()
        assert remaining > 0
        assert remaining <= int(AUTH_COOLDOWN.total_seconds())


# ── End-to-end equivalence with prior inline math ────────────────────


@pytest.mark.unit
class TestEquivalenceWithInlineMath:
    """The helpers must produce the same answers the prior inline math did."""

    def test_cookie_age_matches_prior_cli_formula(self, mock_credentials, tmp_path):
        """``cookie_age_seconds`` matches ``int((now - mtime).total_seconds())``."""
        handler = _make_ori(mock_credentials, tmp_path)
        handler.cookie_file.write_bytes(b"x")

        backdate = datetime.now() - timedelta(minutes=42)
        os.utime(handler.cookie_file, (backdate.timestamp(), backdate.timestamp()))

        # Tiny delay so any clock drift between two ``datetime.now()`` calls is
        # measurable but bounded.
        time.sleep(0.01)
        expected_mtime = datetime.fromtimestamp(handler.cookie_file.stat().st_mtime)
        expected_age = int((datetime.now() - expected_mtime).total_seconds())

        assert handler.cookie_age_seconds() == pytest.approx(expected_age, abs=1)

    def test_cooldown_matches_prior_handler_formula(self, mock_credentials, tmp_path):
        """``cooldown_remaining_seconds`` matches the original handler arithmetic."""
        handler = _make_ori(mock_credentials, tmp_path)
        last = datetime.now() - timedelta(seconds=45)
        handler._last_auth_attempt = last
        handler._last_auth_succeeded = False

        elapsed = (datetime.now() - last).total_seconds()
        expected = int(max(0, AUTH_COOLDOWN.total_seconds() - elapsed))
        assert handler.cooldown_remaining_seconds() == pytest.approx(expected, abs=1)
