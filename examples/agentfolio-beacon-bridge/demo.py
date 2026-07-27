# SPDX-License-Identifier: MIT
"""Interactive demo for the AgentFolio + Beacon Dual-Layer Trust Bridge.

Runs fully offline with real Ed25519 keys. It shows:

1. A genuine mutual-signed-challenge cross-link verifying.
2. A reputation-borrow *forgery* being rejected.
3. A composite score reaching the ``verified`` tier only when cross-verified.
4. A signed trust card whose attestation breaks if any field is mutated.
5. A W3C DID export with a valid `publicKeyMultibase`.

Run: python demo.py
"""

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
from bridge import (
    BridgeClient,
    Ed25519Signer,
    create_cross_link_proof,
)
from beacon_skill.identity import AgentIdentity


def _profile(signer, name, trust_score, endorsements):
    return {
        "agent_id": f"agent_{name.replace('-', '_')}",
        "name": name,
        "public_key_hex": signer.public_key_hex,
        "trust_score": trust_score,
        "endorsement_count": endorsements,
        "status": "active",
    }


def main() -> None:
    bridge = BridgeClient()

    # The operator controls BOTH keys — that is what makes the link real.
    beacon = AgentIdentity.generate()
    af = Ed25519Signer.generate()
    print("Beacon identity :", beacon.agent_id)
    print("AgentFolio key  :", af.public_key_hex[:16], "…")
    print()

    profile = _profile(af, "crow-oracle", trust_score=88, endorsements=9)

    # 1) Genuine cross-link ----------------------------------------------------
    proof = create_cross_link_proof(beacon, af, "crow-oracle")
    with patch.object(bridge, "lookup_agentfolio", return_value=profile):
        good = bridge.verify_cross_identity(proof)
    print("[1] Genuine mutual-signed-challenge link")
    print("    verified:", good["verified"], "| reason:", good["reason"])
    print()

    # 2) Reputation-borrow forgery --------------------------------------------
    # An attacker controls their own Beacon identity and their own AgentFolio
    # key, and tries to claim linkage to the high-reputation "crow-oracle".
    attacker_beacon = AgentIdentity.generate()
    attacker_af = Ed25519Signer.generate()
    forged = create_cross_link_proof(attacker_beacon, attacker_af, "crow-oracle")
    with patch.object(bridge, "lookup_agentfolio", return_value=profile):
        bad = bridge.verify_cross_identity(forged)
    print("[2] Reputation-borrow forgery (attacker lacks crow-oracle's key)")
    print("    verified:", bad["verified"], "| reason:", bad["reason"])
    print()

    # 3) Composite score with vs. without proof --------------------------------
    beacon_data = {"status": "active", "hardware_fingerprint": "fp_demo", "agent_id": beacon.agent_id}
    without = bridge.compute_composite_trust(beacon_data, profile, cross_verified=False)
    with_proof = bridge.compute_composite_trust(beacon_data, profile, cross_verified=True)
    print("[3] Composite trust")
    print(f"    no proof : {without['score']:.4f} ({without['level']})")
    print(f"    verified : {with_proof['score']:.4f} ({with_proof['level']})")
    print()

    # 4) Signed trust card, tamper-evident -------------------------------------
    with patch.object(bridge, "lookup_beacon_atlas", return_value=beacon_data), \
         patch.object(bridge, "lookup_agentfolio", return_value=profile):
        card = bridge.build_trust_card(beacon, "crow-oracle", cross_link_proof=proof)
    print("[4] Signed trust card")
    print("    attestation verifies:", BridgeClient.verify_trust_card(card))
    card["agentfolio"]["trust_score"] = 999  # tamper
    print("    after tampering score:", BridgeClient.verify_trust_card(card))
    print()

    # 5) W3C DID export --------------------------------------------------------
    with patch.object(bridge, "lookup_beacon_atlas", return_value=beacon_data), \
         patch.object(bridge, "lookup_agentfolio", return_value=profile):
        did = bridge.export_portable_identity(beacon, "crow-oracle")
    vm = did["verificationMethod"][0]
    print("[5] W3C DID export")
    print("    DID:", did["id"])
    print("    publicKeyMultibase:", vm["publicKeyMultibase"][:24], "…")
    print("    (starts with z6Mk =", vm["publicKeyMultibase"].startswith("z6Mk"), ")")


if __name__ == "__main__":
    main()
