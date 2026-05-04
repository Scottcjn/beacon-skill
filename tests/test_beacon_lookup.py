# mcp_server/tests/test_beacon_lookup.py
"""
Tests for beacon_lookup.py — Beacon Protocol lookup module.
"""

import json
import time
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
import requests

from mcp_server.beacon_lookup import (
    BeaconInfo,
    BeaconLookupError,
    agentfolio_beacon_lookup,
    lookup_all_beacons,
    lookup_beacon,
)


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def sample_beacon_data():
    """Create sample beacon data from API."""
    return {
        "agent_id": "bcn_abc123def456",
        "public_key_hex": "deadbeef1234567890",
        "hardware_fingerprint": "fp_hash_abc123",
        "registration_ts": 1704067200.0,  # 2024-01-01 00:00:00 UTC
        "atlas_registered": True,
        "last_seen_ts": 1704153600.0,  # 2024-01-02 00:00:00 UTC
        "source_platform": "moltbook",
        "source_handle": "@testagent",
    }


@pytest.fixture
def sample_satp_data():
    """Create sample SATP profile data."""
    return {
        "satp_profile_id": "satp_xyz789",
        "trust_score": 0.92,
        "verification_level": "hardware_verified",
        "verified_at": "2024-01-01T12:00:00Z",
    }


# =============================================================================
# Test BeaconInfo Dataclass
# =============================================================================

class TestBeaconInfo:
    """Tests for BeaconInfo dataclass."""

    def test_from_dict_complete(self, sample_beacon_data):
        """Test BeaconInfo creation from complete dict."""
        beacon = BeaconInfo.from_dict(sample_beacon_data)

        assert beacon.agent_id == "bcn_abc123def456"
        assert beacon.public_key_hex == "deadbeef1234567890"
        assert beacon.hardware_fingerprint == "fp_hash_abc123"
        assert beacon.registration_ts == 1704067200.0
        assert beacon.atlas_registered is True
        assert beacon.last_seen_ts == 1704153600.0
        assert beacon.source_platform == "moltbook"
        assert beacon.source_handle == "@testagent"

    def test_from_dict_minimal(self):
        """Test BeaconInfo creation from minimal dict."""
        data = {
            "agent_id": "bcn_minimal",
            "public_key_hex": "hex",
            "hardware_fingerprint": "fp",
            "registration_ts": 0.0,
        }

        beacon = BeaconInfo.from_dict(data)

        assert beacon.agent_id == "bcn_minimal"
        assert beacon.atlas_registered is False
        assert beacon.last_seen_ts is None
        assert beacon.source_platform is None

    def test_to_dict(self, sample_beacon_data):
        """Test BeaconInfo to_dict conversion."""
        beacon = BeaconInfo.from_dict(sample_beacon_data)
        result = beacon.to_dict()

        assert result["agent_id"] == "bcn_abc123def456"
        assert result["public_key_hex"] == "deadbeef1234567890"
        assert result["registration_ts"] == 1704067200.0
        assert "registration_ts_iso" in result
        assert "last_seen_ts_iso" in result

    def test_to_json(self, sample_beacon_data):
        """Test BeaconInfo to_json serialization."""
        beacon = BeaconInfo.from_dict(sample_beacon_data)
        json_str = beacon.to_json()

        parsed = json.loads(json_str)
        assert parsed["agent_id"] == "bcn_abc123def456"
        assert parsed["atlas_registered"] is True

    def test_to_dict_without_last_seen(self):
        """Test to_dict when last_seen_ts is None."""
        beacon = BeaconInfo(
            agent_id="bcn_test",
            public_key_hex="hex",
            hardware_fingerprint="fp",
            registration_ts=time.time(),
        )

        result = beacon.to_dict()

        assert "last_seen_ts_iso" not in result
        assert result["registration_ts_iso"] is not None


# =============================================================================
# Test lookup_beacon Function
# =============================================================================

class TestLookupBeacon:
    """Tests for lookup_beacon function."""

    @patch("mcp_server.beacon_lookup.requests.get")
    @patch("mcp_server.beacon_lookup._lookup_satp_for_beacon")
    def test_lookup_beacon_online(self, mock_satp, mock_get, sample_beacon_data):
        """Test successful beacon lookup when online."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = sample_beacon_data
        mock_get.return_value = mock_response

        mock_satp.return_value = {
            "satp_profile_id": "satp_xyz",
            "trust_score": 0.85,
        }

        beacon = lookup_beacon("bcn_abc123def456")

        assert beacon.agent_id == "bcn_abc123def456"
        assert beacon.public_key_hex == "deadbeef1234567890"
        assert beacon.source_platform == "moltbook"
        assert beacon.satp_profile_id == "satp_xyz"
        assert beacon.trust_score == 0.85

    @patch("mcp_server.beacon_lookup.requests.get")
    def test_lookup_beacon_not_found(self, mock_get):
        """Test lookup when beacon does not exist."""
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_get.return_value = mock_response

        with pytest.raises(BeaconLookupError) as exc_info:
            lookup_beacon("bcn_nonexistent")

        assert "not found" in str(exc_info.value).lower()
        assert exc_info.value.beacon_id == "bcn_nonexistent"

    @patch("mcp_server.beacon_lookup.requests.get")
    def test_lookup_beacon_api_error(self, mock_get):
        """Test lookup when API returns error."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"
        mock_get.return_value = mock_response

        with pytest.raises(BeaconLookupError) as exc_info:
            lookup_beacon("bcn_test123")

        assert exc_info.value.beacon_id == "bcn_test123"
        assert exc_info.value.details["response"] == "Internal Server Error"

    @patch("mcp_server.beacon_lookup.requests.get")
    def test_lookup_beacon_connection_error(self, mock_get):
        """Test lookup when connection fails."""
        mock_get.side_effect = requests.ConnectionError("Connection refused")

        with pytest.raises(BeaconLookupError) as exc_info:
            lookup_beacon("bcn_test123")

        assert "Failed to connect" in str(exc_info.value)

    def test_lookup_beacon_empty_id(self):
        """Test lookup with empty beacon ID."""
        with pytest.raises(BeaconLookupError) as exc_info:
            lookup_beacon("")

        assert "cannot be empty" in str(exc_info.value)

    @patch("mcp_server.beacon_lookup.requests.get")
    def test_lookup_beacon_missing_prefix(self, mock_get):
        """Test lookup with beacon ID missing bcn_ prefix (warning only)."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "agent_id": "abc123def456",
            "public_key_hex": "hex",
            "hardware_fingerprint": "fp",
            "registration_ts": time.time(),
        }
        mock_get.return_value = mock_response

        # Should still work but log warning
        beacon = lookup_beacon("abc123def456")
        assert beacon.agent_id == "abc123def456"

    @patch("mcp_server.beacon_lookup.requests.get")
    @patch("mcp_server.beacon_lookup._lookup_satp_for_beacon")
    def test_lookup_beacon_expired(self, mock_satp, mock_get):
        """Test lookup of expired beacon."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "agent_id": "bcn_expired",
            "public_key_hex": "hex",
            "hardware_fingerprint": "fp",
            "registration_ts": time.time() - 86400 * 365,  # 1 year ago
            "atlas_registered": False,  # Expired
            "expired": True,
        }
        mock_get.return_value = mock_response

        mock_satp.return_value = None

        beacon = lookup_beacon("bcn_expired")

        assert beacon.agent_id == "bcn_expired"
        assert beacon.atlas_registered is False


# =============================================================================
# Test lookup_all_beacons Function
# =============================================================================

class TestLookupAllBeacons:
    """Tests for lookup_all_beacons function."""

    @patch("mcp_server.beacon_lookup.requests.get")
    def test_lookup_all_beacons_success(self, mock_get):
        """Test successful retrieval of all beacons."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "beacons": [
                {
                    "agent_id": "bcn_beacon1",
                    "public_key_hex": "hex1",
                    "hardware_fingerprint": "fp1",
                    "registration_ts": time.time(),
                },
                {
                    "agent_id": "bcn_beacon2",
                    "public_key_hex": "hex2",
                    "hardware_fingerprint": "fp2",
                    "registration_ts": time.time(),
                },
            ],
            "total": 2,
        }
        mock_get.return_value = mock_response

        beacons = lookup_all_beacons(limit=50)

        assert len(beacons) == 2
        assert beacons[0].agent_id == "bcn_beacon1"
        assert beacons[1].agent_id == "bcn_beacon2"

    @patch("mcp_server.beacon_lookup.requests.get")
    def test_lookup_all_beacons_empty(self, mock_get):
        """Test retrieval when no beacons exist."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"beacons": [], "total": 0}
        mock_get.return_value = mock_response

        beacons = lookup_all_beacons()

        assert len(beacons) == 0

    @patch("mcp_server.beacon_lookup.requests.get")
    def test_lookup_all_beacons_with_pagination(self, mock_get):
        """Test pagination parameters are passed correctly."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"beacons": [], "total": 0}
        mock_get.return_value = mock_response

        lookup_all_beacons(limit=25, offset=50, include_expired=True)

        mock_get.assert_called_once()
        call_args = mock_get.call_args
        assert call_args.kwargs["params"]["limit"] == 25
        assert call_args.kwargs["params"]["offset"] == 50
        assert call_args.kwargs["params"]["include_expired"] is True

    @patch("mcp_server.beacon_lookup.requests.get")
    def test_lookup_all_beacons_connection_error(self, mock_get):
        """Test connection error when listing beacons."""
        mock_get.side_effect = requests.ConnectionError("Timeout")

        with pytest.raises(BeaconLookupError):
            lookup_all_beacons()


# =============================================================================
# Test agentfolio_beacon_lookup Function
# =============================================================================

class TestAgentfolioBeaconLookup:
    """Tests for agentfolio_beacon_lookup function."""

    @patch("mcp_server.beacon_lookup.requests.get")
    def test_agentfolio_beacon_lookup_success(self, mock_get, sample_satp_data):
        """Test successful AgentFolio SATP lookup."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = sample_satp_data
        mock_get.return_value = mock_response

        result = json.loads(agentfolio_beacon_lookup("bcn_test123"))

        assert result is not None
        assert "query" in result
        assert result["query"]["agentfolio_id"] == "bcn_test123"
        assert "beacons" in result
        assert "count" in result

    @patch("mcp_server.beacon_lookup.requests.get")
    def test_agentfolio_beacon_lookup_not_found(self, mock_get):
        """Test AgentFolio lookup when beacon not registered."""
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_get.return_value = mock_response

        with pytest.raises(BeaconLookupError) as exc_info:
            agentfolio_beacon_lookup("bcn_unregistered")

        assert "not found" in str(exc_info.value).lower()

    @patch("mcp_server.beacon_lookup.requests.get")
    def test_agentfolio_beacon_lookup_error(self, mock_get):
        """Test AgentFolio lookup with API error."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Service Unavailable"
        mock_get.return_value = mock_response

        with pytest.raises(BeaconLookupError) as exc_info:
            agentfolio_beacon_lookup("bcn_test123")

        assert "500" in str(exc_info.value)
