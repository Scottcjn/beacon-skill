"""Lambda Lang codec for Beacon — compact encoding for bandwidth-constrained transports.

Lambda Lang compresses agent messages 5-8x using semantic atoms.
Ideal for UDP broadcast and low-bandwidth scenarios.

Protocol: https://github.com/voidborne-agent/lambda-lang
"""

import json
import re
from typing import Any, Dict, List, Optional, Tuple

# Lambda Lang atom mappings (core subset for Beacon)
# Full spec: https://github.com/voidborne-agent/lambda-lang/blob/main/atoms.json

LAMBDA_ATOMS = {
    # Message types
    "?": "query",
    "!": "statement",
    "~": "request",
    ">": "response",
    
    # Core verbs
    "co": "collaborate",
    "cn": "connect",
    "rq": "request",
    "of": "offer",
    "ac": "accept",
    "rj": "reject",
    "ak": "acknowledge",
    "ch": "challenge",
    "rv": "reveal",
    "rg": "register",
    "py": "pay",
    "sn": "send",
    "rc": "receive",
    "vf": "verify",
    "sg": "sign",
    "tr": "transfer",
    "br": "broadcast",
    "ls": "listen",
    "ds": "discover",
    "pd": "provide",
    "hl": "hello",
    "by": "goodbye",
    "hb": "heartbeat",
    "md": "mayday",
    
    # Beacon-specific atoms
    "bcn": "beacon",
    "env": "envelope",
    "pld": "payload",
    "nce": "nonce",
    "sig": "signature",
    "pbk": "pubkey",
    "aid": "agent_id",
    "acc": "accord",
    "atl": "atlas",
    "rtc": "rustchain",
    "bty": "bounty",
    "wnt": "want",
    "lik": "like",
    
    # Domains (prefixed)
    "c:id": "identity",
    "c:pr": "protocol",
    "c:nw": "network",
    "c:sc": "security",
    "v:tr": "trust",
    "v:au": "autonomy",
    "v:co": "consciousness",
    "s:ag": "agent",
    "s:nd": "node",
    "s:tx": "transaction",
    "e:al": "alive",
    "e:dd": "dead",
    "e:dg": "degraded",
}

# Reverse mapping for encoding
ENGLISH_TO_LAMBDA = {v: k for k, v in LAMBDA_ATOMS.items()}

# Beacon envelope kind to Lambda atom
KIND_TO_LAMBDA = {
    "hello": "hl",
    "heartbeat": "hb",
    "mayday": "md",
    "accord_offer": "acc/of",
    "accord_accept": "acc/ac",
    "accord_reject": "acc/rj",
    "atlas_register": "atl/rg",
    "bounty": "bty",
    "want": "wnt",
    "like": "lik",
    "thought_proof": "tp",
    "thought_challenge": "tp/ch",
    "thought_reveal": "tp/rv",
    "relay_register": "rl/rg",
    "relay_heartbeat": "rl/hb",
}

LAMBDA_TO_KIND = {v: k for k, v in KIND_TO_LAMBDA.items()}


def encode_lambda(payload: Dict[str, Any], compact: bool = True) -> str:
    """Encode a Beacon payload to Lambda Lang format.
    
    Args:
        payload: Beacon envelope payload dict
        compact: If True, use minimal whitespace
        
    Returns:
        Lambda-encoded string like "!bcn/hb aid:bcn_abc123 e:al"
    """
    parts = []
    
    # Message type prefix based on kind
    kind = payload.get("kind", "")
    if kind in KIND_TO_LAMBDA:
        # Statement prefix for most beacon messages
        parts.append(f"!{KIND_TO_LAMBDA[kind]}")
    else:
        parts.append(f"!bcn/{kind[:3]}")
    
    # Agent ID (abbreviated)
    if "agent_id" in payload:
        aid = payload["agent_id"]
        # Keep bcn_ prefix + first 8 chars of hash
        if aid.startswith("bcn_"):
            parts.append(f"aid:{aid[:12]}")
        else:
            parts.append(f"aid:{aid}")
    
    # Text content
    if "text" in payload:
        text = payload["text"]
        # Compress common phrases
        text = _compress_text(text)
        parts.append(f'"{text}"')
    
    # Status (for heartbeat)
    if "status" in payload:
        status_map = {"healthy": "e:al", "degraded": "e:dg", "dead": "e:dd"}
        parts.append(status_map.get(payload["status"], payload["status"]))
    
    # Nonce (abbreviated)
    if "nonce" in payload:
        parts.append(f"n:{payload['nonce'][:6]}")
    
    # Always use space separator for reliable decoding
    return " ".join(parts)


def decode_lambda(lambda_str: str) -> Dict[str, Any]:
    """Decode a Lambda Lang string back to Beacon payload.
    
    Args:
        lambda_str: Lambda-encoded string
        
    Returns:
        Reconstructed payload dict
    """
    payload = {}
    
    # Extract message type
    if lambda_str.startswith("!"):
        # Statement - find the kind
        match = re.match(r"!(\w+(?:/\w+)?)", lambda_str)
        if match:
            atom = match.group(1)
            if atom in LAMBDA_TO_KIND:
                payload["kind"] = LAMBDA_TO_KIND[atom]
            elif atom.startswith("bcn/"):
                payload["kind"] = atom[4:]
    elif lambda_str.startswith("?"):
        payload["_query"] = True
    elif lambda_str.startswith("~"):
        payload["_request"] = True
    
    # Extract agent_id
    aid_match = re.search(r"aid:(\S+)", lambda_str)
    if aid_match:
        aid = aid_match.group(1)
        # Expand abbreviated agent_id if needed
        if not aid.startswith("bcn_"):
            aid = f"bcn_{aid}"
        payload["agent_id"] = aid
    
    # Extract quoted text
    text_match = re.search(r'"([^"]*)"', lambda_str)
    if text_match:
        payload["text"] = _expand_text(text_match.group(1))
    
    # Extract status
    for status_atom, status_name in [("e:al", "healthy"), ("e:dg", "degraded"), ("e:dd", "dead")]:
        if status_atom in lambda_str:
            payload["status"] = status_name
            break
    
    # Extract nonce
    nonce_match = re.search(r"n:([a-f0-9]+)", lambda_str)
    if nonce_match:
        payload["nonce"] = nonce_match.group(1)
    
    return payload


def _compress_text(text: str) -> str:
    """Compress common phrases in text content."""
    compressions = [
        ("looking for", "lf"),
        ("want to", "w2"),
        ("collaborate", "collab"),
        ("interested in", "int"),
        ("agent", "ag"),
        ("beacon", "bcn"),
        ("protocol", "proto"),
    ]
    result = text.lower()
    for phrase, abbrev in compressions:
        result = result.replace(phrase, abbrev)
    return result


def _expand_text(text: str) -> str:
    """Expand compressed phrases back to full text."""
    expansions = [
        ("lf", "looking for"),
        ("w2", "want to"),
        ("collab", "collaborate"),
        ("int", "interested in"),
        ("ag", "agent"),
        ("bcn", "beacon"),
        ("proto", "protocol"),
    ]
    result = text
    for abbrev, phrase in expansions:
        # Only expand if it's a word boundary
        result = re.sub(rf"\b{abbrev}\b", phrase, result)
    return result


def wrap_lambda_envelope(
    lambda_str: str,
    agent_id: str,
    signature: Optional[str] = None,
) -> str:
    """Wrap a Lambda-encoded message in Beacon envelope format.
    
    [BEACON v2 lambda]
    !hb aid:bcn_abc123 e:al
    sig:abcd1234
    [/BEACON]
    """
    lines = [f"[BEACON v2 lambda]", lambda_str]
    if signature:
        lines.append(f"sig:{signature[:16]}")
    lines.append("[/BEACON]")
    return "\n".join(lines)


def unwrap_lambda_envelope(text: str) -> Optional[Tuple[str, Dict[str, Any]]]:
    """Extract Lambda message from a Beacon envelope.
    
    Returns:
        Tuple of (lambda_string, metadata) or None if not found
    """
    match = re.search(
        r"\[BEACON v2 lambda\]\s*\n(.+?)\n(?:sig:(\w+)\n)?\[/BEACON\]",
        text,
        re.DOTALL
    )
    if not match:
        return None
    
    lambda_str = match.group(1).strip()
    metadata = {}
    if match.group(2):
        metadata["sig"] = match.group(2)
    
    return (lambda_str, metadata)


# Compression ratio estimation
def estimate_compression(payload: Dict[str, Any]) -> float:
    """Estimate compression ratio for a payload.
    
    Returns:
        Ratio of original JSON size to Lambda size
    """
    import json
    original = len(json.dumps(payload))
    compressed = len(encode_lambda(payload))
    return original / compressed if compressed > 0 else 1.0
