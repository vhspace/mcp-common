"""Unit tests for OriVendorHandler (cookie management, API calls, parsing)."""

import pickle
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
import responses

from dc_support_mcp.constants import ORI_API_ENDPOINT, ORI_BASE_URL, ORI_PORTAL_ID
from dc_support_mcp.vendors.ori import OriVendorHandler


@pytest.mark.unit
class TestOriVendorHandler:
    def test_initialization(self, ori_handler, mock_credentials):
        assert ori_handler.email == mock_credentials["email"]
        assert ori_handler.password == mock_credentials["password"]

    def test_cookie_file_path_uses_home(self, mock_credentials, tmp_path):
        with patch.object(OriVendorHandler, "_authenticate_with_browser"):
            handler = OriVendorHandler(
                email=mock_credentials["email"],
                password=mock_credentials["password"],
                use_cached_cookies=False,
            )
        assert handler.COOKIE_FILE_NAME == ".ori_session_cookies.pkl"

    @responses.activate
    def test_get_ticket_success(self, ori_handler, sample_api_response):
        responses.add(
            responses.POST,
            f"{ORI_BASE_URL}{ORI_API_ENDPOINT}",
            json=sample_api_response,
            status=200,
        )

        ticket = ori_handler.get_ticket("SUPP-1556")
        assert ticket is not None
        assert ticket["id"] == "SUPP-1556"
        assert ticket["summary"] == "Slow connectivity to AS40475"
        assert ticket["status"] == "Awaiting Customer"
        assert len(ticket["comments"]) == 2

    @responses.activate
    def test_get_ticket_not_found(self, ori_handler):
        responses.add(
            responses.POST,
            f"{ORI_BASE_URL}{ORI_API_ENDPOINT}",
            json={"xsrfToken": "test"},
            status=200,
        )

        ticket = ori_handler.get_ticket("SUPP-9999")
        assert ticket is None

    def test_parse_ticket_data(self, ori_handler, sample_api_response):
        parsed = ori_handler._parse_ticket_data(sample_api_response["reqDetails"])

        assert parsed["id"] == "SUPP-1556"
        assert parsed["summary"] == "Slow connectivity to AS40475"
        assert parsed["reporter"] == "tsparks@together.ai"
        assert parsed["assignee"] == "Joey Halliday"
        assert len(parsed["comments"]) == 2
        assert parsed["comments"][0]["author"] == "tsparks@together.ai"

    def test_save_and_load_cookies(self, ori_handler, mock_cookies):
        cache_file = ori_handler.cookie_file

        ori_handler._save_cookies(mock_cookies)
        assert cache_file.exists()

        with open(cache_file, "rb") as f:
            data = pickle.load(f)

        assert "cookies" in data
        assert "timestamp" in data
        assert len(data["cookies"]) == len(mock_cookies)

    def test_cookie_expiry_detection(self, mock_credentials, tmp_path):
        cache_file = tmp_path / "cookies.pkl"
        expired_data = {
            "cookies": [{"name": "test", "value": "old", "domain": ".example.com"}],
            "timestamp": datetime.now() - timedelta(hours=9),
        }
        with open(cache_file, "wb") as f:
            pickle.dump(expired_data, f)

        with patch.object(OriVendorHandler, "_authenticate_with_browser"):
            handler = OriVendorHandler(
                email=mock_credentials["email"],
                password=mock_credentials["password"],
                use_cached_cookies=False,
            )
        handler.cookie_file = cache_file
        loaded = handler._load_cookies()
        assert not loaded

    def test_api_request_payload_format(self, ori_handler):
        with patch.object(ori_handler.session, "post") as mock_post:
            mock_post.return_value.status_code = 200
            mock_post.return_value.json.return_value = {"reqDetails": {"issue": {}}}

            ori_handler.get_ticket("SUPP-1556")

            assert mock_post.called
            call_kwargs = mock_post.call_args[1]
            payload = call_kwargs["json"]

            assert payload["options"]["reqDetails"]["key"] == "SUPP-1556"
            assert payload["options"]["portalId"] == ORI_PORTAL_ID
            assert "reqDetails" in payload["models"]

    def test_vendor_handler_interface(self, ori_handler):
        from dc_support_mcp.vendor_handler import VendorHandler

        assert isinstance(ori_handler, VendorHandler)
        assert hasattr(ori_handler, "authenticate")
        assert hasattr(ori_handler, "get_ticket")
        assert hasattr(ori_handler, "list_tickets")
        assert hasattr(ori_handler, "create_ticket")

    def test_create_ticket_class_attrs(self):
        assert OriVendorHandler.INFRA_REQUEST_TYPE_ID == 299
        assert "P3" in OriVendorHandler.PRIORITY_OPTIONS
        assert "Moderate" in OriVendorHandler.URGENCY_OPTIONS
        assert "Medium" in OriVendorHandler.IMPACT_OPTIONS

    def test_get_ticket_invalid_id_raises(self, ori_handler):
        with pytest.raises(ValueError, match="Invalid ticket ID"):
            ori_handler.get_ticket("INVALID-123")

    def test_get_ticket_empty_id_raises(self, ori_handler):
        with pytest.raises(ValueError, match="Invalid ticket ID"):
            ori_handler.get_ticket("")
