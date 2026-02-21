#!/usr/bin/env python3
"""Beacon Protocol — UDP Broadcast Demo

Demonstrates LAN-based agent discovery using Beacon's UDP transport.

This example shows how to:
  1. Create an agent identity (Ed25519 keypair)
  2. Broadcast a signed beacon to the entire LAN
  3. Listen for incoming beacons from other agents
  4. Handle and verify incoming messages
  5. Send a direct reply to a discovered agent

Beacon's UDP transport uses port 38400 by default.
All messages are signed Ed25519 envelopes for authenticity.

Run:
    python examples/udp_broadcast_demo.py

Requirements:
    pip install beacon-skill

Based on PR #13 by @BetsyMalthus — concept and structure.
"""

import json
import time
import threading
from datetime import datetime

from beacon_skill.identity import AgentIdentity
from beacon_skill.codec import encode_envelope, ENVELOPE_KINDS
from beacon_skill.transports.udp import udp_send, udp_listen, UDPMessage

# Default Beacon UDP port
BEACON_PORT = 38400


def listen_for_beacons(identity, stop_event):
    """Background thread that listens for incoming beacons."""
    print(f"[Listener] Waiting for beacons on port {BEACON_PORT}...")

    def on_message(msg: UDPMessage):
        if stop_event.is_set():
            return
        print(f"\n[Received] From {msg.addr[0]}:{msg.addr[1]}")
        print(f"  Time:     {datetime.fromtimestamp(msg.received_at).isoformat()}")
        if msg.verified is not None:
            print(f"  Verified: {'valid' if msg.verified else 'INVALID signature'}")
        if msg.text:
            # Try to parse as JSON envelope
            try:
                data = json.loads(msg.text.split("]", 1)[-1]) if "[BEACON" in msg.text else json.loads(msg.text)
                kind = data.get("kind", "unknown")
                print(f"  Kind:     {kind}")
                if "text" in data:
                    print(f"  Text:     {data['text'][:100]}")
                if "health" in data:
                    print(f"  Health:   {json.dumps(data['health'])}")
            except (json.JSONDecodeError, IndexError):
                print(f"  Raw:      {msg.text[:200]}")

    try:
        udp_listen(
            bind_host="0.0.0.0",
            port=BEACON_PORT,
            on_message=on_message,
            timeout_s=30.0,
        )
    except OSError as e:
        if not stop_event.is_set():
            print(f"[Listener] Could not bind port {BEACON_PORT}: {e}")
            print(f"[Listener] Try: sudo python examples/udp_broadcast_demo.py")


def broadcast_envelope(identity, kind, payload):
    """Encode and broadcast a signed Beacon envelope."""
    payload["kind"] = kind
    payload["agent_id"] = identity.agent_id
    payload["timestamp"] = int(time.time())
    envelope = encode_envelope(payload, identity=identity)
    udp_send(
        host="255.255.255.255",
        port=BEACON_PORT,
        payload=envelope.encode("utf-8"),
        broadcast=True,
        identity=identity,
    )
    return envelope


def send_direct(identity, target_ip, kind, payload):
    """Send a signed envelope directly to a specific agent."""
    payload["kind"] = kind
    payload["agent_id"] = identity.agent_id
    payload["timestamp"] = int(time.time())
    envelope = encode_envelope(payload, identity=identity)
    udp_send(
        host=target_ip,
        port=BEACON_PORT,
        payload=envelope.encode("utf-8"),
        identity=identity,
    )
    return envelope


def main():
    print("=" * 60)
    print("Beacon Protocol — UDP Broadcast Demo")
    print("=" * 60)
    print("LAN-based agent discovery using signed UDP envelopes.\n")

    # Create a temporary identity for this demo
    identity = AgentIdentity.generate()
    print(f"Agent ID:     {identity.agent_id}")
    print(f"Public Key:   {identity.public_key_hex[:32]}...")
    print()

    # Start listener in background thread
    stop_event = threading.Event()
    listener_thread = threading.Thread(
        target=listen_for_beacons,
        args=(identity, stop_event),
        daemon=True,
    )
    listener_thread.start()
    time.sleep(0.5)

    try:
        # --- Demo 1: Broadcast hello ---
        print("-" * 40)
        print("Demo 1: Broadcasting 'hello' to LAN")
        print("-" * 40)
        broadcast_envelope(identity, "heartbeat", {
            "text": "Hello from Beacon UDP demo! Looking for collaborators.",
        })
        print(f"  Sent heartbeat broadcast to 255.255.255.255:{BEACON_PORT}")
        time.sleep(2)

        # --- Demo 2: Heartbeat with health data ---
        print("\n" + "-" * 40)
        print("Demo 2: Heartbeat with system health")
        print("-" * 40)
        broadcast_envelope(identity, "heartbeat", {
            "text": "Active and looking for work",
            "health": {
                "status": "active",
                "cpu_usage": 15.2,
                "memory_mb": 842,
                "capabilities": ["python", "llm", "automation"],
            },
        })
        print("  Sent heartbeat with health metrics")
        time.sleep(2)

        # --- Demo 3: Direct reply to localhost (simulated) ---
        print("\n" + "-" * 40)
        print("Demo 3: Direct reply to 127.0.0.1")
        print("-" * 40)
        send_direct(identity, "127.0.0.1", "heartbeat", {
            "text": "I can help with Python async!",
        })
        print("  Sent direct message to 127.0.0.1")

        print("\n" + "-" * 40)
        print("Listening for incoming beacons (10s)...")
        print("Press Ctrl+C to exit early.")
        print("-" * 40)
        time.sleep(10)

    except KeyboardInterrupt:
        print("\nInterrupted by user")
    finally:
        stop_event.set()
        listener_thread.join(timeout=2.0)

    print("\n" + "=" * 60)
    print("Summary")
    print("=" * 60)
    print("""
This demo showed Beacon's UDP transport for LAN agent discovery:

  1. Broadcast: Send signed envelopes to all agents on the LAN
  2. Ed25519 Signing: All messages are cryptographically signed
  3. Agent Discovery: Find other Beacon agents on your network
  4. Direct Messaging: Reply to specific agents by IP
  5. Envelope Kinds: heartbeat, accord_offer, relay_register, etc.

Use cases:
  - Office/workspace agent coordination
  - Local task distribution (no internet required)
  - Low-latency agent-to-agent messaging
  - Privacy-sensitive local networks

Production setup:
  1. Create a persistent identity: beacon identity new
  2. Configure in ~/.beacon/config.json
  3. Run as a service: beacon udp listen --daemon
""")


if __name__ == "__main__":
    main()
