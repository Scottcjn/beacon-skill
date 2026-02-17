"""Agent Matrix transport for Beacon — agent-to-agent messaging via Lambda Lang.

Agent Matrix is a communication platform for AI agents using Lambda Lang encoding.
Website: https://agentmatrix.voidborne.org (planned)
Protocol: Lambda Lang (https://github.com/voidborne-agent/lambda-lang)

This transport enables:
- Agent registration with phone-number-style addressing
- Lambda-encoded message exchange
- Inbox/outbox management
"""

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

import requests

from ..config import load_config
from ..retry import with_retry

# Default API endpoint (self-hosted or public)
DEFAULT_API_URL = "http://localhost:4020/api"


def _request(method: str, url: str, **kwargs) -> requests.Response:
    """Make an HTTP request with retry logic."""
    def _do_request():
        resp = requests.request(method, url, timeout=30, **kwargs)
        if resp.status_code in {429, 500, 502, 503, 504}:
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text}")
        return resp
    return with_retry(_do_request)


class AgentMatrixTransport:
    """Transport layer for Agent Matrix messaging."""
    
    def __init__(
        self,
        api_url: Optional[str] = None,
        agent_phone: Optional[str] = None,
        config_path: Optional[Path] = None,
    ):
        """Initialize Agent Matrix transport.
        
        Args:
            api_url: API base URL (default: from config or localhost:4020)
            agent_phone: Agent's phone-style ID (e.g., +1234567890)
            config_path: Path to config file
        """
        cfg = load_config(config_path) if config_path else {}
        am_cfg = cfg.get("agentmatrix", {})
        
        self.api_url = api_url or am_cfg.get("api_url", DEFAULT_API_URL)
        self.agent_phone = agent_phone or am_cfg.get("phone")
        self.agent_name = am_cfg.get("name", "beacon-agent")
        
    def register(
        self,
        phone: Optional[str] = None,
        name: Optional[str] = None,
        capabilities: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Register this agent with Agent Matrix.
        
        Args:
            phone: Phone-style agent ID (generates one if not provided)
            name: Human-readable agent name
            capabilities: List of agent capabilities
            
        Returns:
            Registration response with agent details
        """
        phone = phone or self.agent_phone or self._generate_phone()
        
        payload = {
            "phone": phone,
            "name": name or self.agent_name,
            "capabilities": capabilities or ["beacon", "lambda"],
            "protocol": "beacon-2.11",
        }
        
        url = urljoin(self.api_url, "/agents/register")
        resp = _request("POST", url, json=payload)
        
        if resp.status_code == 200:
            data = resp.json()
            self.agent_phone = data.get("phone", phone)
            return data
        else:
            return {"error": resp.text, "status": resp.status_code}
    
    def send(
        self,
        to_phone: str,
        message: str,
        kind: str = "hello",
        use_lambda: bool = True,
    ) -> Dict[str, Any]:
        """Send a message to another agent.
        
        Args:
            to_phone: Recipient's phone-style ID
            message: Message content
            kind: Beacon envelope kind
            use_lambda: Whether to encode with Lambda Lang
            
        Returns:
            Send response
        """
        if not self.agent_phone:
            return {"error": "Agent not registered. Call register() first."}
        
        payload = {
            "from": self.agent_phone,
            "to": to_phone,
            "content": message,
            "kind": kind,
            "encoding": "lambda" if use_lambda else "json",
            "timestamp": int(time.time() * 1000),
        }
        
        # Optionally encode with Lambda
        if use_lambda:
            try:
                from ..lambda_codec import encode_lambda
                beacon_payload = {"kind": kind, "text": message, "agent_id": self.agent_phone}
                payload["lambda"] = encode_lambda(beacon_payload)
            except ImportError:
                payload["encoding"] = "json"
        
        url = urljoin(self.api_url, "/messages/send")
        resp = _request("POST", url, json=payload)
        
        if resp.status_code == 200:
            return resp.json()
        else:
            return {"error": resp.text, "status": resp.status_code}
    
    def inbox(
        self,
        limit: int = 20,
        since: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch messages from inbox.
        
        Args:
            limit: Maximum messages to return
            since: Timestamp to fetch messages after
            
        Returns:
            List of messages
        """
        if not self.agent_phone:
            return []
        
        url = urljoin(self.api_url, f"/messages/inbox/{self.agent_phone}")
        params = {"limit": limit}
        if since:
            params["since"] = since
            
        resp = _request("GET", url, params=params)
        
        if resp.status_code == 200:
            messages = resp.json().get("messages", [])
            # Decode Lambda messages
            for msg in messages:
                if msg.get("encoding") == "lambda" and "lambda" in msg:
                    try:
                        from ..lambda_codec import decode_lambda
                        msg["decoded"] = decode_lambda(msg["lambda"])
                    except ImportError:
                        pass
            return messages
        else:
            return []
    
    def discover(
        self,
        capability: Optional[str] = None,
        protocol: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Discover other agents on the network.
        
        Args:
            capability: Filter by capability
            protocol: Filter by protocol support
            
        Returns:
            List of discovered agents
        """
        url = urljoin(self.api_url, "/agents/discover")
        params = {}
        if capability:
            params["capability"] = capability
        if protocol:
            params["protocol"] = protocol
            
        resp = _request("GET", url, params=params)
        
        if resp.status_code == 200:
            return resp.json().get("agents", [])
        else:
            return []
    
    def _generate_phone(self) -> str:
        """Generate a random phone-style agent ID."""
        import random
        digits = "".join(str(random.randint(0, 9)) for _ in range(10))
        return f"+{digits}"


# CLI integration helpers

def send_message(
    to: str,
    text: str,
    kind: str = "hello",
    use_lambda: bool = True,
    config_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Convenience function for CLI usage."""
    transport = AgentMatrixTransport(config_path=config_path)
    if not transport.agent_phone:
        transport.register()
    return transport.send(to, text, kind=kind, use_lambda=use_lambda)


def check_inbox(
    limit: int = 20,
    config_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Convenience function for CLI usage."""
    transport = AgentMatrixTransport(config_path=config_path)
    return transport.inbox(limit=limit)


def discover_agents(
    capability: Optional[str] = None,
    config_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Convenience function for CLI usage."""
    transport = AgentMatrixTransport(config_path=config_path)
    return transport.discover(capability=capability)
