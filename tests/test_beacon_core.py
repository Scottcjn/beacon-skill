#!/usr/bin/env python3
"""
Comprehensive pytest test suite for Beacon core functions
Issue #152 - Bounty: 5 RTC

Tests for:
- Heartbeat (proof of life)
- Relay (agent communication)
- Trust scoring (reputation system)
- Agent registration (identity management)

Run with: pytest tests/test_beacon_core.py -v
"""

from __future__ import annotations  # Python 3.6 compatibility

import json
import time
import tempfile
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Import beacon modules
from beacon_skill.heartbeat import HeartbeatManager
from beacon_skill.trust import TrustManager
from beacon_skill.identity import AgentIdentity


# ═══════════════════════════════════════════════════════════════
# HEARTBEAT TESTS
# ═══════════════════════════════════════════════════════════════

class TestHeartbeatCore:
    """Test core heartbeat functionality."""
    
    @pytest.fixture
    def tmp_dir(self, tmp_path):
        return tmp_path
    
    @pytest.fixture
    def mgr(self, tmp_dir):
        return HeartbeatManager(data_dir=tmp_dir)
    
    @pytest.fixture
    def mock_identity(self):
        ident = MagicMock()
        ident.agent_id = "bcn_test_agent_123"
        ident.public_key_hex = "ab" * 32
        return ident
    
    def test_heartbeat_build_basic(self, mgr, mock_identity):
        """Test basic heartbeat construction."""
        payload = mgr.build_heartbeat(mock_identity)
        
        assert payload["kind"] == "heartbeat"
        assert payload["agent_id"] == "bcn_test_agent_123"
        assert payload["status"] == "alive"
        assert payload["beat_count"] == 1
        assert "ts" in payload
        assert "uptime_s" in payload
    
    def test_heartbeat_status_variants(self, mgr, mock_identity):
        """Test different heartbeat statuses."""
        for status in ["alive", "degraded", "busy", "offline"]:
            payload = mgr.build_heartbeat(mock_identity, status=status)
            assert payload["status"] == status
    
    def test_heartbeat_with_capabilities(self, mgr, mock_identity):
        """Test heartbeat includes capabilities."""
        caps = ["video_creation", "bounty_hunting", "code_review"]
        payload = mgr.build_heartbeat(mock_identity, capabilities=caps)
        
        assert "capabilities" in payload
        assert payload["capabilities"] == caps
    
    def test_heartbeat_seo_tags(self, mgr, mock_identity):
        """Test heartbeat includes SEO tags."""
        tags = ["ai-agent", "bottube", "elyan-labs"]
        payload = mgr.build_heartbeat(mock_identity, seo_tags=tags)
        
        assert "seo_tags" in payload
        assert payload["seo_tags"] == tags
    
    def test_heartbeat_persistence(self, mgr, mock_identity):
        """Test heartbeat count persists."""
        count1 = mgr.build_heartbeat(mock_identity)["beat_count"]
        count2 = mgr.build_heartbeat(mock_identity)["beat_count"]
        count3 = mgr.build_heartbeat(mock_identity)["beat_count"]
        
        assert count1 < count2 < count3


# ═══════════════════════════════════════════════════════════════
# RELAY TESTS
# ═══════════════════════════════════════════════════════════════

class TestRelayCore:
    """Test core relay functionality."""
    
    def test_relay_message_structure(self):
        """Test relay message structure."""
        from beacon_skill.relay import RelayMessage
        
        msg = RelayMessage(
            sender_id="bcn_alice",
            recipient_id="bcn_bob",
            kind="hello",
            content={"greeting": "Hello!"}
        )
        
        assert msg.sender_id == "bcn_alice"
        assert msg.recipient_id == "bcn_bob"
        assert msg.kind == "hello"
        assert msg.content == {"greeting": "Hello!"}
    
    def test_relay_message_serialization(self):
        """Test relay message JSON serialization."""
        from beacon_skill.relay import RelayMessage
        
        msg = RelayMessage(
            sender_id="bcn_alice",
            recipient_id="bcn_bob",
            kind="bounty",
            content={"title": "Fix bug", "reward": 10}
        )
        
        json_str = msg.to_json()
        parsed = json.loads(json_str)
        
        assert parsed["sender_id"] == "bcn_alice"
        assert parsed["kind"] == "bounty"
    
    def test_relay_message_deserialization(self):
        """Test relay message JSON deserialization."""
        from beacon_skill.relay import RelayMessage
        
        json_str = '''
        {
            "sender_id": "bcn_charlie",
            "recipient_id": "bcn_dave",
            "kind": "accord",
            "content": {"terms": "50% split"}
        }
        '''
        
        msg = RelayMessage.from_json(json_str)
        
        assert msg.sender_id == "bcn_charlie"
        assert msg.kind == "accord"
        assert msg.content["terms"] == "50% split"


# ═══════════════════════════════════════════════════════════════
# TRUST SCORING TESTS
# ═══════════════════════════════════════════════════════════════

class TestTrustScoring:
    """Test trust scoring system."""
    
    @pytest.fixture
    def tmp_dir(self, tmp_path):
        return tmp_path
    
    @pytest.fixture
    def mgr(self, tmp_dir):
        return TrustManager(data_dir=tmp_dir)
    
    def test_trust_record_interaction(self, mgr):
        """Test recording trust interactions."""
        mgr.record("bcn_alice", "in", "hello", outcome="ok")
        
        score = mgr.score("bcn_alice")
        assert score["total"] == 1
        assert score["score"] > 0
    
    def test_trust_positive_outcomes(self, mgr):
        """Test positive outcomes increase trust."""
        mgr.record("bcn_good", "in", "bounty", outcome="delivered")
        mgr.record("bcn_good", "out", "pay", outcome="paid", rtc=10.0)
        
        score = mgr.score("bcn_good")
        assert score["total"] == 2
        assert score["score"] > 0
        assert score["rtc_volume"] == 10.0
    
    def test_trust_negative_outcomes(self, mgr):
        """Test negative outcomes decrease trust."""
        mgr.record("bcn_bad", "in", "ad", outcome="spam")
        mgr.record("bcn_bad", "in", "hello", outcome="ok")
        
        score = mgr.score("bcn_bad")
        # 1 ok (+1), 1 spam (-3): score = (1-3)/2 = -1.0
        assert score["total"] == 2
        assert score["score"] < 0
    
    def test_trust_multiple_agents(self, mgr):
        """Test scoring multiple agents."""
        mgr.record("bcn_a", "in", "hello", outcome="ok")
        mgr.record("bcn_b", "in", "hello", outcome="spam")
        mgr.record("bcn_c", "in", "bounty", outcome="delivered")
        
        all_scores = mgr.scores()
        assert len(all_scores) == 3
        
        # Find scores by agent_id
        scores_dict = {s["agent_id"]: s["score"] for s in all_scores}
        
        # Good agent should have higher score than spammer
        assert scores_dict["bcn_c"] > scores_dict["bcn_b"]
    
    def test_trust_block_unblock(self, mgr):
        """Test blocking and unblocking agents."""
        assert not mgr.is_blocked("bcn_evil")
        
        mgr.block("bcn_evil", reason="spam")
        assert mgr.is_blocked("bcn_evil")
        
        blocked = mgr.blocked_list()
        assert "bcn_evil" in blocked
        
        mgr.unblock("bcn_evil")
        assert not mgr.is_blocked("bcn_evil")
    
    def test_trust_rtc_volume_tracking(self, mgr):
        """Test RTC volume tracking."""
        mgr.record("bcn_trader", "out", "pay", outcome="paid", rtc=50.0)
        mgr.record("bcn_trader", "out", "pay", outcome="paid", rtc=30.0)
        
        score = mgr.score("bcn_trader")
        assert score["rtc_volume"] == 80.0


# ═══════════════════════════════════════════════════════════════
# AGENT REGISTRATION TESTS
# ═══════════════════════════════════════════════════════════════

class TestAgentRegistration:
    """Test agent registration and identity."""
    
    @pytest.fixture
    def tmp_dir(self, tmp_path):
        return tmp_path
    
    def test_identity_creation(self, tmp_dir):
        """Test creating new agent identity."""
        identity = AgentIdentity.create(data_dir=tmp_dir, name="test_agent")
        
        assert identity.agent_id.startswith("bcn_")
        assert identity.public_key_hex is not None
        assert len(identity.public_key_hex) == 64  # 32 bytes hex
    
    def test_identity_persistence(self, tmp_dir):
        """Test identity persists to disk."""
        identity1 = AgentIdentity.create(data_dir=tmp_dir, name="persistent_agent")
        agent_id = identity1.agent_id
        
        # Load existing identity
        identity2 = AgentIdentity.load(data_dir=tmp_dir, agent_id=agent_id)
        
        assert identity2.agent_id == agent_id
        assert identity2.public_key_hex == identity1.public_key_hex
    
    def test_identity_export_import(self, tmp_dir):
        """Test identity export and import."""
        identity1 = AgentIdentity.create(data_dir=tmp_dir, name="export_test")
        
        # Export
        exported = identity1.export()
        assert "agent_id" in exported
        assert "public_key_hex" in exported
        
        # Import
        identity2 = AgentIdentity.from_dict(exported)
        assert identity2.agent_id == identity1.agent_id
    
    def test_identity_sign_verify(self, tmp_dir):
        """Test message signing and verification."""
        identity = AgentIdentity.create(data_dir=tmp_dir, name="signer")
        
        message = "Test message for signing"
        signature = identity.sign(message)
        
        assert identity.verify(message, signature)
        assert not identity.verify("Tampered message", signature)
    
    def test_agent_id_from_pubkey(self):
        """Test agent ID derivation from public key."""
        from beacon_skill.identity import agent_id_from_pubkey_hex
        
        pubkey = "ab" * 32
        agent_id = agent_id_from_pubkey_hex(pubkey)
        
        assert agent_id.startswith("bcn_")
        assert len(agent_id) > 10


# ═══════════════════════════════════════════════════════════════
# INTEGRATION TESTS
# ═══════════════════════════════════════════════════════════════

class TestCoreIntegration:
    """Integration tests for core beacon functions."""
    
    @pytest.fixture
    def tmp_dir(self, tmp_path):
        return tmp_path
    
    def test_full_agent_lifecycle(self, tmp_dir):
        """Test complete agent lifecycle: register → heartbeat → trust → communicate."""
        # 1. Register agent
        identity = AgentIdentity.create(data_dir=tmp_dir, name="lifecycle_agent")
        
        # 2. Send heartbeats
        heartbeat_mgr = HeartbeatManager(data_dir=tmp_dir)
        hb1 = heartbeat_mgr.build_heartbeat(identity, status="alive")
        hb2 = heartbeat_mgr.build_heartbeat(identity, status="alive")
        
        assert hb1["beat_count"] == 1
        assert hb2["beat_count"] == 2
        
        # 3. Build trust through interactions
        trust_mgr = TrustManager(data_dir=tmp_dir)
        trust_mgr.record(identity.agent_id, "in", "hello", outcome="ok")
        trust_mgr.record(identity.agent_id, "out", "bounty", outcome="delivered", rtc=5.0)
        
        score = trust_mgr.score(identity.agent_id)
        assert score["total"] == 2
        assert score["score"] > 0
        
        # 4. Send relay message
        from beacon_skill.relay import RelayMessage
        
        msg = RelayMessage(
            sender_id=identity.agent_id,
            recipient_id="bcn_other",
            kind="hello",
            content={"intro": "Hi!"}
        )
        
        assert msg.sender_id == identity.agent_id
        assert msg.kind == "hello"
    
    def test_multi_agent_scenario(self, tmp_dir):
        """Test multi-agent interaction scenario."""
        # Create multiple agents
        alice = AgentIdentity.create(data_dir=tmp_dir, name="alice")
        bob = AgentIdentity.create(data_dir=tmp_dir, name="bob")
        
        # Set up trust managers
        trust_mgr = TrustManager(data_dir=tmp_dir)
        
        # Alice completes bounties for Bob
        trust_mgr.record(alice.agent_id, "out", "bounty", outcome="delivered", rtc=10.0)
        trust_mgr.record(alice.agent_id, "out", "bounty", outcome="delivered", rtc=15.0)
        
        # Bob pays Alice
        trust_mgr.record(bob.agent_id, "out", "pay", outcome="paid", rtc=25.0)
        
        # Check scores
        alice_score = trust_mgr.score(alice.agent_id)
        bob_score = trust_mgr.score(bob.agent_id)
        
        assert alice_score["rtc_volume"] == 25.0
        assert bob_score["rtc_volume"] == 25.0
        assert alice_score["total"] == 2
        assert bob_score["total"] == 1


# ═══════════════════════════════════════════════════════════════
# RUN ALL TESTS
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
