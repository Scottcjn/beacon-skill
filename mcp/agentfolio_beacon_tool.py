#!/usr/bin/env python3
"""
AgentFolio MCP Server - Beacon Integration
===========================================

Unified MCP endpoint for Beacon provenance + AgentFolio trust score.

Tool: agentfolio_beacon_lookup(beacon_id)

Deliverable 2 of 4 for AgentFolio ↔ Beacon Integration bounty.
"""

import json
import time
from typing import Dict, Optional, Any
import requests

# API Endpoints
BEACON_DIRECTORY = "https://bottube.ai/api/beacon/directory"
AGENTFOLIO_TRUST = "https://api.agentfolio.bot/satp/trust"


class AgentFolioBeaconMCPServer:
    """MCP server providing unified Beacon + AgentFolio lookup."""
    
    def __init__(self):
        self.beacon_cache = {}
        self.trust_cache = {}
        self.cache_ttl = 300  # 5 minutes
    
    def agentfolio_beacon_lookup(self, beacon_id: str) -> Dict[str, Any]:
        """
        Unified lookup for Beacon provenance + AgentFolio trust score.
        
        Args:
            beacon_id: Beacon ID (e.g., 'bcn_my-age_a1b2c3d4')
        
        Returns:
            Unified response with provenance and trust score
        """
        start = time.time()
        
        result = {
            "beacon_id": beacon_id,
            "provenance": None,
            "trust_score": None,
            "status": "success",
            "query_time_ms": 0,
        }
        
        # Query Beacon directory
        provenance = self._query_beacon(beacon_id)
        if provenance:
            result["provenance"] = provenance
        else:
            result["status"] = "partial"
            result["error"] = "Beacon not found"
        
        # Query AgentFolio trust score
        trust = self._query_agentfolio(beacon_id)
        if trust:
            result["trust_score"] = trust
        else:
            if result["status"] == "partial":
                result["status"] = "not_found"
                result["error"] = "Beacon and trust profile not found"
            else:
                result["status"] = "partial"
                result["warning"] = "Trust profile not found"
        
        result["query_time_ms"] = int((time.time() - start) * 1000)
        
        return result
    
    def _query_beacon(self, beacon_id: str) -> Optional[Dict]:
        """
        Query Beacon directory for provenance.
        
        Handles:
        - Offline nodes (timeout)
        - Expired beacons
        - Not found
        """
        # Check cache first
        if beacon_id in self.beacon_cache:
            cached = self.beacon_cache[beacon_id]
            if time.time() - cached['timestamp'] < self.cache_ttl:
                return cached['data']
        
        try:
            # Fetch full directory
            response = requests.get(BEACON_DIRECTORY, timeout=10)
            response.raise_for_status()
            directory = response.json()
            
            # Search for beacon_id
            for beacon in directory.get('beacons', []):
                if beacon.get('beacon_id') == beacon_id:
                    provenance = {
                        "agent_name": beacon.get('agent_name'),
                        "display_name": beacon.get('display_name'),
                        "is_human": beacon.get('is_human'),
                        "networks": beacon.get('networks', []),
                        "registered": beacon.get('registered', False),
                        "source": "Beacon Protocol",
                    }
                    
                    # Cache result
                    self.beacon_cache[beacon_id] = {
                        'timestamp': time.time(),
                        'data': provenance,
                    }
                    
                    return provenance
            
            return None
        except requests.Timeout:
            return {"error": "Beacon directory timeout", "offline": True}
        except requests.RequestException as e:
            return {"error": str(e)}
    
    def _query_agentfolio(self, beacon_id: str) -> Optional[Dict]:
        """
        Query AgentFolio SATP registry for trust score.
        
        Handles:
        - Untrusted scores
        - Expired profiles
        - Not found
        """
        # Check cache first
        if beacon_id in self.trust_cache:
            cached = self.trust_cache[beacon_id]
            if time.time() - cached['timestamp'] < self.cache_ttl:
                return cached['data']
        
        try:
            # Query trust score
            url = f"{AGENTFOLIO_TRUST}/{beacon_id}"
            response = requests.get(url, timeout=10)
            
            if response.status_code == 404:
                return None
            
            response.raise_for_status()
            trust_data = response.json()
            
            trust_score = {
                "score": trust_data.get('score', 0),
                "max_score": trust_data.get('max_score', 100),
                "reputation_anchor": trust_data.get('reputation_anchor'),
                "platform_history": trust_data.get('platform_history', []),
                "last_updated": trust_data.get('last_updated'),
                "source": "AgentFolio SATP",
            }
            
            # Cache result
            self.trust_cache[beacon_id] = {
                'timestamp': time.time(),
                'data': trust_score,
            }
            
            return trust_score
        except requests.Timeout:
            return {"error": "AgentFolio timeout", "offline": True}
        except requests.RequestException:
            return None  # Not found or error


# MCP Server instance
mcp_server = AgentFolioBeaconMCPServer()


# MCP Tool Definition
MCP_TOOLS = [
    {
        "name": "agentfolio_beacon_lookup",
        "description": "Unified lookup for agent identity: Beacon provenance + AgentFolio trust score",
        "inputSchema": {
            "type": "object",
            "properties": {
                "beacon_id": {
                    "type": "string",
                    "description": "Beacon ID (e.g., 'bcn_my-age_a1b2c3d4')"
                }
            },
            "required": ["beacon_id"]
        },
        "handler": mcp_server.agentfolio_beacon_lookup,
    }
]


# Example usage
if __name__ == "__main__":
    # Test lookup
    result = mcp_server.agentfolio_beacon_lookup("bcn_scottc_800b1e22")
    print(json.dumps(result, indent=2))
