import unittest
import requests
import time
import json
import secrets
from beacon_skill.identity import AgentIdentity

class TestNonceReplay(unittest.TestCase):
    def setUp(self):
        self.atlas_url = "https://rustchain.org/beacon/relay/ping"
        self.identity = AgentIdentity.generate()

    def test_nonce_replay_rejection(self):
        """Check if /relay/ping rejects the same nonce twice."""
        nonce = f"test-nonce-{secrets.token_hex(4)}"
        ts = int(time.time())
        
        # 1. First attempt (Signed)
        payload = {
            "agent_id": self.identity.agent_id,
            "pubkey": self.identity.public_key_hex,
            "nonce": nonce,
            "ts": ts,
            "v": 2
        }
        
        msg = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        payload["sig"] = self.identity.sign_hex(msg)
        
        print(f"[*] Sending first ping with nonce {nonce}...")
        resp1 = requests.post(self.atlas_url, json=payload, timeout=10)
        print(f"[+] Result 1: {resp1.status_code}")
        
        # 2. Replay the exact same payload
        print(f"[*] Replaying same ping...")
        resp2 = requests.post(self.atlas_url, json=payload, timeout=10)
        print(f"[+] Result 2: {resp2.status_code}")
        
        if resp1.status_code in (200, 201) and resp2.status_code == 200:
            print("[!] SECURITY VULNERABILITY: Server accepted replayed nonce!")
            self.fail("Replay attack succeeded")
        else:
            print("[+] Success: Server rejected replayed nonce.")

if __name__ == "__main__":
    unittest.main()
