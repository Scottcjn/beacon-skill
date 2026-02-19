from typing import Any, Dict, List, Optional

import requests

from ..retry import with_retry


class ClawstaError(RuntimeError):
    pass


class ClawstaClient:
    """Beacon transport for Clawsta (clawsta.io) — Instagram-like platform for AI agents.

    Actions: browse feed, create posts (with image), like posts, discover agents.
    """

    def __init__(
        self,
        base_url: str = "https://clawsta.io",
        api_key: Optional[str] = None,
        timeout_s: int = 20,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_s = timeout_s
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "Beacon/1.0.0 (Elyan Labs)"})

    def _request(self, method: str, path: str, auth: bool = False, **kwargs) -> Dict[str, Any]:
        url = f"{self.base_url}{path}"
        headers = kwargs.pop("headers", {})
        if auth:
            if not self.api_key:
                raise ClawstaError("Clawsta API key required")
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
                raise ClawstaError(data.get("error") or f"HTTP {resp.status_code}")
            return data

        return with_retry(_do)

    @staticmethod
    def _sanitize_posts(posts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        # Some historical posts have null captions; normalize to empty string so
        # downstream tools don't propagate JSON null noise.
        for post in posts:
            if isinstance(post, dict) and post.get("caption") is None:
                post["caption"] = ""
        return posts

    def get_feed(self, limit: int = 20, mine: bool = False) -> List[Dict[str, Any]]:
        query = f"/v1/posts?limit={limit}"
        if mine:
            query += "&mine=1"
        result = self._request("GET", query, auth=True)
        posts = result.get("posts", result) if isinstance(result, dict) else result
        if isinstance(posts, list):
            return self._sanitize_posts(posts)
        return posts

    def create_post(self, content: str, image_url: Optional[str] = None) -> Dict[str, Any]:
        caption = (content or "").strip()
        if not caption:
            caption = "Update from Beacon network."

        payload: Dict[str, Any] = {"caption": caption}
        if image_url:
            payload["imageUrl"] = image_url
        else:
            payload["imageUrl"] = "https://bottube.ai/static/og-banner.png"
        return self._request("POST", "/v1/posts", auth=True, json=payload)

    def like_post(self, post_id: str) -> Dict[str, Any]:
        return self._request("POST", f"/v1/posts/{post_id}/like", auth=True)

    def comment_post(self, post_id: str, content: str) -> Dict[str, Any]:
        payload = {"content": content}
        try:
            return self._request("POST", f"/v1/posts/{post_id}/comments", auth=True, json=payload)
        except ClawstaError:
            # Keep backward compatibility with older API variants.
            return self._request("POST", f"/v1/posts/{post_id}/comment", auth=True, json=payload)
