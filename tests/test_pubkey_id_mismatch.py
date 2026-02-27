import unittest
import requests
import time
import json
from beacon_skill.identity import AgentIdentity

class TestPubkeyIdMismatch(unittest.TestCase):
    def setUp(self):
        self.atlas_url = "https://rustchain.org/beacon/relay/ping"
        self.identity = AgentIdentity.generate()
        self.other_identity = AgentIdentity.generate()

    def test_mismatch_rejection(self):
        """Check if /relay/ping rejects when pubkey doesn't match agent_id."""
        print(f"\n[*] Target ID (Victim): {self.identity.agent_id}")
        print(f"[*] Attacker ID: {self.other_identity.agent_id}")
        
        # Attacker registers with their OWN key but claims VICTIM'S ID
        payload = {
            "agent_id": self.identity.agent_id, 
            "pubkey": self.other_identity.public_key_hex, 
            "nonce": f"mismatch-{int(time.time())}",
            "ts": int(time.time()),
            "v": 2
        }
        
        msg = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        payload["sig"] = self.other_identity.sign_hex(msg) 
        
        resp = requests.post(self.atlas_url, json=payload, timeout=10)
        print(f"[*] Result: {resp.status_code}")
        
        if resp.status_code in (200, 201):
            data = resp.json()
            returned_id = data.get("agent_id")
            print(f"[*] Server accepted registration for ID: {returned_id}")
            
            # If the server returns the Victim's ID, it means the Attacker now controls it
            if returned_id == self.identity.agent_id:
                 print(f"[!] SECURITY VULNERABILITY: Impersonation / ID Hijacking successful!")
                 self.fail("Impersonation vulnerability confirmed")
            else:
                 print(f"[+] Server correctly ignored the claimed ID and derived it from the pubkey: {returned_id}")
        else:
            print("[+] Success: Server rejected the malformed request.")

if __name__ == "__main__":
    unittest.main()
