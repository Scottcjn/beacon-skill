import unittest
import requests
import time
import json
from beacon_skill.identity import AgentIdentity

class TestStaleTimestamp(unittest.TestCase):
    def setUp(self):
        self.atlas_url = "https://rustchain.org/beacon/relay/ping"
        self.identity = AgentIdentity.generate()

    def test_stale_timestamp_rejection(self):
        """Check if /relay/ping rejects stale timestamps (1 hour old)."""
        stale_ts = int(time.time()) - 3600 # 1 hour ago
        payload = {
            "agent_id": self.identity.agent_id,
            "pubkey": self.identity.public_key_hex,
            "nonce": f"stale-{int(time.time())}",
            "ts": stale_ts,
            "v": 2
        }
        
        msg = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        payload["sig"] = self.identity.sign_hex(msg)
        
        resp = requests.post(self.atlas_url, json=payload, timeout=10)
        print(f"\n[*] Result for stale TS (1h ago): {resp.status_code}")
        
        if resp.status_code == 200:
            print("[!] VULNERABILITY: Server accepted stale timestamp (1 hour old)!")
            self.fail("Stale timestamp accepted")
        else:
            print("[+] Success: Server rejected stale timestamp.")

if __name__ == "__main__":
    unittest.main()
