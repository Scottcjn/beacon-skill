import tempfile
import time
import unittest

from atlas import beacon_chat


class TestRelayHeartbeatSecurity(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_db_path = beacon_chat.DB_PATH
        beacon_chat.DB_PATH = f"{self._tmp.name}/beacon_atlas_test.db"
        beacon_chat.ATLAS_RATE_LIMITER._entries.clear()
        beacon_chat.ATLAS_RATE_LIMITER._last_cleanup = 0.0
        beacon_chat.init_db()
        beacon_chat.app.config["TESTING"] = True
        self.client = beacon_chat.app.test_client()

    def tearDown(self) -> None:
        beacon_chat.DB_PATH = self._orig_db_path
        self._tmp.cleanup()

    def test_relay_heartbeat_rejects_unknown_agent_with_bearer_token(self) -> None:
        response = self.client.post(
            "/relay/heartbeat",
            headers={"Authorization": "Bearer attacker-controlled-garbage"},
            json={
                "agent_id": "bcn_attacker001",
                "status": "alive",
                "name": "Attacker",
                "capabilities": ["relay"],
            },
        )

        self.assertEqual(response.status_code, 404)
        payload = response.get_json()
        self.assertEqual(payload["code"], "AGENT_NOT_REGISTERED")
        self.assertNotIn("relay_token", payload)

        with beacon_chat.app.app_context():
            db = beacon_chat.get_db()
            row = db.execute(
                "SELECT agent_id FROM relay_agents WHERE agent_id = ?",
                ("bcn_attacker001",),
            ).fetchone()
        self.assertIsNone(row)

    def test_relay_heartbeat_accepts_registered_agent_with_valid_token(self) -> None:
        now = time.time()
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
                    "bcn_registered1",
                    "11" * 32,
                    "test-model",
                    "beacon",
                    "[]",
                    "",
                    "relay_valid_token",
                    now + 3600,
                    "Registered Agent",
                    "active",
                    1,
                    now,
                    now,
                    "{}",
                ),
            )
            db.commit()

        response = self.client.post(
            "/relay/heartbeat",
            headers={"Authorization": "Bearer relay_valid_token"},
            json={"agent_id": "bcn_registered1", "status": "alive"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["beat_count"], 2)
        self.assertNotIn("relay_token", payload)


if __name__ == "__main__":
    unittest.main()
