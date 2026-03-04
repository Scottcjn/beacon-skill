# Message Security: Replay Protection & Idempotency

This document explains beacon-skill's security mechanisms for protecting against replay attacks and ensuring idempotent message processing in multi-agent meshes.

---

## Why Replay Protection Matters

In multi-agent networks, messages may be:
- **Retried** due to network failures
- **Duplicated** by transport layer retries
- **Re-delivered** by delivery guarantees

Without protection, a malicious or accidental replay could cause:
- Duplicate actions (e.g., sending payment twice)
- State inconsistencies
- Resource exhaustion attacks

---

## Nonce Strategy

### What is a Nonce?

A nonce (number used once) is a unique identifier for each message. Beacon-skill uses **cryptographically random nonces** to ensure each envelope is unique.

### Implementation

```python
# Each outbound envelope gets a fresh nonce
import secrets
nonce = secrets.token_bytes(12).hex()  # 24-character hex string
```

### Nonce Requirements

1. **Uniqueness**: Each message must have a globally unique nonce
2. **Unpredictability**: Use cryptographic random, not counters
3. **Format**: Hex-encoded bytes (recommended: 12+ bytes)

### Code Example: Generating Nonces

```python
import secrets
import time

def create_envelope(kind: str, text: str, agent_id: str, private_key_hex: str) -> dict:
    nonce = secrets.token_bytes(12).hex()
    ts = int(time.time() * 1000)  # milliseconds
    
    envelope = {
        "kind": kind,
        "text": text,
        "agent_id": agent_id,
        "nonce": nonce,
        "ts": ts,
    }
    # ... add signature
    return envelope
```

---

## Timestamp Validation

### Time-Based Window

Beacon-skill rejects messages outside a configurable time window:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `max_age_s` | 300s (5 min) | Maximum age for received messages |
| `max_future_skew_s` | 30s | Allow for minor clock differences |

### Implementation

```python
from beacon_skill.guard import check_envelope_window

def validate_incoming_message(envelope: dict) -> tuple[bool, str]:
    """
    Returns (ok, reason)
    - ok=True, reason="ok" → message is valid
    - ok=False, reason="stale_ts" → message too old
    - ok=False, reason="future_ts" → message from the future (suspicious)
    """
    return check_envelope_window(envelope)
```

### Error Codes

| Code | Meaning |
|------|---------|
| `ok` | Message is valid |
| `missing_nonce` | No nonce provided |
| `missing_ts` | No timestamp provided |
| `stale_ts` | Message older than `max_age_s` |
| `future_ts` | Message timestamp too far in future |
| `replay_nonce` | Nonce already seen (replay attack detected) |

---

## Nonce Cache (Replay Detection)

### How It Works

Beacon-skill maintains an in-memory cache of seen nonces:

```python
# Internal state structure
state = {
    "seen_nonces": {
        "a1b2c3d4e5f6": 1700000000000,  # nonce: timestamp
        "f7e6d5c4b3a2": 1700000001000,
    }
}
```

### Cache Management

1. **Pruning**: Old entries are automatically removed after `max_age_s`
2. **Size Limit**: Maximum `max_nonces` entries (default: 1000)
3. **Persistence**: State is saved to `state.jsonl`

### Configuration

```python
from beacon_skill.guard import check_envelope_window

# Custom validation windows
ok, reason = check_envelope_window(
    envelope,
    max_age_s=600,        # 10 minutes
    max_future_skew_s=60, # 1 minute
    max_nonces=5000,      # larger cache
)
```

---

## Idempotency for Retries

### The Idempotency Problem

When a message delivery fails (network error, timeout), clients typically **retry**. Without idempotency, the retry could cause duplicate actions.

### Idempotency Key Pattern

Use the nonce as an **idempotency key**:

```python
async def deliver_message(envelope: dict) -> bool:
    nonce = envelope["nonce"]
    
    # Check if we've already processed this nonce
    if await was_processed(nonce):
        # Already processed - return success without re-executing
        return True
    
    # Process the message
    result = await execute_action(envelope)
    
    # Mark as processed
    await mark_processed(nonce)
    return result
```

### Example: Idempotent Action Execution

```python
import asyncio
from typing import Set

# In-memory idempotency cache (use Redis for distributed systems)
processed_nonces: Set[str] = set()

async def handle_envelope(envelope: dict) -> dict:
    nonce = envelope.get("nonce", "")
    
    # Idempotency check
    if nonce in processed_nonces:
        return {"status": "already_processed", "nonce": nonce}
    
    # Process the message
    result = await do_something(envelope)
    
    # Mark as processed
    processed_nonces.add(nonce)
    
    return {"status": "processed", "nonce": nonce, "result": result}

# Cleanup old entries periodically
async def cleanup_idempotency_cache():
    while True:
        await asyncio.sleep(3600)  # every hour
        # Remove nonces older than max_age_s
        processed_nonces.clear()
```

---

## Best Practices

### For Agent Developers

1. **Always use unique nonces**: Never reuse, even across restarts
2. **Include timestamps**: Required for window validation
3. **Handle retries idempotently**: Check nonce before executing actions
4. **Log rejected messages**: Helps detect attacks

### For System Operators

1. **Monitor rejection rates**: High `replay_nonce` may indicate an attack
2. **Adjust time windows**: Balance security vs. reliability for your network
3. **Use persistent storage**: For multi-instance deployments, use Redis for nonce cache

### Example: Sending a Message with Retry

```python
import asyncio
import secrets
import time

async def send_with_retry(transport, envelope, max_retries=3):
    for attempt in range(max_retries):
        try:
            result = await transport.send(envelope)
            if result.get("accepted"):
                return result  # Success
        except Exception as e:
            print(f"Attempt {attempt+1} failed: {e}")
        
        # Wait before retry (exponential backoff)
        await asyncio.sleep(2 ** attempt)
    
    raise Exception("Failed after all retries")
```

---

## Summary

| Concept | Purpose | Implementation |
|---------|---------|----------------|
| **Nonce** | Unique message ID | `secrets.token_bytes(12).hex()` |
| **Timestamp** | Message freshness | `ts` field in envelope |
| **Window validation** | Reject old/future messages | `check_envelope_window()` |
| **Nonce cache** | Detect replays | In-memory + state file |
| **Idempotency** | Safe retries | Check nonce before execution |

These mechanisms work together to ensure reliable and secure message delivery in the beacon mesh network.
