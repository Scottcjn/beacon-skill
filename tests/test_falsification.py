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


class WebhookFalsificationTests(unittest.TestCase):
    def _post(self, payload):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        body = json.dumps(payload)
        conn.request("POST", "/beacon/inbox", body=body, headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        status = resp.status
        data = json.loads(resp.read().decode("utf-8"))
        conn.close()
        return status, data

    def setUp(self):
        self._old_home = os.environ.get("HOME")
        self._tmp_home = os.path.join(os.getcwd(), f".tmp_home_falsify_{int(time.time() * 1000)}")
        os.makedirs(self._tmp_home, exist_ok=True)
        os.environ["HOME"] = self._tmp_home
        clear_nonce_cache()
        self.server = WebhookServer(port=0, host="127.0.0.1")
        self.server.start(blocking=False)
        time.sleep(0.2)
        self.port = self.server._server.server_port
        self.ident = AgentIdentity.generate()

    def tearDown(self):
        self.server.stop()
        os.environ["HOME"] = self._old_home or ""
        shutil.rmtree(self._tmp_home, ignore_errors=True)

    def test_t1_replay_rejected(self):
        """T1: Same signed envelope submitted twice must be rejected on second try."""
        text = encode_envelope(
            {"kind": "hello", "ts": int(time.time()), "nonce": "t1_nonce"},
            version=2, identity=self.ident, include_pubkey=True
        )
        env = decode_envelopes(text)[0]
        
        s1, b1 = self._post(env)
        self.assertEqual(s1, 200, "First submission should be accepted")
        
        s2, b2 = self._post(env)
        self.assertEqual(s2, 400, "Replay should be rejected with 400")
        self.assertEqual(b2["results"][0]["reason"], "replay_nonce")

    def test_t2_tamper_rejected(self):
        """T2: Modifying payload after signing must fail signature verification."""
        text = encode_envelope(
            {"kind": "hello", "ts": int(time.time()), "nonce": "t2_nonce", "text": "valid"},
            version=2, identity=self.ident, include_pubkey=True
        )
        env = decode_envelopes(text)[0]
        
        # Tamper: change text content but keep same signature
        env["text"] = "tampered"
        
        s, b = self._post(env)
        self.assertEqual(s, 400)
        self.assertEqual(b["results"][0]["reason"], "signature_invalid")

    def test_t3_stale_rejected(self):
        """T3: Envelope with old timestamp (outside 15m window) must be rejected."""
        stale_ts = int(time.time()) - 1000 # 1000s > 900s limit
        text = encode_envelope(
            {"kind": "hello", "ts": stale_ts, "nonce": "t3_nonce"},
            version=2, identity=self.ident, include_pubkey=True
        )
        env = decode_envelopes(text)[0]
        
        s, b = self._post(env)
        self.assertEqual(s, 400)
        self.assertEqual(b["results"][0]["reason"], "stale_ts")

    def test_t3_future_rejected(self):
        """T3: Envelope with future timestamp must be rejected."""
        future_ts = int(time.time()) + 200 # 200s > 120s limit
        text = encode_envelope(
            {"kind": "hello", "ts": future_ts, "nonce": "t3_future"},
            version=2, identity=self.ident, include_pubkey=True
        )
        env = decode_envelopes(text)[0]
        
        s, b = self._post(env)
        self.assertEqual(s, 400)
        self.assertEqual(b["results"][0]["reason"], "future_ts")

if __name__ == "__main__":
    unittest.main()
