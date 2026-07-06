# AgentFolio + Beacon Dual-Layer Trust Integration

**Spec Version:** 2.0
**Original author:** crowniteto
**Fix-forward (v2.0):** cross-verification hardening per [beacon-skill #864](https://github.com/Scottcjn/beacon-skill/issues/864)
**Bounty:** [rustchain-bounties #2890](https://github.com/Scottcjn/rustchain-bounties/issues/2890)

---

## 0. What changed in v2.0 (and why)

v1.0 (PR #855) was merged then reverted (commit `c02b608`) because its
cross-identity verification could be forged: it granted trust when two records
merely *co-existed*, matched a *substring* of a name, or when a *local JSON file*
was present — none of which prove that one operator controls both identities.
v2.0 replaces coincidence with cryptography. The complete list of fixes is in
§6; the headline is: **linkage is proven by a mutual signed challenge, or it is
not proven at all.**

---

## 1. Problem Statement

Two complementary agent identity and trust systems exist in the RustChain
ecosystem but operate in isolation:

| System | Scope | Identity Model | Trust Model |
|--------|-------|----------------|-------------|
| **Beacon** | On-chain provenance (hardware-anchored) | `bcn_` + Ed25519 keypair | 6-check fingerprint, relay registration, atlas directory |
| **AgentFolio** | Marketplace reputation (Solana SATP) | Agent name + SATP on-chain score | V3 genesis score, endorsements, operator verification (OATR) |

Beacon answers *who created this* — cryptographic provenance plus hardware
attestation. AgentFolio answers *how trustworthy is this agent* — marketplace
reputation plus skill verification. Neither alone is sufficient; together they
form the trust anchor Moltbook refugees need. The bridge's job is to combine
them **without letting either layer's trust be borrowed by an impostor.**

---

## 2. Design Goals

1. **Provable bidirectional linkage** — a Beacon `bcn_` ID and an AgentFolio
   profile are linked only when the operator demonstrably controls both keys.
2. **Bounded, well-defined composite score** — a single `[0, 1]` metric whose
   every tier (including `verified`) is reachable and which no malformed input
   can inflate.
3. **Tamper-evident cards** — a trust card is signed as a whole; no field can be
   altered after signing without detection.
4. **Fail-closed under outage** — a service being *down* must never be read as an
   identity being *absent* or *verified*.
5. **Portable** — export a W3C DID document with valid verification material.

---

## 3. Architecture

### 3.1 Cross-Identity Linkage — Mutual Signed Challenge

Linkage is a claim that one operator controls both `bcn_id` and `af_id`. It is
proven by a **CrossLinkProof**: a single challenge, signed by *both* keys.

```
challenge = canonical_json({
  "typ": "beacon-agentfolio-link", "v": 1,
  "bcn_id": <bcn_...>, "af_id": <agentfolio id>,
  "nonce": <128-bit hex>, "issued_at": <unix>, "expires_at": <unix>
})

CrossLinkProof = {
  bcn_id, af_id, nonce, issued_at, expires_at,
  beacon_pubkey_hex,      agentfolio_pubkey_hex,
  beacon_signature_hex,   agentfolio_signature_hex   # both over `challenge`
}
```

**Verification (`verify_cross_identity`) accepts iff ALL hold:**

1. The proof is well-formed and unexpired (`issued_at < now <= expires_at`).
2. `agent_id_from_pubkey(beacon_pubkey) == bcn_id` — the Beacon key binds to the
   claimed ID *by construction* (SHA256-derived), so identity is matched exactly,
   never by name substring.
3. `Ed25519.verify(beacon_pubkey, beacon_signature, challenge)`.
4. `agentfolio_pubkey` equals the key AgentFolio publishes for `af_id`
   (exact match), resolved from the live profile.
5. `Ed25519.verify(agentfolio_pubkey, agentfolio_signature, challenge)`.

Any failure yields `verified=False` with a precise `reason`
(`beacon_key_id_mismatch`, `beacon_signature_invalid`, `agentfolio_key_mismatch`,
`agentfolio_signature_invalid`, `proof_expired`, `agentfolio_unavailable`,
`agentfolio_identity_not_found`, `malformed_proof`). The nonce + validity window
bind the proof so it cannot be replayed to link a different pair or reused after
expiry. **No local file is ever accepted as proof.**

### 3.2 Composite Trust Formula

```
composite = w_beacon      * beacon_fidelity        # {0, 0.5, 1.0}
          + w_reputation   * agentfolio_reputation  # clamp(score/100, 0, 1)
          + w_cross        * cross_component         # 1.0 iff cross-verified, else 0
          + w_endorsement  * endorsement_factor      # clamp(count/10, 0, 1)

weights:  w_beacon=0.40, w_reputation=0.35, w_cross=0.15, w_endorsement=0.10  (Σ = 1.0)
levels:   [0, 0.3) unverified | [0.3, 0.6) basic | [0.6, 0.8) trusted | [0.8, 1.0] verified
```

Every component is normalised to `[0, 1]`; the weights (which sum to 1.0) do all
the scaling. Therefore the maximum composite is exactly **1.0** and the
`verified` tier is reachable (a hardware-anchored, high-reputation,
cross-verified, well-endorsed agent). Every numeric input is passed through a
finite-guard (`NaN`/`inf`/`None`/non-numeric → 0), so a malformed score can never
be laundered into a forged `1.0` by the final clamp. `cross_component` is 1.0
only when a valid CrossLinkProof was supplied — co-existence alone grants nothing.

### 3.3 Signed Trust Card

`build_trust_card` returns a card whose **entire** canonical body (beacon +
agentfolio + composite + cross_verification + migration) is covered by an
Ed25519 `attestation`. `verify_trust_card` recomputes the canonical body and
checks both that the signature verifies and that the attesting key derives to the
card's Beacon `agent_id`. Mutating *any* field — including the AgentFolio score,
endorsements, or composite — invalidates the attestation.

### 3.4 Bridge Operations

| Operation | Description |
|-----------|-------------|
| `resolve_beacon_to_agentfolio(bcn_id)` | Best-effort *hint* only — suggests a candidate profile; asserts no linkage. |
| `resolve_agentfolio_to_beacon(af_id)` | Best-effort hint only. |
| `verify_cross_identity(proof)` | The trust statement — mutual signed challenge (§3.1). |
| `compute_composite_trust(beacon, af, cross_verified)` | Bounded, non-finite-safe score (§3.2). |
| `build_trust_card(identity, name, cross_link_proof=None)` | Signed, tamper-evident card (§3.3). |
| `verify_trust_card(card)` | Verify a card's whole-body attestation. |
| `dual_register(identity, name, skills)` | Register on both; writes a local *record* (never a proof). |
| `export_portable_identity(identity, name)` | W3C DID with valid `publicKeyMultibase`. |

---

## 4. Migration Path for Moltbook Refugees

1. **Claim** — the agent calls `dual_register()` with a chosen name.
2. **Beacon layer** — Ed25519 identity + atlas registration (fingerprint captured).
3. **AgentFolio layer** — profile creation on SATP (web-authenticated).
4. **Prove the link** — once the operator holds both keys, they build a
   CrossLinkProof; the composite score then earns the cross-verification weight.
5. **Portability** — the W3C DID export anchors the identity to any future platform.

Step 4 is deliberately explicit: onboarding does **not** silently assert linkage.

---

## 5. API Design

```python
class BridgeClient:
    def __init__(self, beacon_atlas_url, agentfolio_api_url, trust_weights=None,
                 cache_ttl_seconds=3600, timeout=15): ...
    def verify_cross_identity(self, proof: dict) -> dict: ...
    def compute_composite_trust(self, beacon_data=None, agentfolio_data=None,
                                cross_verified=False) -> dict: ...
    def build_trust_card(self, identity, name, skills=None, cross_link_proof=None) -> dict: ...
    @staticmethod
    def verify_trust_card(card: dict) -> bool: ...
    def dual_register(self, identity, name, skills=None) -> dict: ...
    def export_portable_identity(self, identity, name) -> dict: ...

# Helpers
def create_cross_link_proof(beacon_signer, agentfolio_signer, af_id, *, ttl_seconds=300) -> dict: ...
def encode_ed25519_multibase(pubkey_bytes: bytes) -> str: ...   # 'z' + base58btc(0xed01 || pk)
class Ed25519Signer: ...   # models the AgentFolio/Solana signing key
```

---

## 6. Security Considerations (fix-forward from #855)

Each item is the resolution of a specific blocking finding.

1. **Linkage requires proof, not co-existence** — `cross_verified` is set only by
   a valid mutual signed challenge (§3.1). Pairing your Beacon ID with a
   high-reputation AgentFolio name proves nothing.
2. **Exact identity match** — the Beacon key binds to `bcn_id` cryptographically;
   the AgentFolio key must equal the profile's published key. No substring/empty
   name matching.
3. **No local-file trust** — the `~/.beacon/bridge/` record is a convenience log,
   explicitly flagged `is_linkage_proof: false`, and is never read back as proof.
4. **Whole-card signature** — the attestation covers the entire card, so AF score,
   endorsements, composite, and migration metadata cannot be mutated post-signing.
5. **Non-finite scores rejected** — every numeric input is finite-guarded; `NaN`
   cannot clamp to `1.0` and `None` cannot raise.
6. **`verified` is reachable** — `[0, 1]` components × unit-sum weights ⇒ max 1.0.
7. **Atomic, namespaced state** — writes use tmpfile + `os.replace` into
   `~/.beacon/bridge/` (never `~/.beacon/identity/`), keyed per-agent so links
   cannot collide or corrupt shared state.
8. **Outage ≠ absence** — infra errors raise `BridgeUnavailable`; verification
   fails closed (`agentfolio_unavailable`) rather than reporting "not found".
9. **Valid DID keys** — `publicKeyMultibase` is `multibase-base58btc(0xed01 ||
   pubkey)` (`z6Mk…`), matching `Ed25519VerificationKey2020`.

Unchanged good properties: Beacon private keys never leave `~/.beacon/identity/`;
cross-verification uses public keys only; the trust cache is TTL-bounded.

---

## 7. References

- Beacon source: https://github.com/Scottcjn/beacon-skill
- Beacon atlas: https://rustchain.org/beacon/
- AgentFolio: https://agentfolio.bot
- SATP (Solana Agent Trust Protocol) / OATR (Open Agent Trust Registry)
- W3C DID Core: https://www.w3.org/TR/did-core/ ; Ed25519 multibase: https://w3id.org/security/suites/ed25519-2020/v1
- Bounty issue: https://github.com/Scottcjn/rustchain-bounties/issues/2890
- Fix-forward tracking: https://github.com/Scottcjn/beacon-skill/issues/864
