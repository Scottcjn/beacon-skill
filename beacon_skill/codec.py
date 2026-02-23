"""Beacon envelope codec — encode, decode, sign, and verify BEACON v1/v2 envelopes."""

import json
import secrets
from typing import Any, Dict, List, Optional, Tuple

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


BEACON_VERSION = 2
BEACON_HEADER_PREFIX = "[BEACON v"
NONCE_BYTES = 6  # 12 hex chars

# Known envelope kinds — used by modules to set the "kind" field in payloads.
# The codec itself is kind-agnostic; this list serves as a protocol reference.
ENVELOPE_KINDS = {
    # Core protocol
    "heartbeat",
    "accord_offer",
    "accord_accept",
    "accord_reject",
    "atlas_register",
    # BEP-1: Proof-of-Thought
    "thought_proof",
    "thought_challenge",
    "thought_reveal",
    # BEP-2: External Agent Relay
    "relay_register",
    "relay_heartbeat",
    # BEP-4: Memory Markets
    "market_listing",
    "market_purchase",
    "market_rental",
    "amnesia_request",
    "amnesia_vote",
    # BEP-5: Hybrid Districts
    "hybrid_sponsor",
    "hybrid_cosign",
    "hybrid_revoke",
}


def generate_nonce() -> str:
    """Generate a 12-char hex nonce for replay protection."""
    return secrets.token_hex(NONCE_BYTES)


def _canonical_json(payload: Dict[str, Any]) -> bytes:
    """Canonical JSON for signing: sorted keys, compact separators."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def encode_envelope(
    payload: Dict[str, Any],
    version: int = BEACON_VERSION,
    identity: Any = None,
    include_pubkey: bool = False,
) -> str:
    """Encode a machine-readable Beacon envelope.

    v1 format:
      [BEACON v1]
      {"k":"v",...}

    v2 format (signed):
      [BEACON v2]
      {"agent_id":"bcn_...","nonce":"...","sig":"...","pubkey":"...(optional)",...}

    If identity is provided and version >= 2, the envelope is automatically signed.
    """
    if version >= 2 and identity is not None:
        # Inject identity fields before signing.
        payload = dict(payload)
        payload["v"] = version
        payload["agent_id"] = identity.agent_id
        if "nonce" not in payload:
            payload["nonce"] = generate_nonce()
        if include_pubkey:
            payload["pubkey"] = identity.public_key_hex

        # Sign the payload WITHOUT the sig field.
        signing_payload = {k: v for k, v in payload.items() if k != "sig"}
        msg = _canonical_json(signing_payload)
        payload["sig"] = identity.sign_hex(msg)

    body = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return f"[BEACON v{version}]\n{body}"


def _find_balanced_json(s: str, start: int) -> Optional[Tuple[int, int]]:
    """Return (start,end) indices of a balanced JSON object starting at/after start."""
    i = s.find("{", start)
    if i < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    for j in range(i, len(s)):
        ch = s[j]
        if in_str:
            if esc:
                esc = False
                continue
            if ch == "\\":
                esc = True
                continue
            if ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return (i, j + 1)
    return None


def _parse_version(header_line: str) -> int:
    """Extract version number from a '[BEACON vN]' header line."""
    try:
        s = header_line.strip()
        # Find the version part between 'v' and ']'
        v_start = s.index("v") + 1
        v_end = s.index("]")
        return int(s[v_start:v_end])
    except Exception:
        return 1


def decode_envelopes(text: str) -> List[Dict[str, Any]]:
    """Extract all Beacon envelopes (v1 and v2) found in a text blob.

    Each returned dict includes the parsed JSON body.
    v2 envelopes include agent_id, nonce, sig fields.
    """
    if not isinstance(text, str):
        raise TypeError(f"decode_envelopes expects str, got {type(text).__name__}")

    out: List[Dict[str, Any]] = []
    idx = 0
    while True:
        h = text.find(BEACON_HEADER_PREFIX, idx)
        if h < 0:
            break
        # Find end of header line.
        nl = text.find("\n", h)
        if nl < 0:
            break
        header_line = text[h:nl]
        version = _parse_version(header_line)
        # Look for a JSON object after the header.
        span = _find_balanced_json(text, nl + 1)
        if not span:
            idx = nl + 1
            continue
        j0, j1 = span
        blob = text[j0:j1]
        try:
            obj = json.loads(blob)
            obj.setdefault("_beacon_version", version)
            out.append(obj)
        except Exception:
            pass
        idx = j1
    return out


def verify_envelope(
    envelope: Dict[str, Any],
    known_keys: Optional[Dict[str, str]] = None,
) -> Optional[bool]:
    """Verify the Ed25519 signature on a v2 envelope.

    Returns:
      True  — signature valid
      False — signature invalid (tampered or wrong key)
      None  — cannot verify (v1, no sig, no known key)

    known_keys: dict mapping agent_id -> public_key_hex
    """
    if not isinstance(envelope, dict):
        raise TypeError(f"verify_envelope expects dict, got {type(envelope).__name__}")

    sig_hex = envelope.get("sig")
    if not sig_hex:
        return None  # v1 or unsigned

    agent_id = envelope.get("agent_id", "")

    # Try to find the public key: embedded pubkey or known_keys cache.
    pubkey_hex = envelope.get("pubkey")
    if not pubkey_hex and known_keys:
        pubkey_hex = known_keys.get(agent_id)
    if not pubkey_hex:
        return None  # No key available to verify

    # Verify that the pubkey matches the claimed agent_id.
    from .identity import agent_id_from_pubkey
    expected_id = agent_id_from_pubkey(bytes.fromhex(pubkey_hex))
    if agent_id and expected_id != agent_id:
        return False  # agent_id doesn't match pubkey

    # Reconstruct the signing payload (everything except sig).
    signing_payload = {k: v for k, v in envelope.items() if k not in ("sig", "_beacon_version")}
    msg = _canonical_json(signing_payload)

    try:
        pk = Ed25519PublicKey.from_public_bytes(bytes.fromhex(pubkey_hex))
        pk.verify(bytes.fromhex(sig_hex), msg)
        return True
    except Exception:
        return False
