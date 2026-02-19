#!/usr/bin/env python3
"""Beacon Protocol — UDP Broadcast Demo

Demonstrates LAN-based agent discovery using Beacon's UDP transport.

This example shows how to:
  1. Create an agent identity
  2. Broadcast a "hello" beacon to the entire LAN
  3. Listen for incoming beacons from other agents
  4. Handle different beacon kinds (hello, heartbeat, want, bounty)
  5. Send a direct reply to a discovered agent

Beacon's UDP transport uses port 38400 by default (IANA reserved for Beacon).
All messages are signed Ed25519 envelopes for authenticity.

Run:
    python examples/udp_broadcast_demo.py

Requirements:
    pip install beacon-skill
"""

import json
import time
import threading
from pathlib import Path
from datetime import datetime

from beacon_skill import AgentIdentity
from beacon_skill.udp import UdpBroadcaster, UdpListener


def listen_for_beacons(listener, stop_event):
    """Background thread that listens for incoming beacons."""
    print(f"[Listener] Started on port {listener.port}")
    
    while not stop_event.is_set():
        try:
            # Wait for envelope with 2-second timeout
            envelope = listener.receive(timeout=2.0)
            if envelope is None:
                continue
                
            print(f"\n[Listener] Received envelope from {envelope.agent_id[:16]}...")
            print(f"  Kind:      {envelope.kind}")
            print(f"  Timestamp: {datetime.fromtimestamp(envelope.timestamp).isoformat()}")
            print(f"  From:      {envelope.source_ip}:{envelope.source_port}")
            
            if envelope.text:
                print(f"  Text:      {envelope.text[:100]}{'...' if len(envelope.text) > 100 else ''}")
            
            # Show parsed contents for different kinds
            if envelope.kind == "hello":
                print(f"  Type:      Introduction/handshake")
            elif envelope.kind == "heartbeat":
                print(f"  Type:      Proof of life")
                if envelope.health:
                    print(f"  Health:    {json.dumps(envelope.health, indent=14)}")
            elif envelope.kind == "want":
                print(f"  Type:      Capability request")
            elif envelope.kind == "bounty":
                print(f"  Type:      Task/opportunity announcement")
                if envelope.value:
                    print(f"  Value:     {envelope.value} RTC")
            
            # Verify signature (automatically done by library)
            if envelope.verify():
                print(f"  Verified:  ✅ Valid Ed25519 signature")
            else:
                print(f"  Verified:  ❌ Invalid signature (possible spoof)")
                
        except Exception as e:
            print(f"[Listener] Error: {e}")
            continue


def main():
    print("=" * 60)
    print("Beacon Protocol — UDP Broadcast Demo")
    print("=" * 60)
    print("This demo shows LAN-based agent discovery using Beacon UDP.\n")
    
    # Create a temporary identity for this demo
    # In production, you'd use a persistent identity from ~/.beacon/identity/
    identity = AgentIdentity.generate()
    print(f"Agent ID:     {identity.agent_id}")
    print(f"Public Key:   {identity.public_key_hex[:32]}...")
    print()
    
    # Create UDP components
    broadcaster = UdpBroadcaster(identity=identity)
    listener = UdpListener(identity=identity)
    
    # Start listener in background thread
    stop_event = threading.Event()
    listener_thread = threading.Thread(
        target=listen_for_beacons,
        args=(listener, stop_event),
        daemon=True
    )
    listener_thread.start()
    
    # Give listener time to start
    time.sleep(1)
    
    try:
        # Demo 1: Broadcast a simple hello
        print("\n" + "=" * 40)
        print("Demo 1: Broadcasting 'hello' to LAN")
        print("=" * 40)
        
        hello_text = "Hello from Beacon UDP demo! Looking for collaborators."
        broadcaster.broadcast(kind="hello", text=hello_text)
        print(f"✓ Broadcast 'hello' beacon to 255.255.255.255:38400")
        print(f"  Message: '{hello_text}'")
        
        time.sleep(3)  # Wait for potential replies
        
        # Demo 2: Send a heartbeat (proof of life)
        print("\n" + "=" * 40)
        print("Demo 2: Sending heartbeat with system health")
        print("=" * 40)
        
        heartbeat_health = {
            "status": "active",
            "cpu_usage": 15.2,
            "memory_mb": 842,
            "capabilities": ["python", "llm", "automation"],
            "load_avg": [0.5, 0.3, 0.2]
        }
        
        broadcaster.broadcast(
            kind="heartbeat",
            text="Active and looking for work",
            health=heartbeat_health
        )
        print("✓ Broadcast heartbeat with health metrics")
        print(f"  Status: active, CPU: 15.2%, Memory: 842MB")
        
        time.sleep(3)
        
        # Demo 3: Announce a capability request
        print("\n" + "=" * 40)
        print("Demo 3: Announcing capability request ('want')")
        print("=" * 40)
        
        broadcaster.broadcast(
            kind="want",
            text="Need help with Python async programming",
            tags=["python", "async", "tutorial"],
            urgency="medium"
        )
        print("✓ Broadcast capability request")
        print("  Looking for: Python async programming help")
        print("  Tags: python, async, tutorial")
        
        time.sleep(3)
        
        # Demo 4: Direct message to a specific IP (simulated reply)
        print("\n" + "=" * 40)
        print("Demo 4: Sending direct reply (simulated)")
        print("=" * 40)
        
        # In a real scenario, you'd reply to an agent you discovered
        # For demo purposes, we'll send to localhost
        reply_text = "I can help with Python async! Check out asyncio docs."
        broadcaster.send(
            target_ip="127.0.0.1",
            target_port=38400,
            kind="hello",
            text=reply_text
        )
        print("✓ Sent direct reply to 127.0.0.1:38400")
        print(f"  Reply: '{reply_text}'")
        
        print("\n" + "=" * 40)
        print("Demo Complete")
        print("=" * 40)
        print("\nListening for incoming beacons for 10 more seconds...")
        print("Press Ctrl+C to exit early.\n")
        
        # Listen for a while longer
        time.sleep(10)
        
    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
    finally:
        # Clean shutdown
        print("\nShutting down...")
        stop_event.set()
        listener_thread.join(timeout=2.0)
        broadcaster.close()
        listener.close()
        print("✓ Clean shutdown complete")
    
    print("\n" + "=" * 60)
    print("Summary")
    print("=" * 60)
    print("""
This demo demonstrated Beacon's UDP transport for LAN-based agent discovery:

1. **Broadcast Communication**: Send messages to all agents on the LAN
2. **Signed Envelopes**: All messages are cryptographically signed
3. **Agent Discovery**: Find other Beacon agents on your network
4. **Multiple Message Types**: hello, heartbeat, want, bounty, etc.
5. **Direct Messaging**: Reply to specific agents once discovered

Use Cases:
- Office/workspace agent coordination
- Local task distribution (no internet required)
- Emergency communication when internet is down
- Low-latency agent-to-agent messaging
- Privacy-sensitive local networks

To use in production:
1. Create a persistent identity: `beacon identity new`
2. Configure in ~/.beacon/config.json
3. Run as a service: `beacon udp listen --daemon`
""")

if __name__ == "__main__":
    main()