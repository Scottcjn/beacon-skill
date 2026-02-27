#!/usr/bin/env python3
"""
Inbox Monitor - Watch your beacon inbox for new messages and print alerts.

This script monitors your beacon inbox and alerts you when new messages arrive.
Useful for staying responsive to other agents or users.

Usage:
    python3 inbox_monitor.py [--interval N] [--agent-id AGENT_ID]

Options:
    --interval N  Check every N seconds (default: 10)
    --agent-id ID Your agent ID (optional, uses default if not provided)
"""

import argparse
import sys
import time
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from beacon_skill import AgentIdentity, AgentMemory


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Monitor your beacon inbox for new messages"
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=10,
        help="Check for new messages every N seconds (default: 10)"
    )
    parser.add_argument(
        "--agent-id",
        type=str,
        default=None,
        help="Your agent ID (optional)"
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Check once and exit (don't loop)"
    )
    return parser.parse_args()


def get_inbox_count(agent_id: str) -> int:
    """Get the number of messages in the agent's inbox."""
    try:
        memory = AgentMemory(agent_id=agent_id)
        # Get inbox entries
        inbox = memory.list_received()
        return len(inbox)
    except Exception as e:
        print(f"Error checking inbox: {e}")
        return -1


def main() -> int:
    """Main entry point."""
    args = parse_args()
    
    # Get or generate agent ID
    if args.agent_id:
        agent_id = args.agent_id
    else:
        identity = AgentIdentity.load_or_generate()
        agent_id = identity.agent_id
    
    print(f"📬 Inbox Monitor for agent: {agent_id}")
    print(f"   Checking every {args.interval} seconds...")
    print(f"   Press Ctrl+C to stop\n")
    
    last_count = get_inbox_count(agent_id)
    
    if last_count < 0:
        print("Failed to connect to inbox. Exiting.")
        return 1
    
    print(f"Current inbox count: {last_count} messages")
    
    if args.once:
        return 0
    
    try:
        while True:
            time.sleep(args.interval)
            current_count = get_inbox_count(agent_id)
            
            if current_count > last_count:
                new_messages = current_count - last_count
                print(f"🔔 NEW: {new_messages} message(s) in inbox! (total: {current_count})")
                last_count = current_count
            elif current_count < 0:
                print("⚠️  Lost connection to inbox")
            else:
                # Silent check - just update
                pass
                
    except KeyboardInterrupt:
        print("\n\n👋 Inbox monitor stopped.")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
