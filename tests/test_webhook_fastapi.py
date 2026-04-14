import http.client
import json
import os
import shutil
import time
import unittest
import multiprocessing

from beacon_skill.codec import decode_envelopes, encode_envelope
from beacon_skill.guard import clear_nonce_cache
from beacon_skill.identity import AgentIdentity
from beacon_skill.transports.webhook_fastapi import FastAPIWebhookServer

def run_server(port):
    server = FastAPIWebhookServer(port=port, host="127.0.0.1")
    server.run()

class WebhookFastAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.port = 8403
        cls.server_process = multiprocessing.Process(target=run_server, args=(cls.port,))
        cls.server_process.start()
        time.sleep(2) # Wait for FastAPI to boot

    @classmethod
    def tearDownClass(cls):
        cls.server_process.terminate()
        cls.server_process.join()

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
        clear_nonce_cache()
        self.ident = AgentIdentity.generate()

    def test_fastapi_health(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", "/beacon/health")
        resp = conn.getresponse()
        data = json.loads(resp.read().decode("utf-8"))
        self.assertEqual(resp.status, 200)
        self.assertEqual(data["engine"], "FastAPI")

    def test_fastapi_valid_envelope(self):
        text = encode_envelope(
            {"kind": "hello", "ts": int(time.time()), "nonce": "fastapi_001"},
            version=2, identity=self.ident, include_pubkey=True
        )
        env = decode_envelopes(text)[0]
        s, b = self._post(env)
        self.assertEqual(s, 200)
        self.assertTrue(b["ok"])

    def test_fastapi_replay_rejected(self):
        text = encode_envelope(
            {"kind": "hello", "ts": int(time.time()), "nonce": "fastapi_replay"},
            version=2, identity=self.ident, include_pubkey=True
        )
        env = decode_envelopes(text)[0]
        s1, b1 = self._post(env)
        self.assertEqual(s1, 200)
        
        s2, b2 = self._post(env)
        self.assertEqual(s2, 400) # FastAPI sends 400 for validation failure

if __name__ == "__main__":
    unittest.main()
