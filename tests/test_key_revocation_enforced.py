"""Regression tests: a locally revoked key must not verify.

Before the fix, ``beacon keys revoke`` only flipped a flag in known_keys.json.
verify_envelope() preferred the envelope's embedded pubkey and was handed only
bare hex strings by inbox/fastapi, so envelopes signed by a revoked (e.g.
compromised) key still came back ``verified=True`` from read_inbox() and were
accepted by the webhook servers.
"""

import http.client
import json
import os
import shutil
import tempfile
import time
import unittest
from unittest import mock

from beacon_skill import key_management
from beacon_skill.codec import decode_envelopes, encode_envelope, verify_envelope
from beacon_skill.guard import clear_nonce_cache
from beacon_skill.identity import AgentIdentity


def _signed_env(ident, *, include_pubkey=True, nonce=None):
    payload = {"kind": "hello", "ts": int(time.time())}
    if nonce:
        payload["nonce"] = nonce
    text = encode_envelope(payload, version=2, identity=ident, include_pubkey=include_pubkey)
    return text, decode_envelopes(text)[0]


class _TmpHome(unittest.TestCase):
    def setUp(self):
        self._tmp_home = tempfile.mkdtemp(prefix="beacon_revoke_")
        self._env = mock.patch.dict(os.environ, {"HOME": self._tmp_home}, clear=False)
        self._env.start()
        os.environ.pop("BEACON_INBOX_PATH", None)

    def tearDown(self):
        self._env.stop()
        shutil.rmtree(self._tmp_home, ignore_errors=True)


class TestCodecRevocation(unittest.TestCase):
    def test_revoked_metadata_rejects_embedded_pubkey_envelope(self):
        ident = AgentIdentity.generate()
        _, env = _signed_env(ident, include_pubkey=True)
        keys = {ident.agent_id: {"pubkey_hex": ident.public_key_hex, "revoked": True}}
        self.assertIs(verify_envelope(env, known_keys=keys), False)

    def test_revoked_metadata_rejects_known_key_envelope(self):
        ident = AgentIdentity.generate()
        _, env = _signed_env(ident, include_pubkey=False)
        keys = {ident.agent_id: {"pubkey_hex": ident.public_key_hex, "revoked": True}}
        self.assertIs(verify_envelope(env, known_keys=keys), False)

    def test_non_revoked_metadata_still_verifies(self):
        ident = AgentIdentity.generate()
        _, env = _signed_env(ident, include_pubkey=True)
        keys = {ident.agent_id: {"pubkey_hex": ident.public_key_hex, "revoked": False}}
        self.assertIs(verify_envelope(env, known_keys=keys), True)


class TestInboxRevocation(_TmpHome):
    def _write_inbox(self, text):
        from beacon_skill.storage import _inbox_path

        _inbox_path().write_text(
            json.dumps({"platform": "udp", "received_at": time.time(), "text": text}) + "\n",
            encoding="utf-8",
        )

    def test_read_inbox_marks_revoked_sender_unverified(self):
        from beacon_skill.inbox import read_inbox

        ident = AgentIdentity.generate()
        text, _ = _signed_env(ident, include_pubkey=True)
        self._write_inbox(text)

        # Sanity: before revocation the envelope verifies (and is TOFU-learned).
        self.assertIs(read_inbox()[0]["verified"], True)

        self.assertTrue(key_management.revoke_key(ident.agent_id, "compromised"))
        self.assertIs(read_inbox()[0]["verified"], False)


class TestWebhookRevocation(_TmpHome):
    def setUp(self):
        super().setUp()
        from beacon_skill.transports.webhook import WebhookServer

        clear_nonce_cache()
        self.server = WebhookServer(port=0, host="127.0.0.1")
        self.server.start(blocking=False)
        time.sleep(0.2)
        self.port = self.server._server.server_port  # type: ignore[attr-defined]

    def tearDown(self):
        self.server.stop()
        super().tearDown()

    def _post(self, payload):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request("POST", "/beacon/inbox", body=json.dumps(payload),
                         headers={"Content-Type": "application/json"})
            resp = conn.getresponse()
            return resp.status, json.loads(resp.read().decode("utf-8"))
        finally:
            conn.close()

    def test_revoked_sender_rejected(self):
        ident = AgentIdentity.generate()
        key_management.trust_key(ident.agent_id, ident.public_key_hex)
        key_management.revoke_key(ident.agent_id, "compromised")
        _, env = _signed_env(ident, include_pubkey=True, nonce="revokednonce1")

        status, body = self._post(env)
        self.assertEqual(status, 400)
        self.assertFalse(body["results"][0]["accepted"])
        self.assertEqual(body["results"][0]["reason"], "signature_invalid")

    def test_known_sender_without_embedded_pubkey_accepted(self):
        # Exercises the metadata-shaped known_keys path end to end: this
        # request used to crash the handler with a TypeError.
        ident = AgentIdentity.generate()
        key_management.trust_key(ident.agent_id, ident.public_key_hex)
        _, env = _signed_env(ident, include_pubkey=False, nonce="knownnokey01")

        status, body = self._post(env)
        self.assertEqual(status, 200)
        self.assertTrue(body["results"][0]["accepted"])
        self.assertIs(body["results"][0]["verified"], True)


if __name__ == "__main__":
    unittest.main()
