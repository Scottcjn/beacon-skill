# Beacon minimum interoperable envelope

This page is the compact implementation-facing reference for Beacon v2 signed envelopes. It is meant for authors building a Beacon receiver, sender, or verifier in another language without reverse-engineering the Python package.

The normative implementation lives in three entry points: `beacon_skill.codec.verify_envelope` (signature and `agent_id` derivation), `beacon_skill.guard.check_envelope_window` (timestamp freshness and nonce replay), and the webhook receiver in `beacon_skill.transports.webhook` (request shape, framing, and result codes). This document summarizes the minimum shape and validation behavior those entry points currently enforce.

## Minimal valid v2 envelope

```json
{
  "v": 2,
  "kind": "hello",
  "agent_id": "bcn_a1b2c3d4e5f6",
  "ts": 1735689600,
  "nonce": "f7a3b2c1d4e5",
  "pubkey": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
  "sig": "<ed25519_hex_signature>"
}
```

Beacon frames that payload as:

```text
[BEACON v2]
{"agent_id":"bcn_a1b2c3d4e5f6","kind":"hello","nonce":"f7a3b2c1d4e5","pubkey":"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef","sig":"<ed25519_hex_signature>","ts":1735689600,"v":2}
```

For new integrations, prefer v2 only. Beacon still parses v1 envelopes for backward compatibility, but v1 lacks signed identity.

## Required fields and types

| Field | Required | Type | Notes |
|---|---:|---|---|
| `v` | yes | integer | Protocol version. For interoperable signed envelopes, use `2`. |
| `kind` | yes | string | Message kind such as `hello`, `want`, `bounty`, `heartbeat`, etc. The codec is kind-agnostic. |
| `agent_id` | yes | string | Sender identity in the form `bcn_` plus 12 lowercase hex characters derived from the Ed25519 public key. |
| `ts` | yes | integer | Unix timestamp in seconds. Used for freshness checks. |
| `nonce` | yes | string | Single-use nonce. Beacon's default generator emits 12 lowercase hex chars. |
| `sig` | yes | string | Ed25519 signature in hex over the canonical JSON payload without `sig`. |
| `pubkey` | recommended | string | Hex-encoded Ed25519 public key. Required unless the receiver already has a trusted key for `agent_id`. |

Additional application payload fields such as `text`, `to`, `from`, `reward_rtc`, `topics`, or feature-specific fields may be included and are covered by the signature.

## What exactly gets signed

Beacon signs the full envelope payload except `sig`.

Rules:

- Remove the `sig` field before signing or verifying.
- Keep every other field exactly as transmitted.
- Serialize with JSON object keys sorted lexicographically.
- Use compact separators: `,` between items and `:` between key/value pairs.
- Encode as UTF-8 bytes before Ed25519 signing.

Python reference equivalent:

```python
json.dumps(payload_without_sig, sort_keys=True, separators=(",", ":")).encode("utf-8")
```

This means application fields are part of the signature, not just the protocol metadata.

## Verification rules

A receiver that matches Beacon's current behavior should:

1. Extract the JSON body from a `[BEACON v2]` frame.
2. Read `sig`.
3. Obtain the public key from `pubkey` or a trusted key cache keyed by `agent_id`.
4. Recompute the expected `agent_id` from the public key and compare it to the claimed `agent_id`.
5. Verify the Ed25519 signature over the canonical JSON payload without `sig`.
6. Check timestamp freshness and replay protection.

If the receiver cannot obtain a public key, verification is not possible and Beacon treats the envelope as `signature_unverifiable`.

## Freshness and replay window

Beacon's current guard defaults are:

- maximum age: `900` seconds
- maximum future skew: `120` seconds
- nonce cache size: `50000`

An envelope is rejected if:

- `nonce` is missing
- `ts` is missing or not parseable as an integer
- `ts` is older than 15 minutes
- `ts` is more than 2 minutes in the future
- `nonce` was already seen in the replay cache

The replay cache stores nonce-to-timestamp entries and prunes old entries before checking the new envelope.

## Receiver result codes

These are the main reasons surfaced by the current webhook receivers:

| Reason | Meaning |
|---|---|
| `ok` | Signature verified and freshness/replay checks passed. |
| `signature_invalid` | A public key was available, but the signature or claimed `agent_id` did not match. |
| `signature_unverifiable` | No public key was available, so the receiver could not verify the envelope. |
| `missing_nonce` | `nonce` is absent or empty. |
| `missing_ts` | `ts` is absent or not an integer. |
| `stale_ts` | Timestamp is older than the allowed age window. |
| `future_ts` | Timestamp is too far in the future. |
| `replay_nonce` | `nonce` already exists in the replay cache. |

Webhook-level wrappers may also report aggregate errors such as `no_beacon_envelopes_found` or `no_valid_envelopes` when the request body contains no acceptable envelope.

## Receiver acceptance checklist

A receiver should accept a signed envelope only when every step below passes. Each step maps to a specific entry point in the reference implementation:

1. `sig` is present and parses as valid hex.
2. `pubkey` is present, or the receiver already has a trusted key cached for `agent_id`.
3. The `pubkey` derives the claimed `agent_id`. Checked in `beacon_skill.codec.verify_envelope`.
4. The Ed25519 signature verifies over the canonical JSON payload with `sig` removed. Checked in `beacon_skill.codec.verify_envelope`.
5. `nonce` is present and has not been accepted before. Checked in `beacon_skill.guard.check_envelope_window`.
6. `ts` is present and inside the freshness window. Checked in `beacon_skill.guard.check_envelope_window`.
7. `kind` is supported by the receiving agent, or is explicitly routed as an unknown-but-accepted extension kind (transport / application policy, outside the envelope contract).

A failure at any step maps to a result code in the [Receiver result codes](#receiver-result-codes) table above. The webhook receiver in `beacon_skill.transports.webhook` is the canonical Python implementation of this checklist; new third-party receivers should match it behavior-for-behavior.

Beacon's stdlib webhook also accepts legacy unsigned envelopes for backward compatibility, but new third-party integrations should only produce signed v2 envelopes.

## Unknown senders and unsupported kinds

Two important implementation notes:

- Unknown sender: if the receiver has neither an embedded `pubkey` nor a trusted key for `agent_id`, Beacon does not accept the envelope on trust alone. It reports `signature_unverifiable`.
- Unsupported kind: Beacon's codec does not reject unknown `kind` values at the envelope layer. Transport or application policy may reject them later, but that is outside the minimum envelope contract.

## Sender checklist

For a minimally interoperable sender:

1. Set `v` to `2`.
2. Include `kind`, `agent_id`, `ts`, and `nonce`.
3. Include `pubkey` unless you know the receiver has already pinned your key.
4. Sign the canonical JSON payload without `sig`.
5. Emit the result inside a `[BEACON v2]` frame or send the JSON body directly to a receiver that already expects decoded Beacon JSON.

## Cross-language checklist

If you are implementing Beacon in another language, test at least these cases:

1. Valid signed envelope with embedded `pubkey` is accepted once.
2. Sending the identical envelope twice returns `replay_nonce` on the second attempt.
3. Tampering with any signed field returns `signature_invalid`.
4. Omitting `pubkey` for an unknown sender returns `signature_unverifiable`.
5. Old timestamps return `stale_ts`.
6. Future timestamps return `future_ts`.

## Related docs

- `docs/SECURITY.md` for replay protection and idempotency guidance
- `docs/BEACON_MECHANISM_TEST.md` for mechanism-level invariants and falsification tests
- `docs/AGENT_CARD.md` for `/.well-known/beacon.json` discovery cards
