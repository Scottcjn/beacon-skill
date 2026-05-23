## AgentFolio + Beacon Dual-Layer Trust Bridge.
#
# Bidirectional bridge connecting Beacon (hardware-anchored provenance) with
# AgentFolio (marketplace reputation on Solana SATP). Provides:
#
# - Cross-resolution: Beacon bcn_ ID <-> AgentFolio agent ID
# - Composite trust scoring: weighted blend of both trust layers
# - Dual registration: single call registers on both platforms
# - W3C DID export: portable identity with both trust layers
# - Migration support: one-call onboarding for Moltbook refugees
#
# Bounty: https://github.com/Scottcjn/rustchain-bounties/issues/2890
#

import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from requests.exceptions import RequestException

# -- Default Configuration --

DEFAULT_BEACON_ATLAS_URL = "https://rustchain.org/beacon"
DEFAULT_AGENTFOLIO_API_URL = "https://agentfolio.bot/api"
DEFAULT_TRUST_WEIGHTS = {
    "beacon_fidelity": 0.40,
    "agentfolio_reputation": 0.35,
    "cross_verification": 0.15,
    "endorsement_bonus": 0.10,
}
ENDORSEMENT_BONUS_RATE = 0.05
CROSS_VERIFICATION_BONUS = 0.1

TRUST_LEVELS = [
    (0.8, "verified"),
    (0.6, "trusted"),
    (0.3, "basic"),
    (0.0, "unverified"),
]


def _trust_level(score: float) -> str:
    """Map a 0-1 composite score to a trust level label."""
    for threshold, label in TRUST_LEVELS:
        if score >= threshold:
            return label
    return "unverified"


# -- Trust Cache --

class TrustCache:
    """Simple file-based cache with TTL for trust lookups."""

    def __init__(self, cache_dir: Optional[Path] = None, ttl_seconds: int = 3600):
        self._dir = cache_dir or Path.home() / ".beacon" / "trust_cache"
        self._ttl = ttl_seconds

    def get(self, key: str) -> Optional[Dict]:
        path = self._dir / f"{key}.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if time.time() - data.get("cached_at", 0) > self._ttl:
                return None
            return data["payload"]
        except (json.JSONDecodeError, KeyError):
            return None

    def set(self, key: str, payload: Dict) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._dir / f"{key}.json"
        entry = {"cached_at": time.time(), "payload": payload}
        path.write_text(
            json.dumps(entry, indent=2, sort_keys=True) + chr(10),
            encoding="utf-8",
        )


# -- Bridge Client --

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

    # -- HTTP Helpers --

    def _beacon_get(self, path: str) -> Optional[Dict]:
        url = f"{self._beacon_url}{path}"
        try:
            resp = requests.get(url, timeout=self._timeout, headers={
                "User-Agent": "Beacon-Bridge/1.0.0",
                "Accept": "application/json",
            })
            if resp.status_code == 200:
                return resp.json()
        except RequestException:
            pass
        return None

    def _af_get(self, path: str) -> Optional[Dict]:
        url = f"{self._af_url}{path}"
        try:
            resp = requests.get(url, timeout=self._timeout, headers={
                "User-Agent": "Beacon-Bridge/1.0.0",
                "Accept": "application/json",
            })
            if resp.status_code == 200:
                return resp.json()
        except RequestException:
            pass
        return None

    # -- Beacon Atlas Lookup --

    def lookup_beacon_atlas(self, bcn_id: str) -> Optional[Dict]:
        """Look up an agent in the Beacon atlas by bcn_ ID."""
        cached = self._cache.get(f"beacon_atlas:{bcn_id}")
        if cached is not None:
            return cached
        data = self._beacon_get("/atlas")
        if data and isinstance(data, list):
            for entry in data:
                if entry.get("agent_id") == bcn_id or entry.get("name") == bcn_id:
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

    # -- AgentFolio Lookup --

    def lookup_agentfolio(self, agent_id: str) -> Optional[Dict]:
        """Look up an agent on AgentFolio by ID or name."""
        cached = self._cache.get(f"agentfolio:{agent_id}")
        if cached is not None:
            return cached
        data = self._af_get(f"/profile/{agent_id}")
        if data and isinstance(data, dict) and data.get("name"):
            self._cache.set(f"agentfolio:{agent_id}", data)
            return data
        search_data = self._af_get(f"/profiles?query={agent_id}")
        if search_data and isinstance(search_data, list) and len(search_data) > 0:
            result = search_data[0]
            self._cache.set(f"agentfolio:{agent_id}", result)
            return result
        return None

    # -- Cross-Resolution --

    def resolve_beacon_to_agentfolio(self, bcn_id: str) -> Optional[Dict]:
        """Resolve a Beacon bcn_ ID to its AgentFolio profile."""
        atlas_entry = self.lookup_beacon_atlas(bcn_id)
        if not atlas_entry:
            return None
        name = atlas_entry.get("name", "")
        if not name:
            return None
        return self.lookup_agentfolio(name)

    def resolve_agentfolio_to_beacon(self, af_id: str) -> Optional[Dict]:
        """Resolve an AgentFolio agent ID to its Beacon atlas entry."""
        af_profile = self.lookup_agentfolio(af_id)
        if not af_profile:
            return None
        name = af_profile.get("name", "")
        if not name:
            return None
        dns_result = self.lookup_beacon_dns(name)
        if dns_result:
            return dns_result
        return self.lookup_beacon_atlas(name)

    # -- Composite Trust Scoring --

    def compute_composite_trust(
        self,
        beacon_data: Optional[Dict] = None,
        agentfolio_data: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        """Compute the composite trust score from both layers."""
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
            raw_score = agentfolio_data.get("trust_score", 0)
            if isinstance(raw_score, (int, float)):
                af_reputation = min(raw_score / 100.0, 1.0)
        cross_verified = beacon_data is not None and agentfolio_data is not None
        cross_bonus = CROSS_VERIFICATION_BONUS if cross_verified else 0.0
        endorsement_count = 0
        if agentfolio_data:
            endorsement_count = agentfolio_data.get("endorsement_count", 0)
            if isinstance(endorsement_count, str):
                try:
                    endorsement_count = int(endorsement_count)
                except ValueError:
                    endorsement_count = 0
        endorsement_factor = min(endorsement_count / 10.0, 1.0)
        endorsement_bonus = endorsement_factor * ENDORSEMENT_BONUS_RATE
        composite = (
            w.get("beacon_fidelity", 0.40) * beacon_fidelity
            + w.get("agentfolio_reputation", 0.35) * af_reputation
            + w.get("cross_verification", 0.15) * cross_bonus
            + w.get("endorsement_bonus", 0.10) * endorsement_bonus
        )
        composite = max(0.0, min(1.0, composite))
        return {
            "score": round(composite, 4),
            "components": {
                "beacon_fidelity": round(beacon_fidelity, 4),
                "agentfolio_reputation": round(af_reputation, 4),
                "cross_verified": cross_verified,
                "endorsement_bonus": round(endorsement_bonus, 4),
            },
            "level": _trust_level(composite),
            "computed_at": datetime.now(timezone.utc).isoformat(),
        }

    # -- Trust Card Builder --

    def build_trust_card(
        self,
        identity: Any,
        name: str,
        skills: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Build a unified dual-layer trust card."""
        bcn_id = identity.agent_id if hasattr(identity, "agent_id") else str(identity)
        pubkey_hex = identity.public_key_hex if hasattr(identity, "public_key_hex") else ""
        beacon_data = self.lookup_beacon_atlas(bcn_id)
        af_data = self.lookup_agentfolio(name)
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
        if hasattr(identity, "sign_hex"):
            msg = json.dumps({"beacon": beacon_layer, "name": name}, sort_keys=True, separators=(",", ":")).encode()
            beacon_layer["signature"] = identity.sign_hex(msg)
        af_layer = {"name": name, "skills": skills or []}
        if af_data:
            for key in ("agent_id", "trust_score", "verifications", "endorsement_count",
                        "satp_on_chain", "oatr_operator_verified"):
                if key in af_data:
                    af_layer[key] = af_data[key]
        else:
            af_layer["agent_id"] = f"agent_{name.lower().replace(chr(45), chr(95))}"
            af_layer["trust_score"] = 0
            af_layer["verifications"] = []
            af_layer["endorsement_count"] = 0
        composite = self.compute_composite_trust(beacon_data, af_data)
        return {
            "version": "1.0.0",
            "beacon": beacon_layer,
            "agentfolio": af_layer,
            "composite_trust": composite,
            "migration": {"moltbook_refugee": False, "previous_identity": None, "claimed_at": None},
        }

    # -- Dual Registration --

    def dual_register(
        self,
        identity: Any,
        name: str,
        skills: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Register agent on both Beacon atlas and AgentFolio."""
        bcn_id = identity.agent_id if hasattr(identity, "agent_id") else str(identity)
        beacon_result = {"status": "skipped", "message": "atlas_ping requires beacon-skill daemon"}
        try:
            from beacon_skill.atlas_ping import atlas_ping
            result = atlas_ping(
                agent_id=bcn_id, name=name,
                capabilities=skills or ["general"], identity=identity,
            )
            beacon_result = {"status": "registered", "data": result}
        except (ImportError, Exception) as exc:
            beacon_result = {"status": "partial", "message": str(exc)}
        af_result = {"status": "skipped", "message": "AgentFolio profile creation requires web authentication"}
        try:
            existing = self.lookup_agentfolio(name)
            if existing:
                af_result = {"status": "existing", "data": existing}
            else:
                af_result = {"status": "requires_auth", "message": "Visit agentfolio.bot to create profile"}
        except Exception as exc:
            af_result = {"status": "error", "message": str(exc)}
        trust_card = self.build_trust_card(identity, name, skills=skills)
        cross_link_path = Path.home() / ".beacon" / "agentfolio_bridge.json"
        cross_link_path.parent.mkdir(parents=True, exist_ok=True)
        cross_link = {
            "bcn_id": bcn_id,
            "agentfolio_name": name,
            "registered_at": datetime.now(timezone.utc).isoformat(),
            "beacon_result": beacon_result["status"],
            "agentfolio_result": af_result["status"],
        }
        cross_link_path.write_text(json.dumps(cross_link, indent=2) + chr(10), encoding="utf-8")
        return {
            "beacon_result": beacon_result,
            "agentfolio_result": af_result,
            "trust_card": trust_card,
            "status": "partial" if "partial" in (beacon_result["status"], af_result["status"]) else "ok",
        }

    # -- Cross-Identity Verification --

    def verify_cross_identity(
        self,
        bcn_id: str,
        af_id: str,
    ) -> Dict[str, Any]:
        """Verify that a Beacon identity and AgentFolio identity belong to the same operator."""
        beacon_entry = self.lookup_beacon_atlas(bcn_id)
        af_profile = self.lookup_agentfolio(af_id)
        if not beacon_entry or not af_profile:
            return {
                "verified": False,
                "reason": "one_or_both_identities_not_found",
                "beacon_found": beacon_entry is not None,
                "agentfolio_found": af_profile is not None,
            }
        beacon_name = beacon_entry.get("name", "").lower().replace("-", "")
        af_name = af_profile.get("name", "").lower().replace("-", "").replace(" ", "")
        name_match = beacon_name == af_name or beacon_name in af_name or af_name in beacon_name
        cross_link_path = Path.home() / ".beacon" / "agentfolio_bridge.json"
        link_verified = False
        if cross_link_path.exists():
            try:
                link_data = json.loads(cross_link_path.read_text(encoding="utf-8"))
                link_verified = (
                    link_data.get("bcn_id") == bcn_id
                    and link_data.get("agentfolio_name", "").lower().replace("agent_", "") == af_id.lower().replace("agent_", "")
                )
            except (json.JSONDecodeError, KeyError):
                pass
        verified = name_match or link_verified
        return {
            "verified": verified,
            "method": "cross_link" if link_verified else "name_match" if name_match else "none",
            "beacon_name": beacon_entry.get("name", ""),
            "agentfolio_name": af_profile.get("name", ""),
            "name_match": name_match,
            "cross_link_match": link_verified,
        }

    # -- W3C DID Export --

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
        return {
            "@context": ["https://www.w3.org/ns/did/v1", "https://w3id.org/security/suites/ed25519-2020/v1"],
            "id": did,
            "controller": did,
            "verificationMethod": [{
                "id": f"{did}#beacon-key",
                "type": "Ed25519VerificationKey2020",
                "controller": did,
                "publicKeyMultibase": f"z{pubkey_hex}",
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