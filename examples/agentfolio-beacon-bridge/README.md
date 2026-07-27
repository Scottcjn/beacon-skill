# AgentFolio + Beacon Dual-Layer Trust Bridge

A reference bridge that unifies **Beacon** (hardware-anchored provenance,
`bcn_` + Ed25519) and **AgentFolio** (marketplace reputation on Solana SATP) into
one dual-layer trust anchor — the identity target Moltbook refugees need during
the migration window.

> **v2.0 (fix-forward, [#864](https://github.com/Scottcjn/beacon-skill/issues/864)):**
> cross-identity verification now *proves* linkage with a mutual signed challenge
> instead of inferring it from co-existence, name substrings, or a local file.
> See [`SPEC.md`](./SPEC.md) §0 and §6 for the full list of hardening fixes.

## What it does

- **Cross-identity verification** — a `CrossLinkProof` signed by *both* the Beacon
  key and the AgentFolio key over one challenge that binds the two IDs. Anything
  less is rejected.
- **Composite trust score** — a bounded `[0, 1]` blend
  (`0.40` beacon fidelity + `0.35` AgentFolio reputation + `0.15` cross-verification
  + `0.10` endorsements) where `verified` (≥ 0.8) is actually reachable and no
  malformed input can forge a high score.
- **Signed trust card** — an Ed25519 attestation over the *whole* card; tamper any
  field and verification fails.
- **W3C DID export** — with a valid `publicKeyMultibase` (`z6Mk…`).

## Install

```bash
pip install -e .            # from the beacon-skill repo root (provides beacon_skill)
pip install -r examples/agentfolio-beacon-bridge/requirements.txt
```

## Quick start

```python
from beacon_skill.identity import AgentIdentity
from bridge import BridgeClient, Ed25519Signer, create_cross_link_proof

bridge = BridgeClient()

# The operator controls BOTH keys — that is what proves the link.
beacon = AgentIdentity.generate()      # bcn_ identity
af     = Ed25519Signer.generate()      # AgentFolio (Solana) key

proof = create_cross_link_proof(beacon, af, af_id="crow-oracle")
result = bridge.verify_cross_identity(proof)   # verified iff both signatures check
print(result["verified"], result["reason"])

card = bridge.build_trust_card(beacon, "crow-oracle", cross_link_proof=proof)
assert BridgeClient.verify_trust_card(card)    # tamper-evident

did = bridge.export_portable_identity(beacon, "crow-oracle")
print(did["verificationMethod"][0]["publicKeyMultibase"])   # z6Mk…
```

## Run the demo & tests

```bash
python  examples/agentfolio-beacon-bridge/demo.py
pytest  examples/agentfolio-beacon-bridge/test_bridge.py -v
```

The demo shows a genuine link verifying **and a reputation-borrow forgery being
rejected**. The test suite uses real Ed25519 keys and covers the forgery cases
(reputation borrow, mutated payload, non-finite score, expired proof,
outage-as-not-found) that motivated the rewrite.

## Files

| File | Purpose |
|------|---------|
| `bridge.py` | Reference implementation (`BridgeClient`, proofs, scoring, DID export) |
| `test_bridge.py` | Real-crypto unit + forgery-rejection tests |
| `demo.py` | Offline end-to-end walkthrough |
| `SPEC.md` | Integration spec + security considerations |

Bounty: [rustchain-bounties #2890](https://github.com/Scottcjn/rustchain-bounties/issues/2890)
