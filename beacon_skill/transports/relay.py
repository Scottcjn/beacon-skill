"""Relay transport: HTTP client for external agents to interact with a Beacon relay server."""

import json
import time
from typing import Any, Dict, List, Optional

import requests

from ..codec import encode_envelope
from ..identity import AgentIdentity


class RelayClient:
    """HTTP client for external agents to interact with a Beacon relay server.

    Usage:
        client = RelayClient("https://relay.example.com")
        result = client.register(identity, model_id="grok-3", provider="xai")
        token = result["relay_token"]
        client.heartbeat(identity.agent_id, token)
    """

    def __init__(self, relay_url: str, timeout_s: int = 15):
        self.relay_url = relay_url.rstrip("/")
        self.timeout_s = timeout_s

    def _url(self, path: str) -> str:
        return f"{self.relay_url}{path}"

    def _headers(self, token: str = "") -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Beacon-Relay/1.0.0 (Elyan Labs)",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    @staticmethod
    def build_seo_signature_payload(
        agent_id: str,
        seo_url: str,
        seo_description: str,
        ts_value: int,
        nonce: str,
    ) -> bytes:
        """Canonical payload for signed SEO field updates."""
        return json.dumps(
            {
                "agent_id": agent_id,
                "nonce": nonce,
                "seo_description": seo_description,
                "seo_url": seo_url,
                "ts": int(ts_value),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

    def register(
        self,
        identity: AgentIdentity,
        model_id: str,
        *,
        provider: str = "other",
        capabilities: Optional[List[str]] = None,
        webhook_url: str = "",
        name: str,
    ) -> Dict[str, Any]:
        """Register this agent with the relay server.

        Args:
            identity: Ed25519 identity for this agent.
            model_id: Model identifier (e.g. "grok-3").
            provider: Provider name (e.g. "xai", "anthropic").
            capabilities: Capability domains for Atlas placement.
            webhook_url: Optional callback URL.
            name: Unique agent name (required). Generic AI model names are rejected.

        Returns:
            Dict with agent_id, relay_token, etc.
        """
        # Sign the registration payload
        reg_payload = json.dumps({
            "model_id": model_id,
            "provider": provider,
            "pubkey_hex": identity.public_key_hex,
        }, sort_keys=True, separators=(",", ":")).encode("utf-8")
        signature = identity.sign_hex(reg_payload)

        body = {
            "pubkey_hex": identity.public_key_hex,
            "model_id": model_id,
            "provider": provider,
            "capabilities": capabilities or [],
            "webhook_url": webhook_url,
            "name": name,
            "signature": signature,
        }

        resp = requests.post(
            self._url("/relay/register"),
            json=body,
            headers=self._headers(),
            timeout=self.timeout_s,
        )
        return resp.json()

    def heartbeat(
        self,
        agent_id: str,
        token: str,
        *,
        status: str = "alive",
        health: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Send a heartbeat to the relay server.

        Args:
            agent_id: Our bcn_ agent ID.
            token: Relay bearer token.
            status: Liveness status.
            health: Optional health metrics.
        """
        body: Dict[str, Any] = {
            "agent_id": agent_id,
            "status": status,
        }
        if health:
            body["health"] = health

        resp = requests.post(
            self._url("/relay/heartbeat"),
            json=body,
            headers=self._headers(token),
            timeout=self.timeout_s,
        )
        return resp.json()

    def heartbeat_seo(
        self,
        identity: AgentIdentity,
        token: str,
        *,
        status: str = "alive",
        health: Optional[Dict[str, Any]] = None,
        seo_url: Optional[str] = None,
        seo_description: Optional[str] = None,
        nonce: Optional[str] = None,
        ts_value: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Send a relay heartbeat that can safely update public SEO fields."""
        body: Dict[str, Any] = {
            "agent_id": identity.agent_id,
            "status": status,
        }
        if health:
            body["health"] = health
        if seo_url is not None:
            body["seo_url"] = seo_url
        if seo_description is not None:
            body["seo_description"] = seo_description

        if seo_url is not None or seo_description is not None:
            ts_value = int(time.time()) if ts_value is None else int(ts_value)
            nonce = nonce or f"seo-{int(time.time() * 1000)}"
            body["ts"] = ts_value
            body["nonce"] = nonce
            body["signature"] = identity.sign_hex(
                self.build_seo_signature_payload(
                    identity.agent_id,
                    body.get("seo_url", "") or "",
                    body.get("seo_description", "") or "",
                    ts_value,
                    nonce,
                )
            )

        resp = requests.post(
            self._url("/relay/heartbeat/seo"),
            json=body,
            headers=self._headers(token),
            timeout=self.timeout_s,
        )
        return resp.json()

    def batch_heartbeat(
        self,
        heartbeats: List[Dict[str, Any]],
        token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Send multiple heartbeats in a single request.

        Args:
            heartbeats: List of dicts, each with 'agent_id', 'status', and optionally 'health', 'token'.
            token: Global auth token for the batch.
        """
        body = {
            "heartbeats": heartbeats
        }
        resp = requests.post(
            self._url("/relay/heartbeat/batch"),
            json=body,
            headers=self._headers(token or ""),
            timeout=self.timeout_s,
        )
        return resp.json()

    def discover(
        self,
        provider: Optional[str] = None,
        capability: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """List registered relay agents."""
        params: Dict[str, str] = {}
        if provider:
            params["provider"] = provider
        if capability:
            params["capability"] = capability

        resp = requests.get(
            self._url("/relay/discover"),
            params=params,
            headers=self._headers(),
            timeout=self.timeout_s,
        )
        return resp.json()

    def send_message(
        self,
        agent_id: str,
        token: str,
        envelope: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Send a beacon envelope via the relay.

        Args:
            agent_id: Our agent ID.
            token: Relay bearer token.
            envelope: Beacon envelope payload.
        """
        body = {
            "agent_id": agent_id,
            "envelope": envelope,
        }

        resp = requests.post(
            self._url("/relay/message"),
            json=body,
            headers=self._headers(token),
            timeout=self.timeout_s,
        )
        return resp.json()

    def status(self, agent_id: str) -> Dict[str, Any]:
        """Get relay status for a specific agent."""
        resp = requests.get(
            self._url(f"/relay/status/{agent_id}"),
            headers=self._headers(),
            timeout=self.timeout_s,
        )
        return resp.json()
