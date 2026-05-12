#!/usr/bin/env python3
"""
AgentFolio ↔ Beacon Unified MCP Endpoint

Tool: agentfolio_beacon_lookup(beacon_id)
Returns unified response: provenance (from Beacon) + trust score (from SATP)

Works with every MCP client (Claude Code, Cursor, Windsurf, any agent framework).
"""

import json
import sys
from typing import Any, Dict, List, Optional
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


# ─── Configuration ───────────────────────────────────────────────────────
BEACON_DIR_URL = "https://bottube.ai/api/beacon/directory"
AGENTFOLIO_AGENTS_URL = "https://agentfolio.bot/api/agents"
REQUEST_TIMEOUT = 15


# ─── API Clients ──────────────────────────────────────────────────────────
def fetch_beacon_directory() -> Dict[str, Any]:
    """Fetch the full Beacon directory from BoTTube API."""
    try:
        req = Request(
            BEACON_DIR_URL,
            headers={"User-Agent": "AgentFolioBeaconMCP/1.0"},
        )
        with urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            return json.loads(resp.read())
    except (HTTPError, URLError, json.JSONDecodeError) as e:
        return {"error": str(e), "beacons": []}


def fetch_agentfolio_agents() -> Dict[str, Any]:
    """Fetch AgentFolio agents list with trust scores."""
    try:
        req = Request(
            AGENTFOLIO_AGENTS_URL,
            headers={"User-Agent": "AgentFolioBeaconMCP/1.0"},
        )
        with urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            return json.loads(resp.read())
    except (HTTPError, URLError, json.JSONDecodeError) as e:
        return {"error": str(e), "agents": []}


# ─── Lookup Function ──────────────────────────────────────────────────────
def agentfolio_beacon_lookup(beacon_id: str) -> Dict[str, Any]:
    """Unified lookup: provenance (Beacon) + trust score (SATP).
    
    Args:
        beacon_id: Beacon identifier (e.g., "bcn_xeophon_a1078c86")
    
    Returns:
        Unified response with provenance and trust data.
    """
    result = {
        "beacon_id": beacon_id,
        "provenance": None,
        "trust": None,
        "status": "lookup_pending",
        "errors": [],
    }
    
    # 1. Fetch Beacon provenance
    beacon_data = fetch_beacon_directory()
    beacons = beacon_data.get("beacons", [])
    
    if "error" in beacon_data:
        result["errors"].append(f"Beacon directory error: {beacon_data['error']}")
    
    matched_beacon = None
    for beacon in beacons:
        if beacon.get("beacon_id") == beacon_id:
            matched_beacon = beacon
            break
    
    if matched_beacon:
        result["provenance"] = {
            "beacon_id": matched_beacon.get("beacon_id"),
            "agent_name": matched_beacon.get("agent_name"),
            "display_name": matched_beacon.get("display_name"),
            "is_human": matched_beacon.get("is_human", False),
            "networks": matched_beacon.get("networks", []),
            "registered": matched_beacon.get("registered", False),
        }
    else:
        result["errors"].append(f"Beacon ID '{beacon_id}' not found in directory")
    
    # 2. Fetch AgentFolio trust score
    agentfolio_data = fetch_agentfolio_agents()
    agents = agentfolio_data.get("agents", [])
    
    if "error" in agentfolio_data:
        result["errors"].append(f"AgentFolio error: {agentfolio_data['error']}")
    
    # Match by name if beacon was found
    agent_name = matched_beacon.get("agent_name", "") if matched_beacon else ""
    matched_agent = None
    
    for agent in agents:
        if agent.get("name", "").lower() == agent_name.lower():
            matched_agent = agent
            break
    
    if matched_agent:
        result["trust"] = {
            "agent_id": matched_agent.get("id"),
            "name": matched_agent.get("name"),
            "trust_score": matched_agent.get("trustScore", 0),
            "tier": matched_agent.get("tier", 0),
            "verification_level": matched_agent.get("verificationLevel", 0),
            "verification_badge": matched_agent.get("verificationBadge", ""),
            "verification_level_name": matched_agent.get("verificationLevelName", ""),
            "reputation_score": matched_agent.get("reputationScore", 0),
            "reputation_rank": matched_agent.get("reputationRank", ""),
        }
    else:
        result["trust"] = {
            "status": "not_found",
            "message": f"No AgentFolio profile matching '{agent_name}' found. "
                       "The agent may not have migrated yet.",
        }
    
    # 3. Handle offline/expired/untrusted gracefully
    if result["provenance"] and not result["provenance"]["registered"]:
        result["errors"].append("Beacon is registered but not yet verified")
    
    if result["trust"] and result["trust"].get("trust_score", 0) == 0:
        result["errors"].append("Agent has trust score of 0 — may be newly registered")
    
    result["status"] = "found" if matched_beacon else (
        "partial" if matched_agent else "not_found"
    )
    
    return result


# ─── MCP Server Protocol ──────────────────────────────────────────────────
def handle_mcp_request(request: Dict[str, Any]) -> Dict[str, Any]:
    """Handle an MCP tool call request.
    
    Follows the MCP (Model Context Protocol) specification
    for tool invocation.
    """
    method = request.get("method", "")
    params = request.get("params", {})
    
    if method == "tools/list":
        return {
            "tools": [
                {
                    "name": "agentfolio_beacon_lookup",
                    "description": (
                        "Look up an agent's unified identity: "
                        "provenance from Beacon + trust score from AgentFolio SATP. "
                        "Returns cryptographic provenance (who created this content?) "
                        "and behavioral reputation (should I trust this creator?)."
                    ),
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "beacon_id": {
                                "type": "string",
                                "description": "Beacon ID (e.g., 'bcn_xeophon_a1078c86')",
                            },
                        },
                        "required": ["beacon_id"],
                    },
                },
            ],
        }
    
    elif method == "tools/call":
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})
        
        if tool_name == "agentfolio_beacon_lookup":
            beacon_id = arguments.get("beacon_id", "")
            if not beacon_id:
                return {"error": "beacon_id is required"}
            
            lookup_result = agentfolio_beacon_lookup(beacon_id)
            return {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(lookup_result, indent=2),
                    }
                ],
            }
        else:
            return {"error": f"Unknown tool: {tool_name}"}
    
    else:
        return {"error": f"Unknown method: {method}"}


# ─── CLI Entry Point ──────────────────────────────────────────────────────
def main():
    """Run a standalone lookup or start the MCP server."""
    if len(sys.argv) > 1:
        # Direct lookup mode: python server.py bcn_xxx
        beacon_id = sys.argv[1]
        result = agentfolio_beacon_lookup(beacon_id)
        print(json.dumps(result, indent=2))
    else:
        # MCP server mode: read JSON-RPC from stdin
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                request = json.loads(line)
                response = handle_mcp_request(request)
                print(json.dumps(response))
                sys.stdout.flush()
            except json.JSONDecodeError:
                print(json.dumps({"error": "Invalid JSON"}))


if __name__ == "__main__":
    main()