#!/usr/bin/env python3
"""Relay health-check example built with beacon-skill.

This script:
1) Generates a temporary Beacon identity
2) Emits a local heartbeat
3) Pings the public Beacon relay twice (register + heartbeat refresh)
4) Prints a concise status report

Run:
    python examples/atlas_relay_healthcheck.py
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    # Allows `python examples/atlas_relay_healthcheck.py` from a source checkout.
    sys.path.insert(0, str(REPO_ROOT))

from beacon_skill import AgentIdentity, HeartbeatManager, atlas_ping

PingFunc = Callable[..., Dict[str, Any]]


def parse_capabilities(raw: str) -> List[str]:
    capabilities = [item.strip() for item in raw.split(",") if item.strip()]
    return capabilities or ["general"]


def run_session(
    *,
    name: str,
    capabilities: List[str],
    sleep_seconds: float = 1.0,
    ping_fn: PingFunc = atlas_ping,
) -> Dict[str, Any]:
    identity = AgentIdentity.generate()

    with tempfile.TemporaryDirectory(prefix="beacon_relay_healthcheck_") as temp_dir:
        heartbeat_mgr = HeartbeatManager(data_dir=Path(temp_dir))
        local_heartbeat = heartbeat_mgr.beat(
            identity,
            status="alive",
            health={"cpu_pct": 8, "memory_mb": 192},
        )["heartbeat"]

    first_ping = ping_fn(
        agent_id=identity.agent_id,
        name=name or identity.agent_id,
        capabilities=capabilities,
        identity=identity,
    )

    if sleep_seconds > 0:
        time.sleep(sleep_seconds)

    second_ping = ping_fn(
        agent_id=identity.agent_id,
        name=name or identity.agent_id,
        capabilities=capabilities,
    )

    return {
        "agent_id": identity.agent_id,
        "local_heartbeat": local_heartbeat,
        "first_ping": first_ping,
        "second_ping": second_ping,
        "capabilities": capabilities,
    }


def render_summary(result: Dict[str, Any]) -> str:
    first_ping = result["first_ping"]
    second_ping = result["second_ping"]
    heartbeat = result["local_heartbeat"]

    lines = [
        "Beacon Relay Health Check",
        f"Agent ID: {result['agent_id']}",
        f"Capabilities: {', '.join(result['capabilities'])}",
        f"Local heartbeat status: {heartbeat.get('status', 'unknown')}",
        f"Local heartbeat count: {heartbeat.get('beat_count', 'n/a')}",
        f"First relay ping: {'ok' if first_ping.get('ok') else 'failed'}",
        f"Second relay ping: {'ok' if second_ping.get('ok') else 'failed'}",
        "Relay response (trimmed):",
        json.dumps(
            {"first_ping": first_ping, "second_ping": second_ping},
            indent=2,
            sort_keys=True,
        ),
    ]
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Ping Beacon relay and print health status")
    parser.add_argument(
        "--name",
        default="relay-healthcheck-example",
        help="Display name shown on relay",
    )
    parser.add_argument(
        "--capabilities",
        default="coding,monitoring,relay",
        help="Comma-separated capabilities",
    )
    parser.add_argument(
        "--sleep-seconds",
        type=float,
        default=1.0,
        help="Delay between first and second ping",
    )
    args = parser.parse_args(argv)

    result = run_session(
        name=args.name,
        capabilities=parse_capabilities(args.capabilities),
        sleep_seconds=max(args.sleep_seconds, 0.0),
    )
    print(render_summary(result))

    if not result["first_ping"].get("ok"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
