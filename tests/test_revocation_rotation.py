import os
import tempfile
import time
import unittest
import json
import secrets
import hashlib
from typing import Optional

# Set environment variable before importing beacon_chat
os.environ["RC_ADMIN_KEY"] = "test-admin-key"

from atlas import beacon_chat

try:
    from nacl.signing import SigningKey
    HAS_NACL = True
except ImportError:
    HAS_NACL = False

class TestKeyRevocationRotation(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_db_path = beacon_chat.DB_PATH
        beacon_chat.DB_PATH = f"{self._tmp.name}/beacon_atlas_test.db"
        beacon_chat.init_db()
        beacon_chat.app.config["TESTING"] = True
        self.client = beacon_chat.app.test_client()
        self.admin_key = "test-admin-key"

    def tearDown(self) -> None:
        beacon_chat.DB_PATH = self._orig_db_path
        self._tmp.cleanup()

    def _insert_agent(self, agent_id: str, pubkey_hex: str, status: str = "active"):
        now = time.time()
        with beacon_chat.app.app_context():
            db = beacon_chat.get_db()
            db.execute(
                """
                INSERT INTO relay_agents (
                    agent_id, pubkey_hex, model_id, provider, capabilities, webhook_url,
                    relay_token, token_expires, name, status, beat_count, registered_at,
                    last_heartbeat, metadata, origin_ip
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (agent_id, pubkey_hex, "test-model", "beacon", "[]", "", 
                 "relay_token_" + agent_id, now + 3600, "Test Agent", status, 
                 1, now, now, "{}", "127.0.0.1")
            )
            db.commit()

    def test_revocation_prevents_ping(self):
        agent_id = "bcn_revoked01"
        self._insert_agent(agent_id, "11" * 32, status="revoked")
        
        response = self.client.post(
            "/relay/ping",
            json={
                "agent_id": agent_id,
                "relay_token": "relay_token_" + agent_id
            }
        )
        self.assertEqual(response.status_code, 403)
        self.assertIn("revoked", response.get_json()["error"])

    def test_revocation_prevents_registration(self):
        # We need the ACTUAL agent_id derived from the pubkey
        pubkey = "22" * 32
        pubkey_bytes = bytes.fromhex(pubkey)
        agent_id = "bcn_" + hashlib.sha256(pubkey_bytes).hexdigest()[:12]
        
        self._insert_agent(agent_id, pubkey, status="revoked")
        
        # Try to register again with same pubkey
        response = self.client.post(
            "/relay/register",
            json={
                "pubkey_hex": pubkey,
                "model_id": "new-model",
                "name": "New Name",
                "provider": "beacon"
            }
        )
        self.assertEqual(response.status_code, 403)
        self.assertIn("revoked", response.get_json()["error"])

    @unittest.skipUnless(HAS_NACL, "pynacl not installed")
    def test_key_rotation_success(self):
        # Create a real key pair
        old_sk = SigningKey.generate()
        old_vk = old_sk.verify_key
        old_pubkey_hex = old_vk.encode().hex()
        
        new_sk = SigningKey.generate()
        new_pubkey_hex = new_sk.verify_key.encode().hex()
        
        agent_id = "bcn_rotate01"
        self._insert_agent(agent_id, old_pubkey_hex)
        
        # Sign rotation message: "rotate:<agent_id>:<new_pubkey_hex>"
        msg = f"rotate:{agent_id}:{new_pubkey_hex}".encode("utf-8")
        sig = old_sk.sign(msg).signature.hex()
        
        response = self.client.post(
            "/relay/identity/rotate",
            json={
                "agent_id": agent_id,
                "new_pubkey_hex": new_pubkey_hex,
                "signature": sig
            }
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["ok"])
        
        # Verify db updated
        with beacon_chat.app.app_context():
            db = beacon_chat.get_db()
            row = db.execute("SELECT pubkey_hex FROM relay_agents WHERE agent_id = ?", (agent_id,)).fetchone()
            self.assertEqual(row["pubkey_hex"], new_pubkey_hex)
            
            # Verify rotation log
            log = db.execute("SELECT * FROM relay_identity_rotations WHERE agent_id = ?", (agent_id,)).fetchone()
            self.assertIsNotNone(log)
            self.assertEqual(log["old_pubkey_hex"], old_pubkey_hex)
            self.assertEqual(log["new_pubkey_hex"], new_pubkey_hex)

    @unittest.skipUnless(HAS_NACL, "pynacl not installed")
    def test_key_rotation_invalid_signature(self):
        old_sk = SigningKey.generate()
        wrong_sk = SigningKey.generate()
        old_pubkey_hex = old_sk.verify_key.encode().hex()
        new_pubkey_hex = "33" * 32
        
        agent_id = "bcn_rotate02"
        self._insert_agent(agent_id, old_pubkey_hex)
        
        # Sign with WRONG key
        msg = f"rotate:{agent_id}:{new_pubkey_hex}".encode("utf-8")
        sig = wrong_sk.sign(msg).signature.hex()
        
        response = self.client.post(
            "/relay/identity/rotate",
            json={
                "agent_id": agent_id,
                "new_pubkey_hex": new_pubkey_hex,
                "signature": sig
            }
        )
        self.assertEqual(response.status_code, 403)
        self.assertIn("Invalid signature", response.get_json()["error"])

    def test_admin_revoke(self):
        agent_id = "bcn_to_revoke"
        self._insert_agent(agent_id, "44" * 32)
        
        response = self.client.post(
            "/relay/identity/revoke",
            headers={"X-Admin-Key": self.admin_key},
            json={
                "agent_id": agent_id,
                "reason": "compromised"
            }
        )
        self.assertEqual(response.status_code, 200)
        
        # Verify status in DB
        with beacon_chat.app.app_context():
            db = beacon_chat.get_db()
            row = db.execute("SELECT status FROM relay_agents WHERE agent_id = ?", (agent_id,)).fetchone()
            self.assertEqual(row["status"], "revoked")

    def test_admin_revoke_unauthorized(self):
        response = self.client.post(
            "/relay/identity/revoke",
            headers={"X-Admin-Key": "wrong-key"},
            json={"agent_id": "some_agent"}
        )
        self.assertEqual(response.status_code, 401)

if __name__ == "__main__":
    unittest.main()
