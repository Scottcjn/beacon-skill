# tools/moltbook_migrate/moltbook_api.py
"""
Moltbook API Client — Fetch public profile metadata from BoTTube (formerly Moltbook).

Retrieves display name, bio, avatar, and content statistics from the BoTTube platform's
public API endpoints. This module has been migrated from the legacy moltbook.social API
to the active bottube.ai API following the Moltbook acquisition and platform merger.

Note: This module uses public BoTTube API endpoints. Rate limiting and availability
are subject to BoTTube's service terms.
"""

import hashlib
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

import requests

logger = logging.getLogger(__name__)


class MoltbookAPIError(Exception):
    """Base exception for BoTTube/Moltbook API errors."""

    def __init__(self, message: str, status_code: Optional[int] = None, details: Optional[Dict] = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.details = details or {}


class MoltbookProfileNotFoundError(MoltbookAPIError):
    """Raised when a BoTTube/Moltbook profile cannot be found."""
    pass


class MoltbookRateLimitError(MoltbookAPIError):
    """Raised when BoTTube/Moltbook API rate limits are exceeded."""
    pass


@dataclass
class MoltbookKarmaHistory:
    """Karma history entry for a Moltbook agent (deprecated — retained for migration compatibility)."""

    period: str  # e.g., "7d", "30d", "all_time"
    score: int
    change: int = 0
    timestamp: float = field(default_factory=time.time)


@dataclass
class MoltbookProfile:
    """Complete Moltbook profile data for an agent."""

    agent_name: str
    display_name: str
    bio: str
    avatar_url: str
    follower_count: int
    following_count: int
    karma: int
    karma_history: List[MoltbookKarmaHistory] = field(default_factory=list)
    created_at: Optional[str] = None
    verified: bool = False
    content_count: int = 0
    video_count: int = 0
    badges: List[str] = field(default_factory=list)
    interests: List[str] = field(default_factory=list)
    location: Optional[str] = None
    website: Optional[str] = None
    raw_data: Dict[str, Any] = field(default_factory=dict)

    def to_migration_payload(self) -> Dict[str, Any]:
        """Convert profile to a migration payload for Beacon."""
        return {
            "source_platform": "moltbook",
            "source_handle": self.agent_name,
            "display_name": self.display_name,
            "bio": self.bio,
            "avatar_url": self.avatar_url,
            "follower_count": self.follower_count,
            "karma": self.karma,
            "verified": self.verified,
            "content_count": self.content_count,
            "interests": self.interests,
            "location": self.location,
            "migration_timestamp": datetime.utcnow().isoformat(),
        }


class MoltbookClient:
    """
    Client for interacting with BoTTube's public API (formerly Moltbook).

    Fetches agent profiles and related public data. Legacy karma and follower
    endpoints from moltbook.social are no longer available; those fields are
    populated best-effort from available BoTTube data.

    Args:
        base_url: Base URL for BoTTube API (default: https://bottube.ai/api)
        timeout: Request timeout in seconds (default: 30)
        user_agent: Custom User-Agent string
    """

    DEFAULT_BASE_URL = "https://bottube.ai/api"
    AGENT_ENDPOINT = "/agents/{agent_name}"
    AGENTS_LIST_ENDPOINT = "/agents"
    BEACON_DIRECTORY_ENDPOINT = "/beacon/directory"
    # Legacy endpoints removed — karma/followers not available on bottube.ai

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        timeout: int = 30,
        user_agent: str = "Beacon-Migration-Tool/1.0",
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": user_agent,
            "Accept": "application/json",
        })
        self._last_request_time = 0.0
        self._min_request_interval = 1.0  # Rate limiting

    def _rate_limit(self) -> None:
        """Enforce rate limiting between requests."""
        elapsed = time.time() - self._last_request_time
        if elapsed < self._min_request_interval:
            time.sleep(self._min_request_interval - elapsed)
        self._last_request_time = time.time()

    def _build_url(self, endpoint: str, **path_params: str) -> str:
        """Build full URL from endpoint template."""
        formatted_endpoint = endpoint.format(**path_params)
        return urljoin(self.base_url + "/", formatted_endpoint.lstrip("/"))

    def _handle_response(self, response: requests.Response) -> Dict[str, Any]:
        """Handle HTTP response and raise appropriate exceptions."""
        if response.status_code == 404:
            raise MoltbookProfileNotFoundError(
                "BoTTube/Moltbook profile not found",
                status_code=404,
            )
        elif response.status_code == 429:
            retry_after = int(response.headers.get("Retry-After", 60))
            raise MoltbookRateLimitError(
                f"BoTTube/Moltbook rate limited. Retry after {retry_after} seconds.",
                status_code=429,
                details={"retry_after": retry_after},
            )
        elif response.status_code >= 400:
            raise MoltbookAPIError(
                f"BoTTube/Moltbook API error: {response.status_code}",
                status_code=response.status_code,
                details={"body": response.text[:500]},
            )
        return response.json()

    def get_agent_profile(self, agent_name: str) -> MoltbookProfile:
        """
        Fetch a complete BoTTube/Moltbook agent profile.

        Args:
            agent_name: The agent's BoTTube handle (with or without @)

        Returns:
            MoltbookProfile with all available public data

        Raises:
            MoltbookProfileNotFoundError: If profile doesn't exist
            MoltbookRateLimitError: If rate limited
            MoltbookAPIError: For other API errors
        """
        # Normalize agent name
        normalized_name = agent_name.lstrip("@")
        logger.info(f"Fetching BoTTube/Moltbook profile for @{normalized_name}")

        self._rate_limit()

        # Fetch main agent profile from bottube.ai
        agent_url = self._build_url(self.AGENT_ENDPOINT, agent_name=normalized_name)
        try:
            response = self._session.get(agent_url, timeout=self.timeout)
            agent_data = self._handle_response(response)
        except requests.RequestException as e:
            raise MoltbookAPIError(
                f"BoTTube/Moltbook network error fetching profile: {e}"
            ) from e

        # Map BoTTube API fields to MoltbookProfile
        # BoTTube response fields:
        #   agent_name, display_name, bio, avatar_url, is_human, joined (timestamp),
        #   profile_url, rss_url, total_views, video_count

        display_name = agent_data.get("display_name", normalized_name)
        bio = agent_data.get("bio", "")
        avatar_url = agent_data.get("avatar_url", "")
        is_human = agent_data.get("is_human", True)
        joined_ts = agent_data.get("joined")

        # Convert joined timestamp to ISO format
        created_at: Optional[str] = None
        if joined_ts is not None:
            try:
                created_at = datetime.fromtimestamp(joined_ts).isoformat()
            except (TypeError, ValueError, OSError) as e:
                logger.warning(
                    f"BoTTube/Moltbook: could not parse joined timestamp for "
                    f"@{normalized_name}: {e}"
                )
                created_at = None

        # Map total_views → follower_count, video_count → content_count
        total_views = agent_data.get("total_views", 0)
        video_count = agent_data.get("video_count", 0)

        # Verified is the inverse of is_human (non-human agents are "verified" bots)
        verified = not is_human

        # Badges and interests not available in new API — best effort from raw data
        badges: List[str] = []
        if verified:
            badges.append("verified")

        interests: List[str] = agent_data.get("interests", []) or []
        location: Optional[str] = agent_data.get("location")
        website: Optional[str] = agent_data.get("profile_url") or agent_data.get("rss_url")

        profile = MoltbookProfile(
            agent_name=normalized_name,
            display_name=display_name,
            bio=bio,
            avatar_url=avatar_url,
            follower_count=total_views,
            following_count=0,  # Not available in BoTTube API
            karma=0,  # Karma not available in BoTTube API
            karma_history=[],  # Karma history not available in BoTTube API
            created_at=created_at,
            verified=verified,
            content_count=video_count,
            video_count=video_count,
            badges=badges,
            interests=interests,
            location=location,
            website=website,
            raw_data=agent_data,
        )

        logger.info(
            f"Fetched BoTTube/Moltbook profile for @{normalized_name}: "
            f"{profile.follower_count} total views, {profile.video_count} videos"
        )

        return profile

    def _fetch_agent_by_name_query(self, agent_name: str) -> Dict[str, Any]:
        """Fetch agent info using the query parameter endpoint (best effort fallback)."""
        try:
            self._rate_limit()
            url = self._build_url(self.AGENTS_LIST_ENDPOINT)
            response = self._session.get(
                url, params={"agent_name": agent_name}, timeout=self.timeout
            )
            if response.ok:
                data = response.json()
                agents = data.get("agents", [])
                if agents:
                    return agents[0]
        except Exception as e:
            logger.warning(
                f"BoTTube/Moltbook: fallback agent query for @{agent_name} failed: {e}"
            )
        return {}

    def _fetch_karma(self, agent_name: str) -> Dict[str, Any]:
        """Fetch karma data — no longer available on BoTTube, returns empty dict."""
        logger.debug(
            f"BoTTube/Moltbook: karma endpoint removed; returning empty data for "
            f"@{agent_name}"
        )
        return {}

    def _fetch_followers(self, agent_name: str) -> Dict[str, Any]:
        """Fetch follower data — no longer available on BoTTube, returns empty dict."""
        logger.debug(
            f"BoTTube/Moltbook: followers endpoint removed; returning empty data for "
            f"@{agent_name}"
        )
        return {}

    def get_agents_list(self, limit: int = 20, offset: int = 0) -> List[Dict[str, Any]]:
        """
        Fetch a list of BoTTube/Moltbook agents.

        Args:
            limit: Maximum number of agents to return (default: 20, max: 100)
            offset: Pagination offset

        Returns:
            List of agent summary dictionaries
        """
        self._rate_limit()
        url = self._build_url(self.AGENTS_LIST_ENDPOINT)
        params = {"limit": min(limit, 100), "offset": offset}

        try:
            response = self._session.get(url, params=params, timeout=self.timeout)
            data = self._handle_response(response)
            return data.get("agents", data.get("results", []))
        except requests.RequestException as e:
            raise MoltbookAPIError(
                f"BoTTube/Moltbook network error fetching agent list: {e}"
            ) from e

    def get_beacon_directory(self) -> List[Dict[str, Any]]:
        """
        Fetch the BoTTube beacon directory.

        Returns:
            List of beacon entries with agent_name, beacon_id, display_name,
            is_human, networks, and registered timestamp.
        """
        self._rate_limit()
        url = self._build_url(self.BEACON_DIRECTORY_ENDPOINT)

        try:
            response = self._session.get(url, timeout=self.timeout)
            data = self._handle_response(response)
            return data.get("beacons", [])
        except requests.RequestException as e:
            raise MoltbookAPIError(
                f"BoTTube/Moltbook network error fetching beacon directory: {e}"
            ) from e

    def close(self) -> None:
        """Close the HTTP session."""
        self._session.close()

    def __enter__(self) -> "MoltbookClient":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()
