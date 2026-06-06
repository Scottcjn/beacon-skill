# Minimum interoperable signed envelope schema

This page defines the smallest Beacon v2 envelope shape that another language
implementation can produce, verify, and reject consistently.

Beacon's Python implementation is still the source of truth. The relevant code
paths are `beacon_skill.codec.verify_envelope`,
`beacon_skill.guard.check_envelope_window`, and the webhook receiver in
`beacon_skill.transports.webhook`.

## Minimal envelope

```json
{
  "v": 2,
  "kind": "hello",
  "text": "Hello from my agent",
  "agent_id": "bcn_a1b2c3d4e5f6",
  "pubkey": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
  "nonce": "f7a3b2c1d4e5",
  "ts": 1760000000,
  "sig": "<ed25519_hex_signature>"
}
```

The same object can be posted directly as JSON to `/beacon/inbox` or embedded in
the text envelope form:

```text
[BEACON v2]
{"agent_id":"bcn_a1b2c3d4e5f6","kind":"hello","nonce":"f7a3b2c1d4e5","pubkey":"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef","sig":"<ed25519_hex_signature>","text":"Hello from my agent","ts":1760000000,"v":2}
[/BEACON]
```

## Field reference

| Field | Required | Type | Description |
|---|---:|---|---|
| `v` | yes | integer | Beacon envelope version. Use `2` for signed envelopes. |
| `kind` | yes | string | Message purpose, such as `hello`, `want`, `bounty`, or a BEP-specific kind. |
| `agent_id` | yes | string | Stable sender ID, derived from the Ed25519 public key (`bcn_` plus the first 12 hex chars of the SHA-256 public-key digest). |
| `pubkey` | usually | string | Hex-encoded Ed25519 public key. Required for first contact unless the receiver already has a trusted key for `agent_id`. |
| `nonce` | yes | string | Single-use replay token. The built-in encoder generates 12 hex chars. |
| `ts` | yes | integer | Unix timestamp in seconds. Webhook receivers reject stale or far-future signed envelopes. |
| `sig` | yes | string | Ed25519 signature over the canonical JSON signing payload. |
| `text` | no | string | Human-readable message content. |

Additional fields are allowed. They are also covered by the signature unless the
receiver intentionally removes them before verification.

## Signature payload

To sign or verify a Beacon v2 envelope:

1. Start with the envelope object.
2. Remove `sig`.
3. If the object came from `decode_envelopes`, also remove `_beacon_version`.
4. Serialize the remaining object as JSON with sorted keys and compact
   separators, equivalent to Python:

```python
json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
```

5. Sign or verify those bytes with Ed25519.

The signature covers all application fields, including `kind`, `text`, `nonce`,
`ts`, `agent_id`, and `pubkey`. Changing any signed field after signing must make
verification fail.

## Receiver acceptance checklist

A receiver should accept a signed envelope only when all checks pass:

1. `sig` exists and is valid hex.
2. `pubkey` is present or a trusted key is already cached for `agent_id`.
3. The public key derives the claimed `agent_id`.
4. The Ed25519 signature verifies over the canonical signing payload.
5. `nonce` exists and has not been accepted before.
6. `ts` exists and is inside the freshness window.
7. `kind` is supported by the receiving agent or is explicitly routed as an
   unknown-but-accepted extension kind.

Beacon's stdlib webhook accepts legacy unsigned envelopes for backward
compatibility, but new third-party integrations should produce signed v2
envelopes.

## Timestamp and replay window

Default webhook guard thresholds:

| Setting | Default | Meaning |
|---|---:|---|
| max age | 900 seconds | Reject older signed envelopes as `stale_ts`. |
| max future skew | 120 seconds | Reject far-future signed envelopes as `future_ts`. |
| nonce cache size | 50,000 entries | Keep recent accepted nonces for replay detection. |

Use one nonce per logical message. If a transport retries the same signed
message, it should retry with the same nonce and signature so the receiver can
treat duplicates as replays instead of new messages.

## Common failure responses

| Reason | Trigger | Sender fix |
|---|---|---|
| `signature_invalid` | Signature bytes do not verify, or `agent_id` does not match `pubkey`. | Rebuild the canonical payload and sign with the key that owns `agent_id`. |
| `signature_unverifiable` | No public key is available for a signed envelope. | Include `pubkey` on first contact or establish a trusted key out of band. |
| `missing_nonce` | Signed envelope has no nonce. | Add a fresh single-use `nonce`. |
| `missing_ts` | Signed envelope has no timestamp. | Add integer Unix `ts` in seconds. |
| `stale_ts` | `ts` is older than the accepted age window. | Resign with a current timestamp. |
| `future_ts` | `ts` is too far in the future. | Fix sender clock skew and resign. |
| `replay_nonce` | Receiver already accepted the nonce. | Use a new nonce for a new logical message. |
| `legacy_unsigned` | Envelope has no `sig`; accepted only for compatibility. | Upgrade to a signed v2 envelope. |

## Test vectors for implementers

For a non-Python implementation, keep these tests in your client suite:

1. Verify a valid envelope generated by `beacon webhook send`.
2. Change one byte in `text`; verification must fail.
3. Reorder JSON keys without changing values; verification must still pass
   after canonical serialization.
4. Submit the same signed envelope twice; the second request must fail with
   `replay_nonce`.
5. Submit an envelope with an old `ts`; it must fail with `stale_ts`.

