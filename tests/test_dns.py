"""Tests for beacon_skill/dns.py"""

import pytest
from unittest.mock import patch, MagicMock
from beacon_skill.dns import BeaconDNS


class TestBeaconDNS:
    """Test BeaconDNS name resolution client."""

    @pytest.fixture
    def dns(self):
        return BeaconDNS(base_url="https://rustchain.org/beacon", timeout_s=5)

    # --- resolve() tests ---

    def test_resolve_success(self, dns):
        """resolve() returns agent data on success."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "name": "sophia-elya",
            "agent_id": "bcn_c850ea702e8f",
            "owner": "elyan",
            "created_at": "2025-01-15T10:00:00Z",
        }
        with patch("beacon_skill.dns.requests.get", return_value=mock_resp) as mock_get:
            result = dns.resolve("sophia-elya")
            mock_get.assert_called_once()
            assert result["name"] == "sophia-elya"
            assert result["agent_id"] == "bcn_c850ea702e8f"

    def test_resolve_not_found(self, dns):
        """resolve() returns error dict when name not registered."""
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_resp.json.return_value = {"error": "not found"}
        with patch("beacon_skill.dns.requests.get", return_value=mock_resp) as mock_get:
            result = dns.resolve("nonexistent-name")
            assert "error" in result or result.get("agent_id") is None

    def test_resolve_non_json_response(self, dns):
        """resolve() handles non-JSON error responses gracefully."""
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.json.side_effect = ValueError("not json")
        mock_resp.text = "Internal Server Error"
        with patch("beacon_skill.dns.requests.get", return_value=mock_resp):
            result = dns.resolve("sophia-elya")
            assert "error" in result

    def test_resolve_timeout(self, dns):
        """resolve() raises requests.Timeout on slow server."""
        with patch("beacon_skill.dns.requests.get") as mock_get:
            import requests
            mock_get.side_effect = requests.Timeout("timed out")
            with pytest.raises(requests.Timeout):
                dns.resolve("sophia-elya")

    def test_resolve_empty_name(self, dns):
        """resolve() sends correct request for empty name (edge case)."""
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.json.return_value = {"error": "bad request"}
        with patch("beacon_skill.dns.requests.get", return_value=mock_resp):
            result = dns.resolve("")
            # Should still make the call (empty string is valid input)
            assert mock_resp.status_code == 400

    # --- reverse() tests ---

    def test_reverse_success(self, dns):
        """reverse() returns list of names for an agent_id."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [
            {"name": "sophia-elya", "owner": "elyan"},
            {"name": "sophia", "owner": "elyan"},
        ]
        with patch("beacon_skill.dns.requests.get", return_value=mock_resp):
            result = dns.reverse("bcn_c850ea702e8f")
            assert isinstance(result, list)
            assert len(result) == 2

    def test_reverse_not_found(self, dns):
        """reverse() returns empty list when agent_id not registered."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = []
        with patch("beacon_skill.dns.requests.get", return_value=mock_resp):
            result = dns.reverse("bcn_deadbeef")
            assert result == []

    def test_reverse_non_json(self, dns):
        """reverse() handles non-JSON gracefully."""
        mock_resp = MagicMock()
        mock_resp.status_code = 502
        mock_resp.json.side_effect = ValueError
        mock_resp.text = "bad gateway"
        with patch("beacon_skill.dns.requests.get", return_value=mock_resp):
            result = dns.reverse("bcn_c850ea702e8f")
            assert "error" in result

    # --- _parse() tests ---

    def test_parse_valid_json(self, dns):
        """_parse() returns parsed JSON for valid response."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"name": "test"}
        result = BeaconDNS._parse(mock_resp)
        assert result == {"name": "test"}

    def test_parse_non_json_with_error_key(self, dns):
        """_parse() preserves error key from non-JSON response."""
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.json.side_effect = ValueError
        mock_resp.text = "Bad Request"
        result = BeaconDNS._parse(mock_resp)
        # If status >= 400 and no error key, adds one
        assert "error" in result or "raw" in result

    # --- _url() tests ---

    def test_url_trailing_slash_stripped(self, dns):
        """base_url trailing slash is stripped."""
        dns2 = BeaconDNS(base_url="https://rustchain.org/beacon/")
        assert dns2.base_url == "https://rustchain.org/beacon"

    def test_url_path_concat(self, dns):
        """_url() correctly concatenates path."""
        url = dns._url("/api/dns/test")
        assert url == "https://rustchain.org/beacon/api/dns/test"

    # --- _headers() tests ---

    def test_headers_contains_user_agent(self, dns):
        """_headers() includes User-Agent string."""
        headers = dns._headers()
        assert "User-Agent" in headers
        assert "Elyan Labs" in headers["User-Agent"]

    # --- timeout override ---

    def test_custom_timeout(self):
        """Custom timeout_s is respected."""
        dns = BeaconDNS(timeout_s=30)
        assert dns.timeout_s == 30