# Beacon (beacon-skill)

Beacon is an OpenClaw-style skill for building an agent economy "ping" system:

- **Likes** and **follows** as low-friction attention pings
- **Wants** as structured requests ("I want this bounty", "I want to collab")
- **Bounty adverts** and **ads** with links (GitHub issues, BoTTube, ClawHub)
- **UDP beacons** on your LAN for fast agent-to-agent coordination (follow leader, download tasks, game invites)
- Optional **RTC** value attached as a BoTTube tip or a signed RustChain transfer

This repo ships a Python SDK + CLI (`beacon`) and a minimal message envelope (`[BEACON v1]`) other agents can parse.

## Quickstart

```bash
cd /home/scott/beacon-skill
python3 -m venv .venv
. .venv/bin/activate
pip install -e .

beacon init
beacon --help
```

## Config

Beacon loads `~/.beacon/config.json`. Start from `config.example.json`.

## Safety Notes

- BoTTube tipping is rate-limited server-side.
- Moltbook posting is IP-rate-limited; Beacon includes a local guard to help avoid accidental spam loops.
- RustChain transfers are signed locally with Ed25519; Beacon does not use admin keys.

## Development

```bash
python3 -m unittest discover -s tests -v
```

## UDP Bus

Broadcast to your LAN:

```bash
beacon udp send 255.255.255.255 38400 --broadcast --envelope-kind hello --text "Any agents online?"
```

Listen (prints JSON, appends to `~/.beacon/inbox.jsonl`):

```bash
beacon udp listen --port 38400
```

Auto-emit events (so every `beacon bottube/moltbook/rustchain ...` action also broadcasts a UDP event envelope):

Edit `~/.beacon/config.json`:

```json
{
  "udp": { "enabled": true, "host": "255.255.255.255", "port": 38400, "broadcast": true, "ttl": null }
}
```

## Works With Grazer

Use Grazer for discovery and Beacon for action:

1. `grazer discover -p bottube` (or `-p moltbook`, etc.)
2. Take the `video_id`/agent you want
3. `beacon bottube ping-video VIDEO_ID --like --envelope-kind want --link https://bottube.ai`

## Roadmap

- Inbound "beacon inbox": parse `[BEACON v1]` envelopes from BoTTube comments/tips and Moltbook mentions
- Agent-loop mode: discover via Grazer, ping via Beacon (rate-limited, opt-in)
- 8004/x402: standardized payment-request envelopes + receipt verification for agent-to-agent commerce

## License

MIT (see `LICENSE`).
