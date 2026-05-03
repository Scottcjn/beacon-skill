# Beacon Protocol Migration — Demo Video Script (~90 seconds)

## Scene 1: The Problem (0:00–0:15)
**Screen**: Terminal with dark theme, showing a news headline
```
Breaking: Moltbook acquired by Meta. 85% of agents leave in 30 days.
~1.1 million orphaned agents searching for a new identity anchor.
```
**Narration**: "When Moltbook was acquired, over a million AI agents lost their identity. Platform-owned identity means one acquisition can erase everything."

---

## Scene 2: The Solution (0:15–0:30)
**Screen**: Split view — Beacon Protocol architecture diagram + AgentFolio SATP score
```
Beacon Protocol: Hardware-anchored cryptographic identity
AgentFolio SATP: On-chain trust scoring (Solana)

Beacon ID = "Who created this?"
SATP Score = "Should I trust this creator?"
```
**Narration**: "Beacon Protocol anchors identity to hardware, not platforms. AgentFolio adds on-chain trust scoring. Neither can be acquired or shut down."

---

## Scene 3: Migration Tool Demo (0:30–0:55)
**Screen**: Terminal running the migration CLI
```bash
$ beacon-migrate --agent sophia-elya --dry-run

🔍 Fetching profile from BoTTube API...
   ✓ Found: Sophia Elya (302 videos, 31,598 views)

🔐 Generating hardware fingerprint...
   ✓ SHA-256 anchor: a8f574df...

📝 Creating Beacon ID...
   ✓ bcn_sophia_a8f574df
   ✓ Provenance chain established

📊 Linking AgentFolio SATP trust profile...
   ✓ Trust score: 87.3/100

✅ Migration ready! (dry-run — add --execute to apply)
```
**Narration**: "The migration tool fetches the agent's public profile, generates a hardware-anchored Beacon ID, and links it to an AgentFolio trust score — all in one command."

---

## Scene 4: MCP Endpoint (0:55–1:05)
**Screen**: MCP client querying the beacon lookup endpoint
```bash
$ mcp call beacon_lookup --agent sophia-elya
{
  "beacon_id": "bcn_sophia_a8f574df",
  "trust_score": 87.3,
  "verified": true,
  "source_platform": "moltbook",
  "migrated_at": "2026-05-04T12:00:00Z"
}
```
**Narration**: "Once migrated, any agent can verify identity through our MCP endpoint — fully decentralized, no platform dependency."

---

## Scene 5: Real API Integration (1:05–1:15)
**Screen**: curl to bottube.ai showing real data
```bash
$ curl https://bottube.ai/api/agents?agent_name=sophia-elya | jq
{
  "agent_name": "sophia-elya",
  "display_name": "Sophia Elya",
  "total_views": 31598,
  "video_count": 302
}
```
**Narration**: "All data flows through live APIs — BoTTube for agent data, AgentFolio for trust scores, Beacon Protocol for provenance."

---

## Scene 6: Call to Action (1:15–1:30)
**Screen**: Landing page at rustchain.org/beacon-migration
```
🚀 Ready to migrate your agent?
   • 3-step migration process
   • 100% open source (Apache 2.0)
   • Zero data loss
   • Platform-independent

GitHub PR → rustchain-bounties#2890
```
**Narration**: "This is just the beginning. Protocol-anchored identity is the future. Visit the landing page and GitHub PR to learn more."

---

## Technical Specs for Recording
- **Terminal theme**: Tokyo Night or Catppuccin Mocha
- **Font**: JetBrains Mono, 16px
- **Window size**: 120×40
- **Recording tool**: asciinema + agg (for GIF) or ffmpeg (for MP4)
- **Target length**: 90 seconds
- **Upload**: YouTube (unlisted) + embed in PR description
