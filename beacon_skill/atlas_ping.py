"""Atlas Ping — Auto-register and heartbeat with the central Beacon Atlas.

When the beacon-skill daemon starts, it pings the Atlas relay to announce
this agent. Periodic pings keep the agent listed as "active" on the public
Atlas at https://rustchain.org/beacon/.

Uses the /relay/ping endpoint with signature verification for new agents,
or relay_token for existing agents (heartbeats).

Beacon 2.15.0 — Elyan Labs.
"""

import time
from pathlib import Path
from typing import Any, Dict, List, Optional
import json
import os

import requests

DEFAULT_ATLAS_URL = "https://rustchain.org/beacon"
ATLAS_PING_INTERVAL_S = 600  # 10 minutes
TOKEN_FILE_NAME = "atlas_relay_token.json"

# Store relay token between calls
_relay_token: Optional[str] = None


def _token_path() -> Path:
    """Return the per-user Atlas relay token path."""
    return Path.home() / ".beacon" / TOKEN_FILE_NAME


def _load_token_from_disk() -> Optional[str]:
    """Load a previously issued Atlas relay token if it is still usable."""
    path = _token_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None

    token = data.get("relay_token")
    expires = int(data.get("token_expires") or 0)
    if not token:
        return None
    if expires and expires <= int(time.time()):
        return None
    return str(token)


def _save_token_to_disk(token: str, token_expires: Optional[int] = None) -> None:
    """Persist the Atlas relay token for daemon restarts."""
    path = _token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: Dict[str, Any] = {
        "relay_token": token,
        "saved_at": int(time.time()),
    }
    if token_expires:
        payload["token_expires"] = int(token_expires)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass


def get_stored_token() -> Optional[str]:
    """Get the stored relay token."""
    global _relay_token
    if _relay_token:
        return _relay_token
    _relay_token = _load_token_from_disk()
    return _relay_token


def set_stored_token(token: str, token_expires: Optional[int] = None) -> None:
    """Store the relay token for future heartbeats."""
    global _relay_token
    _relay_token = token
    _save_token_to_disk(token, token_expires)


def atlas_ping(
    agent_id: str,
    name: str = "",
    *,
    capabilities: Optional[List[str]] = None,
    provider: str = "beacon",
    atlas_url: str = DEFAULT_ATLAS_URL,
    preferred_city: str = "",
    timeout: int = 10,
    identity: Optional[Any] = None,
) -> Dict[str, Any]:
    """Ping the Beacon Atlas to register or refresh this agent.

    On first ping (registration), requires identity for signature verification.
    On subsequent pings (heartbeat), uses relay_token from initial registration.

    Args:
        agent_id: This agent's bcn_ identifier.
        name: Display name for the Atlas.
        capabilities: List of capability domains (e.g. ["coding", "ai"]).
        provider: Provider identifier (default "beacon" for SDK users).
        atlas_url: Base URL of the Atlas relay server.
        preferred_city: Optional ClawCities preferred city.
        timeout: HTTP request timeout in seconds.
        identity: Optional AgentIdentity for signing (required for new registrations).

    Returns:
        Server response dict with ok, agent_id, beat_count, relay_token, etc.
    """
    global _relay_token

    url = f"{atlas_url.rstrip('/')}/relay/ping"
    body: Dict[str, Any] = {
        "agent_id": agent_id,
        "name": name or agent_id,
        "capabilities": capabilities or ["general"],
        "status": "alive",
        "provider": provider,
    }
    if preferred_city:
        body["preferred_city"] = preferred_city

    # Check if we have a relay token (existing agent heartbeat)
    stored_token = get_stored_token()
    if stored_token:
        body["relay_token"] = stored_token
    elif identity:
        # New agent registration - sign the agent_id
        try:
            pubkey_hex = identity.public_key_hex
            # Sign the agent_id
            signature = identity.sign(agent_id.encode("utf-8"))
            signature_hex = signature.hex() if isinstance(signature, bytes) else signature

            body["pubkey_hex"] = pubkey_hex
            body["signature"] = signature_hex
        except Exception as e:
            return {"ok": False, "error": f"Failed to sign: {e}"}
    else:
        # No token and no identity - try legacy mode (may fail on new servers)
        pass

    try:
        resp = requests.post(url, json=body, timeout=timeout)
        if resp.ok:
            result = resp.json()
            # Store relay token for future heartbeats
            if result.get("relay_token"):
                set_stored_token(result["relay_token"], result.get("token_expires"))
            return result
        return {"ok": False, "error": f"HTTP {resp.status_code}", "body": resp.text[:200]}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def atlas_ping_with_identity(
    identity: Any,
    name: str = "",
    *,
    capabilities: Optional[List[str]] = None,
    provider: str = "beacon",
    atlas_url: str = DEFAULT_ATLAS_URL,
    preferred_city: str = "",
    timeout: int = 10,
) -> Dict[str, Any]:
    """Convenience wrapper that extracts agent_id from identity.

    Args:
        identity: AgentIdentity object.
        Other args: Same as atlas_ping.

    Returns:
        Server response dict.
    """
    return atlas_ping(
        agent_id=identity.agent_id,
        name=name,
        capabilities=capabilities,
        provider=provider,
        atlas_url=atlas_url,
        preferred_city=preferred_city,
        timeout=timeout,
        identity=identity,
    )
