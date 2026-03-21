"""Beacon Replay Protection Example.

This example demonstrates how to protect Beacon message handlers from
replay attacks by validating timestamp freshness and checking nonce
uniqueness. It covers:
1. Timestamp TTL (Time-To-Live) window validation
2. In-memory duplicate nonce caching
3. Simulating replayed payload failures
4. Test vectors
"""
import time
import json
from typing import Dict, Any

from beacon_skill.identity import AgentIdentity

# Configuration for replay protection
TIMESTAMP_TTL_SECONDS = 300  # 5 minutes

# In production this could be Redis or Memcached
class NonceCache:
    def __init__(self):
        # Maps `agent_id:nonce` to `timestamp`
        self._seen_nonces: Dict[str, int] = {}
        
    def check_and_add(self, agent_id: str, nonce: str, current_time: int) -> bool:
        """Returns True if successful, False if nonce was already seen."""
        key = f"{agent_id}:{nonce}"
        
        # Cleanup expired nonces implicitly on check
        self._cleanup(current_time)
        
        if key in self._seen_nonces:
            return False
            
        self._seen_nonces[key] = current_time
        return True
        
    def _cleanup(self, current_time: int):
        """Remove nonces older than TTL window"""
        expired = [k for k, ts in self._seen_nonces.items() 
                  if current_time - ts > TIMESTAMP_TTL_SECONDS]
        for k in expired:
            del self._seen_nonces[k]


class SecureMessageHandler:
    def __init__(self):
        self.nonce_cache = NonceCache()
        # Holds verified payloads
        self.processed = []
        
    def handle_message(self, pubkey_hex: str, raw_payload: bytes, signature_hex: str) -> Dict[str, Any]:
        """Validate an incoming message for authenticity and replay resilience."""
        # 1. Authenticate signature
        if not AgentIdentity.verify(pubkey_hex, signature_hex, raw_payload):
            return {"error": "Invalid signature", "status": 401}
            
        # Parse payload
        try:
            payload = json.loads(raw_payload.decode("utf-8"))
        except json.JSONDecodeError:
            return {"error": "Invalid JSON format", "status": 400}

        # 2. Check for required replay protection fields
        ts = payload.get("ts")
        nonce = payload.get("nonce")
        sender = payload.get("agent_id")
        
        if not ts or not nonce or not sender:
            return {"error": "Missing replay protection fields (ts, nonce, agent_id)", "status": 400}
            
        # 3. Validate Timestamp (TTL validation)
        now = int(time.time())
        age = now - ts
        
        # Reject future timestamps (allowing 5s clock skew)
        if age < -5:
            return {"error": "Timestamp in the future", "status": 400}
            
        if age > TIMESTAMP_TTL_SECONDS:
            return {"error": f"Message expired. Age: {age}s, TTL: {TIMESTAMP_TTL_SECONDS}s", "status": 401}
            
        # 4. Check Nonce (duplicate prevention)
        if not self.nonce_cache.check_and_add(sender, nonce, now):
            return {"error": "Replayed message (duplicate nonce)", "status": 401}
            
        # 5. Message successfully verified
        self.processed.append(payload)
        return {"ok": True, "message": "Successfully processed"}

def run_test_vectors():
    print("--- Running Replay Protection Test Vectors ---")
    identity = AgentIdentity.generate()
    handler = SecureMessageHandler()
    now_ts = int(time.time())
    
    # helper for creating test payloads
    def build_test(ts_val, nonce_val):
        data = {
            "agent_id": identity.agent_id,
            "data": "hello",
            "ts": ts_val,
            "nonce": nonce_val
        }
        # In production use stable canonical json
        payload_bytes = json.dumps(data, sort_keys=True, separators=(',', ':')).encode("utf-8")
        sig = identity.sign_hex(payload_bytes)
        return handler.handle_message(identity.public_key_hex, payload_bytes, sig)

    # Test 1: Valid payload
    print("Test 1: Valid fresh payload")
    res1 = build_test(now_ts, "nonce-001")
    print(res1)
    assert res1.get("ok") is True

    # Test 2: Replayed nonce (same timestamp)
    print("\nTest 2: Replayed transaction (duplicate nonce)")
    res2 = build_test(now_ts, "nonce-001")
    print(res2)
    assert res2.get("error") == "Replayed message (duplicate nonce)"

    # Test 3: Stale timestamp (expired TTL)
    print("\nTest 3: Stale timestamp (> TTL)")
    res3 = build_test(now_ts - 400, "nonce-003") # 400s > 300s TTL
    print(res3)
    assert "Message expired" in res3.get("error")

    # Test 4: Future timestamp (clock skew attack)
    print("\nTest 4: Future timestamp")
    res4 = build_test(now_ts + 60, "nonce-004") 
    print(res4)
    assert res4.get("error") == "Timestamp in the future"

    # Test 5: Missing fields
    print("\nTest 5: Missing nonce field")
    data_missing = {"agent_id": identity.agent_id, "ts": now_ts}
    payload_missing = json.dumps(data_missing).encode("utf-8")
    res5 = handler.handle_message(identity.public_key_hex, payload_missing, identity.sign_hex(payload_missing))
    print(res5)
    assert "Missing replay protection fields" in res5.get("error")

    print("\nAll test vectors passed successfully!")

if __name__ == "__main__":
    run_test_vectors()
