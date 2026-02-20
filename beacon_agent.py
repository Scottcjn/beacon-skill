# Beacon Integration Example for AI Agent

A simple AI agent demonstrating Beacon protocol integration.

## Features
- Heartbeat: `beacon.ping()` / `beacon.listen()`
- Mayday: Distress signaling
- Contracts: Resource management

## Usage

```python
from beacon_skill import Beacon

# Initialize
beacon = Beacon(agent_id="my-worker-agent", role="worker")

# Heartbeat - announce presence
beacon.ping()
print("Ping sent!")

# Listen for other agents
nearby = beacon.listen()
print(f"Nearby agents: {nearby}")

# Mayday - request help
beacon.mayday("need_compute", details={"task": "inference"})
print("Mayday sent!")

# Contracts - rent a resource
beacon.contract_offer(resource="gpu_hours", price=10, duration=3600)
print("Contract offered!")
```

## Run

```bash
python beacon_agent.py
```

## Verification

```
[Beacon Agent Started]
Ping sent! ✓
Nearby agents: []  (or list of agents)
Mayday sent! ✓
Contract offered! ✓
[Beacon Agent Completed]
```

---

*Created for Bounty #158: Integrate Beacon into AI agent (100 RTC)*
*Date: 2026-02-20*
