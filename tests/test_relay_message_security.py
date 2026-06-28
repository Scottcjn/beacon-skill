import tempfile
import time
import unittest

from atlas import beacon_chat
from beacon_skill.codec import decode_envelopes, encode_envelope
from beacon_skill.identity import AgentIdentity


class TestRelayMessageSecurity(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_db_path = beacon_chat.DB_PATH
        beacon_chat.DB_PATH = f"{self._tmp.name}/beacon_atlas_test.db"
        beacon_chat.ATLAS_RATE_LIMITER._entries.clear()
        beacon_chat.ATLAS_RATE_LIMITER._last_cleanup = 0.0
        beacon_chat.init_db()
        beacon_chat.app.config["TESTING"] = True
        self.client = beacon_chat.app.test_client()
        self.identity = AgentIdentity.generate()
        self.agent_id = self.identity.agent_id
        self.relay_token = "relay_valid_token"
        self._insert_agent()

    def tearDown(self) -> None:
        beacon_chat.DB_PATH = self._orig_db_path
        self._tmp.cleanup()

    def _insert_agent(self) -> None:
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
                    self.agent_id,
                    self.identity.public_key_hex,
                    "test-model",
                    "beacon",
                    "[]",
                    "",
                    self.relay_token,
                    now + 3600,
                    "Message Agent",
                    "active",
                    1,
                    now,
                    now,
                    "{}",
                ),
            )
            db.commit()

    def _signed_envelope(self, *, nonce: str | None = None, ts: int | None = None) -> dict:
        payload = {
            "kind": "hello",
            "text": "signed relay message",
            "ts": int(ts if ts is not None else time.time()),
        }
        if nonce is not None:
            payload["nonce"] = nonce
        text = encode_envelope(payload, version=2, identity=self.identity, include_pubkey=True)
        return decode_envelopes(text)[0]

    def _post_message(self, envelope: dict, *, agent_id: str | None = None):
        return self.client.post(
            "/relay/message",
            headers={"Authorization": f"Bearer {self.relay_token}"},
            json={"agent_id": agent_id or self.agent_id, "envelope": envelope},
        )

    def test_relay_message_rejects_unsigned_envelope_even_with_valid_token(self) -> None:
        response = self._post_message(
            {
                "kind": "hello",
                "agent_id": self.agent_id,
                "nonce": "unsigned-message",
                "ts": int(time.time()),
            }
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["code"], "SIGNATURE_UNVERIFIABLE")

    def test_relay_message_rejects_tampered_envelope(self) -> None:
        envelope = self._signed_envelope()
        envelope["text"] = "tampered after signing"

        response = self._post_message(envelope)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()["code"], "SIGNATURE_INVALID")

    def test_relay_message_rejects_agent_id_mismatch(self) -> None:
        other_identity = AgentIdentity.generate()
        text = encode_envelope(
            {"kind": "hello", "text": "wrong sender", "ts": int(time.time())},
            version=2,
            identity=other_identity,
            include_pubkey=True,
        )
        envelope = decode_envelopes(text)[0]

        response = self._post_message(envelope)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()["code"], "AGENT_ID_MISMATCH")

    def test_relay_message_rejects_nonce_replay(self) -> None:
        envelope = self._signed_envelope(nonce="fixed-message-nonce")

        first = self._post_message(envelope)
        second = self._post_message(envelope)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 409)
        self.assertEqual(second.get_json()["code"], "NONCE_REPLAY")

    def test_relay_message_accepts_valid_signed_envelope(self) -> None:
        envelope = self._signed_envelope()

        response = self._post_message(envelope)

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["forwarded"])
        self.assertEqual(payload["nonce"], envelope["nonce"])


if __name__ == "__main__":
    unittest.main()
