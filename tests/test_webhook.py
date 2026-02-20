import http.client
import json
import os
import shutil
import time
import unittest

from beacon_skill.codec import decode_envelopes, encode_envelope
from beacon_skill.guard import clear_nonce_cache
from beacon_skill.identity import AgentIdentity
from beacon_skill.transports.webhook import WebhookServer


class WebhookSignatureGateTests(unittest.TestCase):
    def _post_with_retry(self, payload, attempts: int = 5):
        last = None
        for _ in range(attempts):
            try:
                conn = http.client.HTTPConnection(
                    "127.0.0.1",
                    self.port,
                    timeout=5,
                    source_address=("127.0.0.1", 0),
                )
                body = json.dumps(payload)
                conn.request("POST", "/beacon/inbox", body=body, headers={"Content-Type": "application/json"})
                resp = conn.getresponse()
                status = resp.status
                data = json.loads(resp.read().decode("utf-8"))
                conn.close()
                return status, data
            except OSError as exc:
                last = exc
                time.sleep(0.1)
        raise last

    def setUp(self):
        self._old_home = os.environ.get("HOME")
        self._tmp_home = os.path.join(os.getcwd(), f".tmp_home_webhook_{int(time.time() * 1000)}")
        os.makedirs(self._tmp_home, exist_ok=True)
        os.environ["HOME"] = self._tmp_home
        clear_nonce_cache()
        self.server = WebhookServer(port=0, host="127.0.0.1")
        self.server.start(blocking=False)
        time.sleep(0.2)
        self.port = self.server._server.server_port  # type: ignore[attr-defined]
        self.url = f"http://127.0.0.1:{self.port}/beacon/inbox"

    def tearDown(self):
        self.server.stop()
        if self._old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self._old_home
        shutil.rmtree(self._tmp_home, ignore_errors=True)

    def test_signed_unverifiable_is_rejected(self):
        env = {
            "kind": "hello",
            "agent_id": "bcn_fakeagent",
            "nonce": "n0ncevuln001",
            "ts": int(time.time()),
            "sig": "00" * 32,
        }
        status, body = self._post_with_retry(env)
        self.assertEqual(status, 400)
        self.assertEqual(body["results"][0]["accepted"], False)
        self.assertEqual(body["results"][0]["reason"], "signature_unverifiable")
        self.assertIsNone(body["results"][0]["verified"])

    def test_signed_valid_once_then_replay_rejected(self):
        ident = AgentIdentity.generate()
        text = encode_envelope(
            {"kind": "hello", "ts": int(time.time()), "nonce": "noncefixed123"},
            version=2,
            identity=ident,
            include_pubkey=True,
        )
        env = decode_envelopes(text)[0]

        first_status, first_body = self._post_with_retry(env)
        self.assertEqual(first_status, 200)
        self.assertTrue(first_body["results"][0]["accepted"])
        self.assertTrue(first_body["results"][0]["verified"])
        self.assertEqual(first_body["results"][0]["reason"], "ok")

        second_status, second_body = self._post_with_retry(env)
        self.assertEqual(second_status, 400)
        self.assertFalse(second_body["results"][0]["accepted"])
        self.assertEqual(second_body["results"][0]["reason"], "replay_nonce")


if __name__ == "__main__":
    unittest.main()
