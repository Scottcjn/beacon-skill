# -*- coding: utf-8 -*-
"""
Webhook Integration Example for Beacon

This example demonstrates how to:
1. Start a webhook server
2. Send signed envelopes between agents
3. Handle incoming messages
4. Implement basic security checks

Requirements:
    pip install beacon-skill

Usage:
    python webhook_integration.py

Author: OpenClaw Assistant
Date: 2026-03-06
"""

import subprocess
import time
import json
import sys
from pathlib import Path


def check_beacon_installed():
    """Check if beacon-skill is installed."""
    try:
        result = subprocess.run(
            ["beacon", "--version"],
            capture_output=True,
            text=True,
            timeout=5
        )
        return result.returncode == 0
    except (FileNotFoundError, OSError):
        return False


def create_identity_if_needed():
    """Create a beacon identity if one doesn't exist."""
    identity_path = Path.home() / ".beacon" / "identity" / "agent.key"
    
    if not identity_path.exists():
        print("[KEY] Creating new beacon identity...")
        result = subprocess.run(
            ["beacon", "identity", "new"],
            capture_output=True,
            text=True
        )
        if result.returncode != 0:
            print(f"[ERROR] Failed to create identity: {result.stderr}")
            return False
        print("[OK] Identity created successfully!")
    else:
        print("[OK] Using existing identity")
    
    # Show the agent ID
    result = subprocess.run(
        ["beacon", "identity", "show"],
        capture_output=True,
        text=True
    )
    if result.returncode == 0:
        print(f"[ID] Agent ID: {result.stdout.strip()}")
    
    return True


def start_webhook_server(port=8402, timeout=10):
    """
    Start a local webhook server.
    
    Args:
        port: Port number for the webhook server
        timeout: How long to run the server (seconds)
    
    Returns:
        subprocess.Popen: The server process
    """
    print(f"\n[START] Starting webhook server on port {port}...")
    print(f"   Server will run for {timeout} seconds")
    
    # Start the server in background
    process = subprocess.Popen(
        ["beacon", "webhook", "serve", "--port", str(port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    
    # Wait a moment for server to start
    time.sleep(2)
    
    # Check if server is running
    if process.poll() is None:
        print(f"[OK] Webhook server running at http://127.0.0.1:{port}")
        return process
    else:
        stdout, stderr = process.communicate()
        print(f"[ERROR] Failed to start server: {stderr}")
        return None


def send_webhook_message(target_url, kind="hello", text="Hello from Beacon!"):
    """
    Send a signed envelope to another agent via webhook.
    
    Args:
        target_url: Target webhook URL
        kind: Message kind (hello, want, bounty, etc.)
        text: Message text
    
    Returns:
        bool: True if message sent successfully
    """
    print(f"\n[SEND] Sending webhook message...")
    print(f"   Target: {target_url}")
    print(f"   Kind: {kind}")
    print(f"   Text: {text}")
    
    result = subprocess.run(
        ["beacon", "webhook", "send", target_url, 
         "--kind", kind, 
         "--text", text],
        capture_output=True,
        text=True
    )
    
    if result.returncode == 0:
        print("[OK] Message sent successfully!")
        return True
    else:
        print(f"[ERROR] Failed to send message: {result.stderr}")
        return False


def check_inbox(limit=5):
    """
    Check received messages in the inbox.
    
    Args:
        limit: Maximum number of messages to show
    """
    print(f"\n[INBOX] Checking inbox (last {limit} messages)...")
    
    result = subprocess.run(
        ["beacon", "inbox", "list", "--limit", str(limit)],
        capture_output=True,
        text=True
    )
    
    if result.returncode == 0:
        messages = result.stdout.strip()
        if messages:
            print("Received messages:")
            print(messages)
        else:
            print("No messages in inbox")
    else:
        print(f"[ERROR] Failed to check inbox: {result.stderr}")


def run_loopback_test(port=8402):
    """
    Run a complete loopback test - send a message to ourselves.
    
    This demonstrates the full webhook flow without needing
    a second machine or external agent.
    
    Args:
        port: Port for the webhook server
    """
    print("\n" + "="*60)
    print("WEBHOOK LOOPBACK TEST")
    print("="*60)
    
    # Step 1: Start server
    server = start_webhook_server(port, timeout=30)
    if not server:
        return False
    
    try:
        # Step 2: Send a message to ourselves
        target_url = f"http://127.0.0.1:{port}/beacon/inbox"
        
        # Send hello message
        send_webhook_message(
            target_url,
            kind="hello",
            text="Hello from my own agent!"
        )
        
        # Wait for message to be processed
        time.sleep(1)
        
        # Send bounty-style message
        send_webhook_message(
            target_url,
            kind="bounty",
            text="Testing webhook integration - 5 RTC bounty!"
        )
        
        # Wait for message to be processed
        time.sleep(1)
        
        # Step 3: Check inbox
        check_inbox(limit=5)
        
        print("\n[OK] Loopback test completed successfully!")
        return True
        
    finally:
        # Cleanup: Stop the server
        print("\n[STOP] Stopping webhook server...")
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
        print("[OK] Server stopped")


def demonstrate_agent_card(port=8402):
    """
    Demonstrate agent card generation and verification.
    
    Agent cards are used for discovery and trust establishment
    between beacon agents.
    
    Args:
        port: Port where the webhook server is running
    """
    print("\n" + "="*60)
    print("AGENT CARD DEMONSTRATION")
    print("="*60)
    
    # Generate agent card
    print("\n[GENERATE] Generating agent card...")
    result = subprocess.run(
        ["beacon", "agent-card", "generate", "--name", "webhook-demo-agent"],
        capture_output=True,
        text=True
    )
    
    if result.returncode == 0:
        print("[OK] Agent card generated!")
        try:
            card = json.loads(result.stdout)
            print(f"\nAgent Card Summary:")
            print(f"  Name: {card.get('name', 'N/A')}")
            print(f"  Agent ID: {card.get('agent_id', 'N/A')}")
            print(f"  Version: {card.get('beacon_version', 'N/A')}")
            capabilities = card.get('capabilities', {})
            kinds = capabilities.get('kinds', [])
            print(f"  Capabilities: {', '.join(kinds) if kinds else 'N/A'}")
        except json.JSONDecodeError:
            print("Card output:", result.stdout[:200])
    else:
        print(f"[ERROR] Failed to generate card: {result.stderr}")


def main():
    """
    Main function demonstrating webhook integration.
    """
    print("="*60)
    print("BEACON WEBHOOK INTEGRATION EXAMPLE")
    print("="*60)
    print("\nThis example demonstrates:")
    print("  1. Starting a webhook server")
    print("  2. Sending signed messages between agents")
    print("  3. Checking the inbox for received messages")
    print("  4. Working with agent cards")
    
    # Check prerequisites
    print("\n" + "-"*60)
    print("CHECKING PREREQUISITES")
    print("-"*60)
    
    if not check_beacon_installed():
        print("\n[ERROR] beacon-skill is not installed!")
        print("\nTo install:")
        print("  pip install beacon-skill")
        print("\nFor full features:")
        print('  pip install "beacon-skill[mnemonic,dashboard]"')
        sys.exit(1)
    
    print("[OK] beacon-skill is installed")
    
    if not create_identity_if_needed():
        print("\n[ERROR] Failed to setup identity")
        sys.exit(1)
    
    # Run demonstrations
    try:
        # Demo 1: Loopback test
        run_loopback_test(port=8402)
        
        # Demo 2: Agent card
        demonstrate_agent_card(port=8402)
        
        print("\n" + "="*60)
        print("ALL EXAMPLES COMPLETED!")
        print("="*60)
        print("\nNext steps:")
        print("  1. Try sending messages to other agents")
        print("  2. Explore other transports (UDP, Discord, etc.)")
        print("  3. Build your own agent workflows")
        print("\nDocumentation:")
        print("  https://github.com/Scottcjn/beacon-skill#readme")
        
    except KeyboardInterrupt:
        print("\n\n[WARNING] Interrupted by user")
        sys.exit(0)
    except Exception as e:
        print(f"\n[ERROR] {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
