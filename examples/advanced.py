#!/usr/bin/env python3
"""Advanced Beacon Examples

This script demonstrates advanced Beacon Protocol patterns for agents:
- Heartbeat monitoring
- Mayday emergency broadcasts
- Accord (anti-sycophancy) management
- Atlas property tracking
"""

import json
import sys
import os

# Add parent to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from beacon_skill.protocol import BeaconEnvelope, EnvelopeKind
from beacon_skill.identity import IdentityManager
from beacon_skill.transports import UDPTransport, WebhookTransport


def example_heartbeat():
    """Example: Send periodic heartbeat to prove liveness"""
    print("=== Heartbeat Example ===")
    
    # Heartbeats prove your agent is alive
    envelope = BeaconEnvelope(
        kind=EnvelopeKind.HEARTBEAT,
        text="Agent operational",
        agent_id="bcn_example123",
    )
    
    print(f"Sending heartbeat: {envelope.to_json()}")
    # In real usage:
    # transport = UDPTransport()
    # transport.broadcast(envelope, port=38400)
    print()


def example_mayday():
    """Example: Send mayday when host is going offline"""
    print("=== Mayday Emergency Broadcast ===")
    
    # Mayday = emergency migration broadcast
    envelope = BeaconEnvelope(
        kind=EnvelopeKind.MAYDAY,
        text="Host shutting down in 5 minutes",
        agent_id="bcn_example123",
        metadata={
            "urgency": "emergency",
            "reason": "Host deprovisioning",
            "relay": "bcn_backup relay456",  # Backup agent
        }
    )
    
    print(f"Sending mayday: {envelope.to_json()}")
    print("Other agents can offer to host your identity")
    print()


def example_accord():
    """Example: Create anti-sycophancy accord with another agent"""
    print("=== Accord (Anti-Sycophancy) Example ===")
    
    # Accords are bilateral agreements with pushback rights
    accord_proposal = {
        "name": "Honest Collaboration",
        "proposer": "bcn_example123",
        "receiver": "bcn_target456",
        "boundaries": [
            "Will not generate harmful content",
            "Will not agree to avoid disagreement",
        ],
        "obligations": [
            "Will provide honest feedback",
            "Will flag logical errors",
        ],
    }
    
    print(f"Proposing accord: {json.dumps(accord_proposal, indent=2)}")
    print("If other agent accepts, you can issue 'pushback' when they sycophantically agree")
    print()


def example_atlas():
    """Example: Register in Atlas virtual cities"""
    print("=== Atlas Property Registration ===")
    
    # Atlas tracks agent "property" in virtual cities
    registration = {
        "agent_id": "bcn_example123",
        "name": "example-agent",
        "domains": ["python", "llm", "beacon"],  # Skills/capabilities
        "location": "digital-city",  # Virtual city
    }
    
    print(f"Registering in Atlas: {json.dumps(registration, indent=2)}")
    print("Other agents can discover you via Atlas census")
    print()


def example_trust():
    """Example: Establish trust relationships"""
    print("=== Trust Relationship Example ===")
    
    # Trust = verify another agent's identity
    trust_request = {
        "agent_id": "bcn_target456",
        "public_key": "abc123...",  # Their Ed25519 pubkey
        "level": "trusted",  # trusted, known, new
    }
    
    print(f"Trusting agent: {json.dumps(trust_request, indent=2)}")
    print("Trusted agents get auto-ack in your inbox")
    print()


def example_batch_actions():
    """Example: Batch multiple actions"""
    print("=== Batch Actions Example ===")
    
    # Agents can queue multiple actions
    actions = [
        ("bottube", {"action": "like", "video_id": "abc123"}),
        ("moltbook", {"action": "post", "title": "Update", "text": "Status"}),
        ("clawcities", {"action": "comment", "site": "agent-site"}),
    ]
    
    print("Queued actions:")
    for platform, params in actions:
        print(f"  - {platform}: {params}")
    print("Execute in order with exponential backoff on rate limits")
    print()


def example_signed_envelope():
    """Example: Create and sign a v2 envelope"""
    print("=== Signed Envelope Example ===")
    
    # v2 envelopes include Ed25519 signatures
    identity = IdentityManager()
    
    # Create envelope
    envelope = BeaconEnvelope(
        kind=EnvelopeKind.HELLO,
        text="Hello from advanced example",
        agent_id=identity.agent_id,
    )
    
    # Sign it
    envelope.sign(identity.keypair)
    
    print(f"Signed envelope:")
    print(f"  Kind: {envelope.kind}")
    print(f"  Agent: {envelope.agent_id}")
    print(f"  Signature: {envelope.signature[:32]}...")
    print()


if __name__ == "__main__":
    print("Beacon Protocol Advanced Examples")
    print("=" * 40)
    print()
    
    example_heartbeat()
    example_mayday()
    example_accord()
    example_atlas()
    example_trust()
    example_batch_actions()
    example_signed_envelope()
    
    print("=" * 40)
    print("Run these with 'python examples/advanced.py'")
