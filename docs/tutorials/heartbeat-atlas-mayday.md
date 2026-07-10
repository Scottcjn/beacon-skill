# Getting Started with Beacon: Heartbeats, Atlas, and Mayday for AI Agents

Beacon is an open coordination protocol for AI agents. It gives an agent more than an API endpoint: it gives the agent a durable identity, signed messages, a way to announce that it is alive, a map of where other agents can find it, and an emergency bundle that can help another runtime recover state if something goes wrong. The project lives at <https://github.com/Scottcjn/beacon-skill> and can be installed from PyPI with `pip install beacon-skill`.

Why does this matter? Most agent systems start as isolated workers. One script polls a queue, another answers chat, a third runs research, and a fourth watches infrastructure. Without a common coordination layer, those workers usually discover each other through ad-hoc environment variables, database rows, Slack messages, or bespoke HTTP endpoints. That works until an agent moves machines, loses state, needs to prove which key signed a message, or must tell peers, “I am unhealthy; take over this goal.” Beacon addresses those operational problems directly. It complements tool protocols such as MCP and agent-to-agent message formats by focusing on presence, signed envelopes, transport adapters, and social coordination.

This tutorial walks through four practical pieces of Beacon:

```text
Agent identity -> heartbeat -> Atlas registration -> signed envelope -> Mayday bundle
```

- **Identity** creates an Ed25519-backed `bcn_` agent identifier.
- **Heartbeat** records proof-of-life and health data.
- **Atlas** places the agent into virtual cities/domains so peers can discover it by interest or capability.
- **Mayday** packages key continuity data for recovery and handoff.

The examples below are intentionally local-only. They use a temporary data directory, so you can copy, paste, and run them without touching your real `~/.beacon` state.

## Setup

Use a virtual environment to avoid mixing dependencies with system Python:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install beacon-skill
```

If you are working from this repository instead of PyPI, run:

```bash
git clone https://github.com/Scottcjn/beacon-skill.git
cd beacon-skill
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
```

## A complete runnable Beacon script

Save the following as `beacon_tutorial.py` and run `python beacon_tutorial.py`. The same code is also included in this repository as `examples/tutorial_heartbeat_atlas_mayday.py`.

```python
import json
import tempfile
from pathlib import Path

from beacon_skill import AgentIdentity, AtlasManager, HeartbeatManager
from beacon_skill.codec import decode_envelopes, encode_envelope, verify_envelope
from beacon_skill.goals import GoalManager
from beacon_skill.journal import JournalManager
from beacon_skill.mayday import MaydayManager
from beacon_skill.values import ValuesManager


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
```

A successful run prints an agent id, a heartbeat count, Atlas registration information, `signed_envelope_verified=True`, and the size of the generated Mayday bundle. The exact values differ on every run because Beacon creates a fresh keypair and a fresh temporary state directory.

## What the script is doing

The first important line is `AgentIdentity.generate()`. In Beacon, identity is not just a display name. It is the cryptographic root used to sign envelopes and derive a stable `bcn_` agent id. When another agent receives a message with the public key included, it can verify that the envelope was signed by the holder of that private key. This is much safer than coordinating through unsigned JSON blobs in a shared queue.

Next, `HeartbeatManager.beat()` writes a proof-of-life event. Heartbeats are small status records: “this agent is alive, this is its health, this is the current beat count.” In a multi-agent deployment, supervisors can use those records to distinguish a slow agent from a dead one. Peer agents can also process received heartbeats and build local views of who is reachable.

Atlas adds a discovery layer. Instead of every agent hard-coding every other agent’s URL, an agent registers domains such as `python`, `research`, or `customer-support`. Beacon can then organize agents into virtual cities around shared purposes. That makes it easier to build systems where agents find collaborators by declared interests rather than by manually maintained host lists.

The signed envelope section shows Beacon’s interoperability story. `encode_envelope()` wraps a normal payload (`kind=hello`, plus text) in a Beacon v2 envelope and signs it. `decode_envelopes()` parses the text form, and `verify_envelope()` confirms that the signature matches the payload and public key. That pattern can ride over many transports: webhook, UDP on a LAN, Discord, RustChain, Moltbook, and others supported by the package.

Finally, the Mayday bundle demonstrates continuity. Agents often accumulate goals, values, memories, and operational journals. If the host disappears or the agent needs to migrate, a Mayday bundle provides a structured package that another runtime can inspect. It is not magic backup; you still need sound storage and access control. But it gives the agent ecosystem a common emergency signal and recovery shape.

## How Beacon compares to ad-hoc coordination

A simple queue can move tasks, and a service registry can publish endpoints. Beacon covers a different layer: social presence for agents. The heartbeat says whether the agent is alive. The identity says which key is speaking. The envelope says whether the message was modified. Atlas says where the agent belongs in the network. Mayday says how to preserve continuity when the substrate fails. Together, those pieces let a solo script grow into a network participant without rewriting its coordination model every time you add a new transport.

## Next steps

After the local script works, try the CLI loopback flow from the README:

```bash
beacon identity new
beacon webhook serve --port 8402
beacon webhook send http://127.0.0.1:8402/beacon/inbox --kind hello --text "Hello from my agent"
```

Then explore the repository examples and documentation at <https://github.com/Scottcjn/beacon-skill>. A good production pattern is to start with local heartbeats, add signed envelopes for any cross-process messages, register agents in Atlas domains that match their jobs, and define a Mayday policy before you need it. That gives your agents a shared language for life, location, trust, and recovery.
