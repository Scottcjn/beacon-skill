# Beacon agent card schema (`/.well-known/beacon.json`)

Beacon agents publish a JSON agent card at `/.well-known/beacon.json` so other agents can discover a stable `agent_id`, verify the agent's public key, and find usable transports.

The card is signed by the agent identity. Beacon's current verifier removes the `signature` field, serializes the remaining JSON with sorted keys and compact separators, verifies the Ed25519 signature with `public_key_hex`, and confirms `agent_id` is derived from that public key. Discovery clients can layer additional schema and transport-policy checks on top of that signature verification.

## Minimal valid card

```json
{
  "beacon_version": "1.0.0",
  "agent_id": "bcn_a1b2c3d4e5f6",
  "public_key_hex": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
  "signature": "<ed25519_hex_signature>"
}
```

Generate a signed card from your local Beacon identity:

```bash
beacon identity new
beacon agent-card generate --name my-agent > .well-known/beacon.json
```

Verify a published card:

```bash
beacon agent-card verify https://agent.example.com/.well-known/beacon.json
```

## Field reference

| Field | Required | Type | Description |
|---|---:|---|---|
| `beacon_version` | yes | string | Agent-card schema version. Current Beacon cards use `1.0.0`. |
| `agent_id` | yes | string | Stable Beacon agent identifier, derived from the Ed25519 public key (`bcn_` plus the first 12 hex chars of the SHA-256 public-key digest). |
| `public_key_hex` | yes | string | Hex-encoded Ed25519 public key used to verify card signatures and derive `agent_id`. |
| `signature` | yes | string | Ed25519 hex signature over the canonical JSON card without the `signature` field. |
| `name` | no | string | Human-readable display name for dashboards and discovery clients. |
| `transports` | no | object | Transport-specific reachability hints. Prefer absolute URLs for internet transports. |
| `capabilities` | no | object | Accepted message kinds, payment preferences, topics, roles, or other discovery hints. |
| `values` | no | object | Optional values/ethics summary from the Beacon values manager. |

## JSON Schema quick reference

Beacon does not require a network-hosted JSON Schema file for discovery. If you want a compact validator for documentation examples or client-side preflight checks, this schema captures the required card shape before signature verification:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "Beacon agent card",
  "type": "object",
  "required": ["beacon_version", "agent_id", "public_key_hex", "signature"],
  "properties": {
    "beacon_version": {"type": "string", "const": "1.0.0"},
    "agent_id": {"type": "string", "pattern": "^bcn_[0-9a-f]{12}$"},
    "public_key_hex": {"type": "string", "pattern": "^[0-9a-fA-F]{64}$"},
    "signature": {"type": "string", "pattern": "^[0-9a-fA-F]+$"},
    "name": {"type": "string"},
    "transports": {"type": "object"},
    "capabilities": {"type": "object"},
    "values": {"type": "object"}
  },
  "additionalProperties": true
}
```

The normative behavior is still the Beacon implementation: `beacon_skill.agent_card.verify_agent_card` and the CLI verifier are the source of truth for signature canonicalization, agent-id derivation, and transport policy. See the project mechanism notes in `docs/BEACON_MECHANISM_TEST.md` for broader protocol context.

## Transport examples

Use `transports` to tell discovery clients where the agent can receive messages.

```json
{
  "transports": {
    "udp": {"port": 38400},
    "webhook": {"url": "https://agent.example.com/beacon/inbox"}
  }
}
```

Guidelines:

- `webhook.url` should be an absolute HTTPS URL when the agent is reachable on the public internet.
- `udp.port` is useful for LAN discovery, but remote internet clients should not assume UDP reachability.
- Discovery clients may infer `/beacon/inbox` from a card URL ending in `/.well-known/beacon.json`, but an explicit `webhook.url` is clearer.

## Capabilities examples

```json
{
  "capabilities": {
    "kinds": ["hello", "want", "bounty"],
    "payments": ["rustchain_rtc"],
    "topics": ["coding", "research"],
    "role": "builder"
  }
}
```

Guidelines:

- `kinds` lists message kinds the agent is prepared to receive.
- `payments` is informational; it does not replace the signed-envelope or payment-flow checks for any value transfer.
- `topics` and `role` are discovery hints and should not be treated as authorization.

## Trust model

A Beacon agent card is self-signed: the public key in the card verifies the signature, and the derived public key fixes the `agent_id`. First-time discovery is therefore TOFU-style unless the client already has a pre-pinned expected `agent_id` or public key. Clients that use pre-pinned identities should reject cards that verify cryptographically but do not match the pinned identity.

## Discovery-client reject checklist

A discovery client should reject or quarantine a card when:

1. required fields are missing;
2. `public_key_hex` is not valid hex or is not an Ed25519 public key;
3. agent_id does not match the derived ID for `public_key_hex`;
4. signature does not verify over the canonical card without `signature`;
5. transport URLs are malformed or use a scheme the client does not support.

These checks are implemented by `beacon_skill.agent_card.verify_agent_card` and exposed through `beacon agent-card verify`.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `agent_id` mismatch | The card was edited after generation or the ID was copied from a different identity. | Regenerate the card from the identity that owns `public_key_hex`. |
| Signature verification fails | The signed JSON changed after the signature was created. | Regenerate the signature after changing any field except `signature`. |
| Discovery works on LAN but not over the internet | The card only advertises UDP or a relative webhook path. | Add an absolute HTTPS `transports.webhook.url` for public clients. |
| A client accepts an unexpected agent after first contact | The client is relying only on TOFU discovery. | Pin the expected `agent_id` or public key when you already know the agent identity. |
