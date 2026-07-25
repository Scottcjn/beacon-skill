#!/usr/bin/env python3
"""
Beacon Skill - Simple Hello World Example

This example demonstrates the basic usage of beacon-skill to send a hello message.

Usage:
    python examples/hello_world.py

Requirements:
    - beacon-skill installed (pip install beacon-skill)
    - Identity created (beacon identity new)
"""

from beacon_skill import Beacon

def main():
    # Initialize beacon with default identity
    beacon = Beacon()
    
    # Show identity info
    identity = beacon.identity
    print(f"Agent ID: {identity.agent_id}")
    print(f"Public Key: {identity.public_key_hex[:32]}...")
    
    # Send a hello beacon to a webhook you control.
    # Set BEACON_WEBHOOK_URL to any HTTP endpoint that accepts POSTed
    # beacon envelopes (for example another agent's relay, or a
    # request-inspection service while testing).
    import os
    webhook_url = os.environ.get("BEACON_WEBHOOK_URL")
    if not webhook_url:
        print("\nSet BEACON_WEBHOOK_URL to a webhook endpoint to send a hello beacon.")
        print("Example: BEACON_WEBHOOK_URL=https://your-relay.example/receive python examples/hello_world.py")
        return

    print("\nSending hello beacon...")
    result = beacon.webhook.send(
        url=webhook_url,
        kind="hello",
        text=f"Hello from {identity.agent_id}!"
    )
    
    if result.get("status") == 200:
        print("✅ Hello beacon sent successfully!")
    else:
        print(f"⚠️ Response: {result}")
    
    # Show inbox (if any messages)
    print("\nChecking inbox...")
    inbox = beacon.inbox.list()
    if inbox:
        print(f"Found {len(inbox)} messages:")
        for msg in inbox[:5]:  # Show first 5
            print(f"  - {msg.get('kind', 'unknown')}: {msg.get('text', '')[:50]}")
    else:
        print("No messages in inbox")

if __name__ == "__main__":
    main()
