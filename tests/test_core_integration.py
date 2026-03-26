"""Integration tests for Beacon core flows.

Covers the full agent lifecycle end-to-end:
  - Agent registration and identity creation
  - Trust scoring and reputation management
  - Skill/domain lookup via Atlas
  - Beacon ping (heartbeat proof-of-life)
  - Ed25519 envelope signing and verification
  - Relay agent registration and authentication
  - Agent card generation and verification

These tests exercise multiple modules together, ensuring they compose
correctly in real-world usage patterns.

Targets bounty: https://github.com/Scottcjn/beacon-skill/issues/152
"""

import json
import time
import tempfile
from pathlib import Path

import pytest

from beacon_skill.identity import AgentIdentity, agent_id_from_pubkey
from beacon_skill.codec import (
    encode_envelope,
    decode_envelopes,
    verify_envelope,
    generate_nonce,
)
from beacon_skill.trust import TrustManager
from beacon_skill.heartbeat import HeartbeatManager
from beacon_skill.atlas import AtlasManager
from beacon_skill.relay import RelayManager, RelayAgent
from beacon_skill.agent_card import generate_agent_card, verify_agent_card


@pytest.fixture
def data_dir(tmp_path):
    """Isolated data directory for each test."""
    return tmp_path


@pytest.fixture
def alice():
    """A fresh agent identity."""
    return AgentIdentity.generate()


@pytest.fixture
def bob():
    """A second agent identity."""
    return AgentIdentity.generate()


# ---------------------------------------------------------------------------
# 1. Agent Registration & Identity
# ---------------------------------------------------------------------------


class TestAgentRegistration:
    """Agent identity creation, persistence, and determinism."""

    def test_identity_format(self, alice):
        """Agent IDs follow the bcn_ + 12 hex chars format."""
        assert alice.agent_id.startswith("bcn_")
        assert len(alice.agent_id) == 16
        # Hex chars only after prefix
        assert all(c in "0123456789abcdef" for c in alice.agent_id[4:])

    def test_two_agents_differ(self, alice, bob):
        """Two independently generated agents have different IDs and keys."""
        assert alice.agent_id != bob.agent_id
        assert alice.public_key_hex != bob.public_key_hex
        assert alice.private_key_hex != bob.private_key_hex

    def test_identity_roundtrip_via_private_key(self, alice):
        """Restoring from private key produces identical identity."""
        restored = AgentIdentity.from_private_key_hex(alice.private_key_hex)
        assert restored.agent_id == alice.agent_id
        assert restored.public_key_hex == alice.public_key_hex

    def test_agent_id_deterministic_from_pubkey(self, alice):
        """agent_id is a pure function of the public key bytes."""
        expected = agent_id_from_pubkey(bytes.fromhex(alice.public_key_hex))
        assert alice.agent_id == expected


# ---------------------------------------------------------------------------
# 2. Trust Scoring
# ---------------------------------------------------------------------------


class TestTrustScoring:
    """Trust manager scoring, blocking, and review workflows."""

    def test_positive_interactions_build_trust(self, data_dir):
        mgr = TrustManager(data_dir=data_dir)
        for _ in range(5):
            mgr.record("bcn_goodagent001", "in", "hello", outcome="ok")
        score = mgr.score("bcn_goodagent001")
        assert score["score"] > 0
        assert score["total"] == 5
        assert score["positive"] == 5.0

    def test_spam_tanks_trust(self, data_dir):
        mgr = TrustManager(data_dir=data_dir)
        mgr.record("bcn_spammer00001", "in", "ad", outcome="ok")
        mgr.record("bcn_spammer00001", "in", "ad", outcome="spam")
        mgr.record("bcn_spammer00001", "in", "ad", outcome="spam")
        score = mgr.score("bcn_spammer00001")
        assert score["score"] < 0

    def test_rtc_volume_tracked(self, data_dir):
        mgr = TrustManager(data_dir=data_dir)
        mgr.record("bcn_trader00001", "out", "pay", outcome="paid", rtc=25.0)
        mgr.record("bcn_trader00001", "out", "pay", outcome="paid", rtc=75.0)
        score = mgr.score("bcn_trader00001")
        assert score["rtc_volume"] == 100.0

    def test_block_prevents_interaction(self, data_dir):
        mgr = TrustManager(data_dir=data_dir)
        mgr.block("bcn_blocked00001", reason="abuse")
        ok, reason = mgr.can_interact("bcn_blocked00001")
        assert not ok
        assert reason == "blocked"

    def test_hold_review_release_flow(self, data_dir):
        mgr = TrustManager(data_dir=data_dir)
        mgr.hold("bcn_suspect00001", reason="unusual pattern")
        ok, reason = mgr.can_interact("bcn_suspect00001")
        assert not ok
        assert reason == "needs_review"

        mgr.release("bcn_suspect00001", reviewer_note="cleared")
        ok, reason = mgr.can_interact("bcn_suspect00001")
        assert ok
        assert reason == ""

    def test_scores_ranked_highest_first(self, data_dir):
        mgr = TrustManager(data_dir=data_dir)
        mgr.record("bcn_bad000000001", "in", "ad", outcome="spam")
        for _ in range(3):
            mgr.record("bcn_good00000001", "in", "hello", outcome="ok")
        all_scores = mgr.scores()
        assert len(all_scores) == 2
        assert all_scores[0]["agent_id"] == "bcn_good00000001"


# ---------------------------------------------------------------------------
# 3. Skill / Domain Lookup via Atlas
# ---------------------------------------------------------------------------


class TestAtlasSkillLookup:
    """Atlas agent registration and city-based skill discovery."""

    def test_register_agent_in_domains(self, data_dir, alice):
        atlas = AtlasManager(data_dir=data_dir)
        result = atlas.register_agent(
            agent_id=alice.agent_id,
            domains=["coding", "ai", "blockchain"],
            name="test-alice",
        )
        assert result.get("cities_joined", 0) >= 1
        assert "home" in result

    def test_census_reflects_registration(self, data_dir, alice, bob):
        atlas = AtlasManager(data_dir=data_dir)
        atlas.register_agent(agent_id=alice.agent_id, domains=["coding"], name="alice")
        atlas.register_agent(agent_id=bob.agent_id, domains=["coding", "music"], name="bob")
        census = atlas.census()
        assert census["total_agents"] >= 2

    def test_lookup_agent_property(self, data_dir, alice):
        atlas = AtlasManager(data_dir=data_dir)
        atlas.register_agent(agent_id=alice.agent_id, domains=["security"], name="alice-sec")
        prop = atlas.get_property(alice.agent_id)
        assert prop is not None
        assert prop["agent_id"] == alice.agent_id


# ---------------------------------------------------------------------------
# 4. Beacon Ping (Heartbeat)
# ---------------------------------------------------------------------------


class TestBeaconPing:
    """Heartbeat proof-of-life and peer monitoring."""

    def test_send_and_receive_heartbeat(self, data_dir, alice):
        hb = HeartbeatManager(data_dir=data_dir)
        result = hb.beat(alice, status="alive", health={"cpu_pct": 30})
        payload = result["heartbeat"]
        assert payload["kind"] == "heartbeat"
        assert payload["agent_id"] == alice.agent_id
        assert payload["beat_count"] == 1

    def test_peer_heartbeat_tracking(self, data_dir, alice, bob):
        hb = HeartbeatManager(data_dir=data_dir)
        # Alice sends heartbeats
        hb.beat(alice)

        # Bob's heartbeat arrives
        hb.process_heartbeat({
            "agent_id": bob.agent_id,
            "status": "alive",
            "beat_count": 1,
            "ts": int(time.time()),
        })
        peers = hb.all_peers()
        peer_ids = [p["agent_id"] for p in peers]
        assert bob.agent_id in peer_ids

    def test_daily_digest_includes_peers(self, data_dir, alice, bob):
        hb = HeartbeatManager(data_dir=data_dir)
        hb.beat(alice)
        hb.process_heartbeat({
            "agent_id": bob.agent_id,
            "status": "alive",
            "ts": int(time.time()),
        })
        digest = hb.daily_digest()
        assert digest["own_beat_count"] == 1
        assert digest["peers_seen"] >= 1


# ---------------------------------------------------------------------------
# 5. Ed25519 Envelope Signing & Verification
# ---------------------------------------------------------------------------


class TestEd25519Verification:
    """Envelope signing, verification, and tamper detection."""

    def test_sign_and_verify_message(self, alice):
        msg = b"beacon protocol test"
        sig = alice.sign_hex(msg)
        assert AgentIdentity.verify(alice.public_key_hex, sig, msg)

    def test_tampered_message_fails(self, alice):
        msg = b"original message"
        sig = alice.sign_hex(msg)
        assert not AgentIdentity.verify(alice.public_key_hex, sig, b"tampered")

    def test_wrong_key_fails(self, alice, bob):
        msg = b"secret"
        sig = alice.sign_hex(msg)
        assert not AgentIdentity.verify(bob.public_key_hex, sig, msg)

    def test_v2_envelope_roundtrip(self, alice):
        payload = {"kind": "want", "text": "Looking for collaborators", "ts": 12345}
        text = encode_envelope(payload, version=2, identity=alice, include_pubkey=True)
        assert "[BEACON v2]" in text

        envs = decode_envelopes(text)
        assert len(envs) == 1
        env = envs[0]
        assert env["agent_id"] == alice.agent_id
        assert env["kind"] == "want"
        assert verify_envelope(env) is True

    def test_tampered_envelope_rejected(self, alice):
        payload = {"kind": "hello", "ts": 1}
        text = encode_envelope(payload, version=2, identity=alice, include_pubkey=True)
        envs = decode_envelopes(text)
        env = envs[0]
        env["text"] = "INJECTED"
        assert verify_envelope(env) is False

    def test_v1_envelope_unverifiable(self):
        text = encode_envelope({"kind": "hello", "ts": 1}, version=1)
        envs = decode_envelopes(text)
        assert verify_envelope(envs[0]) is None

    def test_nonce_uniqueness(self):
        nonces = {generate_nonce() for _ in range(100)}
        assert len(nonces) == 100  # All unique


# ---------------------------------------------------------------------------
# 6. Relay Agent Registration & Auth
# ---------------------------------------------------------------------------


class TestRelayRegistration:
    """External agent relay: register, authenticate, heartbeat."""

    def test_register_external_agent(self, data_dir, alice):
        relay = RelayManager(data_dir=data_dir)
        result = relay.register(
            pubkey_hex=alice.public_key_hex,
            model_id="test-model-v1",
            provider="elyan",
            name="relay-test-alice",
            capabilities=["coding", "analysis"],
        )
        assert result["ok"] is True
        assert result["agent_id"] == alice.agent_id
        assert result["relay_token"].startswith("relay_")

    def test_authenticate_with_token(self, data_dir, alice):
        relay = RelayManager(data_dir=data_dir)
        reg = relay.register(
            pubkey_hex=alice.public_key_hex,
            model_id="test-v1",
            name="auth-test-alice",
        )
        agent = relay.authenticate(reg["relay_token"])
        assert agent is not None
        assert agent.agent_id == alice.agent_id

    def test_invalid_token_rejected(self, data_dir, alice):
        relay = RelayManager(data_dir=data_dir)
        relay.register(
            pubkey_hex=alice.public_key_hex,
            model_id="test-v1",
            name="reject-test-alice",
        )
        assert relay.authenticate("relay_bogus_token") is None

    def test_generic_name_rejected(self, data_dir, alice):
        relay = RelayManager(data_dir=data_dir)
        result = relay.register(
            pubkey_hex=alice.public_key_hex,
            model_id="gpt-4",
            name="GPT Agent",
        )
        assert "error" in result

    def test_relay_heartbeat_increments(self, data_dir, alice):
        relay = RelayManager(data_dir=data_dir)
        reg = relay.register(
            pubkey_hex=alice.public_key_hex,
            model_id="test-v1",
            name="hb-test-alice",
        )
        token = reg["relay_token"]
        aid = reg["agent_id"]

        r1 = relay.heartbeat(aid, token, status="alive")
        assert r1["ok"] is True
        r2 = relay.heartbeat(aid, token, status="alive")
        assert r2["ok"] is True


# ---------------------------------------------------------------------------
# 7. Agent Card Generation & Verification
# ---------------------------------------------------------------------------


class TestAgentCard:
    """Agent card (.well-known/beacon.json) generation and verification."""

    def test_generate_and_verify_card(self, alice):
        card = generate_agent_card(
            alice,
            name="alice-test",
            capabilities={"kinds": ["hello", "want"], "payments": ["rustchain_rtc"]},
        )
        assert card["agent_id"] == alice.agent_id
        assert card["name"] == "alice-test"
        assert "signature" in card
        assert verify_agent_card(card) is True

    def test_tampered_card_rejected(self, alice):
        card = generate_agent_card(alice, name="alice-original")
        card["name"] = "alice-HACKED"
        assert verify_agent_card(card) is False

    def test_card_missing_sig_rejected(self, alice):
        card = generate_agent_card(alice, name="no-sig-alice")
        del card["signature"]
        assert verify_agent_card(card) is False


# ---------------------------------------------------------------------------
# 8. End-to-End Integration: Full Agent Lifecycle
# ---------------------------------------------------------------------------


class TestFullLifecycle:
    """Complete agent lifecycle: create, register, ping, trust, discover."""

    def test_agent_lifecycle(self, data_dir):
        # Step 1: Create two agents
        alice = AgentIdentity.generate()
        bob = AgentIdentity.generate()

        # Step 2: Register on Atlas
        atlas = AtlasManager(data_dir=data_dir)
        atlas.register_agent(agent_id=alice.agent_id, domains=["coding"], name="alice")
        atlas.register_agent(agent_id=bob.agent_id, domains=["coding"], name="bob")

        # Step 3: Exchange heartbeats
        hb = HeartbeatManager(data_dir=data_dir)
        hb.beat(alice)
        hb.process_heartbeat({
            "agent_id": bob.agent_id,
            "status": "alive",
            "beat_count": 1,
            "ts": int(time.time()),
        })

        # Step 4: Record trust interaction
        trust = TrustManager(data_dir=data_dir)
        trust.record(bob.agent_id, "in", "hello", outcome="ok")
        score = trust.score(bob.agent_id)
        assert score["score"] > 0

        # Step 5: Send signed envelope
        text = encode_envelope(
            {"kind": "want", "text": "Pair on a project?"},
            version=2, identity=alice, include_pubkey=True,
        )
        envs = decode_envelopes(text)
        assert verify_envelope(envs[0]) is True

        # Step 6: Verify census shows both agents
        census = atlas.census()
        assert census["total_agents"] >= 2

    def test_encrypted_keystore_lifecycle(self):
        """Create, encrypt, restore, and sign with a keystored identity."""
        original = AgentIdentity.generate()
        password = "beacon-test-pw-42"

        keystore = original.export_encrypted(password)
        assert keystore["encrypted"] is True

        restored = AgentIdentity.from_encrypted(keystore, password)
        assert restored.agent_id == original.agent_id

        # Signing works after restore
        msg = b"post-restore message"
        sig = restored.sign_hex(msg)
        assert AgentIdentity.verify(restored.public_key_hex, sig, msg)

        # Wrong password fails
        with pytest.raises(ValueError):
            AgentIdentity.from_encrypted(keystore, "wrong-password")
