import tempfile
import time
import unittest
from typing import Optional

from atlas import beacon_chat


class TestRelayPingSecurity(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_db_path = beacon_chat.DB_PATH
        beacon_chat.DB_PATH = f"{self._tmp.name}/beacon_atlas_test.db"
        beacon_chat.init_db()
        beacon_chat.app.config["TESTING"] = True
        self.client = beacon_chat.app.test_client()

    def tearDown(self) -> None:
        beacon_chat.DB_PATH = self._orig_db_path
        self._tmp.cleanup()

    def _insert_existing_agent(
        self,
        agent_id: str = "bcn_existing01",
        relay_token: str = "relay_valid_token",
        token_expires: Optional[float] = None,
    ) -> None:
        now = time.time()
        if token_expires is None:
            token_expires = now + 3600
        with beacon_chat.app.app_context():
            db = beacon_chat.get_db()
            db.execute(
                """
                INSERT INTO relay_agents (
                    agent_id, pubkey_hex, model_id, provider, capabilities, webhook_url,
                    relay_token, token_expires, name, status, beat_count, registered_at,
                    last_heartbeat, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    agent_id,
                    "11" * 32,
                    "test-model",
                    "beacon",
                    "[]",
                    "",
                    relay_token,
                    token_expires,
                    "Existing Agent",
                    "active",
                    1,
                    now,
                    now,
                    "{}",
                ),
            )
            db.commit()

    def test_relay_ping_rejects_unsigned_new_agent(self) -> None:
        response = self.client.post(
            "/relay/ping",
            json={
                "agent_id": "bcn_unsigned01",
                "name": "Unsigned Agent",
                "pubkey_hex": "00" * 32,
            },
        )
        self.assertEqual(response.status_code, 400)
        payload = response.get_json()
        self.assertIn("signature required", payload["error"])

    def test_relay_ping_rejects_non_hex_pubkey(self) -> None:
        response = self.client.post(
            "/relay/ping",
            json={
                "agent_id": "bcn_badpubkey01",
                "name": "Bad Pubkey Agent",
                "pubkey_hex": "zzzz",
                "signature": "00",
            },
        )
        self.assertEqual(response.status_code, 400)
        payload = response.get_json()
        self.assertIn("64 hex chars", payload["error"])

    def test_relay_ping_existing_agent_requires_relay_token(self) -> None:
        self._insert_existing_agent()
        response = self.client.post(
            "/relay/ping",
            json={
                "agent_id": "bcn_existing01",
                "name": "Existing Agent",
            },
        )
        self.assertEqual(response.status_code, 401)
        payload = response.get_json()
        self.assertIn("relay_token required", payload["error"])

    def test_relay_ping_existing_agent_rejects_invalid_relay_token(self) -> None:
        self._insert_existing_agent()
        response = self.client.post(
            "/relay/ping",
            json={
                "agent_id": "bcn_existing01",
                "name": "Existing Agent",
                "relay_token": "relay_wrong_token",
            },
        )
        self.assertEqual(response.status_code, 403)
        payload = response.get_json()
        self.assertIn("Invalid relay_token", payload["error"])

    def test_relay_ping_existing_agent_accepts_valid_relay_token(self) -> None:
        self._insert_existing_agent()
        response = self.client.post(
            "/relay/ping",
            json={
                "agent_id": "bcn_existing01",
                "name": "Existing Agent",
                "relay_token": "relay_valid_token",
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["agent_id"], "bcn_existing01")


if __name__ == "__main__":
    unittest.main()
