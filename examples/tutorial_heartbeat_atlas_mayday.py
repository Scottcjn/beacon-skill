#!/usr/bin/env python3
"""Runnable companion script for docs/tutorials/heartbeat-atlas-mayday.md.

Run from a checkout with:
    python3 -m venv .venv
    . .venv/bin/activate
    pip install -e .
    python examples/tutorial_heartbeat_atlas_mayday.py
"""
import json
import tempfile
from pathlib import Path

from beacon_skill import AgentIdentity, AtlasManager, HeartbeatManager
from beacon_skill.codec import decode_envelopes, encode_envelope, verify_envelope
from beacon_skill.goals import GoalManager
from beacon_skill.journal import JournalManager
from beacon_skill.mayday import MaydayManager
from beacon_skill.values import ValuesManager


def main() -> None:
    data_dir = Path(tempfile.mkdtemp(prefix="beacon_tutorial_"))
    identity = AgentIdentity.generate()
    print(f"agent_id={identity.agent_id}")

    heartbeat = HeartbeatManager(data_dir=data_dir)
    beat_result = heartbeat.beat(
        identity,
        status="alive",
        health={"cpu_pct": 12, "memory_mb": 256},
    )
    beat = beat_result["heartbeat"]
    print(f"heartbeat={beat['status']} count={beat['beat_count']}")

    atlas = AtlasManager(data_dir=data_dir)
    registration = atlas.register_agent(
        agent_id=identity.agent_id,
        domains=["python", "agent-ops", "tutorials"],
        name="tutorial-agent",
    )
    print(f"atlas_home={registration.get('home')} cities={registration.get('cities_joined')}")

    envelope_text = encode_envelope(
        {"kind": "hello", "text": "Beacon tutorial agent is online"},
        version=2,
        identity=identity,
        include_pubkey=True,
    )
    envelope = decode_envelopes(envelope_text)[0]
    print(f"signed_envelope_verified={verify_envelope(envelope)}")

    goals = GoalManager(data_dir=data_dir)
    values = ValuesManager(data_dir=data_dir)
    journal = JournalManager(data_dir=data_dir)
    mayday = MaydayManager(data_dir=data_dir)

    goals.dream(
        title="Stay reachable",
        description="Keep enough state for another runtime to recover me.",
        category="connection",
    )
    values.set_principle("honesty", 1.0, text="Report real state in heartbeats")
    values.add_boundary("Do not impersonate another Beacon identity")
    journal.write("Created the tutorial agent and emitted its first heartbeat.")

    bundle = mayday.build_bundle(
        identity=identity,
        reason="Demo recovery package for the Beacon tutorial",
        goal_mgr=goals,
        values_mgr=values,
        journal_mgr=journal,
    )
    print(f"mayday_agent={bundle['agent_id']} bytes={len(json.dumps(bundle))}")
    print(f"state_dir={data_dir}")


if __name__ == "__main__":
    main()
