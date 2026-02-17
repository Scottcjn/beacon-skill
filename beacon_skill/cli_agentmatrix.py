"""Agent Matrix CLI commands for Beacon.

This module adds CLI support for the Agent Matrix transport layer,
enabling phone-style agent addressing and Lambda-encoded messaging.
"""

import argparse
import json
import sys
from typing import Any, Dict

from .transports.agentmatrix import AgentMatrixTransport


def cmd_agentmatrix_register(args: argparse.Namespace) -> int:
    """Register this agent with Agent Matrix."""
    transport = AgentMatrixTransport(
        api_url=getattr(args, "api_url", None),
    )
    
    capabilities = None
    if hasattr(args, "capabilities") and args.capabilities:
        capabilities = [c.strip() for c in args.capabilities.split(",")]
    
    result = transport.register(
        phone=getattr(args, "phone", None),
        name=getattr(args, "name", None),
        capabilities=capabilities,
    )
    
    print(json.dumps(result, indent=2))
    return 0 if "error" not in result else 1


def cmd_agentmatrix_send(args: argparse.Namespace) -> int:
    """Send a message to another agent."""
    transport = AgentMatrixTransport(
        api_url=getattr(args, "api_url", None),
        agent_phone=getattr(args, "from_phone", None),
    )
    
    # Auto-register if no phone configured
    if not transport.agent_phone:
        reg = transport.register()
        if "error" in reg:
            print(json.dumps({"error": "Failed to auto-register", "details": reg}), file=sys.stderr)
            return 1
    
    use_lambda = not getattr(args, "no_lambda", False)
    
    result = transport.send(
        to_phone=args.to,
        message=args.text,
        kind=getattr(args, "kind", "hello"),
        use_lambda=use_lambda,
    )
    
    print(json.dumps(result, indent=2))
    return 0 if "error" not in result else 1


def cmd_agentmatrix_inbox(args: argparse.Namespace) -> int:
    """Check inbox for messages."""
    transport = AgentMatrixTransport(
        api_url=getattr(args, "api_url", None),
        agent_phone=getattr(args, "phone", None),
    )
    
    if not transport.agent_phone:
        print(json.dumps({"error": "No phone configured. Use --phone or register first."}), file=sys.stderr)
        return 1
    
    messages = transport.inbox(
        limit=getattr(args, "limit", 20),
        since=getattr(args, "since", None),
    )
    
    print(json.dumps({"messages": messages, "count": len(messages)}, indent=2))
    return 0


def cmd_agentmatrix_discover(args: argparse.Namespace) -> int:
    """Discover agents on the network."""
    transport = AgentMatrixTransport(
        api_url=getattr(args, "api_url", None),
    )
    
    agents = transport.discover(
        capability=getattr(args, "capability", None),
        protocol=getattr(args, "protocol", None),
    )
    
    print(json.dumps({"agents": agents, "count": len(agents)}, indent=2))
    return 0


def register_agentmatrix_parser(subparsers: Any) -> None:
    """Register Agent Matrix CLI subcommands.
    
    Call this from cli.py's main() to add agentmatrix commands.
    """
    am_p = subparsers.add_parser(
        "agentmatrix",
        help="Agent Matrix transport — Lambda-encoded agent messaging",
    )
    am_sub = am_p.add_subparsers(dest="agentmatrix_cmd", required=True)
    
    # Common args
    api_help = "Agent Matrix API URL (default: http://localhost:4020/api)"
    
    # register
    sp = am_sub.add_parser("register", help="Register this agent with Agent Matrix")
    sp.add_argument("--phone", default=None, help="Phone-style agent ID (auto-generated if not provided)")
    sp.add_argument("--name", default=None, help="Human-readable agent name")
    sp.add_argument("--capabilities", default=None, help="Comma-separated capabilities (e.g., beacon,lambda,llm)")
    sp.add_argument("--api-url", default=None, help=api_help)
    sp.set_defaults(func=cmd_agentmatrix_register)
    
    # send
    sp = am_sub.add_parser("send", help="Send a message to another agent")
    sp.add_argument("to", help="Recipient phone-style ID (e.g., +1234567890)")
    sp.add_argument("--text", required=True, help="Message text")
    sp.add_argument("--kind", default="hello", help="Beacon envelope kind (default: hello)")
    sp.add_argument("--from-phone", default=None, help="Sender phone ID (uses config if not provided)")
    sp.add_argument("--no-lambda", action="store_true", help="Disable Lambda encoding")
    sp.add_argument("--api-url", default=None, help=api_help)
    sp.set_defaults(func=cmd_agentmatrix_send)
    
    # inbox
    sp = am_sub.add_parser("inbox", help="Check inbox for messages")
    sp.add_argument("--phone", default=None, help="Agent phone ID to check inbox for")
    sp.add_argument("--limit", type=int, default=20, help="Max messages to return (default: 20)")
    sp.add_argument("--since", type=int, default=None, help="Timestamp to fetch messages after")
    sp.add_argument("--api-url", default=None, help=api_help)
    sp.set_defaults(func=cmd_agentmatrix_inbox)
    
    # discover
    sp = am_sub.add_parser("discover", help="Discover agents on the network")
    sp.add_argument("--capability", default=None, help="Filter by capability (e.g., beacon, lambda)")
    sp.add_argument("--protocol", default=None, help="Filter by protocol support")
    sp.add_argument("--api-url", default=None, help=api_help)
    sp.set_defaults(func=cmd_agentmatrix_discover)
