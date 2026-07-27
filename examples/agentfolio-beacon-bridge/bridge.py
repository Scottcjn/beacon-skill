# SPDX-License-Identifier: MIT
"""AgentFolio + Beacon Dual-Layer Trust Bridge (reference implementation).

Bidirectional bridge connecting Beacon (hardware-anchored provenance) with
AgentFolio (marketplace reputation on Solana SATP). Provides:

- Cross-resolution: Beacon bcn_ ID <-> AgentFolio agent ID
- Cross-identity verification: a **mutual signed challenge** that actually
  proves both identities are controlled by the same operator
- Composite trust scoring: bounded, non-finite-safe blend of both layers
- Signed trust card: an Ed25519 attestation over the *entire* card
- W3C DID export with valid `publicKeyMultibase` (multibase base58btc)

Bounty: https://github.com/Scottcjn/rustchain-bounties/issues/2890
Tracking / acceptance criteria: https://github.com/Scottcjn/beacon-skill/issues/864

Security model (what a previous revision got wrong, and how this fixes it):

* Linkage is proven by cryptography, never by coincidence. `verify_cross_identity`
  requires a signature from **both** the Beacon key and the AgentFolio key over a
  single challenge that binds the two IDs together. Merely existing on both
  platforms, sharing a substring of a name, or dropping a local JSON file proves
  nothing and grants no trust.
* The Beacon public key is bound to its `bcn_` ID by construction
  (`agent_id_from_pubkey`), so the binding is checked exactly, not by name.
* Trust scores are validated with `math.isfinite`; a NaN/inf/None can never be
  laundered into a forged `1.0` by the final clamp.
* Composite components are in `[0, 1]` and the weights (which sum to 1.0) do the
  scaling, so the `verified` tier (>= 0.8) is actually reachable.
* All local state is written atomically (tmpfile + `os.replace`) into
  `~/.beacon/bridge/`, never into `~/.beacon/identity/`, and is treated as a
  record/cache only, never as proof.
* Infrastructure failures (5xx / DNS / timeout) raise `BridgeUnavailable` and are
  never silently collapsed into "identity not found"; verification fails closed.
"""

import json
import math
import os
import secrets
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

import requests
from requests.exceptions import RequestException

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

# The bridge reuses beacon-skill's real identity primitives so the crypto is
# genuine (not a mock): agent IDs are the SHA256-derived `bcn_` IDs, and
# signatures are verified with the same Ed25519 code the rest of the skill uses.
from beacon_skill.identity import (
    AGENT_ID_PREFIX,
    AgentIdentity,
    agent_id_from_pubkey,
)

# ── Default Configuration ────────────────────────────────────────────────────

DEFAULT_BEACON_ATLAS_URL = "https://rustchain.org/beacon"
DEFAULT_AGENTFOLIO_API_URL = "https://agentfolio.bot/api"

# Weights sum to 1.0. Each component below is normalised to [0, 1], so the
# maximum achievable composite is exactly 1.0 and every tier is reachable.
DEFAULT_TRUST_WEIGHTS = {
    "beacon_fidelity": 0.40,
    "agentfolio_reputation": 0.35,
    "cross_verification": 0.15,
    "endorsement_bonus": 0.10,
}

# A cross-link proof is short-lived: it binds a fresh nonce + timestamp so a
# captured proof cannot be replayed indefinitely.
DEFAULT_PROOF_TTL_SECONDS = 300

LINK_CHALLENGE_TYPE = "beacon-agentfolio-link"
LINK_CHALLENGE_VERSION = 1

TRUST_LEVELS = [
    (0.8, "verified"),
    (0.6, "trusted"),
    (0.3, "basic"),
    (0.0, "unverified"),
]

# Multicodec prefix for an Ed25519 public key (0xed as an unsigned varint).
_MULTICODEC_ED25519_PUB = b"\xed\x01"
_BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


class BridgeUnavailable(Exception):
    """A backing service was unreachable or returned a server error.

    This is deliberately distinct from "identity not found" (which is a ``None``
    return). Trust decisions fail closed on this exception rather than treating
    an outage as evidence that an identity does not exist.
    """


@runtime_checkable
class Signer(Protocol):
    """Anything that can produce an Ed25519 signature and expose its pubkey.

    ``beacon_skill.identity.AgentIdentity`` satisfies this, as does
    :class:`Ed25519Signer` (used to model the AgentFolio/Solana side).
    """

    @property
    def public_key_hex(self) -> str: ...

    def sign(self, data: bytes) -> bytes: ...


class Ed25519Signer:
    """Minimal Ed25519 signer for the AgentFolio (Solana SATP) side of a link.

    AgentFolio identities are Ed25519 keypairs just like Beacon's, but they are
    *not* `bcn_` IDs, so they use this plain signer rather than ``AgentIdentity``.
    """

    def __init__(self, private_key: Ed25519PrivateKey):
        self._sk = private_key
        self._pk_bytes = private_key.public_key().public_bytes(
            Encoding.Raw, PublicFormat.Raw
        )

    @classmethod
    def generate(cls) -> "Ed25519Signer":
        return cls(Ed25519PrivateKey.generate())

    @classmethod
    def from_private_key_hex(cls, hex_key: str) -> "Ed25519Signer":
        return cls(Ed25519PrivateKey.from_private_bytes(bytes.fromhex(hex_key)))

    @property
    def public_key_hex(self) -> str:
        return self._pk_bytes.hex()

    @property
    def private_key_hex(self) -> str:
        return self._sk.private_bytes(
            Encoding.Raw, PrivateFormat.Raw, NoEncryption()
        ).hex()

    def sign(self, data: bytes) -> bytes:
        return self._sk.sign(data)

    def sign_hex(self, data: bytes) -> str:
        return self.sign(data).hex()


# ── Small utilities ──────────────────────────────────────────────────────────

def _trust_level(score: float) -> str:
    """Map a 0-1 composite score to a trust level label."""
    if not math.isfinite(score):
        return "unverified"
    for threshold, label in TRUST_LEVELS:
        if score >= threshold:
            return label
    return "unverified"


def _finite(value: Any, default: float = 0.0) -> float:
    """Coerce *value* to a finite float, or return *default*.

    ``None``, non-numeric input, and non-finite floats (``NaN``/``inf``) all map
    to *default*. This is the single choke point that stops a forged or garbage
    score from ever reaching the composite formula.
    """
    if isinstance(value, bool):  # bool is an int subclass; treat as non-numeric
        return default
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(float(value)) else default
    if isinstance(value, str):
        try:
            f = float(value)
        except ValueError:
            return default
        return f if math.isfinite(f) else default
    return default


def _clamp01(value: float) -> float:
    if not math.isfinite(value):
        return 0.0
    return max(0.0, min(1.0, value))


def _base58btc_encode(data: bytes) -> str:
    """Bitcoin/base58btc encoding (used for multibase `z...` values)."""
    n = int.from_bytes(data, "big")
    out = ""
    while n > 0:
        n, rem = divmod(n, 58)
        out = _BASE58_ALPHABET[rem] + out
    # Preserve leading zero bytes as leading '1's.
    pad = 0
    for byte in data:
        if byte == 0:
            pad += 1
        else:
            break
    return "1" * pad + out


def encode_ed25519_multibase(pubkey_bytes: bytes) -> str:
    """Encode a raw 32-byte Ed25519 public key as a multibase base58btc string.

    Produces the ``z...`` form required by ``Ed25519VerificationKey2020``:
    ``multibase-base58btc(0xed01 || raw_public_key)``. The ``z`` prefix denotes
    base58btc, so (unlike the reverted version) the encoded body is genuine
    base58btc rather than raw hex.
    """
    if not pubkey_bytes:
        return ""
    return "z" + _base58btc_encode(_MULTICODEC_ED25519_PUB + pubkey_bytes)


def _canonical_bytes(obj: Any) -> bytes:
    """Deterministic JSON serialisation for signing/verification."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _atomic_write_text(path: Path, text: str) -> None:
    """Write *text* to *path* atomically (tmpfile + fsync + os.replace)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)  # atomic on POSIX and Windows
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


def _verify_ed25519(pubkey_hex: str, signature_hex: str, data: bytes) -> bool:
    """Verify an Ed25519 signature; any malformed input returns False."""
    try:
        pk = Ed25519PublicKey.from_public_bytes(bytes.fromhex(pubkey_hex))
        pk.verify(bytes.fromhex(signature_hex), data)
        return True
    except Exception:
        return False


# ── Cross-link challenge / proof construction ────────────────────────────────

def build_link_challenge(
    bcn_id: str,
    af_id: str,
    nonce: str,
    issued_at: int,
    expires_at: int,
) -> bytes:
    """Canonical bytes both parties sign to attest a bcn_ <-> AgentFolio link.

    The challenge binds *both* identities plus a nonce and validity window, so a
    signature over it cannot be replayed to link a different pair or reused after
    expiry.
    """
    return _canonical_bytes(
        {
            "typ": LINK_CHALLENGE_TYPE,
            "v": LINK_CHALLENGE_VERSION,
            "bcn_id": bcn_id,
            "af_id": af_id,
            "nonce": nonce,
            "issued_at": issued_at,
            "expires_at": expires_at,
        }
    )


def create_cross_link_proof(
    beacon_signer: Signer,
    agentfolio_signer: Signer,
    af_id: str,
    *,
    ttl_seconds: int = DEFAULT_PROOF_TTL_SECONDS,
    now: Optional[int] = None,
) -> Dict[str, Any]:
    """Build a mutual-signed-challenge proof that the operator controls both IDs.

    The caller must hold *both* private keys (this is exactly what proves the two
    identities share an operator). ``beacon_signer`` is normally an
    ``AgentIdentity``; ``agentfolio_signer`` models the AgentFolio/Solana key.
    """
    bcn_id = agent_id_from_pubkey(bytes.fromhex(beacon_signer.public_key_hex))
    issued_at = int(now if now is not None else time.time())
    expires_at = issued_at + int(ttl_seconds)
    nonce = secrets.token_hex(16)
    challenge = build_link_challenge(bcn_id, af_id, nonce, issued_at, expires_at)
    return {
        "typ": LINK_CHALLENGE_TYPE,
        "v": LINK_CHALLENGE_VERSION,
        "bcn_id": bcn_id,
        "af_id": af_id,
        "nonce": nonce,
        "issued_at": issued_at,
        "expires_at": expires_at,
        "beacon_pubkey_hex": beacon_signer.public_key_hex,
        "agentfolio_pubkey_hex": agentfolio_signer.public_key_hex,
        "beacon_signature_hex": beacon_signer.sign(challenge).hex(),
        "agentfolio_signature_hex": agentfolio_signer.sign(challenge).hex(),
    }


# ── Trust Cache ──────────────────────────────────────────────────────────────

class TrustCache:
    """File-based cache with TTL for trust lookups (atomic writes)."""

    def __init__(self, cache_dir: Optional[Path] = None, ttl_seconds: int = 3600):
        # Under ~/.beacon/bridge/, never ~/.beacon/identity/.
        self._dir = cache_dir or Path.home() / ".beacon" / "bridge" / "trust_cache"
        self._ttl = ttl_seconds

    def get(self, key: str) -> Optional[Dict]:
        path = self._dir / f"{self._safe_key(key)}.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if time.time() - data.get("cached_at", 0) > self._ttl:
                return None
            return data["payload"]
        except (json.JSONDecodeError, KeyError, OSError):
            return None

    def set(self, key: str, payload: Dict) -> None:
        path = self._dir / f"{self._safe_key(key)}.json"
        entry = {"cached_at": time.time(), "payload": payload}
        _atomic_write_text(
            path, json.dumps(entry, indent=2, sort_keys=True) + "\n"
        )

    @staticmethod
    def _safe_key(key: str) -> str:
        # Keep cache filenames from escaping the cache dir or colliding.
        return "".join(c if (c.isalnum() or c in "._-") else "_" for c in key)


# ── Bridge Client ────────────────────────────────────────────────────────────

class BridgeClient:
    """Bidirectional bridge between Beacon and AgentFolio trust systems."""

    def __init__(
        self,
        beacon_atlas_url: str = DEFAULT_BEACON_ATLAS_URL,
        agentfolio_api_url: str = DEFAULT_AGENTFOLIO_API_URL,
        trust_weights: Optional[Dict[str, float]] = None,
        cache_ttl_seconds: int = 3600,
        timeout: int = 15,
    ):
        self._beacon_url = beacon_atlas_url.rstrip("/")
        self._af_url = agentfolio_api_url.rstrip("/")
        self._weights = trust_weights or dict(DEFAULT_TRUST_WEIGHTS)
        self._cache = TrustCache(ttl_seconds=cache_ttl_seconds)
        self._timeout = timeout

    # ── HTTP helpers (infra errors are distinct from not-found) ──

    def _get(self, base: str, path: str) -> Optional[Dict]:
        """GET JSON. Return None on a genuine 404; raise BridgeUnavailable on any
        transport error or 5xx so an outage is never read as 'no such identity'.
        """
        url = f"{base}{path}"
        try:
            resp = requests.get(
                url,
                timeout=self._timeout,
                headers={
                    "User-Agent": "Beacon-Bridge/2.0.0",
                    "Accept": "application/json",
                },
            )
        except RequestException as exc:
            raise BridgeUnavailable(f"request to {url} failed: {exc}") from exc
        if resp.status_code == 200:
            try:
                return resp.json()
            except ValueError as exc:
                raise BridgeUnavailable(f"invalid JSON from {url}: {exc}") from exc
        if resp.status_code == 404:
            return None
        if resp.status_code >= 500:
            raise BridgeUnavailable(f"{url} returned {resp.status_code}")
        # Other 4xx (401/403/etc.) are not a clean "not found"; fail closed.
        raise BridgeUnavailable(f"{url} returned {resp.status_code}")

    def _beacon_get(self, path: str) -> Optional[Dict]:
        return self._get(self._beacon_url, path)

    def _af_get(self, path: str) -> Optional[Dict]:
        return self._get(self._af_url, path)

    # ── Beacon Atlas lookup ──

    def lookup_beacon_atlas(self, bcn_id: str) -> Optional[Dict]:
        """Look up an agent in the Beacon atlas by bcn_ ID (exact match)."""
        cached = self._cache.get(f"beacon_atlas:{bcn_id}")
        if cached is not None:
            return cached
        data = self._beacon_get("/atlas")
        if isinstance(data, list):
            for entry in data:
                if entry.get("agent_id") == bcn_id:
                    self._cache.set(f"beacon_atlas:{bcn_id}", entry)
                    return entry
        return None

    def lookup_beacon_dns(self, name: str) -> Optional[Dict]:
        """Resolve a human-readable name via Beacon DNS."""
        cached = self._cache.get(f"beacon_dns:{name}")
        if cached is not None:
            return cached
        data = self._beacon_get(f"/dns/{name}")
        if data:
            self._cache.set(f"beacon_dns:{name}", data)
            return data
        return None

    # ── AgentFolio lookup ──

    def lookup_agentfolio(self, agent_id: str) -> Optional[Dict]:
        """Look up an agent on AgentFolio by ID or name (exact profile first)."""
        cached = self._cache.get(f"agentfolio:{agent_id}")
        if cached is not None:
            return cached
        data = self._af_get(f"/profile/{agent_id}")
        if isinstance(data, dict) and data.get("name"):
            self._cache.set(f"agentfolio:{agent_id}", data)
            return data
        return None

    # ── Cross-resolution ──

    def resolve_beacon_to_agentfolio(self, bcn_id: str) -> Optional[Dict]:
        """Resolve a Beacon bcn_ ID to its AgentFolio profile (best-effort hint).

        NOTE: resolution is a *convenience*, not a trust statement. It suggests a
        candidate AgentFolio profile; it does not assert the two are linked. Use
        :meth:`verify_cross_identity` (with a signed proof) to establish linkage.
        """
        atlas_entry = self.lookup_beacon_atlas(bcn_id)
        if not atlas_entry:
            return None
        name = atlas_entry.get("name", "")
        if not name:
            return None
        return self.lookup_agentfolio(name)

    def resolve_agentfolio_to_beacon(self, af_id: str) -> Optional[Dict]:
        """Resolve an AgentFolio agent ID to a candidate Beacon atlas entry.

        Convenience only — see :meth:`resolve_beacon_to_agentfolio`.
        """
        af_profile = self.lookup_agentfolio(af_id)
        if not af_profile:
            return None
        name = af_profile.get("name", "")
        if not name:
            return None
        dns_result = self.lookup_beacon_dns(name)
        if dns_result:
            return dns_result
        return None

    # ── Composite trust scoring ──

    def compute_composite_trust(
        self,
        beacon_data: Optional[Dict] = None,
        agentfolio_data: Optional[Dict] = None,
        cross_verified: bool = False,
    ) -> Dict[str, Any]:
        """Compute the composite trust score from both layers.

        ``cross_verified`` must reflect a *proven* linkage (see
        :meth:`verify_cross_identity`); it is False unless a valid signed proof
        was supplied. The mere co-existence of two records grants no bonus.

        All component values are normalised to ``[0, 1]`` and every numeric input
        is passed through :func:`_finite`, so the result is always a finite value
        in ``[0, 1]`` and the ``verified`` tier (>= 0.8) is reachable.
        """
        w = self._weights

        beacon_fidelity = 0.0
        if beacon_data:
            status = beacon_data.get("status", beacon_data.get("atlas_status", ""))
            if status == "active":
                has_fp = bool(
                    beacon_data.get("hardware_fingerprint")
                    or beacon_data.get("fingerprint")
                )
                beacon_fidelity = 1.0 if has_fp else 0.5

        af_reputation = 0.0
        if agentfolio_data:
            af_reputation = _clamp01(_finite(agentfolio_data.get("trust_score", 0)) / 100.0)

        cross_component = 1.0 if cross_verified else 0.0

        endorsement_factor = 0.0
        if agentfolio_data:
            endorsement_factor = _clamp01(
                _finite(agentfolio_data.get("endorsement_count", 0)) / 10.0
            )

        composite = (
            _finite(w.get("beacon_fidelity", 0.40)) * beacon_fidelity
            + _finite(w.get("agentfolio_reputation", 0.35)) * af_reputation
            + _finite(w.get("cross_verification", 0.15)) * cross_component
            + _finite(w.get("endorsement_bonus", 0.10)) * endorsement_factor
        )
        composite = _clamp01(composite)

        return {
            "score": round(composite, 4),
            "components": {
                "beacon_fidelity": round(beacon_fidelity, 4),
                "agentfolio_reputation": round(af_reputation, 4),
                "cross_verified": cross_verified,
                "endorsement_factor": round(endorsement_factor, 4),
            },
            "level": _trust_level(composite),
            "computed_at": datetime.now(timezone.utc).isoformat(),
        }

    # ── Cross-identity verification (the core fix) ──

    def verify_cross_identity(self, proof: Dict[str, Any]) -> Dict[str, Any]:
        """Verify that a Beacon identity and an AgentFolio identity share an operator.

        Linkage is accepted only when the proof carries a valid Ed25519 signature
        from **both** sides over a single challenge that binds the two IDs:

        1. The Beacon public key must derive to ``bcn_id`` (exact, cryptographic).
        2. The Beacon signature over the challenge must verify.
        3. The AgentFolio public key must equal the key AgentFolio publishes for
           ``af_id`` (exact), and the AgentFolio signature must verify.

        Any failure — malformed proof, expired window, key/ID mismatch, bad
        signature — yields ``verified=False`` with a precise ``reason``. Backend
        outages fail closed (``agentfolio_unavailable``), never silently pass.
        """
        result = {
            "verified": False,
            "method": "mutual_signed_challenge",
            "reason": None,
            "bcn_id": proof.get("bcn_id") if isinstance(proof, dict) else None,
            "af_id": proof.get("af_id") if isinstance(proof, dict) else None,
        }

        if not isinstance(proof, dict):
            result["reason"] = "malformed_proof"
            return result

        required = (
            "bcn_id", "af_id", "nonce", "issued_at", "expires_at",
            "beacon_pubkey_hex", "agentfolio_pubkey_hex",
            "beacon_signature_hex", "agentfolio_signature_hex",
        )
        if any(proof.get(k) in (None, "") for k in required):
            result["reason"] = "malformed_proof"
            return result

        bcn_id = proof["bcn_id"]
        af_id = proof["af_id"]

        # Validity window (rejects stale/replayed proofs).
        try:
            issued_at = int(proof["issued_at"])
            expires_at = int(proof["expires_at"])
        except (TypeError, ValueError):
            result["reason"] = "malformed_proof"
            return result
        now = int(time.time())
        if expires_at <= issued_at or now > expires_at:
            result["reason"] = "proof_expired"
            return result

        # (1) The Beacon pubkey must bind to bcn_id by construction — exact match,
        # not a name substring.
        try:
            beacon_pub_bytes = bytes.fromhex(proof["beacon_pubkey_hex"])
        except ValueError:
            result["reason"] = "malformed_proof"
            return result
        if not (bcn_id.startswith(AGENT_ID_PREFIX)
                and agent_id_from_pubkey(beacon_pub_bytes) == bcn_id):
            result["reason"] = "beacon_key_id_mismatch"
            return result

        # (2) The Beacon signature over the bound challenge must verify.
        challenge = build_link_challenge(
            bcn_id, af_id, proof["nonce"], issued_at, expires_at
        )
        if not _verify_ed25519(proof["beacon_pubkey_hex"], proof["beacon_signature_hex"], challenge):
            result["reason"] = "beacon_signature_invalid"
            return result

        # (3) The AgentFolio pubkey must be the one AgentFolio publishes for af_id.
        # An outage here fails closed rather than masquerading as "not found".
        try:
            af_profile = self.lookup_agentfolio(af_id)
        except BridgeUnavailable:
            result["reason"] = "agentfolio_unavailable"
            return result
        if not af_profile:
            result["reason"] = "agentfolio_identity_not_found"
            return result
        published_key = af_profile.get("public_key_hex") or af_profile.get("pubkey_hex")
        if not published_key or published_key.lower() != proof["agentfolio_pubkey_hex"].lower():
            result["reason"] = "agentfolio_key_mismatch"
            return result

        if not _verify_ed25519(
            proof["agentfolio_pubkey_hex"], proof["agentfolio_signature_hex"], challenge
        ):
            result["reason"] = "agentfolio_signature_invalid"
            return result

        result["verified"] = True
        result["reason"] = "ok"
        result["beacon_name"] = None  # linkage is by key, not by name
        result["agentfolio_name"] = af_profile.get("name", "")
        return result

    # ── Trust card builder ──

    def build_trust_card(
        self,
        identity: Any,
        name: str,
        skills: Optional[List[str]] = None,
        cross_link_proof: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Build a unified dual-layer trust card, signed as a whole.

        If ``cross_link_proof`` is supplied and verifies, the card is marked
        cross-verified and the composite score reflects it. The Ed25519
        ``attestation`` covers the *entire* card body (beacon + agentfolio +
        composite + migration), so no field can be mutated after signing without
        invalidating the attestation.
        """
        bcn_id = identity.agent_id if hasattr(identity, "agent_id") else str(identity)
        pubkey_hex = identity.public_key_hex if hasattr(identity, "public_key_hex") else ""
        beacon_data = self.lookup_beacon_atlas(bcn_id)
        af_data = self.lookup_agentfolio(name)

        cross_verified = False
        if cross_link_proof is not None:
            cross_verified = self.verify_cross_identity(cross_link_proof).get("verified", False)

        beacon_layer = {
            "agent_id": bcn_id,
            "public_key_hex": pubkey_hex,
            "atlas_status": (
                beacon_data.get("status", beacon_data.get("atlas_status", "unknown"))
                if beacon_data else "unregistered"
            ),
        }
        if beacon_data:
            for key in ("hardware_fingerprint", "fingerprint", "city", "region"):
                if key in beacon_data:
                    beacon_layer[key] = beacon_data[key]

        af_layer = {"name": name, "skills": skills or []}
        if af_data:
            for key in ("agent_id", "trust_score", "verifications", "endorsement_count",
                        "satp_on_chain", "oatr_operator_verified", "public_key_hex"):
                if key in af_data:
                    af_layer[key] = af_data[key]
        else:
            af_layer["agent_id"] = f"agent_{name.lower().replace('-', '_')}"
            af_layer["trust_score"] = 0
            af_layer["verifications"] = []
            af_layer["endorsement_count"] = 0

        composite = self.compute_composite_trust(beacon_data, af_data, cross_verified=cross_verified)

        card_body = {
            "version": "2.0.0",
            "beacon": beacon_layer,
            "agentfolio": af_layer,
            "composite_trust": composite,
            "cross_verification": {
                "verified": cross_verified,
                "method": "mutual_signed_challenge" if cross_verified else "none",
            },
            "migration": {"moltbook_refugee": False, "previous_identity": None, "claimed_at": None},
        }

        # Sign the ENTIRE canonical card body (finding #4). The attestation binds
        # the signing key too, so a verifier can confirm both integrity and that
        # the signer controls the card's Beacon identity.
        if hasattr(identity, "sign"):
            signature_hex = identity.sign(_canonical_bytes(card_body)).hex()
            card_body["attestation"] = {
                "alg": "Ed25519",
                "public_key_hex": pubkey_hex,
                "signature_hex": signature_hex,
                "signed_at": datetime.now(timezone.utc).isoformat(),
            }
        return card_body

    @staticmethod
    def verify_trust_card(card: Dict[str, Any]) -> bool:
        """Verify a trust card's attestation covers the whole card and binds its key.

        Returns True only if (a) the Ed25519 signature verifies over the canonical
        card body (attestation removed) and (b) the attesting key derives to the
        card's Beacon ``agent_id`` — so a card cannot be re-signed by an unrelated
        key or have any field mutated post-signing.
        """
        if not isinstance(card, dict) or "attestation" not in card:
            return False
        attestation = card["attestation"]
        pubkey_hex = attestation.get("public_key_hex", "")
        signature_hex = attestation.get("signature_hex", "")
        if not pubkey_hex or not signature_hex:
            return False
        try:
            claimed_agent_id = card.get("beacon", {}).get("agent_id", "")
            if agent_id_from_pubkey(bytes.fromhex(pubkey_hex)) != claimed_agent_id:
                return False
        except ValueError:
            return False
        body = {k: v for k, v in card.items() if k != "attestation"}
        return _verify_ed25519(pubkey_hex, signature_hex, _canonical_bytes(body))

    # ── Dual registration ──

    def dual_register(
        self,
        identity: Any,
        name: str,
        skills: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Register agent on both Beacon atlas and AgentFolio.

        Writes a local cross-link *record* (never treated as proof) atomically
        into ``~/.beacon/bridge/`` under a per-agent filename, so it cannot
        collide with another agent's link or corrupt shared state.
        """
        bcn_id = identity.agent_id if hasattr(identity, "agent_id") else str(identity)

        beacon_result = {"status": "skipped", "message": "atlas_ping requires beacon-skill daemon"}
        try:
            from beacon_skill.atlas_ping import atlas_ping
            data = atlas_ping(
                agent_id=bcn_id, name=name,
                capabilities=skills or ["general"], identity=identity,
            )
            beacon_result = {"status": "registered", "data": data}
        except ImportError as exc:
            beacon_result = {"status": "partial", "message": f"beacon-skill unavailable: {exc}"}
        except Exception as exc:  # network / daemon error — reported, not fatal
            beacon_result = {"status": "partial", "message": str(exc)}

        af_result = {"status": "skipped", "message": "AgentFolio profile creation requires web authentication"}
        try:
            existing = self.lookup_agentfolio(name)
            if existing:
                af_result = {"status": "existing", "data": existing}
            else:
                af_result = {"status": "requires_auth", "message": "Visit agentfolio.bot to create profile"}
        except BridgeUnavailable as exc:
            af_result = {"status": "unavailable", "message": str(exc)}

        trust_card = self.build_trust_card(identity, name, skills=skills)

        # Record only — explicitly NOT a linkage proof. A real link requires a
        # mutual signed challenge via verify_cross_identity().
        record_dir = Path.home() / ".beacon" / "bridge" / "links"
        record_path = record_dir / f"{bcn_id}.json"
        cross_link_record = {
            "record_type": "bridge_registration",
            "is_linkage_proof": False,
            "bcn_id": bcn_id,
            "agentfolio_name": name,
            "registered_at": datetime.now(timezone.utc).isoformat(),
            "beacon_result": beacon_result["status"],
            "agentfolio_result": af_result["status"],
        }
        _atomic_write_text(record_path, json.dumps(cross_link_record, indent=2) + "\n")

        return {
            "beacon_result": beacon_result,
            "agentfolio_result": af_result,
            "trust_card": trust_card,
            "cross_link_record": str(record_path),
            "status": "partial" if "partial" in (beacon_result["status"], af_result["status"]) else "ok",
        }

    # ── W3C DID export ──

    def export_portable_identity(
        self,
        identity: Any,
        name: str,
    ) -> Dict[str, Any]:
        """Export a W3C DID-compatible identity document with both trust layers."""
        bcn_id = identity.agent_id if hasattr(identity, "agent_id") else str(identity)
        pubkey_hex = identity.public_key_hex if hasattr(identity, "public_key_hex") else ""
        trust_card = self.build_trust_card(identity, name)
        composite = trust_card["composite_trust"]
        did = f"did:beacon:{bcn_id}"
        now = datetime.now(timezone.utc).isoformat()

        multibase_key = ""
        if pubkey_hex:
            try:
                multibase_key = encode_ed25519_multibase(bytes.fromhex(pubkey_hex))
            except ValueError:
                multibase_key = ""

        return {
            "@context": ["https://www.w3.org/ns/did/v1", "https://w3id.org/security/suites/ed25519-2020/v1"],
            "id": did,
            "controller": did,
            "verificationMethod": [{
                "id": f"{did}#beacon-key",
                "type": "Ed25519VerificationKey2020",
                "controller": did,
                "publicKeyMultibase": multibase_key,
            }],
            "authentication": [f"{did}#beacon-key"],
            "assertionMethod": [f"{did}#beacon-key"],
            "service": [{
                "id": f"{did}#agentfolio",
                "type": "AgentFolioTrust",
                "serviceEndpoint": f"https://agentfolio.bot/api/profile/{name}",
            }, {
                "id": f"{did}#beacon-atlas",
                "type": "BeaconAtlas",
                "serviceEndpoint": f"https://rustchain.org/beacon/atlas/{bcn_id}",
            }],
            "trustMetadata": {
                "compositeScore": composite["score"],
                "trustLevel": composite["level"],
                "beaconFidelity": composite["components"]["beacon_fidelity"],
                "agentfolioReputation": composite["components"]["agentfolio_reputation"],
                "crossVerified": composite["components"]["cross_verified"],
                "exportedAt": now,
            },
            "alsoKnownAs": [f"agentfolio:{name}", f"beacon:{bcn_id}"],
        }
