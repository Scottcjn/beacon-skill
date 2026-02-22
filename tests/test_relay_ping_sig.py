"""Tests for relay_ping signature verification (RustChain bounty #388)."""

import importlib.util
import json
import sqlite3
import secrets
from pathlib import Path

import pytest

from beacon_skill.identity import AgentIdentity

MODULE_PATH = Path(__file__).resolve().parents[1] / "atlas" / "beacon_chat.py"
spec = importlib.util.spec_from_file_location("atlas_beacon_chat", MODULE_PATH)
beacon_chat = importlib.util.module_from_spec(spec)
spec.loader.exec_module(beacon_chat)


def setup_function():
    """Reset rate limiter and ensure relay_agents table exists."""
    beacon_chat.ATLAS_RATE_LIMITER._entries.clear()
    beacon_chat.ATLAS_RATE_LIMITER._last_cleanup = 0.0

    conn = sqlite3.connect(beacon_chat.DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS relay_agents (
            agent_id TEXT PRIMARY KEY,
            pubkey_hex TEXT,
            model_id TEXT,
            provider TEXT,
            capabilities TEXT,
            webhook_url TEXT,
            relay_token TEXT NOT NULL,
            token_expires REAL,
            name TEXT,
            status TEXT,
            beat_count INTEGER,
            registered_at REAL,
            last_heartbeat REAL,
            metadata TEXT,
            origin_ip TEXT
        )
    """)
    conn.execute("DELETE FROM relay_agents")
    conn.commit()
    conn.close()


# Use a counter to generate unique IPs for each test to avoid rate limiting
_ip_counter = 0


def _next_ip():
    global _ip_counter
    _ip_counter += 1
    return f"10.100.{_ip_counter // 256}.{_ip_counter % 256}"


class TestRelayPingSignatureVerification:
    """Tests for /relay/ping endpoint signature verification."""

    def test_new_agent_requires_pubkey_hex(self):
        """New agent registration must provide pubkey_hex."""
        beacon_chat.app.config["TESTING"] = True
        client = beacon_chat.app.test_client()

        response = client.post("/relay/ping", json={
            "agent_id": "bcn_test123456",
            "name": "TestAgent",
        }, environ_overrides={"REMOTE_ADDR": _next_ip()})

        assert response.status_code == 400
        data = json.loads(response.data)
        assert "pubkey_hex" in data["error"]

    def test_new_agent_requires_signature(self):
        """New agent registration must provide Ed25519 signature."""
        beacon_chat.app.config["TESTING"] = True
        client = beacon_chat.app.test_client()

        ident = AgentIdentity.generate()
        agent_id = ident.agent_id

        response = client.post("/relay/ping", json={
            "agent_id": agent_id,
            "pubkey_hex": ident.public_key_hex,
            "name": "TestAgent",
        }, environ_overrides={"REMOTE_ADDR": _next_ip()})

        assert response.status_code == 400
        data = json.loads(response.data)
        assert "signature" in data["error"]

    def test_new_agent_rejects_invalid_signature(self):
        """Invalid Ed25519 signature should be rejected."""
        beacon_chat.app.config["TESTING"] = True
        client = beacon_chat.app.test_client()

        ident = AgentIdentity.generate()
        agent_id = ident.agent_id

        response = client.post("/relay/ping", json={
            "agent_id": agent_id,
            "pubkey_hex": ident.public_key_hex,
            "name": "TestAgent",
            "signature": "0" * 128,
        }, environ_overrides={"REMOTE_ADDR": _next_ip()})

        assert response.status_code == 403
        data = json.loads(response.data)
        assert "Invalid Ed25519 signature" in data["error"]

    def test_new_agent_accepts_valid_signature(self):
        """Valid Ed25519 signature should be accepted for new agent registration."""
        beacon_chat.app.config["TESTING"] = True
        client = beacon_chat.app.test_client()

        ident = AgentIdentity.generate()
        agent_id = ident.agent_id
        signature = ident.sign_hex(agent_id.encode("utf-8"))

        response = client.post("/relay/ping", json={
            "agent_id": agent_id,
            "pubkey_hex": ident.public_key_hex,
            "name": "TestAgent",
            "signature": signature,
        }, environ_overrides={"REMOTE_ADDR": _next_ip()})

        assert response.status_code == 201
        data = json.loads(response.data)
        assert data["ok"] is True
        assert data["auto_registered"] is True
        assert "relay_token" in data
        assert data["signature_verified"] is True

    def test_new_agent_pubkey_must_derive_to_agent_id(self):
        """pubkey_hex must derive to the provided agent_id."""
        beacon_chat.app.config["TESTING"] = True
        client = beacon_chat.app.test_client()

        ident = AgentIdentity.generate()
        wrong_agent_id = "bcn_wrong123456"

        signature = ident.sign_hex(wrong_agent_id.encode("utf-8"))

        response = client.post("/relay/ping", json={
            "agent_id": wrong_agent_id,
            "pubkey_hex": ident.public_key_hex,
            "name": "TestAgent",
            "signature": signature,
        }, environ_overrides={"REMOTE_ADDR": _next_ip()})

        assert response.status_code == 400
        data = json.loads(response.data)
        assert "agent_id mismatch" in data["error"]

    def test_existing_agent_requires_relay_token(self):
        """Existing agents must provide relay_token for heartbeat updates."""
        beacon_chat.app.config["TESTING"] = True
        client = beacon_chat.app.test_client()

        # First register an agent with valid signature
        ident = AgentIdentity.generate()
        agent_id = ident.agent_id
        signature = ident.sign_hex(agent_id.encode("utf-8"))

        reg_response = client.post("/relay/ping", json={
            "agent_id": agent_id,
            "pubkey_hex": ident.public_key_hex,
            "name": "TestAgent",
            "signature": signature,
        }, environ_overrides={"REMOTE_ADDR": _next_ip()})
        assert reg_response.status_code == 201

        # Now try to heartbeat without relay_token
        heartbeat_response = client.post("/relay/ping", json={
            "agent_id": agent_id,
            "status": "alive",
        }, environ_overrides={"REMOTE_ADDR": _next_ip()})

        assert heartbeat_response.status_code == 401
        data = json.loads(heartbeat_response.data)
        assert "Authorization" in data["error"]

    def test_existing_agent_rejects_invalid_relay_token(self):
        """Invalid relay_token should be rejected for existing agent."""
        beacon_chat.app.config["TESTING"] = True
        client = beacon_chat.app.test_client()

        # First register an agent
        ident = AgentIdentity.generate()
        agent_id = ident.agent_id
        signature = ident.sign_hex(agent_id.encode("utf-8"))

        reg_response = client.post("/relay/ping", json={
            "agent_id": agent_id,
            "pubkey_hex": ident.public_key_hex,
            "name": "TestAgent",
            "signature": signature,
        }, environ_overrides={"REMOTE_ADDR": _next_ip()})
        assert reg_response.status_code == 201

        # Try to heartbeat with wrong token
        heartbeat_response = client.post("/relay/ping",
            json={"agent_id": agent_id, "status": "alive"},
            headers={"Authorization": "Bearer wrong_token"},
            environ_overrides={"REMOTE_ADDR": _next_ip()}
        )

        assert heartbeat_response.status_code == 403
        data = json.loads(heartbeat_response.data)
        assert "Invalid relay_token" in data["error"]

    def test_existing_agent_heartbeat_succeeds_with_valid_token(self):
        """Existing agents can heartbeat with valid relay_token."""
        beacon_chat.app.config["TESTING"] = True
        client = beacon_chat.app.test_client()

        # First register an agent
        ident = AgentIdentity.generate()
        agent_id = ident.agent_id
        signature = ident.sign_hex(agent_id.encode("utf-8"))

        reg_response = client.post("/relay/ping", json={
            "agent_id": agent_id,
            "pubkey_hex": ident.public_key_hex,
            "name": "TestAgent",
            "signature": signature,
        }, environ_overrides={"REMOTE_ADDR": _next_ip()})
        assert reg_response.status_code == 201
        reg_data = json.loads(reg_response.data)
        relay_token = reg_data["relay_token"]

        # Heartbeat with valid token
        heartbeat_response = client.post("/relay/ping",
            json={"agent_id": agent_id, "status": "alive"},
            headers={"Authorization": f"Bearer {relay_token}"},
            environ_overrides={"REMOTE_ADDR": _next_ip()}
        )

        assert heartbeat_response.status_code == 200
        data = json.loads(heartbeat_response.data)
        assert data["ok"] is True
        assert data["beat_count"] == 2

    def test_unsigned_ping_rejected(self):
        """Ensure unsigned ping is rejected - main test for bounty #388."""
        beacon_chat.app.config["TESTING"] = True
        client = beacon_chat.app.test_client()

        # Try to register without any signature
        response = client.post("/relay/ping", json={
            "agent_id": "bcn_unsigned123",
            "name": "UnsignedAgent",
        }, environ_overrides={"REMOTE_ADDR": _next_ip()})

        # Should fail with signature or pubkey_hex requirement
        assert response.status_code in (400, 401, 403)
        data = json.loads(response.data)
        assert "signature" in data["error"].lower() or "pubkey_hex" in data["error"].lower()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
