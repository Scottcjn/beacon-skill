"""Beacon envelope codec — encode, decode, sign, and verify BEACON v1/v2 envelopes."""

import json
import secrets
from typing import Any, Dict, List, Optional, Tuple

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


BEACON_VERSION = 2
BEACON_HEADER_PREFIX = "[BEACON v"
NONCE_BYTES = 6  # 12 hex chars

# Cap on envelopes decoded from a single text blob. Guards against memory
# exhaustion when fed adversarially large inputs; decode_envelopes silently
# stops after this many.
MAX_ENVELOPES = 100

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

    Decoding stops after MAX_ENVELOPES envelopes (memory-exhaustion guard);
    any further envelopes in the blob are silently ignored.
    """
    if not isinstance(text, str):
        raise TypeError(f"decode_envelopes expects str, got {type(text).__name__}")

    out: List[Dict[str, Any]] = []
    idx = 0
    while True:
        if len(out) >= MAX_ENVELOPES:
            break

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


def _known_key_entry(
    known_keys: Optional[Dict[str, Any]], agent_id: str
) -> Tuple[Optional[str], bool]:
    """Look up ``agent_id`` in ``known_keys`` and return ``(pubkey_hex, revoked)``.

    ``known_keys`` values may be either a bare public-key hex string (legacy
    shape) or the metadata dict stored by ``key_management`` (``pubkey_hex``,
    ``revoked``, ...). Callers such as the CLI UDP listener and the stdlib
    webhook server pass the metadata shape straight from ``load_known_keys()``.
    """
    if not known_keys or not agent_id:
        return None, False
    entry = known_keys.get(agent_id)
    if isinstance(entry, str):
        return entry or None, False
    if isinstance(entry, dict):
        pubkey = entry.get("pubkey_hex")
        return (pubkey if isinstance(pubkey, str) and pubkey else None), bool(entry.get("revoked"))
    return None, False


def _is_revoked(
    known_keys: Optional[Dict[str, Any]],
    agent_ids: Tuple[str, ...],
    pubkey_hex: Optional[str],
) -> bool:
    """Return True if any of ``agent_ids`` or ``pubkey_hex`` is revoked locally.

    Revocation must not depend only on the agent_id an envelope *claims*: an
    envelope can omit agent_id entirely while embedding (and signing with) a
    revoked key. So we check the claimed id, the id derived from the key, and
    any known_keys entry whose stored pubkey matches the key being used.
    """
    if not known_keys:
        return False
    for aid in agent_ids:
        if aid and _known_key_entry(known_keys, aid)[1]:
            return True
    if pubkey_hex:
        needle = pubkey_hex.lower()
        for entry in known_keys.values():
            if (
                isinstance(entry, dict)
                and entry.get("revoked")
                and isinstance(entry.get("pubkey_hex"), str)
                and entry["pubkey_hex"].lower() == needle
            ):
                return True
    return False


def verify_envelope(
    envelope: Dict[str, Any],
    known_keys: Optional[Dict[str, Any]] = None,
) -> Optional[bool]:
    """Verify the Ed25519 signature on a v2 envelope.

    Returns:
      True  — signature valid
      False — signature invalid (tampered, wrong key, malformed key/signature,
              or the signing key is marked revoked in known_keys)
      None  — cannot verify (v1 / unsigned: ``sig`` absent or None; or no
              public key available: ``pubkey`` absent/None/"" and no known key)

    known_keys: dict mapping agent_id -> public_key_hex, or agent_id -> key
    metadata dict as returned by ``key_management.load_known_keys()``.

    Malformed attacker-controlled fields (non-hex or wrong-length pubkey/sig,
    non-string or empty sig, non-string pubkey/agent_id) yield False rather
    than raising, so one bad envelope cannot crash a listener or inbox reader.
    Revocation is checked against the claimed agent_id, the agent_id derived
    from the key actually used, and the key itself, so omitting agent_id
    cannot bypass it.
    """
    if not isinstance(envelope, dict):
        raise TypeError(f"verify_envelope expects dict, got {type(envelope).__name__}")

    sig_hex = envelope.get("sig")
    if sig_hex is None:
        return None  # v1 or unsigned
    if not isinstance(sig_hex, str) or not sig_hex:
        return False  # present but malformed ([], {}, 0, "", ...)

    agent_id = envelope.get("agent_id", "")
    if agent_id is None:
        agent_id = ""
    if not isinstance(agent_id, str):
        return False

    known_pubkey, revoked = _known_key_entry(known_keys, agent_id)
    if revoked:
        # A locally revoked key must never verify, even when the envelope
        # embeds the (still mathematically valid) public key itself.
        return False

    # Try to find the public key: embedded pubkey or known_keys cache.
    # Only None / "" count as "no embedded key"; any other non-string value
    # is malformed rather than silently replaced by the known key.
    embedded = envelope.get("pubkey")
    if embedded is None or embedded == "":
        pubkey_hex = known_pubkey
    elif isinstance(embedded, str):
        pubkey_hex = embedded
    else:
        return False
    if not pubkey_hex:
        return None  # No key available to verify

    try:
        pubkey_bytes = bytes.fromhex(pubkey_hex)
        sig_bytes = bytes.fromhex(sig_hex)
    except ValueError:
        return False
    if len(pubkey_bytes) != 32 or len(sig_bytes) != 64:
        return False

    # Verify that the pubkey matches the claimed agent_id.
    from .identity import agent_id_from_pubkey
    expected_id = agent_id_from_pubkey(pubkey_bytes)
    if agent_id and expected_id != agent_id:
        return False  # agent_id doesn't match pubkey

    if _is_revoked(known_keys, (expected_id,), pubkey_hex):
        return False

    # Reconstruct the signing payload (everything except sig).
    signing_payload = {k: v for k, v in envelope.items() if k not in ("sig", "_beacon_version")}
    try:
        msg = _canonical_json(signing_payload)
        pk = Ed25519PublicKey.from_public_bytes(pubkey_bytes)
        pk.verify(sig_bytes, msg)
        return True
    except Exception:
        return False
