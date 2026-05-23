"""Demo script for AgentFolio + Beacon Dual-Layer Trust Bridge."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from bridge import BridgeClient


def main():
    print("=" * 60)
    print("AgentFolio + Beacon Dual-Layer Trust Bridge Demo")
    print("=" * 60)

    bridge = BridgeClient()

    print("Composite Trust Scoring Demo")
    print()

    scenarios = [
        ("Both layers (active beacon + agentfolio)",
         {"status": "active", "hardware_fingerprint": "fp_abc123"},
         {"trust_score": 85.0, "endorsement_count": 7}),
        ("Beacon only (active, no fingerprint)",
         {"status": "active"},
         None),
        ("AgentFolio only (high trust)",
         None,
         {"trust_score": 92.0, "endorsement_count": 15}),
        ("Neither layer",
         None,
         None),
    ]

    for label, beacon_data, af_data in scenarios:
        result = bridge.compute_composite_trust(beacon_data, af_data)
        print(f"  {label}:")
        print(f"    Score: {result['score']:.4f} | Level: {result['level']}")
        comp = result['components']
        print(f"    Beacon Fidelity: {comp['beacon_fidelity']}")
        print(f"    AgentFolio Reputation: {comp['agentfolio_reputation']}")
        print(f"    Cross Verified: {comp['cross_verified']}")
        print(f"    Endorsement Bonus: {comp['endorsement_bonus']}")
        print()

    print("Trust Card Builder Demo")
    print()

    class MockIdentity:
        agent_id = "bcn_demo_bridge_01"
        public_key_hex = "a1b2c3" * 11 + "a1"
        def sign_hex(self, msg):
            return "deadbeef" * 16

    identity = MockIdentity()
    card = bridge.build_trust_card(identity, name="demo-bridge-agent", skills=["trust-bridge", "integration"])

    print(f"  Trust Card Version: {card['version']}")
    print(f"  Beacon Agent ID: {card['beacon']['agent_id']}")
    print(f"  Beacon Atlas Status: {card['beacon']['atlas_status']}")
    print(f"  AgentFolio Name: {card['agentfolio']['name']}")
    print(f"  AgentFolio Skills: {card['agentfolio']['skills']}")
    print(f"  Composite Score: {card['composite_trust']['score']:.4f}")
    print(f"  Trust Level: {card['composite_trust']['level']}")
    print()

    print("W3C DID Export Demo")
    print()

    did_doc = bridge.export_portable_identity(identity, name="demo-bridge-agent")
    print(f"  DID: {did_doc['id']}")
    print(f"  Verification Methods: {len(did_doc['verificationMethod'])}")
    print(f"  Services: {len(did_doc['service'])}")
    print(f"  Trust Score: {did_doc['trustMetadata']['compositeScore']:.4f}")
    print(f"  Trust Level: {did_doc['trustMetadata']['trustLevel']}")
    print(f"  Also Known As: {did_doc['alsoKnownAs']}")
    print()

    print("=" * 60)
    print("Demo complete. See SPEC.md for full integration spec.")
    print("=" * 60)


if __name__ == "__main__":
    main()
