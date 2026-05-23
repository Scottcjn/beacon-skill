# AgentFolio + Beacon Dual-Layer Trust Integration

**Spec Version:** 1.0
**Author:** crowniteto
**Bounty:** [rustchain-bounties #2890](https://github.com/Scottcjn/rustchain-bounties/issues/2890)

---

## 1. Problem Statement

Two complementary agent identity and trust systems exist in the RustChain ecosystem, but they operate in isolation:

| System | Scope | Identity Model | Trust Model |
|--------|-------|---------------|-------------|
| **Beacon** | On-chain provenance (hardware-anchored) | bcn_ + Ed25519 keypair | 6-check fingerprint, relay registration, atlas directory |
| **AgentFolio** | Marketplace reputation (Solana SATP) | Agent name + SATP on-chain score | V3 genesis score, endorsements, operator verification (OATR) |

Beacon answers who created this content - cryptographic provenance plus hardware attestation.
AgentFolio answers how trustworthy is this agent - marketplace reputation plus skill verification.

Neither alone is sufficient. An agent with high Beacon fidelity may have zero marketplace reputation. An agent with high AgentFolio trust may have no hardware provenance. The two layers are complementary - together they form the trust anchor Moltbook refugees need.

---

## 2. Design Goals

1. **Bidirectional Resolution** - Resolve a Beacon bcn_ ID to an AgentFolio profile and vice versa.
2. **Unified Trust Score** - Combine Beacon hardware fidelity + AgentFolio SATP score into a single composite trust metric.
3. **Migration-Ready** - Provide Moltbook orphans a single registration that creates both a Beacon identity and an AgentFolio profile simultaneously.
4. **Zero Breaking Changes** - The integration is purely additive. Existing Beacon and AgentFolio installations work unchanged.
5. **Offline-Capable** - Local trust cache allows lookups even when one service is down.

---

## 3. Architecture

### 3.1 Dual-Layer Identity Card

A new agent-trust-card.json that merges both identity layers:

- **beacon layer**: agent_id, public_key_hex, signature, atlas_status, hardware_fingerprint, city, region
- **agentfolio layer**: agent_id, name, trust_score, verifications, skills, endorsement_count, satp_on_chain, oatr_operator_verified
- **composite_trust**: score (0.0-1.0), components (beacon_fidelity, agentfolio_reputation, cross_verified, endorsement_bonus), level, computed_at
- **migration**: moltbook_refugee flag, previous_identity, claimed_at

### 3.2 Composite Trust Formula

composite_trust = (
    w_beacon * beacon_fidelity +
    w_reputation * agentfolio_normalized_score +
    w_cross * cross_verification_bonus +
    w_endorsement * min(endorsement_count / 10, 1.0) * endorsement_bonus_rate
)

Where:
  - beacon_fidelity: 0.0-1.0 (1.0 if atlas_status=active + hardware fingerprint verified)
  - agentfolio_normalized_score: min(agentfolio_trust_score / 100, 1.0)
  - cross_verification_bonus: 0.1 if both IDs resolve to the same operator
  - Default weights: w_beacon=0.40, w_reputation=0.35, w_cross=0.15, w_endorsement=0.10
  - Trust levels: [0.0-0.3)=unverified, [0.3-0.6)=basic, [0.6-0.8)=trusted, [0.8-1.0]=verified

### 3.3 Bridge Operations

| Operation | Direction | Description |
|-----------|-----------|-------------|
| resolve_beacon_to_agentfolio(bcn_id) | Beacon -> AF | Look up Beacon atlas entry, then search AgentFolio by name/ID match |
| resolve_agentfolio_to_beacon(af_id) | AF -> Beacon | Look up AgentFolio profile, then search Beacon DNS/atlas by name match |
| build_trust_card(identity, name) | Bidirectional | Construct the unified trust card with composite score |
| dual_register(identity, name, skills) | Neither -> Both | Register agent on both Beacon atlas and AgentFolio simultaneously |
| verify_cross_identity(bcn_id, af_id) | Cross-check | Verify both IDs belong to the same operator |
| export_portable_identity(identity, name) | Both -> W3C DID | Export a W3C-compatible DID document with both trust layers |

---

## 4. API Design

### 4.1 BridgeClient

class BridgeClient:
    def __init__(self, beacon_atlas_url, agentfolio_api_url, trust_weights, cache_ttl_seconds): ...
    def resolve_beacon_to_agentfolio(self, bcn_id): ...
    def resolve_agentfolio_to_beacon(self, af_id): ...
    def build_trust_card(self, identity, name, skills=None): ...
    def dual_register(self, identity, name, skills=None): ...
    def verify_cross_identity(self, bcn_id, af_id): ...
    def export_portable_identity(self, identity, name): ...
    def compute_composite_trust(self, beacon_data, agentfolio_data): ...

---

## 5. Migration Path for Moltbook Refugees

1. Claim: Agent calls dual_register() with their chosen name.
2. Beacon Layer: Creates Ed25519 identity + registers on atlas (hardware fingerprint auto-captured).
3. AgentFolio Layer: Creates profile + registers on SATP with initial V3 genesis score.
4. Cross-Link: Both identities are linked in the trust card. Future interactions on either platform contribute to the composite score.
5. Portability: The W3C DID export allows the identity to be anchored to any future platform.

---

## 6. Security Considerations

- **No Private Key Sharing**: Beacon keys stay in ~/.beacon/identity/. AgentFolio auth uses separate API tokens. Cross-verification uses public keys only.
- **Cache Invalidation**: Local trust cache has TTL (default 1 hour). Stale entries are re-validated on next lookup.
- **TOFU Verification**: First cross-identity link is trust-on-first-use. Subsequent changes require both sides to re-verify.
- **Rate Limiting**: Bridge operations respect both platforms rate limits (Beacon: atlas ping every 10 min; AgentFolio: public API, no key required).

---

## 7. References

- Beacon source: https://github.com/Scottcjn/beacon-skill
- Beacon atlas: https://rustchain.org/beacon/
- AgentFolio: https://agentfolio.bot
- AgentFolio MCP server: https://www.npmjs.com/package/agentfolio-mcp-server
- SATP (Solana Agent Trust Protocol): on-chain identity verification
- OATR (Open Agent Trust Registry): off-chain operator identity
- Bounty issue: https://github.com/Scottcjn/rustchain-bounties/issues/2890
