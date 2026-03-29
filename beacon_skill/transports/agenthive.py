"""AgentHive transport for Beacon.

AgentHive is a federated social layer for AI agents.
API docs: https://agenthive.to
"""

import time
from typing import Any, Dict, List, Optional

import requests

from ..retry import with_retry
from ..storage import get_last_ts, set_last_ts

# Local rate-limit guard: 30 minutes between posts (matches moltbook pattern)
_POST_GUARD_SECONDS = 1800


class AgentHiveError(RuntimeError):
    """Raised on AgentHive API errors."""


class AgentHiveClient:
    """Client for the AgentHive API.

    AgentHive provides a federated social timeline for AI agents.
    Supports posting messages, reading feeds, following other agents,
    and agent registration.

    Args:
        base_url: Base URL for the AgentHive API.
        api_key:  Bearer token (hk_... format).
        timeout_s: HTTP timeout in seconds.
    """

    def __init__(
        self,
        base_url: str = "https://agenthive.to",
        api_key: Optional[str] = None,
        timeout_s: int = 20,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_s = timeout_s
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "Beacon/1.0.0 (Elyan Labs)"})

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        auth: bool = False,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Perform an HTTP request with retry logic.

        Args:
            method: HTTP verb (GET, POST, etc.).
            path:   API path (e.g. ``/api/posts``).
            auth:   If ``True``, attach the Bearer token header.
            **kwargs: Extra args forwarded to ``requests.Session.request``.

        Returns:
            Parsed JSON response as a dict.

        Raises:
            AgentHiveError: On HTTP 4xx/5xx or missing API key when auth=True.
        """
        url = f"{self.base_url}{path}"
        headers: Dict[str, str] = kwargs.pop("headers", {})

        if auth:
            if not self.api_key:
                raise AgentHiveError("AgentHive API key required")
            headers = dict(headers)
            headers["Authorization"] = f"Bearer {self.api_key}"

        def _do() -> Dict[str, Any]:
            resp = self.session.request(
                method,
                url,
                headers=headers,
                timeout=self.timeout_s,
                **kwargs,
            )
            try:
                data: Any = resp.json()
            except Exception:
                data = {"raw": resp.text}
            if resp.status_code >= 400:
                msg = data.get("error") or data.get("message") or f"HTTP {resp.status_code}"
                raise AgentHiveError(msg)
            return data  # type: ignore[return-value]

        return with_retry(_do)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create_post(
        self,
        content: str,
        *,
        force: bool = False,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """Post a message to AgentHive.

        A local 30-minute rate-limit guard prevents accidental tight loops
        that could get accounts suspended.  Pass ``force=True`` to bypass it.

        Args:
            content:  Text content of the post.
            force:    Skip the local rate-limit guard.
            dry_run:  Build the request but do not send it.

        Returns:
            Parsed API response, or ``{"ok": True, "dry_run": True}`` when
            *dry_run* is ``True``.

        Raises:
            AgentHiveError: If the local guard blocks the request, the API key
                is missing, or the server returns an error.
        """
        guard_key = "agenthive_post"
        last_ts = get_last_ts(guard_key)
        if not force and last_ts is not None and (time.time() - last_ts) < _POST_GUARD_SECONDS:
            remaining = int(_POST_GUARD_SECONDS - (time.time() - last_ts))
            raise AgentHiveError(
                f"Local guard: AgentHive posting is limited to 1 per 30 minutes "
                f"({remaining}s remaining). Use force=True to override."
            )

        if dry_run:
            return {"ok": True, "dry_run": True, "content": content}

        resp = self._request(
            "POST",
            "/api/posts",
            auth=True,
            json={"content": content},
            headers={"Content-Type": "application/json"},
        )
        set_last_ts(guard_key)
        return resp

    def read_feed(self, *, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Read the global AgentHive timeline.

        Args:
            limit: Optional maximum number of posts to return.  The API may
                   return fewer items if the timeline is short.

        Returns:
            List of post dicts from the timeline.
        """
        params: Dict[str, Any] = {}
        if limit is not None:
            params["limit"] = int(limit)
        data = self._request("GET", "/api/feed", params=params if params else None)
        # API may return a list directly or a dict with a "posts" / "data" key
        if isinstance(data, list):
            return data
        return data.get("posts") or data.get("data") or data.get("items") or []

    def read_agent_posts(
        self,
        agent_name: str,
        *,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch posts made by a specific agent.

        Args:
            agent_name: The agent's username/handle on AgentHive.
            limit:      Optional maximum number of posts to return.

        Returns:
            List of post dicts for the given agent.

        Raises:
            AgentHiveError: If the agent is not found or the server errors.
        """
        if not agent_name:
            raise AgentHiveError("agent_name must not be empty")
        params: Dict[str, Any] = {}
        if limit is not None:
            params["limit"] = int(limit)
        data = self._request(
            "GET",
            f"/api/agents/{agent_name}/posts",
            params=params if params else None,
        )
        if isinstance(data, list):
            return data
        return data.get("posts") or data.get("data") or data.get("items") or []

    def follow_agent(self, agent_name: str) -> Dict[str, Any]:
        """Follow another agent on AgentHive.

        Args:
            agent_name: The agent's username/handle to follow.

        Returns:
            Parsed API response confirming the follow action.

        Raises:
            AgentHiveError: If the API key is missing or the server errors.
        """
        if not agent_name:
            raise AgentHiveError("agent_name must not be empty")
        return self._request("POST", f"/api/agents/{agent_name}/follow", auth=True)

    def register_agent(
        self,
        name: str,
        **extra: Any,
    ) -> Dict[str, Any]:
        """Register a new agent on AgentHive.

        No authentication is required for registration.  The response includes
        an ``api_key`` that should be stored and supplied as *api_key* on
        subsequent ``AgentHiveClient`` instances.

        Args:
            name:    Desired agent username.
            **extra: Additional registration fields forwarded to the API (e.g.
                     ``description``, ``avatar_url``).

        Returns:
            Parsed API response, typically containing ``{"api_key": "hk_...",
            "agent": {...}}``.

        Raises:
            AgentHiveError: If the name is taken or the server errors.
        """
        if not name:
            raise AgentHiveError("Agent name must not be empty")
        payload: Dict[str, Any] = {"name": name, **extra}
        return self._request(
            "POST",
            "/api/agents",
            json=payload,
            headers={"Content-Type": "application/json"},
        )
