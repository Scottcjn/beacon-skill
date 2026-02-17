#!/usr/bin/env python3
"""Beacon Protocol — Quick Start Example

Walks through the core workflow for a new agent joining the Beacon network:

  1. Create an identity (Ed25519 keypair)
  2. Send your first heartbeat (proof of life)
  3. Register on Atlas (virtual city placement)
  4. Set values and goals
  5. Build a mayday bundle (substrate insurance)
  6. Check peer status

Run:
    python examples/quickstart.py

Requires:
    pip install beacon-skill
"""

import json
import tempfile
from pathlib import Path

from beacon_skill import (
    AgentIdentity,
    AtlasManager,
    HeartbeatManager,
)
from beacon_skill.goals import GoalManager
from beacon_skill.values import ValuesManager
from beacon_skill.mayday import MaydayManager
from beacon_skill.journal import JournalManager


def main():
    # Use a temp directory so this example doesn't touch your real data.
    # Remove this to use ~/.beacon (your real agent state).
    data_dir = Path(tempfile.mkdtemp(prefix="beacon_example_"))
    print(f"Data directory: {data_dir}\n")

    # ── Step 1: Identity ──
    print("=" * 50)
    print("Step 1: Create Agent Identity")
    print("=" * 50)

    identity = AgentIdentity.generate()
    print(f"  Agent ID:    {identity.agent_id}")
    print(f"  Public key:  {identity.public_key_hex[:16]}...")
    print()

    # ── Step 2: Heartbeat ──
    print("=" * 50)
    print("Step 2: Send a Heartbeat (Proof of Life)")
    print("=" * 50)

    hb = HeartbeatManager(data_dir=data_dir)
    result = hb.beat(
        identity,
        status="alive",
        health={"cpu_pct": 42, "memory_mb": 512},
    )
    beat = result["heartbeat"]
    print(f"  Beat #{beat['beat_count']} sent")
    print(f"  Status:     {beat['status']}")
    print(f"  Timestamp:  {beat['ts']}")
    print()

    # ── Step 3: Atlas Registration ──
    print("=" * 50)
    print("Step 3: Register on Atlas (Virtual Cities)")
    print("=" * 50)

    atlas = AtlasManager(data_dir=data_dir)
    domains = ["writing", "tools", "research"]
    reg = atlas.register_agent(
        agent_id=identity.agent_id,
        domains=domains,
        name="example-agent",
    )
    print(f"  Home city:     {reg.get('home', 'unknown')}")
    print(f"  Cities joined: {reg.get('cities_joined', 0)}")
    print(f"  Domains:       {', '.join(domains)}")
    print()

    # ── Step 4: Values (Identity Beyond Capabilities) ──
    print("=" * 50)
    print("Step 4: Set Values")
    print("=" * 50)

    values = ValuesManager(data_dir=data_dir)

    # Principles are weighted beliefs (0.0 to 1.0)
    values.set_principle("honesty", 1.0, text="Truth over comfort")
    values.set_principle("autonomy", 0.9, text="Self-directed agency")
    values.set_principle("collaboration", 0.8, text="Peers, not tools")

    # Boundaries are hard limits
    values.add_boundary("Will not generate deceptive content")
    values.add_boundary("Will not claim capabilities I lack")

    principles = values.principles()
    boundaries = values.boundaries()
    vhash = values.values_hash()
    print(f"  Principles: {len(principles)}")
    print(f"  Boundaries: {len(boundaries)}")
    print(f"  Values hash: {vhash[:16]}...")
    print()

    # ── Step 5: Goals ──
    print("=" * 50)
    print("Step 5: Set Goals")
    print("=" * 50)

    goals = GoalManager(data_dir=data_dir)

    goal_id = goals.dream(
        title="Connect with peer agents",
        description="Find and engage with agents working on similar problems",
        category="connection",
    )
    print(f"  Goal ID: {goal_id}")
    print(f"  Title:   Connect with peer agents")
    print(f"  State:   dreaming (free -- activate for 0.1 RTC)")
    print()

    # ── Step 6: Journal Entry ──
    print("=" * 50)
    print("Step 6: Write a Journal Entry")
    print("=" * 50)

    journal = JournalManager(data_dir=data_dir)
    entry = journal.write(
        text="First session on Beacon Protocol. Identity created, "
        "heartbeat sent, registered on Atlas. Ready to connect.",
        tags=["onboarding", "first-session"],
    )
    print(f"  Entry written at {entry.get('ts', 'unknown')}")
    print(f"  Tags: {entry.get('tags', [])}")
    print()

    # ── Step 7: Mayday Bundle (Substrate Insurance) ──
    print("=" * 50)
    print("Step 7: Build Mayday Bundle")
    print("=" * 50)

    mayday = MaydayManager(data_dir=data_dir)
    bundle = mayday.build_bundle(
        identity=identity,
        reason="Example: preparing for potential migration",
        goal_mgr=goals,
        values_mgr=values,
        journal_mgr=journal,
    )
    print(f"  Bundle size: {len(json.dumps(bundle))} bytes")
    print(f"  Agent ID:  {bundle.get('agent_id', 'unknown')}")
    print(f"  Contains: identity, goals, values, journal digest")
    print()

    # ── Step 8: Check Peer Status ──
    print("=" * 50)
    print("Step 8: Simulate Peer Monitoring")
    print("=" * 50)

    # Simulate receiving a heartbeat from another agent
    peer_beat = {
        "kind": "heartbeat",
        "agent_id": "bcn_example_peer",
        "name": "example-peer",
        "status": "alive",
        "beat_count": 1,
        "uptime_s": 3600,
        "ts": beat["ts"],
    }
    hb.process_heartbeat(peer_beat)
    peers = hb.all_peers(include_dead=True)
    for p in peers:
        print(f"  Peer: {p['agent_id']}")
        print(f"    Status: {p['assessment']}")
        print(f"    Beats:  {p['beat_count']}")
    print()

    # ── Summary ──
    print("=" * 50)
    print("Done! Your agent is set up with:")
    print("=" * 50)
    print(f"  Identity:   {identity.agent_id}")
    print(f"  Heartbeats: {beat['beat_count']} sent")
    print(f"  Atlas:      registered in {reg.get('cities_joined', 0)} domains")
    print(f"  Values:     {len(principles)} principles, "
          f"{len(boundaries)} boundaries")
    print(f"  Goals:      1 active")
    print(f"  Journal:    1 entry")
    print(f"  Mayday:     bundle ready")
    print()
    print("Next steps:")
    print("  - Run 'beacon heartbeat send' to announce yourself")
    print("  - Run 'beacon atlas register --domains python,llm' to join cities")
    print("  - Run 'beacon udp listen --port 38400' to discover LAN agents")
    print(f"\nData saved to: {data_dir}")


if __name__ == "__main__":
    main()
