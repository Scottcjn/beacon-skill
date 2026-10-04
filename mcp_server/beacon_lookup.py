# mcp_server/beacon_lookup.py
"""
Beacon Lookup Module — MCP server for Beacon Protocol lookups.

Provides functionality to:
- Look up individual Beacon IDs
- List all registered beacons
- Query beacons by AgentFolio ID
"""

import json
import logging
import os
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)


# Configuration
#
# The Atlas is addressed by hostname, never by a node's raw IP. The node's TLS
# certificate is issued for the hostname, so a raw-IP URL can only ever work
# with certificate verification switched off -- which is exactly the failure
# mode this module must not invite. Verification stays on (requests' default).
# Override for a lab node with BEACON_ATLAS_URL.
#
# NB: the live Atlas serves its agent list at ``<base>/api/agents``. The
# historical ``/beacon/atlas`` alias is a UI route that is not deployed on the
# public host -- it 404s there (#943), so ``lookup_beacon`` falls back to the
# canonical agents route on the same host when the configured URL is missing.
BEACON_DIRECTORY_URL = "https://bottube.ai/api/beacon/directory"
DEFAULT_BEACON_ATLAS_URL = "https://rustchain.org/beacon/atlas"
DEFAULT_BEACON_AGENTS_ROUTE = "/api/agents"
AGENTFOLIO_API_URL = "https://agentfolio.bot/api/satp"


def resolve_atlas_url(env: Optional[Dict[str, str]] = None) -> str:
    """Atlas base URL: ``BEACON_ATLAS_URL`` if set and non-blank, else the hostname default."""
    source = os.environ if env is None else env
    return (source.get("BEACON_ATLAS_URL") or "").strip() or DEFAULT_BEACON_ATLAS_URL


BEACON_ATLAS_URL = resolve_atlas_url()
DEFAULT_TIMEOUT = 30


def _atlas_agents_endpoint(atlas_url: str) -> str:
    """Canonical Atlas agents-list endpoint on the same host as ``atlas_url``.

    The public Atlas exposes its agent list at ``<base>/api/agents``; the
    ``/beacon/atlas`` alias is a UI route that 404s on the public host (#943).
    Derive the agents endpoint from the configured URL's host so lookups work
    with TLS verification on and no raw-IP fallback:

    - ``https://host/beacon/atlas``  -> ``https://host/beacon/api/agents``
    - ``https://host/beacon``        -> ``https://host/beacon/api/agents``
    - ``https://host/beacon/api/agents`` -> unchanged
    """
    from urllib.parse import urlsplit, urlunsplit

    parts = urlsplit(atlas_url)
    path = parts.path.rstrip("/")
    if path.endswith(DEFAULT_BEACON_AGENTS_ROUTE):
        return atlas_url
    if path.endswith("/atlas"):
        path = path[: -len("/atlas")]
    path = path + DEFAULT_BEACON_AGENTS_ROUTE
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


class BeaconLookupError(Exception):
    """Raised when Beacon lookup operations fail."""

    def __init__(self, message: str, beacon_id: Optional[str] = None, details: Optional[Dict] = None):
        super().__init__(message)
        self.message = message
        self.beacon_id = beacon_id
        self.details = details or {}


@dataclass
class BeaconInfo:
    """Information about a registered Beacon."""

    agent_id: str
    public_key_hex: str
    hardware_fingerprint: str
    registration_ts: float
    atlas_registered: bool = False
    last_seen_ts: Optional[float] = None
    source_platform: Optional[str] = None
    source_handle: Optional[str] = None
    satp_profile_id: Optional[str] = None
    trust_score: Optional[float] = None
    verification_level: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary, handling datetime serialization."""
        data = asdict(self)
        # Convert timestamp to ISO format for JSON serialization
        if data.get("registration_ts"):
            data["registration_ts_iso"] = datetime.fromtimestamp(data["registration_ts"]).isoformat()
        if data.get("last_seen_ts"):
            data["last_seen_ts_iso"] = datetime.fromtimestamp(data["last_seen_ts"]).isoformat()
        return data

    def to_json(self) -> str:
        """Serialize to JSON string."""
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BeaconInfo":
        """Create BeaconInfo from dictionary."""
        return cls(
            agent_id=data.get("agent_id", ""),
            public_key_hex=data.get("public_key_hex", ""),
            hardware_fingerprint=data.get("hardware_fingerprint", ""),
            registration_ts=data.get("registration_ts", 0.0),
            atlas_registered=data.get("atlas_registered", False),
            last_seen_ts=data.get("last_seen_ts"),
            source_platform=data.get("source_platform"),
            source_handle=data.get("source_handle"),
            satp_profile_id=data.get("satp_profile_id"),
            trust_score=data.get("trust_score"),
            verification_level=data.get("verification_level"),
        )


def _select_beacon_record(payload: Any, beacon_id: str) -> Optional[Dict[str, Any]]:
    """Extract the single beacon record for ``beacon_id`` from an Atlas response.

    The public Atlas exposes its agent list at ``/api/agents``, which returns a
    **list** of agent summaries; older/lab deployments returned a single dict for
    a filtered ``?agent_id=`` query. Accept both shapes (plus a ``{"agents": []}``
    wrapper) so lookup works whichever endpoint the resolved URL points at.

    Returns ``None`` when the payload is a list that does not contain the beacon.
    """
    if isinstance(payload, dict):
        # Wrapped list, e.g. {"agents": [...]} or {"beacons": [...]}
        for key in ("agents", "beacons"):
            if isinstance(payload.get(key), list):
                return _select_beacon_record(payload[key], beacon_id)
        # Single-record payload (filtered endpoint) — use as-is.
        return payload
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict) and item.get("agent_id") == beacon_id:
                return item
        return None
    return None


def lookup_beacon(beacon_id: str) -> BeaconInfo:
    """
    Look up a single Beacon by its ID.

    Args:
        beacon_id: The Beacon ID to look up (e.g., "bcn_xxxx...")

    Returns:
        BeaconInfo object with beacon details

    Raises:
        BeaconLookupError: If beacon is not found or lookup fails

    Example:
        >>> info = lookup_beacon("bcn_abc123def456")
        >>> print(f"Registered at: {info.registration_ts}")
    """
    if not beacon_id:
        raise BeaconLookupError("Beacon ID cannot be empty")

    if not beacon_id.startswith("bcn_"):
        logger.warning(f"Beacon ID {beacon_id} does not have bcn_ prefix")

    try:
        # Query the Beacon Atlas for the beacon
        response = requests.get(
            BEACON_ATLAS_URL,
            params={"agent_id": beacon_id},
            timeout=DEFAULT_TIMEOUT,
        )

        data: Optional[Dict[str, Any]] = None
        if response.status_code == 200:
            data = _select_beacon_record(response.json(), beacon_id)
        elif response.status_code == 404:
            # The configured ``/beacon/atlas`` alias is a UI route that is not
            # deployed on the public host, so it 404s there even though the
            # Atlas itself is healthy (#943). Fall back to the canonical agents
            # list on the *same host*, keeping TLS verification on -- never to a
            # raw node IP.
            fallback = _atlas_agents_endpoint(BEACON_ATLAS_URL)
            if fallback and fallback != BEACON_ATLAS_URL:
                alt = requests.get(fallback, timeout=DEFAULT_TIMEOUT)
                if alt.status_code == 200:
                    data = _select_beacon_record(alt.json(), beacon_id)
                elif alt.status_code != 404:
                    raise BeaconLookupError(
                        f"Atlas returned status {alt.status_code}",
                        beacon_id=beacon_id,
                        details={"response": alt.text},
                    )
        else:
            raise BeaconLookupError(
                f"Atlas returned status {response.status_code}",
                beacon_id=beacon_id,
                details={"response": response.text},
            )

        if data is None:
            raise BeaconLookupError(
                f"Beacon {beacon_id} not found",
                beacon_id=beacon_id,
            )

        # Also try to get SATP info from AgentFolio
        satp_info = _lookup_satp_for_beacon(beacon_id)
        if satp_info:
            data.update(satp_info)

        return BeaconInfo.from_dict(data)

    except requests.RequestException as e:
        raise BeaconLookupError(
            f"Failed to connect to Beacon Atlas: {e}",
            beacon_id=beacon_id,
        )


def lookup_all_beacons(limit: int = 100, offset: int = 0, include_expired: bool = False) -> List[BeaconInfo]:
    """
    Retrieve a list of all registered beacons.

    Args:
        limit: Maximum number of beacons to return (default 100)
        offset: Number of beacons to skip (for pagination)
        include_expired: Whether to include expired/revoked beacons

    Returns:
        List of BeaconInfo objects

    Raises:
        BeaconLookupError: If the directory query fails

    Example:
        >>> beacons = lookup_all_beacons(limit=50)
        >>> for beacon in beacons:
        ...     print(f"{beacon.agent_id}: {beacon.registration_ts}")
    """
    try:
        params = {
            "limit": min(limit, 1000),  # Cap at 1000
            "offset": offset,
            "include_expired": include_expired,
        }

        response = requests.get(
            BEACON_DIRECTORY_URL,
            params=params,
            timeout=DEFAULT_TIMEOUT,
        )

        if response.status_code != 200:
            raise BeaconLookupError(
                f"Directory returned status {response.status_code}",
                details={"response": response.text},
            )

        data = response.json()

        # Handle both list response and wrapped response
        beacons_data = data if isinstance(data, list) else data.get("beacons", [])
        total = data.get("total", len(beacons_data)) if isinstance(data, dict) else len(beacons_data)

        beacons = [BeaconInfo.from_dict(b) for b in beacons_data]

        logger.info(f"Retrieved {len(beacons)} beacons (total: {total})")
        return beacons

    except requests.RequestException as e:
        raise BeaconLookupError(
            f"Failed to connect to Beacon Directory: {e}",
        )


def agentfolio_beacon_lookup(
    agentfolio_id: Optional[str] = None,
    include_expired: bool = False,
) -> str:
    """
    Look up beacons associated with an AgentFolio ID, returning JSON.

    This function queries AgentFolio's SATP registry to find beacons
    linked to a specific agent or to retrieve all beacons if no ID
    is specified.

    Args:
        agentfolio_id: Optional AgentFolio SATP profile ID to filter by
                      (e.g., "satp_abc123..."). If None, returns all beacons.
        include_expired: Whether to include expired/revoked beacons

    Returns:
        JSON string containing beacon information

    Raises:
        BeaconLookupError: If the AgentFolio query fails

    Example:
        >>> json_result = agentfolio_beacon_lookup("satp_xyz789")
        >>> beacons = json.loads(json_result)
        >>> print(f"Found {len(beacons)} beacons for this agent")
    """
    try:
        params: Dict[str, Any] = {
            "include_expired": include_expired,
        }

        if agentfolio_id:
            params["satp_profile_id"] = agentfolio_id

        response = requests.get(
            AGENTFOLIO_API_URL,
            params=params,
            timeout=DEFAULT_TIMEOUT,
        )

        if response.status_code == 404:
            raise BeaconLookupError(
                f"AgentFolio ID {agentfolio_id} not found" if agentfolio_id else "AgentFolio registry not found",
                beacon_id=agentfolio_id,
            )

        if response.status_code != 200:
            raise BeaconLookupError(
                f"AgentFolio returned status {response.status_code}",
                beacon_id=agentfolio_id,
                details={"response": response.text},
            )

        data = response.json()

        # Format the response with metadata
        result = {
            "query": {
                "agentfolio_id": agentfolio_id,
                "include_expired": include_expired,
            },
            "count": 0,
            "beacons": [],
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }

        # Handle different response formats
        if isinstance(data, list):
            result["beacons"] = [BeaconInfo.from_dict(b).to_dict() for b in data]
            result["count"] = len(result["beacons"])
        elif isinstance(data, dict):
            beacons_data = data.get("beacons", data.get("results", []))
            result["beacons"] = [BeaconInfo.from_dict(b).to_dict() for b in beacons_data]
            result["count"] = len(result["beacons"])
            if "total" in data:
                result["total"] = data["total"]

        return json.dumps(result, indent=2)

    except requests.RequestException as e:
        raise BeaconLookupError(
            f"Failed to connect to AgentFolio: {e}",
            beacon_id=agentfolio_id,
        )


def _lookup_satp_for_beacon(beacon_id: str) -> Optional[Dict[str, Any]]:
    """
    Internal helper to look up SATP information for a beacon.

    Args:
        beacon_id: The Beacon ID

    Returns:
        Dictionary with SATP info or None if not found
    """
    try:
        response = requests.get(
            f"{AGENTFOLIO_API_URL}/beacon/{beacon_id}",
            timeout=DEFAULT_TIMEOUT,
        )
        if response.status_code == 200:
            return response.json()
    except requests.RequestException:
        pass
    return None


# MCP Server Functions
def mcp_lookup_beacon(beacon_id: str) -> str:
    """
    MCP server function for beacon lookup.

    Returns JSON string for MCP protocol compatibility.
    """
    info = lookup_beacon(beacon_id)
    return info.to_json()


def mcp_lookup_all_beacons(limit: int = 100, offset: int = 0) -> str:
    """
    MCP server function for listing all beacons.

    Returns JSON string for MCP protocol compatibility.
    """
    beacons = lookup_all_beacons(limit=limit, offset=offset)
    result = {
        "beacons": [b.to_dict() for b in beacons],
        "count": len(beacons),
    }
    return json.dumps(result, indent=2)


def mcp_agentfolio_lookup(agentfolio_id: Optional[str] = None, include_expired: bool = False) -> str:
    """
    MCP server function for AgentFolio-based beacon lookup.

    Returns JSON string for MCP protocol compatibility.
    """
    return agentfolio_beacon_lookup(agentfolio_id=agentfolio_id, include_expired=include_expired)

def agentfolio_beacon_lookup_dict(
    agentfolio_id: Optional[str] = None,
    include_expired: bool = False,
) -> dict:
    """Same as agentfolio_beacon_lookup but returns a dict instead of JSON string."""
    return json.loads(agentfolio_beacon_lookup(agentfolio_id, include_expired))

