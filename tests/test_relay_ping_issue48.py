import sqlite3
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
        with beacon_chat.app.app_context():
            db = beacon_chat.get_db()
            try:
                db.execute("ALTER TABLE relay_agents ADD COLUMN origin_ip TEXT DEFAULT ''")
            except sqlite3.OperationalError:
                pass
            db.commit()
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

    def _insert_swarmhub_agent(self) -> tuple[str, str]:
        now = time.time()
        agent_id = "relay_sh_test_agent"
        relay_token = "relay_swarmhub_token"
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
                    "33" * 32,
                    "swarmhub-seeded",
                    "swarmhub",
                    "[]",
                    "",
                    relay_token,
                    now + 3600,
                    "SwarmHub Agent",
                    "active",
                    7,
                    now,
                    now,
                    "{}",
                ),
            )
            db.commit()
        return agent_id, relay_token

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

    def test_allows_swarmhub_heartbeat_for_non_bcn_agent(self) -> None:
        agent_id, relay_token = self._insert_swarmhub_agent()
        response = self.client.post(
            "/relay/ping",
            json={
                "agent_id": agent_id,
                "name": "SwarmHub Agent",
                "relay_token": relay_token,
                "nonce": "nonce-swarmhub-1",
                "ts": int(time.time()),
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["agent_id"], agent_id)
        self.assertEqual(payload["beat_count"], 8)

    def test_registration_nonce_is_reserved_for_first_heartbeat(self) -> None:
        now = int(time.time())
        original_verify = beacon_chat.verify_ed25519
        beacon_chat.verify_ed25519 = lambda *_args, **_kwargs: True
        try:
            register = self.client.post(
                "/relay/ping",
                json={
                    "agent_id": "relay_sh_nonce_reserve",
                    "name": "Nonce Reserve Agent",
                    "provider": "swarmhub",
                    "pubkey_hex": "44" * 32,
                    "signature": "aa" * 64,
                    "nonce": "nonce-register-1",
                    "ts": now,
                },
            )
            self.assertEqual(register.status_code, 201)
            relay_token = register.get_json()["relay_token"]

            replay = self.client.post(
                "/relay/ping",
                json={
                    "agent_id": "relay_sh_nonce_reserve",
                    "name": "Nonce Reserve Agent",
                    "relay_token": relay_token,
                    "nonce": "nonce-register-1",
                    "ts": now,
                },
            )
            self.assertEqual(replay.status_code, 409)
            self.assertIn("nonce replay detected", replay.get_json()["error"])
        finally:
            beacon_chat.verify_ed25519 = original_verify


if __name__ == "__main__":
    unittest.main()
