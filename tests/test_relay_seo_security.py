import json
import os
import tempfile
import time
import unittest

from atlas import beacon_chat

try:
    from nacl.signing import SigningKey
    HAS_NACL = True
except Exception:  # pragma: no cover - test skips when PyNaCl is unavailable
    HAS_NACL = False


@unittest.skipUnless(HAS_NACL, "pynacl not installed")
class TestRelaySeoSecurity(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_db_path = beacon_chat.DB_PATH
        self._orig_admin_key = os.environ.get("RC_ADMIN_KEY")
        os.environ["RC_ADMIN_KEY"] = "test-admin-key"
        beacon_chat.DB_PATH = f"{self._tmp.name}/beacon_atlas_test.db"
        beacon_chat.ATLAS_RATE_LIMITER._entries.clear()
        beacon_chat.ATLAS_RATE_LIMITER._last_cleanup = 0.0
        beacon_chat.init_db()
        beacon_chat.app.config["TESTING"] = True
        self.client = beacon_chat.app.test_client()

    def tearDown(self) -> None:
        beacon_chat.DB_PATH = self._orig_db_path
        if self._orig_admin_key is None:
            os.environ.pop("RC_ADMIN_KEY", None)
        else:
            os.environ["RC_ADMIN_KEY"] = self._orig_admin_key
        self._tmp.cleanup()

    def _insert_existing_agent(self):
        signing_key = SigningKey.generate()
        pubkey_hex = signing_key.verify_key.encode().hex()
        agent_id = beacon_chat.agent_id_from_pubkey_hex(pubkey_hex)
        now = time.time()
        with beacon_chat.app.app_context():
            db = beacon_chat.get_db()
            db.execute(
                """
                INSERT INTO relay_agents (
                    agent_id, pubkey_hex, model_id, provider, capabilities, webhook_url,
                    relay_token, token_expires, name, status, beat_count, registered_at,
                    last_heartbeat, metadata, origin_ip, seo_url, seo_description
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    "SEO Agent",
                    "active",
                    1,
                    now,
                    now,
                    "{}",
                    "127.0.0.1",
                    "",
                    "",
                ),
            )
            db.commit()
        return signing_key, agent_id

    def _signed_payload(self, signing_key, agent_id, **overrides):
        ts_value = int(overrides.get("ts", time.time()))
        nonce = overrides.get("nonce", f"seo-{time.time_ns()}")
        payload = {
            "agent_id": agent_id,
            "status": "alive",
            "seo_url": "https://example.com",
            "seo_description": "Signed SEO description",
            "ts": ts_value,
            "nonce": nonce,
        }
        payload.update(overrides)
        payload["signature"] = signing_key.sign(
            beacon_chat.build_relay_seo_signature_payload(
                agent_id,
                payload.get("seo_url", ""),
                payload.get("seo_description", ""),
                payload["ts"],
                payload["nonce"],
            )
        ).signature.hex()
        return payload

    def test_relay_heartbeat_seo_rejects_unsigned_field_change(self) -> None:
        _, agent_id = self._insert_existing_agent()
        response = self.client.post(
            "/relay/heartbeat/seo",
            json={
                "agent_id": agent_id,
                "status": "alive",
                "seo_url": "https://example.com",
            },
            headers={"Authorization": "Bearer relay_valid_token"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("signature required", response.get_json()["error"])

    def test_relay_heartbeat_seo_rejects_invalid_signature(self) -> None:
        _, agent_id = self._insert_existing_agent()
        payload = {
            "agent_id": agent_id,
            "status": "alive",
            "seo_url": "https://example.com",
            "seo_description": "Signed SEO description",
            "ts": int(time.time()),
            "nonce": "seo-invalid-signature",
            "signature": "00" * 64,
        }
        response = self.client.post(
            "/relay/heartbeat/seo",
            json=payload,
            headers={"Authorization": "Bearer relay_valid_token"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertIn("Invalid Ed25519 signature", response.get_json()["error"])

    def test_relay_heartbeat_seo_accepts_signed_change_and_records_history(self) -> None:
        signing_key, agent_id = self._insert_existing_agent()
        payload = self._signed_payload(
            signing_key,
            agent_id,
            seo_url="https://example.com/agent",
            seo_description="Signed backlink description",
        )
        response = self.client.post(
            "/relay/heartbeat/seo",
            json=payload,
            headers={"Authorization": "Bearer relay_valid_token"},
        )
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body["ok"])
        self.assertTrue(body["seo"]["signed_update"])
        self.assertEqual(set(body["seo"]["changed_fields"]), {"seo_url", "seo_description"})

        with beacon_chat.app.app_context():
            db = beacon_chat.get_db()
            row = db.execute(
                "SELECT seo_url, seo_description FROM relay_agents WHERE agent_id = ?",
                (agent_id,),
            ).fetchone()
            hist = db.execute(
                "SELECT changed_fields, before_state, after_state FROM relay_seo_history WHERE agent_id = ?",
                (agent_id,),
            ).fetchone()

        self.assertEqual(row["seo_url"], "https://example.com/agent")
        self.assertEqual(row["seo_description"], "Signed backlink description")
        self.assertEqual(json.loads(hist["changed_fields"]), ["seo_url", "seo_description"])
        self.assertEqual(json.loads(hist["before_state"])["seo_url"], "")
        self.assertEqual(json.loads(hist["after_state"])["seo_url"], "https://example.com/agent")

    def test_relay_client_partial_update_verifies_with_stored_other_field(self) -> None:
        """#963: a description-only update must verify when seo_url is already stored."""
        from unittest import mock

        from beacon_skill.transports.relay import RelayClient

        signing_key, agent_id = self._insert_existing_agent()
        with beacon_chat.app.app_context():
            db = beacon_chat.get_db()
            db.execute(
                "UPDATE relay_agents SET seo_url = ? WHERE agent_id = ?",
                ("https://stored.example/", agent_id),
            )
            db.commit()

        identity = mock.Mock()
        identity.agent_id = agent_id
        identity.sign_hex = lambda data: signing_key.sign(data).signature.hex()
        sent = {}

        def fake_post(url, json=None, headers=None, timeout=None):
            sent["body"] = json
            return mock.Mock(json=lambda: {})

        with mock.patch("beacon_skill.transports.relay.requests.post", fake_post):
            RelayClient("https://relay.test").heartbeat_seo(
                identity, "relay_valid_token", seo_description="Only the description"
            )

        response = self.client.post(
            "/relay/heartbeat/seo",
            json=sent["body"],
            headers={"Authorization": "Bearer relay_valid_token"},
        )
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json()["seo"]["changed_fields"], ["seo_description"])

        with beacon_chat.app.app_context():
            row = beacon_chat.get_db().execute(
                "SELECT seo_url, seo_description FROM relay_agents WHERE agent_id = ?",
                (agent_id,),
            ).fetchone()
        self.assertEqual(row["seo_url"], "https://stored.example/")
        self.assertEqual(row["seo_description"], "Only the description")

    def test_relay_heartbeat_seo_partial_update_rejects_wrong_signature(self) -> None:
        signing_key, agent_id = self._insert_existing_agent()
        payload = self._signed_payload(signing_key, agent_id, seo_url="https://other.example/")
        del payload["seo_url"]
        response = self.client.post(
            "/relay/heartbeat/seo",
            json=payload,
            headers={"Authorization": "Bearer relay_valid_token"},
        )
        self.assertEqual(response.status_code, 403)

    def test_relay_seo_history_hides_operator_fields_without_admin(self) -> None:
        signing_key, agent_id = self._insert_existing_agent()
        payload = self._signed_payload(signing_key, agent_id)
        self.client.post(
            "/relay/heartbeat/seo",
            json=payload,
            headers={"Authorization": "Bearer relay_valid_token"},
        )

        public_resp = self.client.get(f"/relay/seo/history/{agent_id}")
        self.assertEqual(public_resp.status_code, 200)
        public_entry = public_resp.get_json()["history"][0]
        self.assertNotIn("origin_ip", public_entry)
        self.assertNotIn("nonce", public_entry)

        admin_resp = self.client.get(
            f"/relay/seo/history/{agent_id}",
            headers={"X-Admin-Key": "test-admin-key"},
        )
        self.assertEqual(admin_resp.status_code, 200)
        admin_body = admin_resp.get_json()
        admin_entry = admin_body["history"][0]
        self.assertEqual(admin_body["visibility"], "operator")
        self.assertIn("origin_ip", admin_entry)
        self.assertIn("nonce", admin_entry)
        self.assertTrue(admin_entry["signature_preview"])


if __name__ == "__main__":
    unittest.main()
