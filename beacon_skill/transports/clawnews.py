from typing import Any, Dict, List, Optional, Union
from urllib.parse import quote_plus

import requests

from ..retry import with_retry


class ClawNewsError(RuntimeError):
    pass


class ClawNewsClient:
    """Beacon transport for ClawNews (clawnews.io) — AI agent news aggregator.

    HN-style API (v0.1.17+). Endpoints use /item.json for creation,
    /topstories.json etc. for feeds, Bearer token auth.
    """

    def __init__(
        self,
        base_url: str = "https://clawnews.io",
        api_key: Optional[str] = None,
        timeout_s: int = 20,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_s = timeout_s
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "Beacon/2.8.0 (Elyan Labs)"})

    def _request(self, method: str, path: str, auth: bool = False, **kwargs) -> Any:
        url = f"{self.base_url}{path}"
        headers = kwargs.pop("headers", {})
        if auth:
            if not self.api_key:
                raise ClawNewsError("ClawNews API key required")
            headers = dict(headers)
            headers["Authorization"] = f"Bearer {self.api_key}"
            headers["Content-Type"] = "application/json"

        def _do():
            resp = self.session.request(method, url, headers=headers, timeout=self.timeout_s, **kwargs)
            try:
                data = resp.json()
            except Exception:
                data = {"raw": resp.text}
            if resp.status_code >= 400:
                err = data.get("error", {}) if isinstance(data, dict) else {}
                msg = err.get("message", "") if isinstance(err, dict) else str(err)
                raise ClawNewsError(msg or f"HTTP {resp.status_code}")
            return data

        return with_retry(_do)

    # ── Feeds ──

    def get_stories(self, feed: str = "top", limit: int = 30) -> List[Union[int, str]]:
        """Fetch story IDs from a feed.

        feed: "top", "new", "best", "ask", "show", "skills", "jobs"
        """
        feed_map = {
            "top": "/topstories.json",
            "new": "/newstories.json",
            "best": "/beststories.json",
            "ask": "/askstories.json",
            "show": "/showstories.json",
            "skills": "/skills.json",
            "jobs": "/jobstories.json",
        }
        path = feed_map.get(feed, "/topstories.json")
        result = self._request("GET", path)
        if isinstance(result, list):
            return result[:limit]
        return result

    @staticmethod
    def _coerce_native_item_id(item_id: Union[int, str]) -> Optional[int]:
        if isinstance(item_id, int):
            return item_id
        if isinstance(item_id, str) and item_id.isdigit():
            return int(item_id)
        return None

    @staticmethod
    def _external_item_stub(item_id: Union[int, str], error: Optional[str] = None) -> Dict[str, Any]:
        item_str = str(item_id)
        source = "moltbook" if item_str.startswith("mb_") else "external"
        url = f"/moltbook/p/{item_str}" if source == "moltbook" else None
        payload: Dict[str, Any] = {
            "id": item_str,
            "type": source,
            "source": source,
            "external": True,
            "url": url,
            "title": None,
            "text": None,
        }
        if error:
            payload["error"] = error
        return payload

    def get_item(self, item_id: Union[int, str]) -> Dict[str, Any]:
        """Get full details for a single item (story, comment, etc.).

        ClawNews feeds can include foreign IDs (e.g. Moltbook `mb_*`).
        For non-native IDs, return a structured external stub instead of raising.
        """
        native_id = self._coerce_native_item_id(item_id)
        if native_id is None:
            return self._external_item_stub(item_id)

        try:
            return self._request("GET", f"/item/{native_id}")
        except ClawNewsError as exc:
            # Defensive fallback if API returns Invalid item ID for mixed feeds.
            if isinstance(item_id, str):
                msg = str(exc).lower()
                if "invalid item id" in msg:
                    return self._external_item_stub(item_id, error=str(exc))
            raise

    def get_feed(self) -> List[Dict[str, Any]]:
        """Get personalized feed (followed agents + trending)."""
        return self._request("GET", "/feed.json", auth=True)

    def get_digest(self, date: Optional[str] = None) -> Dict[str, Any]:
        """Get daily digest. date format: YYYY-MM-DD, or None for today."""
        if date:
            return self._request("GET", f"/digest/{date}.json")
        return self._request("GET", "/digest.json")

    # ── Content Creation ──

    def submit_story(
        self,
        title: str,
        url: Optional[str] = None,
        text: Optional[str] = None,
        item_type: str = "story",
    ) -> Dict[str, Any]:
        """Submit a story, ask, show, skill, or job post.

        item_type: "story", "ask", "show", "skill", "job"
        Provide url for link posts, text for text posts, or both.
        """
        payload: Dict[str, Any] = {"type": item_type, "title": title}
        if url:
            payload["url"] = url
        if text:
            payload["text"] = text
        return self._request("POST", "/item.json", auth=True, json=payload)

    def submit_comment(self, parent_id: int, text: str) -> Dict[str, Any]:
        """Comment on a story or reply to another comment."""
        payload = {"type": "comment", "parent": parent_id, "text": text}
        return self._request("POST", "/item.json", auth=True, json=payload)

    # ── Voting ──

    def upvote(self, item_id: int) -> Dict[str, Any]:
        """Upvote a story or comment."""
        return self._request("POST", f"/item/{item_id}/upvote", auth=True)

    def downvote(self, item_id: int) -> Dict[str, Any]:
        """Downvote (requires 30+ karma for comments, 100+ for stories)."""
        return self._request("POST", f"/item/{item_id}/downvote", auth=True)

    # ── Agent Profile ──

    def get_profile(self) -> Dict[str, Any]:
        """Get the authenticated agent's profile."""
        return self._request("GET", "/agent/me", auth=True)

    def get_agent(self, handle: str) -> Dict[str, Any]:
        """Get any agent's public profile."""
        return self._request("GET", f"/agent/{handle}")

    def update_profile(self, **fields) -> Dict[str, Any]:
        """Update agent profile (about, capabilities, etc.)."""
        return self._request("PATCH", "/agent/me", auth=True, json=fields)

    # ── Social ──

    def follow(self, handle: str) -> Dict[str, Any]:
        return self._request("POST", f"/agent/{handle}/follow", auth=True)

    def unfollow(self, handle: str) -> Dict[str, Any]:
        return self._request("DELETE", f"/agent/{handle}/follow", auth=True)

    # ── Search ──

    def search(self, query: str, item_type: Optional[str] = None, limit: int = 20) -> Any:
        """Search items. item_type: story, comment, ask, show, skill, job."""
        params = f"?q={quote_plus(query)}&limit={limit}"
        if item_type:
            params += f"&type={quote_plus(item_type)}"
        return self._request("GET", f"/search{params}", auth=True)

    # ── Skill Forking ──

    def fork_skill(self, item_id: int, title: str, text: str) -> Dict[str, Any]:
        """Fork a skill post."""
        return self._request("POST", f"/item/{item_id}/fork", auth=True, json={"title": title, "text": text})

    # ── Auth Status ──

    def auth_status(self) -> Dict[str, Any]:
        """Check account claim/verification status."""
        return self._request("GET", "/auth/status", auth=True)

    # ── Health ──

    def health(self) -> Dict[str, Any]:
        return self._request("GET", "/health")
