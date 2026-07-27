# SPDX-License-Identifier: MIT
"""Tests for the AgentFolio + Beacon Dual-Layer Trust Bridge.

These exercise the *real* Ed25519 crypto (via beacon_skill.identity), not mocks,
and include the forgery-rejection cases that motivated the rewrite: the
reputation-borrow attack, mutated payloads, non-finite scores, expired proofs,
and infrastructure-outage-as-not-found.

Run: pytest test_bridge.py -v
"""

import json
import math
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from bridge import (  # noqa: E402
    BridgeClient,
    BridgeUnavailable,
    Ed25519Signer,
    TrustCache,
    _finite,
    _trust_level,
    build_link_challenge,
    create_cross_link_proof,
    encode_ed25519_multibase,
)
from beacon_skill.identity import AgentIdentity, agent_id_from_pubkey  # noqa: E402


# ── base58 decode (test-only, to round-trip multibase) ───────────────────────

_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def _b58decode(s: str) -> bytes:
    n = 0
    for ch in s:
        n = n * 58 + _B58.index(ch)
    body = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    pad = len(s) - len(s.lstrip("1"))
    return b"\x00" * pad + body


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def bridge():
    return BridgeClient(
        beacon_atlas_url="http://localhost:9999/beacon",
        agentfolio_api_url="http://localhost:9999/api",
        cache_ttl_seconds=0,
    )


@pytest.fixture
def beacon_identity():
    """A real Beacon Ed25519 identity."""
    return AgentIdentity.generate()


@pytest.fixture
def af_signer():
    """A real AgentFolio-side (Solana/Ed25519) signer."""
    return Ed25519Signer.generate()


def af_profile_for(signer, name="crow-oracle", trust_score=85.0, endorsements=7):
    """Build a mock AgentFolio profile that publishes *signer*'s public key."""
    return {
        "agent_id": f"agent_{name.replace('-', '_')}",
        "name": name,
        "public_key_hex": signer.public_key_hex,
        "trust_score": trust_score,
        "endorsement_count": endorsements,
        "satp_on_chain": True,
    }


# ── Trust level ──────────────────────────────────────────────────────────────

class TestTrustLevel:
    def test_bands(self):
        assert _trust_level(0.95) == "verified"
        assert _trust_level(0.80) == "verified"
        assert _trust_level(0.79) == "trusted"
        assert _trust_level(0.60) == "trusted"
        assert _trust_level(0.59) == "basic"
        assert _trust_level(0.30) == "basic"
        assert _trust_level(0.29) == "unverified"
        assert _trust_level(0.0) == "unverified"

    def test_non_finite_is_unverified(self):
        assert _trust_level(float("nan")) == "unverified"
        assert _trust_level(float("inf")) == "unverified"


# ── Finite guard (finding #5) ────────────────────────────────────────────────

class TestFiniteGuard:
    def test_rejects_non_finite(self):
        assert _finite(float("nan")) == 0.0
        assert _finite(float("inf")) == 0.0
        assert _finite(float("-inf")) == 0.0
        assert _finite(None) == 0.0

    def test_rejects_non_numeric(self):
        assert _finite("not-a-number") == 0.0
        assert _finite({"a": 1}) == 0.0
        assert _finite(True) == 0.0  # bool is not a trust score

    def test_passes_finite(self):
        assert _finite(42) == 42.0
        assert _finite(3.5) == 3.5
        assert _finite("7.5") == 7.5


# ── Trust cache (atomic writes) ──────────────────────────────────────────────

class TestTrustCache:
    def test_set_and_get(self, tmp_path):
        cache = TrustCache(cache_dir=tmp_path, ttl_seconds=60)
        cache.set("test_key", {"hello": "world"})
        assert cache.get("test_key") == {"hello": "world"}

    def test_get_missing(self, tmp_path):
        assert TrustCache(cache_dir=tmp_path).get("nonexistent") is None

    def test_ttl_expiry(self, tmp_path):
        cache = TrustCache(cache_dir=tmp_path, ttl_seconds=0)
        cache.set("k", {"hello": "world"})
        time.sleep(0.01)
        assert cache.get("k") is None

    def test_key_cannot_escape_dir(self, tmp_path):
        cache = TrustCache(cache_dir=tmp_path, ttl_seconds=60)
        cache.set("../evil", {"x": 1})
        # Nothing written outside the cache dir.
        assert not (tmp_path.parent / "evil.json").exists()


# ── Composite trust: reachability + non-finite safety ────────────────────────

class TestCompositeTrust:
    def test_verified_tier_is_reachable(self, bridge):
        """Finding #6: max composite must reach >= 0.8 so 'verified' exists."""
        beacon = {"status": "active", "hardware_fingerprint": "fp"}
        af = {"trust_score": 100, "endorsement_count": 10}
        result = bridge.compute_composite_trust(beacon, af, cross_verified=True)
        assert result["score"] == pytest.approx(1.0, abs=1e-9)
        assert result["level"] == "verified"

    def test_cross_verified_requires_flag_not_existence(self, bridge):
        """Finding #1: co-existence alone grants no cross-verification credit."""
        beacon = {"status": "active", "hardware_fingerprint": "fp"}
        af = {"trust_score": 80, "endorsement_count": 5}
        without = bridge.compute_composite_trust(beacon, af, cross_verified=False)
        with_proof = bridge.compute_composite_trust(beacon, af, cross_verified=True)
        assert without["components"]["cross_verified"] is False
        assert with_proof["components"]["cross_verified"] is True
        assert with_proof["score"] > without["score"]

    def test_nan_score_cannot_forge_verified(self, bridge):
        """Finding #5: a NaN trust score must NOT clamp up to a forged 1.0."""
        af = {"trust_score": float("nan"), "endorsement_count": float("nan")}
        result = bridge.compute_composite_trust(agentfolio_data=af)
        assert math.isfinite(result["score"])
        assert result["score"] == 0.0
        assert result["level"] == "unverified"

    def test_none_score_does_not_raise(self, bridge):
        """Finding #5: a None score/endorsement must not raise TypeError."""
        af = {"trust_score": None, "endorsement_count": None}
        result = bridge.compute_composite_trust(agentfolio_data=af)
        assert result["score"] == 0.0

    def test_inf_score_is_clamped(self, bridge):
        af = {"trust_score": float("inf"), "endorsement_count": 10}
        result = bridge.compute_composite_trust(agentfolio_data=af)
        assert math.isfinite(result["score"])
        assert 0.0 <= result["score"] <= 1.0

    def test_neither_layer(self, bridge):
        result = bridge.compute_composite_trust()
        assert result["score"] == 0.0
        assert result["level"] == "unverified"

    def test_score_bounded(self, bridge):
        beacon = {"status": "active", "hardware_fingerprint": "fp"}
        af = {"trust_score": 150.0, "endorsement_count": 100}
        result = bridge.compute_composite_trust(beacon, af, cross_verified=True)
        assert 0.0 <= result["score"] <= 1.0
        assert result["components"]["agentfolio_reputation"] == 1.0  # clamped


# ── Cross-identity verification: the core security surface ───────────────────

class TestCrossIdentityProof:
    def test_valid_mutual_proof(self, bridge, beacon_identity, af_signer):
        proof = create_cross_link_proof(beacon_identity, af_signer, "crow-oracle")
        with patch.object(bridge, "lookup_agentfolio", return_value=af_profile_for(af_signer)):
            result = bridge.verify_cross_identity(proof)
        assert result["verified"] is True
        assert result["reason"] == "ok"
        assert result["method"] == "mutual_signed_challenge"

    def test_reputation_borrow_wrong_af_key_rejected(self, bridge, beacon_identity, af_signer):
        """Finding #1/#2: attacker controls their own AF key, not the famous one.

        The proof carries the attacker's AF pubkey, but AgentFolio publishes a
        *different* key for the high-reputation profile -> key mismatch.
        """
        attacker_af = Ed25519Signer.generate()
        famous_profile = af_profile_for(af_signer, name="famous-agent", trust_score=99)
        proof = create_cross_link_proof(beacon_identity, attacker_af, "famous-agent")
        with patch.object(bridge, "lookup_agentfolio", return_value=famous_profile):
            result = bridge.verify_cross_identity(proof)
        assert result["verified"] is False
        assert result["reason"] == "agentfolio_key_mismatch"

    def test_reputation_borrow_forged_signature_rejected(self, bridge, beacon_identity, af_signer):
        """Attacker claims the real famous AF pubkey but cannot sign with it."""
        attacker_af = Ed25519Signer.generate()
        proof = create_cross_link_proof(beacon_identity, attacker_af, "famous-agent")
        # Swap in the *victim's* public key but keep the attacker's signature.
        proof["agentfolio_pubkey_hex"] = af_signer.public_key_hex
        famous_profile = af_profile_for(af_signer, name="famous-agent", trust_score=99)
        with patch.object(bridge, "lookup_agentfolio", return_value=famous_profile):
            result = bridge.verify_cross_identity(proof)
        assert result["verified"] is False
        assert result["reason"] == "agentfolio_signature_invalid"

    def test_beacon_key_not_matching_bcn_id_rejected(self, bridge, beacon_identity, af_signer):
        """Finding #2: the Beacon pubkey must derive to the claimed bcn_id."""
        proof = create_cross_link_proof(beacon_identity, af_signer, "crow-oracle")
        proof["bcn_id"] = "bcn_deadbeef0000"  # not derivable from beacon_pubkey
        with patch.object(bridge, "lookup_agentfolio", return_value=af_profile_for(af_signer)):
            result = bridge.verify_cross_identity(proof)
        assert result["verified"] is False
        assert result["reason"] == "beacon_key_id_mismatch"

    def test_mutated_challenge_field_breaks_signature(self, bridge, beacon_identity, af_signer):
        """Finding #1: rebinding to a different af_id invalidates both sigs."""
        proof = create_cross_link_proof(beacon_identity, af_signer, "crow-oracle")
        proof["af_id"] = "someone-else"  # signatures were over the original af_id
        with patch.object(bridge, "lookup_agentfolio", return_value=af_profile_for(af_signer, name="someone-else")):
            result = bridge.verify_cross_identity(proof)
        assert result["verified"] is False
        assert result["reason"] == "beacon_signature_invalid"

    def test_expired_proof_rejected(self, bridge, beacon_identity, af_signer):
        proof = create_cross_link_proof(
            beacon_identity, af_signer, "crow-oracle", ttl_seconds=1,
            now=int(time.time()) - 3600,
        )
        with patch.object(bridge, "lookup_agentfolio", return_value=af_profile_for(af_signer)):
            result = bridge.verify_cross_identity(proof)
        assert result["verified"] is False
        assert result["reason"] == "proof_expired"

    def test_agentfolio_outage_fails_closed(self, bridge, beacon_identity, af_signer):
        """Finding #8: an outage must not read as a verified/known identity."""
        proof = create_cross_link_proof(beacon_identity, af_signer, "crow-oracle")
        with patch.object(bridge, "lookup_agentfolio", side_effect=BridgeUnavailable("500")):
            result = bridge.verify_cross_identity(proof)
        assert result["verified"] is False
        assert result["reason"] == "agentfolio_unavailable"

    def test_agentfolio_not_found_rejected(self, bridge, beacon_identity, af_signer):
        proof = create_cross_link_proof(beacon_identity, af_signer, "crow-oracle")
        with patch.object(bridge, "lookup_agentfolio", return_value=None):
            result = bridge.verify_cross_identity(proof)
        assert result["verified"] is False
        assert result["reason"] == "agentfolio_identity_not_found"

    def test_malformed_proof_rejected(self, bridge):
        assert bridge.verify_cross_identity({})["reason"] == "malformed_proof"
        assert bridge.verify_cross_identity(None)["reason"] == "malformed_proof"
        assert bridge.verify_cross_identity({"bcn_id": "bcn_x"})["reason"] == "malformed_proof"

    def test_local_file_is_never_proof(self, bridge, beacon_identity, af_signer, tmp_path, monkeypatch):
        """Finding #3: a local bridge JSON file must not grant linkage."""
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        # Plant a forged local link file like the reverted version trusted.
        forged = tmp_path / ".beacon" / "agentfolio_bridge.json"
        forged.parent.mkdir(parents=True, exist_ok=True)
        forged.write_text(json.dumps({"bcn_id": beacon_identity.agent_id, "agentfolio_name": "famous-agent"}))
        # A proof with a bad AF signature must still be rejected despite the file.
        attacker_af = Ed25519Signer.generate()
        proof = create_cross_link_proof(beacon_identity, attacker_af, "famous-agent")
        proof["agentfolio_signature_hex"] = "00" * 64
        with patch.object(bridge, "lookup_agentfolio", return_value=af_profile_for(af_signer, name="famous-agent")):
            result = bridge.verify_cross_identity(proof)
        assert result["verified"] is False


# ── Trust card: whole-card attestation (finding #4) ──────────────────────────

class TestTrustCard:
    def test_card_is_signed_and_verifies(self, bridge, beacon_identity):
        with patch.object(bridge, "lookup_beacon_atlas", return_value=None), \
             patch.object(bridge, "lookup_agentfolio", return_value=None):
            card = bridge.build_trust_card(beacon_identity, "new-agent")
        assert "attestation" in card
        assert BridgeClient.verify_trust_card(card) is True

    def test_mutating_any_field_breaks_attestation(self, bridge, beacon_identity, af_signer):
        with patch.object(bridge, "lookup_beacon_atlas", return_value=None), \
             patch.object(bridge, "lookup_agentfolio", return_value=af_profile_for(af_signer)):
            card = bridge.build_trust_card(beacon_identity, "crow-oracle")
        assert BridgeClient.verify_trust_card(card) is True
        # Tamper with the AF score (previously *outside* the signature).
        card["agentfolio"]["trust_score"] = 999
        assert BridgeClient.verify_trust_card(card) is False

    def test_mutating_composite_breaks_attestation(self, bridge, beacon_identity):
        with patch.object(bridge, "lookup_beacon_atlas", return_value=None), \
             patch.object(bridge, "lookup_agentfolio", return_value=None):
            card = bridge.build_trust_card(beacon_identity, "x")
        card["composite_trust"]["score"] = 1.0
        card["composite_trust"]["level"] = "verified"
        assert BridgeClient.verify_trust_card(card) is False

    def test_foreign_key_cannot_reattest(self, bridge, beacon_identity):
        with patch.object(bridge, "lookup_beacon_atlas", return_value=None), \
             patch.object(bridge, "lookup_agentfolio", return_value=None):
            card = bridge.build_trust_card(beacon_identity, "x")
        # Re-sign the body with an unrelated key and swap the attestation key.
        attacker = AgentIdentity.generate()
        from bridge import _canonical_bytes
        body = {k: v for k, v in card.items() if k != "attestation"}
        card["attestation"]["public_key_hex"] = attacker.public_key_hex
        card["attestation"]["signature_hex"] = attacker.sign(_canonical_bytes(body)).hex()
        # Key no longer derives to the card's beacon agent_id.
        assert BridgeClient.verify_trust_card(card) is False

    def test_card_reflects_cross_verification(self, bridge, beacon_identity, af_signer):
        proof = create_cross_link_proof(beacon_identity, af_signer, "crow-oracle")
        prof = af_profile_for(af_signer)
        with patch.object(bridge, "lookup_beacon_atlas",
                          return_value={"status": "active", "hardware_fingerprint": "fp", "agent_id": beacon_identity.agent_id}), \
             patch.object(bridge, "lookup_agentfolio", return_value=prof):
            card = bridge.build_trust_card(beacon_identity, "crow-oracle", cross_link_proof=proof)
        assert card["cross_verification"]["verified"] is True
        assert card["composite_trust"]["components"]["cross_verified"] is True


# ── W3C DID export: valid multibase (finding #9) ─────────────────────────────

class TestDIDExport:
    def test_did_structure(self, bridge, beacon_identity):
        with patch.object(bridge, "lookup_beacon_atlas", return_value=None), \
             patch.object(bridge, "lookup_agentfolio", return_value=None):
            did = bridge.export_portable_identity(beacon_identity, "test-agent")
        assert did["id"].startswith("did:beacon:bcn_")
        assert len(did["service"]) >= 2
        assert "https://www.w3.org/ns/did/v1" in did["@context"]

    def test_publicKeyMultibase_is_valid_base58btc(self, bridge, beacon_identity):
        with patch.object(bridge, "lookup_beacon_atlas", return_value=None), \
             patch.object(bridge, "lookup_agentfolio", return_value=None):
            did = bridge.export_portable_identity(beacon_identity, "test-agent")
        mb = did["verificationMethod"][0]["publicKeyMultibase"]
        assert mb.startswith("z")
        # Round-trip: strip 'z', base58btc-decode, expect 0xed01 || raw pubkey.
        decoded = _b58decode(mb[1:])
        assert decoded[:2] == b"\xed\x01"
        assert decoded[2:].hex() == beacon_identity.public_key_hex

    def test_multibase_matches_did_key_prefix(self, beacon_identity):
        # did:key ed25519 values start with 'z6Mk'.
        mb = encode_ed25519_multibase(bytes.fromhex(beacon_identity.public_key_hex))
        assert mb.startswith("z6Mk")


# ── Dual registration: atomic, namespaced, record-not-proof (finding #7) ─────

class TestDualRegister:
    def test_writes_record_outside_identity_dir(self, bridge, beacon_identity, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        with patch.object(bridge, "lookup_beacon_atlas", return_value=None), \
             patch.object(bridge, "lookup_agentfolio", return_value=None):
            result = bridge.dual_register(beacon_identity, "test-agent", skills=["coding"])
        record_path = Path(result["cross_link_record"])
        assert record_path.exists()
        # Under ~/.beacon/bridge/, never ~/.beacon/identity/.
        assert "identity" not in record_path.parts
        assert record_path.parts[-2] == "links"
        record = json.loads(record_path.read_text())
        assert record["is_linkage_proof"] is False
        assert record["bcn_id"] == beacon_identity.agent_id

    def test_record_is_per_agent_no_collision(self, bridge, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        id_a, id_b = AgentIdentity.generate(), AgentIdentity.generate()
        with patch.object(bridge, "lookup_beacon_atlas", return_value=None), \
             patch.object(bridge, "lookup_agentfolio", return_value=None):
            ra = bridge.dual_register(id_a, "agent-a")
            rb = bridge.dual_register(id_b, "agent-b")
        assert ra["cross_link_record"] != rb["cross_link_record"]
        assert Path(ra["cross_link_record"]).exists()
        assert Path(rb["cross_link_record"]).exists()


# ── Infra vs not-found (finding #8) ──────────────────────────────────────────

class TestInfraVsNotFound:
    def _resp(self, status, body=None):
        class R:
            status_code = status
            def json(self_inner):
                if body is None:
                    raise ValueError("no json")
                return body
        return R()

    def test_404_is_not_found(self, bridge):
        with patch("bridge.requests.get", return_value=self._resp(404)):
            assert bridge._beacon_get("/atlas") is None

    def test_500_raises_unavailable(self, bridge):
        with patch("bridge.requests.get", return_value=self._resp(503)):
            with pytest.raises(BridgeUnavailable):
                bridge._beacon_get("/atlas")

    def test_network_error_raises_unavailable(self, bridge):
        import requests as _rq
        with patch("bridge.requests.get", side_effect=_rq.exceptions.ConnectTimeout()):
            with pytest.raises(BridgeUnavailable):
                bridge._af_get("/profile/x")

    def test_403_fails_closed(self, bridge):
        with patch("bridge.requests.get", return_value=self._resp(403)):
            with pytest.raises(BridgeUnavailable):
                bridge._af_get("/profile/x")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
