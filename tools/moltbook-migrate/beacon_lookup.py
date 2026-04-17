#!/usr/bin/env python3
"""
Unified MCP Endpoint: agentfolio_beacon_lookup(beacon_id)

Returns unified response: provenance (from Beacon) + trust score (from SATP)
"""

import hashlib
import json
import time
from typing import Any, Dict, Optional


BEACON_DIRECTORY = "https://bottube.ai/api/beacon/directory"
AGENTFOLIO_SATP = "https://agentfolio.bot/api/v1/trust"


def beacon_lookup(beacon_id: str) -> Dict[str, Any]:
    """
    Unified lookup: provenance + trust score.
    
    Args:
        beacon_id: Beacon ID (e.g., bcn_ecc6726f5770)
    
    Returns:
        Unified response with provenance and trust data
    """
    result = {
        "beacon_id": beacon_id,
        "provenance": None,
        "trust": None,
        "status": "unknown",
        "queried_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    # 1. Query Beacon Directory for provenance
    provenance = _query_beacon_directory(beacon_id)
    if provenance:
        result["provenance"] = provenance
        result["status"] = "active"
    else:
        result["provenance"] = {
            "beacon_id": beacon_id,
            "status": "not_found",
            "note": "Beacon not found in directory. May be expired or unregistered.",
        }
        result["status"] = "not_found"

    # 2. Query AgentFolio SATP for trust score
    trust = _query_satp_registry(beacon_id)
    if trust:
        result["trust"] = trust
    else:
        result["trust"] = {
            "beacon_id": beacon_id,
            "trust_score": None,
            "status": "no_profile",
            "note": "No SATP trust profile linked to this Beacon ID.",
        }

    return result


def _query_beacon_directory(beacon_id: str) -> Optional[Dict[str, Any]]:
    """Query bottube.ai Beacon Directory API."""
    import urllib.request
    import urllib.error

    url = f"{BEACON_DIRECTORY}?beacon_id={beacon_id}"
    req = urllib.request.Request(url, headers={"User-Agent": "BeaconLookup/1.0"})

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            return {
                "beacon_id": beacon_id,
                "name": data.get("name", "unknown"),
                "registered_at": data.get("registered_at"),
                "hardware_anchored": data.get("hardware_anchored", False),
                "status": data.get("status", "unknown"),
                "transport_count": len(data.get("transports", [])),
            }
    except urllib.error.HTTPError:
        return None
    except Exception:
        return None


def _query_satp_registry(beacon_id: str) -> Optional[Dict[str, Any]]:
    """Query AgentFolio SATP trust registry."""
    import urllib.request
    import urllib.error

    url = f"{AGENTFOLIO_SATP}?beacon_id={beacon_id}"
    req = urllib.request.Request(url, headers={"User-Agent": "BeaconLookup/1.0"})

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            return {
                "satp_id": data.get("satp_id"),
                "trust_score": data.get("trust_score", 0.0),
                "karma_inherited": data.get("provenance", {}).get("karma_inherited", 0),
                "followers_inherited": data.get("provenance", {}).get("followers_inherited", 0),
                "last_updated": data.get("linked_at"),
            }
    except urllib.error.HTTPError:
        return None
    except Exception:
        return None


def handle_graceful_errors(result: Dict[str, Any]) -> Dict[str, Any]:
    """Handle offline nodes, expired beacons, untrusted scores gracefully."""
    if result["status"] == "not_found":
        result["error"] = {
            "code": "BEACON_NOT_FOUND",
            "message": "This Beacon ID is not registered or has expired.",
            "suggestion": "Register a new beacon with: beacon register --name <your-name>",
        }
    elif isinstance(result.get("trust"), dict) and result["trust"].get("status") == "no_profile":
        result["warning"] = "No SATP trust profile found. Trust score defaults to 0."
    
    return result


# MCP Tool Definition
MCP_TOOL_DEF = {
    "name": "agentfolio_beacon_lookup",
    "description": "Look up unified agent identity: provenance (Beacon) + trust score (SATP)",
    "inputSchema": {
        "type": "object",
        "properties": {
            "beacon_id": {
                "type": "string",
                "description": "Beacon ID to look up (e.g., bcn_ecc6726f5770)",
            }
        },
        "required": ["beacon_id"],
    },
}


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print('Usage: python beacon_lookup.py <beacon_id>')
        sys.exit(1)
    
    beacon_id = sys.argv[1]
    result = beacon_lookup(beacon_id)
    print(json.dumps(result, indent=2))
