"""Key management: TOFU trust, revocation, rotation, and TTL-based expiration."""

import json
import time
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime

from .storage import _dir


KNOWN_KEYS_FILE = "known_keys.json"

# Default TTL: 30 days in seconds
DEFAULT_KEY_TTL = int(os.environ.get("BEACON_KEY_TTL", 30 * 24 * 60 * 60))


def _known_keys_path() -> Path:
    return _dir() / KNOWN_KEYS_FILE


def load_known_keys() -> Dict[str, Dict[str, Any]]:
    """Load agent_id -> key_metadata mapping from disk.

    Key metadata format:
    {
        "pubkey_hex": str,
        "first_seen": float (timestamp),
        "last_seen": float (timestamp),
        "rotation_count": int,
        "previous_key": Optional[str] (hex),
        "revoked": bool,
        "revoked_at": Optional[float],
        "revoked_reason": Optional[str],
    }
    """
    path = _known_keys_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        # Migrate old format (agent_id -> pubkey_hex) to new format
        migrated = {}
        for agent_id, value in data.items():
            if isinstance(value, str):
                # Old format: just pubkey_hex string
                migrated[agent_id] = {
                    "pubkey_hex": value,
                    "first_seen": time.time(),
                    "last_seen": time.time(),
                    "rotation_count": 0,
                    "previous_key": None,
                    "revoked": False,
                    "revoked_at": None,
                    "revoked_reason": None,
                }
            else:
                migrated[agent_id] = value
        return migrated
    except Exception:
        return {}


def save_known_keys(keys: Dict[str, Dict[str, Any]]) -> None:
    """Save known keys to disk."""
    path = _known_keys_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(keys, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def trust_key(agent_id: str, pubkey_hex: str) -> None:
    """Add or update a trusted agent key."""
    keys = load_known_keys()
    now = time.time()

    if agent_id in keys:
        # Update existing key
        keys[agent_id]["last_seen"] = now
    else:
        # New key
        keys[agent_id] = {
            "pubkey_hex": pubkey_hex,
            "first_seen": now,
            "last_seen": now,
            "rotation_count": 0,
            "previous_key": None,
            "revoked": False,
            "revoked_at": None,
            "revoked_reason": None,
        }

    save_known_keys(keys)


def revoke_key(agent_id: str, reason: Optional[str] = None) -> bool:
    """Revoke a known key.

    Returns True if key was found and revoked, False if not found.
    """
    keys = load_known_keys()

    if agent_id not in keys:
        return False

    keys[agent_id]["revoked"] = True
    keys[agent_id]["revoked_at"] = time.time()
    keys[agent_id]["revoked_reason"] = reason or "Manual revocation"

    save_known_keys(keys)
    return True


def rotate_key(
    agent_id: str,
    new_pubkey_hex: str,
    signature_hex: str,
) -> Tuple[bool, str]:
    """Rotate a key with signature verification.

    The signature must be the new public key signed by the old private key.

    Returns (success, message).
    """
    from .identity import AgentIdentity

    keys = load_known_keys()

    if agent_id not in keys:
        return False, f"Agent {agent_id} not found in known keys"

    old_key = keys[agent_id]

    if old_key.get("revoked"):
        return False, f"Agent {agent_id} key is revoked"

    # Verify signature: new_pubkey signed by old_privkey
    try:
        old_pubkey_hex = old_key["pubkey_hex"]

        # Verify using the old public key
        if not AgentIdentity.verify(old_pubkey_hex, signature_hex, bytes.fromhex(new_pubkey_hex)):
            return False, "Invalid signature: rotation not authorized by old key"

    except Exception as e:
        return False, f"Signature verification failed: {e}"

    # Perform rotation
    now = time.time()
    keys[agent_id] = {
        "pubkey_hex": new_pubkey_hex,
        "first_seen": old_key.get("first_seen", now),
        "last_seen": now,
        "rotation_count": old_key.get("rotation_count", 0) + 1,
        "previous_key": old_pubkey_hex,
        "revoked": False,
        "revoked_at": None,
        "revoked_reason": None,
    }

    save_known_keys(keys)
    return True, f"Key rotated successfully (rotation #{keys[agent_id]['rotation_count']})"


def is_key_expired(agent_id: str, ttl: Optional[int] = None) -> bool:
    """Check if a key has expired based on TTL.

    TTL is measured from last_seen timestamp.
    """
    keys = load_known_keys()

    if agent_id not in keys:
        return True  # Unknown key is considered expired

    key = keys[agent_id]

    if key.get("revoked"):
        return True  # Revoked keys are expired

    ttl = ttl or DEFAULT_KEY_TTL
    last_seen = key.get("last_seen", 0)

    return (time.time() - last_seen) > ttl


def update_last_seen(agent_id: str) -> None:
    """Update the last_seen timestamp for a key."""
    keys = load_known_keys()

    if agent_id in keys:
        keys[agent_id]["last_seen"] = time.time()
        save_known_keys(keys)


def list_keys(
    include_revoked: bool = False,
    include_expired: bool = True,
    ttl: Optional[int] = None
) -> List[Dict[str, Any]]:
    """List all known keys with metadata.

    Returns list of key info dicts with:
    - agent_id
    - pubkey_hex
    - first_seen (ISO format)
    - last_seen (ISO format)
    - rotation_count
    - is_revoked
    - is_expired
    - age_days
    """
    keys = load_known_keys()
    results = []

    for agent_id, key in keys.items():
        if not include_revoked and key.get("revoked"):
            continue

        is_expired = is_key_expired(agent_id, ttl)
        if not include_expired and is_expired:
            continue

        first_seen = key.get("first_seen", 0)
        last_seen = key.get("last_seen", 0)

        results.append({
            "agent_id": agent_id,
            "pubkey_hex": key.get("pubkey_hex", ""),
            "first_seen": datetime.fromtimestamp(first_seen).isoformat() if first_seen else None,
            "last_seen": datetime.fromtimestamp(last_seen).isoformat() if last_seen else None,
            "rotation_count": key.get("rotation_count", 0),
            "is_revoked": key.get("revoked", False),
            "revoked_reason": key.get("revoked_reason"),
            "is_expired": is_expired,
            "age_days": int((time.time() - first_seen) / 86400) if first_seen else 0,
        })

    return results


def get_key_info(agent_id: str) -> Optional[Dict[str, Any]]:
    """Get detailed info about a specific key."""
    keys = load_known_keys()

    if agent_id not in keys:
        return None

    key = keys[agent_id]
    first_seen = key.get("first_seen", 0)
    last_seen = key.get("last_seen", 0)

    return {
        "agent_id": agent_id,
        "pubkey_hex": key.get("pubkey_hex", ""),
        "first_seen": datetime.fromtimestamp(first_seen).isoformat() if first_seen else None,
        "last_seen": datetime.fromtimestamp(last_seen).isoformat() if last_seen else None,
        "rotation_count": key.get("rotation_count", 0),
        "previous_key": key.get("previous_key"),
        "is_revoked": key.get("revoked", False),
        "revoked_at": datetime.fromtimestamp(key["revoked_at"]).isoformat() if key.get("revoked_at") else None,
        "revoked_reason": key.get("revoked_reason"),
        "is_expired": is_key_expired(agent_id),
        "age_days": int((time.time() - first_seen) / 86400) if first_seen else 0,
    }


def cleanup_expired_keys(ttl: Optional[int] = None, dry_run: bool = True) -> List[str]:
    """Remove expired keys from the known keys store.

    Returns list of removed agent_ids.
    """
    keys = load_known_keys()
    removed = []

    for agent_id in list(keys.keys()):
        if is_key_expired(agent_id, ttl):
            removed.append(agent_id)
            if not dry_run:
                del keys[agent_id]

    if not dry_run and removed:
        save_known_keys(keys)

    return removed
