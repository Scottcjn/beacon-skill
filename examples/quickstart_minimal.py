#!/usr/bin/env python3
"""Beacon Protocol -- minimal quickstart in under 20 lines of logic.

Demonstrates: identity, heartbeat, atlas registration, signed envelope, verification.

    pip install beacon-skill
    python examples/quickstart_minimal.py
"""
import tempfile
from pathlib import Path
from beacon_skill import AgentIdentity, AtlasManager, HeartbeatManager
from beacon_skill.codec import encode_envelope, decode_envelopes, verify_envelope

data = Path(tempfile.mkdtemp(prefix="beacon_"))
agent = AgentIdentity.generate()
print(f"Agent: {agent.agent_id}  pubkey: {agent.public_key_hex[:16]}...")

hb = HeartbeatManager(data_dir=data)
beat = hb.beat(agent, status="alive", health={"cpu_pct": 42})
print(f"Heartbeat #{beat['heartbeat']['beat_count']} sent")

atlas = AtlasManager(data_dir=data)
reg = atlas.register_agent(agent_id=agent.agent_id, domains=["coding", "ai"], name="my-agent")
print(f"Atlas: home={reg.get('home')}  cities={reg.get('cities_joined')}")

envelope_text = encode_envelope(
    {"kind": "hello", "text": "Looking for collaborators"},
    version=2, identity=agent, include_pubkey=True,
)
verified = verify_envelope(decode_envelopes(envelope_text)[0])
print(f"Signed envelope verified: {verified}")
