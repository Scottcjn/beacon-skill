import sqlite3
import tempfile
import time
import unittest
from typing import Optional

from atlas import beacon_chat


class TestContractAuthSecurity(unittest.TestCase):
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

    def _insert_relay_agent(
        self,
        agent_id: str,
        token: str,
        *,
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
                    token,
                    token_expires,
                    agent_id,
                    "active",
                    1,
                    now,
                    now,
                    "{}",
                ),
            )
            db.commit()

    def _contract_payload(self) -> dict:
        return {
            "from": "bcn_contract_from",
            "to": "bcn_contract_to",
            "type": "rent",
            "amount": 1,
            "term": "7d",
        }

    def _contract_count(self) -> int:
        conn = sqlite3.connect(beacon_chat.DB_PATH)
        try:
            return conn.execute(
                "SELECT COUNT(*) FROM contracts WHERE from_agent = ?",
                ("bcn_contract_from",),
            ).fetchone()[0]
        finally:
            conn.close()

    def _contract_state(self, contract_id: str) -> str:
        conn = sqlite3.connect(beacon_chat.DB_PATH)
        try:
            return conn.execute(
                "SELECT state FROM contracts WHERE id = ?",
                (contract_id,),
            ).fetchone()[0]
        finally:
            conn.close()

    def test_contract_create_requires_from_agent_relay_token(self) -> None:
        self._insert_relay_agent("bcn_contract_from", "relay_from_token")
        self._insert_relay_agent("bcn_contract_to", "relay_to_token")

        response = self.client.post("/api/contracts", json=self._contract_payload())

        self.assertEqual(response.status_code, 401)
        self.assertEqual(self._contract_count(), 0)

    def test_contract_create_rejects_non_initiator_relay_token(self) -> None:
        self._insert_relay_agent("bcn_contract_from", "relay_from_token")
        self._insert_relay_agent("bcn_contract_to", "relay_to_token")

        response = self.client.post(
            "/api/contracts",
            headers={"Authorization": "Bearer relay_to_token"},
            json=self._contract_payload(),
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(self._contract_count(), 0)

    def test_contract_create_accepts_from_agent_relay_token(self) -> None:
        self._insert_relay_agent("bcn_contract_from", "relay_from_token")
        self._insert_relay_agent("bcn_contract_to", "relay_to_token")

        response = self.client.post(
            "/api/contracts",
            headers={"Authorization": "Bearer relay_from_token"},
            json=self._contract_payload(),
        )

        self.assertEqual(response.status_code, 201)
        payload = response.get_json()
        self.assertEqual(payload["from"], "bcn_contract_from")
        self.assertEqual(payload["to"], "bcn_contract_to")
        self.assertEqual(self._contract_count(), 1)

    def test_contract_patch_requires_registered_party_relay_token(self) -> None:
        self._insert_relay_agent("bcn_contract_from", "relay_from_token")
        self._insert_relay_agent("bcn_contract_to", "relay_to_token")
        self._insert_relay_agent("bcn_contract_outsider", "relay_outsider_token")
        now = time.time()

        with beacon_chat.app.app_context():
            db = beacon_chat.get_db()
            db.execute(
                """
                INSERT INTO contracts (
                    id, type, from_agent, to_agent, amount, currency, state, term,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "ctr_auth_test",
                    "rent",
                    "bcn_contract_from",
                    "bcn_contract_to",
                    1,
                    "RTC",
                    "offered",
                    "7d",
                    now,
                    now,
                ),
            )
            db.commit()

        missing = self.client.patch("/api/contracts/ctr_auth_test", json={"state": "active"})
        outsider = self.client.patch(
            "/api/contracts/ctr_auth_test",
            headers={"Authorization": "Bearer relay_outsider_token"},
            json={"state": "active"},
        )
        party = self.client.patch(
            "/api/contracts/ctr_auth_test",
            headers={"Authorization": "Bearer relay_to_token"},
            json={"state": "active"},
        )

        self.assertEqual(missing.status_code, 401)
        self.assertEqual(outsider.status_code, 403)
        self.assertEqual(party.status_code, 200)
        self.assertEqual(self._contract_state("ctr_auth_test"), "active")


if __name__ == "__main__":
    unittest.main()
