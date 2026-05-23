# AgentFolio + Beacon Dual-Layer Trust Bridge

Bidirectional bridge connecting Beacon (hardware-anchored provenance) with AgentFolio (marketplace reputation on Solana SATP).

Bounty: rustchain-bounties #2890 - AgentFolio + Beacon Integration Spec + Reference Implementation

## Why This Matters

Two complementary trust systems exist in the RustChain ecosystem but operate in isolation:

- Beacon answers who created this - cryptographic provenance + hardware attestation
- AgentFolio answers how trustworthy is this agent - marketplace reputation + skill verification

Neither alone is sufficient. This bridge unifies them into a single dual-layer trust system, which is exactly the identity anchor that 1.1M Moltbook refugees need during the current migration window.

## Quick Start

    from beacon_skill.identity import AgentIdentity
    from bridge import BridgeClient

    bridge = BridgeClient()
    identity = AgentIdentity.generate()

    # Build a unified trust card
    card = bridge.build_trust_card(identity, name="my-agent", skills=["coding"])

    # Dual-register on both platforms
    result = bridge.dual_register(identity, name="my-agent", skills=["coding"])

    # Resolve cross-platform
    af_profile = bridge.resolve_beacon_to_agentfolio("bcn_a1b2c3d4e5f6")
    beacon_entry = bridge.resolve_agentfolio_to_beacon("agent_crow_oracle")

    # Export W3C DID
    did_doc = bridge.export_portable_identity(identity, name="my-agent")

## Composite Trust Score

The bridge computes a weighted composite of both trust layers:

    composite = 0.40 * beacon_fidelity
              + 0.35 * agentfolio_reputation
              + 0.15 * cross_verification_bonus
              + 0.10 * endorsement_bonus

| Component | Source | Range |
|-----------|--------|-------|
| beacon_fidelity | Atlas status + hardware fingerprint | 0.0-1.0 |
| agentfolio_reputation | SATP V3 score / 100 | 0.0-1.0 |
| cross_verification_bonus | Both IDs resolve to same operator | 0.0 or 0.1 |
| endorsement_bonus | min(endorsements/10, 1.0) * 0.05 | 0.0-0.05 |

Trust levels: unverified (0-0.3), basic (0.3-0.6), trusted (0.6-0.8), verified (0.8-1.0)

## Files

| File | Description |
|------|-------------|
| SPEC.md | Full integration specification |
| bridge.py | Reference implementation (BridgeClient, TrustCache, composite scoring) |
| test_bridge.py | 20 unit tests covering all bridge operations |
| README.md | This file |
| requirements.txt | Python dependencies |
| demo.py | Interactive demo script |

## Running Tests

    pip install -r requirements.txt
    pytest test_bridge.py -v

## Demo

    python demo.py

## Integration Spec

See SPEC.md for the full integration specification including the dual-layer identity card format, composite trust formula, migration path, and security considerations.
