import tempfile
import time
import unittest

from atlas import beacon_chat


class TestDnsAuthSecurity(unittest.TestCase):
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

    def _insert_agent(
        self,
        agent_id: str = "bcn_dnsagent001",
        relay_token: str = "relay_valid_token",
        token_expires: float | None = None,
    ) -> str:
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
                    "DNS Agent",
                    "active",
                    1,
                    now,
                    now,
                    "{}",
                ),
            )
            db.commit()
        return agent_id

    def test_dns_register_requires_relay_token_for_target_agent(self) -> None:
        agent_id = self._insert_agent()

        response = self.client.post(
            "/api/dns",
            json={"name": "dns-agent", "agent_id": agent_id, "owner": "beacon"},
        )

        self.assertEqual(response.status_code, 401)
        payload = response.get_json()
        self.assertIn("Authorization", payload["error"])

        with beacon_chat.app.app_context():
            db = beacon_chat.get_db()
            row = db.execute("SELECT name FROM beacon_dns WHERE name = ?", ("dns-agent",)).fetchone()
        self.assertIsNone(row)

    def test_dns_register_rejects_wrong_relay_token(self) -> None:
        agent_id = self._insert_agent()

        response = self.client.post(
            "/api/dns",
            headers={"Authorization": "Bearer relay_wrong_token"},
            json={"name": "dns-agent", "agent_id": agent_id},
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()["code"], "AUTH_FAILED")

    def test_dns_register_rejects_expired_relay_token(self) -> None:
        agent_id = self._insert_agent(token_expires=time.time() - 1)

        response = self.client.post(
            "/api/dns",
            headers={"Authorization": "Bearer relay_valid_token"},
            json={"name": "dns-agent", "agent_id": agent_id},
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.get_json()["code"], "TOKEN_EXPIRED")

    def test_dns_register_accepts_target_agent_relay_token(self) -> None:
        agent_id = self._insert_agent()

        response = self.client.post(
            "/api/dns",
            headers={"Authorization": "Bearer relay_valid_token"},
            json={"name": "dns-agent", "agent_id": agent_id, "owner": "beacon"},
        )

        self.assertEqual(response.status_code, 201)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["agent_id"], agent_id)
        self.assertEqual(payload["name"], "dns-agent")

    def test_dns_lookup_remains_public(self) -> None:
        now = time.time()
        with beacon_chat.app.app_context():
            db = beacon_chat.get_db()
            db.execute(
                "INSERT INTO beacon_dns (name, agent_id, owner, created_at) VALUES (?, ?, ?, ?)",
                ("public-agent", "bcn_publicagent", "beacon", now),
            )
            db.commit()

        lookup = self.client.get("/api/dns/public-agent")
        listing = self.client.get("/api/dns")
        reverse = self.client.get("/api/dns/reverse/bcn_publicagent")

        self.assertEqual(lookup.status_code, 200)
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(reverse.status_code, 200)

    def test_dns_options_allows_authorized_registration_preflight(self) -> None:
        response = self.client.open("/api/dns", method="OPTIONS")

        self.assertEqual(response.status_code, 204)
        self.assertIn("POST", response.headers["Access-Control-Allow-Methods"])
        self.assertIn("Authorization", response.headers["Access-Control-Allow-Headers"])


if __name__ == "__main__":
    unittest.main()
