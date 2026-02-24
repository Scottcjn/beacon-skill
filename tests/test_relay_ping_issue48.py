import tempfile
import time
import unittest

from atlas import beacon_chat


class TestRelayPingIssue48(unittest.TestCase):
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

    def _insert_existing_agent(self) -> str:
        now = time.time()
        pubkey_hex = "11" * 32
        agent_id = beacon_chat.agent_id_from_pubkey_hex(pubkey_hex)
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
                    pubkey_hex,
                    "test-model",
                    "beacon",
                    "[]",
                    "",
                    "relay_valid_token",
                    now + 3600,
                    "Existing Agent",
                    "active",
                    1,
                    now,
                    now,
                    "{}",
                ),
            )
            db.commit()
        return agent_id

    def test_rejects_agent_id_pubkey_mismatch(self) -> None:
        response = self.client.post(
            "/relay/ping",
            json={
                "agent_id": "bcn_victim_agent",
                "name": "Mismatch Agent",
                "pubkey_hex": "22" * 32,
                "signature": "00" * 64,
                "nonce": "nonce-mismatch",
                "ts": int(time.time()),
            },
        )
        self.assertEqual(response.status_code, 400)
        payload = response.get_json()
        self.assertIn("agent_id does not match pubkey", payload["error"])

    def test_rejects_replayed_nonce_within_window(self) -> None:
        agent_id = self._insert_existing_agent()
        payload = {
            "agent_id": agent_id,
            "name": "Existing Agent",
            "relay_token": "relay_valid_token",
            "nonce": "nonce-replay",
            "ts": int(time.time()),
        }

        first = self.client.post("/relay/ping", json=payload)
        self.assertEqual(first.status_code, 200)

        replay = self.client.post("/relay/ping", json=payload)
        self.assertEqual(replay.status_code, 409)
        self.assertIn("nonce replay detected", replay.get_json()["error"])


if __name__ == "__main__":
    unittest.main()
