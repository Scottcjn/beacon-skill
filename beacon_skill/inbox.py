"""Inbound parsing: read, verify, filter, and track inbox entries."""

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .codec import decode_envelopes, verify_envelope
from .storage import _dir, read_state, write_state


KNOWN_KEYS_FILE = "known_keys.json"

# Default key TTL: 30 days in seconds
DEFAULT_KEY_TTL_SECONDS = 30 * 24 * 60 * 60


def _known_keys_path() -> Path:
    return _dir() / KNOWN_KEYS_FILE


def load_known_keys() -> Dict[str, Dict[str, Any]]:
    """Load agent_id -> key metadata mapping from disk.
    
    Returns dict with structure:
    {
        agent_id: {
            "pubkey_hex": str,
            "first_seen": float (timestamp),
            "last_seen": float (timestamp),
            "rotation_count": int,
            "previous_keys": [str] (list of old pubkey_hex values)
        }
    }
    """
    path = _known_keys_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_known_keys(keys: Dict[str, Dict[str, Any]]) -> None:
    """Save known keys with metadata to disk."""
    path = _known_keys_path()
    path.write_text(json.dumps(keys, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_known_keys_simple() -> Dict[str, str]:
    """Load agent_id -> public_key_hex mapping (legacy compatibility).
    
    Only returns current active keys, not expired ones.
    """
    keys = load_known_keys()
    now = time.time()
    result = {}
    for agent_id, meta in keys.items():
        # Check if key is expired
        if is_key_expired(meta, now):
            continue
        result[agent_id] = meta.get("pubkey_hex", "")
    return result


def is_key_expired(key_meta: Dict[str, Any], now: Optional[float] = None) -> bool:
    """Check if a key has expired based on TTL."""
    if now is None:
        now = time.time()
    last_seen = key_meta.get("last_seen", key_meta.get("first_seen", 0))
    ttl = key_meta.get("ttl_seconds", DEFAULT_KEY_TTL_SECONDS)
    return (now - last_seen) > ttl


def trust_key(agent_id: str, pubkey_hex: str, allow_rotate: bool = False) -> bool:
    """Add or update a trusted agent key.
    
    Args:
        agent_id: The agent ID to trust
        pubkey_hex: The public key hex to trust
        allow_rotate: If True, allow rotation from existing key
    
    Returns:
        True if key was added/updated, False if rotation required but not allowed
    """
    keys = load_known_keys()
    now = time.time()
    
    if agent_id in keys:
        existing = keys[agent_id]
        # Check if key is the same
        if existing.get("pubkey_hex") == pubkey_hex:
            # Update last_seen
            existing["last_seen"] = now
            save_known_keys(keys)
            return True
        
        # Different key - check if rotation is allowed
        if not allow_rotate:
            return False
        
        # Store old key in previous_keys
        old_key = existing.get("pubkey_hex", "")
        previous_keys = existing.get("previous_keys", [])
        if old_key and old_key not in previous_keys:
            previous_keys.append(old_key)
        
        # Update with new key
        keys[agent_id] = {
            "pubkey_hex": pubkey_hex,
            "first_seen": existing.get("first_seen", now),
            "last_seen": now,
            "rotation_count": existing.get("rotation_count", 0) + 1,
            "previous_keys": previous_keys,
            "ttl_seconds": existing.get("ttl_seconds", DEFAULT_KEY_TTL_SECONDS),
        }
    else:
        # New key
        keys[agent_id] = {
            "pubkey_hex": pubkey_hex,
            "first_seen": now,
            "last_seen": now,
            "rotation_count": 0,
            "previous_keys": [],
            "ttl_seconds": DEFAULT_KEY_TTL_SECONDS,
        }
    
    save_known_keys(keys)
    return True


def revoke_key(agent_id: str) -> bool:
    """Revoke a trusted agent key.
    
    Args:
        agent_id: The agent ID to revoke
    
    Returns:
        True if key was revoked, False if not found
    """
    keys = load_known_keys()
    if agent_id in keys:
        del keys[agent_id]
        save_known_keys(keys)
        return True
    return False


def rotate_key(agent_id: str, new_pubkey_hex: str, signed_by_old_key: bytes) -> bool:
    """Rotate a key - accept new key signed by the old key.
    
    Args:
        agent_id: The agent ID whose key is being rotated
        new_pubkey_hex: The new public key hex
        signed_by_old_key: Signature from the old private key, signing the new pubkey
    
    Returns:
        True if rotation successful, False otherwise
    """
    from .identity import AgentIdentity
    
    keys = load_known_keys()
    if agent_id not in keys:
        return False
    
    existing = keys[agent_id]
    old_pubkey = existing.get("pubkey_hex", "")
    
    if not old_pubkey:
        return False
    
    # Verify the signature is from the old key signing the new pubkey
    try:
        new_pubkey_bytes = bytes.fromhex(new_pubkey_hex)
        if not AgentIdentity.verify(old_pubkey, signed_by_old_key.hex(), new_pubkey_bytes):
            return False
    except Exception:
        return False
    
    # Perform the rotation
    return trust_key(agent_id, new_pubkey_hex, allow_rotate=True)


def list_keys(show_expired: bool = False) -> Dict[str, Dict[str, Any]]:
    """List all known keys with metadata.
    
    Args:
        show_expired: If True, include expired keys
    
    Returns:
        Dict of agent_id -> key metadata
    """
    keys = load_known_keys()
    if not show_expired:
        now = time.time()
        keys = {k: v for k, v in keys.items() if not is_key_expired(v, now)}
    return keys


def get_key_metadata(agent_id: str) -> Optional[Dict[str, Any]]:
    """Get metadata for a specific agent's key."""
    keys = load_known_keys()
    return keys.get(agent_id)


def _learn_key_from_envelope(env: Dict[str, Any], keys: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Auto-learn pubkey from v2 envelopes (trust on first use).
    
    Supports key rotation if the new key is signed by the old key.
    """
    agent_id = env.get("agent_id", "")
    pubkey = env.get("pubkey", "")
    signature = env.get("sig", "")
    
    if agent_id and pubkey:
        from .identity import agent_id_from_pubkey
        expected = agent_id_from_pubkey(bytes.fromhex(pubkey))
        if expected == agent_id:
            # Check if we already have a key for this agent
            if agent_id in keys:
                existing = keys[agent_id]
                existing_pubkey = existing.get("pubkey_hex", "")
                
                if existing_pubkey == pubkey:
                    # Same key - update last_seen
                    existing["last_seen"] = time.time()
                else:
                    # Different key - check for rotation signature
                    if signature:
                        # Try to verify rotation
                        rotation_ok = rotate_key(agent_id, pubkey, bytes.fromhex(signature))
                        if not rotation_ok:
                            # Rotation failed, ignore this key
                            pass
            else:
                # New key - learn it
                now = time.time()
                keys[agent_id] = {
                    "pubkey_hex": pubkey,
                    "first_seen": now,
                    "last_seen": now,
                    "rotation_count": 0,
                    "previous_keys": [],
                    "ttl_seconds": DEFAULT_KEY_TTL_SECONDS,
                }
    
    return keys


def _read_nonces() -> set:
    """Get set of already-read nonces from state."""
    state = read_state()
    return set(state.get("read_nonces", []))


def _save_read_nonce(nonce: str) -> None:
    """Mark a nonce as read."""
    state = read_state()
    nonces = set(state.get("read_nonces", []))
    nonces.add(nonce)
    # Keep bounded (last 10000 nonces).
    if len(nonces) > 10000:
        nonces = set(list(nonces)[-10000:])
    state["read_nonces"] = sorted(nonces)
    write_state(state)


def read_inbox(
    *,
    kind: Optional[str] = None,
    agent_id: Optional[str] = None,
    since: Optional[float] = None,
    unread_only: bool = False,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Read and filter inbox entries from inbox.jsonl.

    Each entry is enriched with:
      - verified: True/False/None (signature verification result)
      - is_read: bool (whether this nonce was marked read)
    """
    path = _dir() / "inbox.jsonl"
    if not path.exists():
        return []

    # Use legacy format for signature verification (needs simple dict)
    known_keys_simple = load_known_keys_simple()
    known_keys_with_meta = load_known_keys()
    read_nonces = _read_nonces()
    results: List[Dict[str, Any]] = []

    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except Exception:
            continue

        # Extract envelopes from the entry.
        envelopes = entry.get("envelopes", [])
        if not envelopes and entry.get("text"):
            envelopes = decode_envelopes(entry["text"])

        # Process each envelope in the entry.
        for env in envelopes:
            # Auto-learn keys (with full metadata)
            _learn_key_from_envelope(env, known_keys_with_meta)

            # Verify signature (uses simple format)
            verified = verify_envelope(env, known_keys=known_keys_simple)
            nonce = env.get("nonce", "")
            is_read = nonce in read_nonces if nonce else False

            enriched = dict(entry)
            enriched["envelope"] = env
            enriched["verified"] = verified
            enriched["is_read"] = is_read

            # Apply filters.
            if kind and env.get("kind") != kind:
                continue
            if agent_id and env.get("agent_id") != agent_id:
                continue
            if since and entry.get("received_at", 0) < since:
                continue
            if unread_only and is_read:
                continue

            results.append(enriched)

        # If no envelopes, include the raw entry (e.g., plain text UDP).
        if not envelopes:
            enriched = dict(entry)
            enriched["envelope"] = None
            enriched["verified"] = None
            enriched["is_read"] = False

            if kind or agent_id:
                continue  # Can't filter raw entries by kind/agent_id.
            if since and entry.get("received_at", 0) < since:
                continue

            results.append(enriched)

    # Save any newly learned keys (with metadata).
    save_known_keys(known_keys_with_meta)

    if limit:
        results = results[-limit:]

    return results


def mark_read(nonce: str) -> None:
    """Mark an envelope nonce as read."""
    _save_read_nonce(nonce)


def inbox_count(unread_only: bool = False) -> int:
    """Return the count of inbox entries."""
    entries = read_inbox(unread_only=unread_only)
    return len(entries)


def get_entry_by_nonce(nonce: str) -> Optional[Dict[str, Any]]:
    """Find a specific inbox entry by its nonce."""
    entries = read_inbox()
    for entry in entries:
        env = entry.get("envelope")
        if env and env.get("nonce") == nonce:
            return entry
    return None
