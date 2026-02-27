import unittest
import requests
import time

class TestAtlasPingSecurity(unittest.TestCase):
    def setUp(self):
        self.atlas_url = "https://rustchain.org/beacon/relay/ping"

    def test_unsigned_new_agent_rejection(self):
        """Check if /relay/ping enforces signatures for new registrations."""
        new_agent_id = f"bcn_test_{int(time.time())}"
        payload = {
            "agent_id": new_agent_id,
            "name": "Security-Test-Agent",
            "capabilities": ["security-test"]
        }
        
        resp = requests.post(self.atlas_url, json=payload, timeout=10)
        
        # If it returns 200 or 201, it accepted the registration without a signature.
        if resp.status_code in (200, 201):
            print(f"\n[!] VULNERABILITY: /relay/ping accepted unsigned registration for {new_agent_id} (HTTP {resp.status_code})")
            self.fail(f"Unsigned registration accepted: HTTP {resp.status_code}")
        else:
            print(f"\n[+] PASSED: Server rejected unsigned registration with code {resp.status_code}")

if __name__ == "__main__":
    unittest.main()
