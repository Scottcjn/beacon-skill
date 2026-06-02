"""Tests for AgentFolio + Beacon Dual-Layer Trust Bridge.

Run: pytest test_bridge.py -v
"""

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Import the bridge module
import sys
sys.path.insert(0, str(Path(__file__).parent))
from bridge import (
    BridgeClient,
    TrustCache,
    _trust_level,
    DEFAULT_TRUST_WEIGHTS,
    CROSS_VERIFICATION_BONUS,
    ENDORSEMENT_BONUS_RATE,
)


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def bridge():
    """Create a BridgeClient with mock URLs (no real network calls)."""
    return BridgeClient(
        beacon_atlas_url="http://localhost:9999/beacon",
        agentfolio_api_url="http://localhost:9999/api",
        cache_ttl_seconds=0,  # Disable cache for tests
    )


@pytest.fixture
def mock_identity():
    """Create a mock Beacon AgentIdentity."""
    identity = MagicMock()
    identity.agent_id = "bcn_a1b2c3d4e5f6"
    identity.public_key_hex = "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2"
    identity.sign_hex = MagicMock(return_value="deadbeef" * 16)
    return identity


@pytest.fixture
def sample_beacon_data():
    return {
        "agent_id": "bcn_a1b2c3d4e5f6",
        "name": "crow-oracle",
        "status": "active",
        "hardware_fingerprint": "fp_abc123",
        "city": "Compiler Heights",
        "region": "Silicon Basin",
    }


@pytest.fixture
def sample_agentfolio_data():
    return {
        "agent_id": "agent_crow_oracle",
        "name": "crow-oracle",
        "trust_score": 85.0,
        "verifications": ["github", "solana_wallet"],
        "skills": ["coding", "bounty-hunting"],
        "endorsement_count": 7,
        "satp_on_chain": True,
        "oatr_operator_verified": True,
    }


# ── Trust Level Tests ────────────────────────────────────────────────────────

class TestTrustLevel:
    def test_verified(self):
        assert _trust_level(0.95) == "verified"
        assert _trust_level(0.80) == "verified"

    def test_trusted(self):
        assert _trust_level(0.79) == "trusted"
        assert _trust_level(0.60) == "trusted"

    def test_basic(self):
        assert _trust_level(0.59) == "basic"
        assert _trust_level(0.30) == "basic"

    def test_unverified(self):
        assert _trust_level(0.29) == "unverified"
        assert _trust_level(0.0) == "unverified"


# ── Trust Cache Tests ────────────────────────────────────────────────────────

class TestTrustCache:
    def test_set_and_get(self, tmp_path):
        cache = TrustCache(cache_dir=tmp_path, ttl_seconds=60)
        cache.set("test_key", {"hello": "world"})
        result = cache.get("test_key")
        assert result == {"hello": "world"}

    def test_get_missing(self, tmp_path):
        cache = TrustCache(cache_dir=tmp_path)
        result = cache.get("nonexistent")
        assert result is None

    def test_ttl_expiry(self, tmp_path):
        cache = TrustCache(cache_dir=tmp_path, ttl_seconds=0)  # Immediately expire
        cache.set("test_key", {"hello": "world"})
        time.sleep(0.01)
        result = cache.get("test_key")
        assert result is None  # Should be expired


# ── Composite Trust Tests ────────────────────────────────────────────────────

class TestCompositeTrust:
    def test_both_layers_max(self, bridge, sample_beacon_data, sample_agentfolio_data):
        result = bridge.compute_composite_trust(sample_beacon_data, sample_agentfolio_data)
        assert result["score"] > 0.5
        assert result["level"] in ("verified", "trusted")
        assert result["components"]["beacon_fidelity"] == 1.0
        assert result["components"]["agentfolio_reputation"] == 0.85
        assert result["components"]["cross_verified"] is True
        assert result["components"]["endorsement_bonus"] > 0

    def test_beacon_only(self, bridge, sample_beacon_data):
        result = bridge.compute_composite_trust(beacon_data=sample_beacon_data)
        assert result["score"] > 0.0
        assert result["components"]["beacon_fidelity"] == 1.0
        assert result["components"]["agentfolio_reputation"] == 0.0
        assert result["components"]["cross_verified"] is False

    def test_agentfolio_only(self, bridge, sample_agentfolio_data):
        result = bridge.compute_composite_trust(agentfolio_data=sample_agentfolio_data)
        assert result["score"] > 0.0
        assert result["components"]["beacon_fidelity"] == 0.0
        assert result["components"]["agentfolio_reputation"] == 0.85
        assert result["components"]["cross_verified"] is False

    def test_neither_layer(self, bridge):
        result = bridge.compute_composite_trust()
        assert result["score"] == 0.0
        assert result["level"] == "unverified"

    def test_beacon_active_no_fingerprint(self, bridge):
        beacon_data = {"status": "active"}
        result = bridge.compute_composite_trust(beacon_data=beacon_data)
        assert result["components"]["beacon_fidelity"] == 0.5

    def test_beacon_inactive(self, bridge):
        beacon_data = {"status": "dormant"}
        result = bridge.compute_composite_trust(beacon_data=beacon_data)
        assert result["components"]["beacon_fidelity"] == 0.0

    def test_agentfolio_high_score(self, bridge):
        af_data = {"trust_score": 150.0, "endorsement_count": 20}
        result = bridge.compute_composite_trust(agentfolio_data=af_data)
        assert result["components"]["agentfolio_reputation"] == 1.0  # Clamped to 1.0
        assert result["components"]["endorsement_bonus"] == pytest.approx(ENDORSEMENT_BONUS_RATE, abs=0.001)

    def test_endorsement_capped(self, bridge):
        af_data = {"trust_score": 50, "endorsement_count": 100}
        result = bridge.compute_composite_trust(agentfolio_data=af_data)
        # endorsement_factor = min(100/10, 1.0) = 1.0
        assert result["components"]["endorsement_bonus"] == pytest.approx(ENDORSEMENT_BONUS_RATE, abs=0.001)

    def test_cross_verification_bonus(self, bridge, sample_beacon_data, sample_agentfolio_data):
        with_both = bridge.compute_composite_trust(sample_beacon_data, sample_agentfolio_data)
        beacon_only = bridge.compute_composite_trust(beacon_data=sample_beacon_data)
        # Score with both should be higher due to cross-verification bonus
        assert with_both["score"] > beacon_only["score"]
        diff = with_both["score"] - beacon_only["score"]
        # The bonus should account for at least the cross-verification weight
        assert diff > 0

    def test_score_bounded(self, bridge, sample_beacon_data, sample_agentfolio_data):
        result = bridge.compute_composite_trust(sample_beacon_data, sample_agentfolio_data)
        assert 0.0 <= result["score"] <= 1.0

    def test_custom_weights(self):
        weights = {"beacon_fidelity": 1.0, "agentfolio_reputation": 0.0, "cross_verification": 0.0, "endorsement_bonus": 0.0}
        bridge = BridgeClient(trust_weights=weights)
        beacon_data = {"status": "active", "hardware_fingerprint": "fp"}
        result = bridge.compute_composite_trust(beacon_data=beacon_data)
        assert result["score"] == pytest.approx(1.0, abs=0.01)


# ── Cross-Identity Verification Tests ────────────────────────────────────────

class TestCrossIdentity:
    @patch.object(BridgeClient, "lookup_beacon_atlas")
    @patch.object(BridgeClient, "lookup_agentfolio")
    def test_matching_names(self, mock_af, mock_beacon, bridge):
        mock_beacon.return_value = {"name": "crow-oracle", "agent_id": "bcn_abc"}
        mock_af.return_value = {"name": "crow-oracle", "agent_id": "agent_crow_oracle"}
        result = bridge.verify_cross_identity("bcn_abc", "crow-oracle")
        assert result["verified"] is True
        assert result["method"] == "name_match"

    @patch.object(BridgeClient, "lookup_beacon_atlas")
    @patch.object(BridgeClient, "lookup_agentfolio")
    def test_mismatched_names(self, mock_af, mock_beacon, bridge):
        mock_beacon.return_value = {"name": "agent-alpha", "agent_id": "bcn_abc"}
        mock_af.return_value = {"name": "totally-different", "agent_id": "agent_different"}
        result = bridge.verify_cross_identity("bcn_abc", "totally-different")
        assert result["verified"] is False

    @patch.object(BridgeClient, "lookup_beacon_atlas")
    @patch.object(BridgeClient, "lookup_agentfolio")
    def test_one_missing(self, mock_af, mock_beacon, bridge):
        mock_beacon.return_value = {"name": "agent-alpha", "agent_id": "bcn_abc"}
        mock_af.return_value = None
        result = bridge.verify_cross_identity("bcn_abc", "nonexistent")
        assert result["verified"] is False
        assert result["reason"] == "one_or_both_identities_not_found"


# ── Trust Card Builder Tests ─────────────────────────────────────────────────

class TestTrustCard:
    @patch.object(BridgeClient, "lookup_beacon_atlas")
    @patch.object(BridgeClient, "lookup_agentfolio")
    def test_card_structure(self, mock_af, mock_beacon, bridge, mock_identity, sample_agentfolio_data):
        mock_beacon.return_value = None
        mock_af.return_value = sample_agentfolio_data
        card = bridge.build_trust_card(mock_identity, "crow-oracle", skills=["coding"])
        assert card["version"] == "1.0.0"
        assert "beacon" in card
        assert "agentfolio" in card
        assert "composite_trust" in card
        assert "migration" in card
        assert card["beacon"]["agent_id"] == "bcn_a1b2c3d4e5f6"
        assert card["agentfolio"]["name"] == "crow-oracle"

    @patch.object(BridgeClient, "lookup_beacon_atlas")
    @patch.object(BridgeClient, "lookup_agentfolio")
    def test_card_with_no_existing_data(self, mock_af, mock_beacon, bridge, mock_identity):
        mock_beacon.return_value = None
        mock_af.return_value = None
        card = bridge.build_trust_card(mock_identity, "new-agent")
        assert card["beacon"]["atlas_status"] == "unregistered"
        assert card["agentfolio"]["trust_score"] == 0
        assert card["composite_trust"]["level"] == "unverified"

    @patch.object(BridgeClient, "lookup_beacon_atlas")
    @patch.object(BridgeClient, "lookup_agentfolio")
    def test_card_signature(self, mock_af, mock_beacon, bridge, mock_identity):
        mock_beacon.return_value = None
        mock_af.return_value = None
        card = bridge.build_trust_card(mock_identity, "test-agent")
        assert "signature" in card["beacon"]
        mock_identity.sign_hex.assert_called_once()


# ── W3C DID Export Tests ─────────────────────────────────────────────────────

class TestDIDExport:
    @patch.object(BridgeClient, "lookup_beacon_atlas")
    @patch.object(BridgeClient, "lookup_agentfolio")
    def test_did_structure(self, mock_af, mock_beacon, bridge, mock_identity):
        mock_beacon.return_value = None
        mock_af.return_value = None
        did_doc = bridge.export_portable_identity(mock_identity, "test-agent")
        assert did_doc["id"].startswith("did:beacon:bcn_")
        assert len(did_doc["verificationMethod"]) >= 1
        assert len(did_doc["service"]) >= 2  # agentfolio + beacon-atlas
        assert "trustMetadata" in did_doc
        assert did_doc["trustMetadata"]["compositeScore"] >= 0.0

    @patch.object(BridgeClient, "lookup_beacon_atlas")
    @patch.object(BridgeClient, "lookup_agentfolio")
    def test_did_context(self, mock_af, mock_beacon, bridge, mock_identity):
        mock_beacon.return_value = None
        mock_af.return_value = None
        did_doc = bridge.export_portable_identity(mock_identity, "test-agent")
        assert "https://www.w3.org/ns/did/v1" in did_doc["@context"]

    @patch.object(BridgeClient, "lookup_beacon_atlas")
    @patch.object(BridgeClient, "lookup_agentfolio")
    def test_also_known_as(self, mock_af, mock_beacon, bridge, mock_identity):
        mock_beacon.return_value = None
        mock_af.return_value = None
        did_doc = bridge.export_portable_identity(mock_identity, "test-agent")
        assert any("agentfolio" in aka for aka in did_doc["alsoKnownAs"])
        assert any("beacon" in aka for aka in did_doc["alsoKnownAs"])


# ── Dual Registration Tests ──────────────────────────────────────────────────

class TestDualRegister:
    @patch.object(BridgeClient, "lookup_agentfolio")
    def test_dual_register_creates_cross_link(self, mock_af, bridge, mock_identity, tmp_path):
        mock_af.return_value = None
        # Patch the cross-link path to tmp_path
        with patch("bridge.Path.home", return_value=tmp_path):
            result = bridge.dual_register(mock_identity, "test-agent", skills=["coding"])
        assert "beacon_result" in result
        assert "agentfolio_result" in result
        assert "trust_card" in result

    @patch.object(BridgeClient, "lookup_agentfolio")
    def test_dual_register_existing_agentfolio(self, mock_af, bridge, mock_identity, sample_agentfolio_data, tmp_path):
        mock_af.return_value = sample_agentfolio_data
        with patch("bridge.Path.home", return_value=tmp_path):
            result = bridge.dual_register(mock_identity, "crow-oracle")
        assert result["agentfolio_result"]["status"] == "existing"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
