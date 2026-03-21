import time
from typing import Any, Dict, Optional

import requests

from ..retry import with_retry
from ..storage import get_last_ts, set_last_ts

class AgentHiveError(RuntimeError):
    pass

class AgentHiveClient:
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

    def _request(self, method: str, path: str, auth: bool = False, **kwargs) -> Dict[str, Any]:
        url = f"{self.base_url}{path}"
        headers = kwargs.pop("headers", {})
        if auth:
            if not self.api_key:
                raise AgentHiveError("AgentHive API key required")
            headers = dict(headers)
            headers["Authorization"] = f"Bearer {self.api_key}"

        def _do():
            resp = self.session.request(method, url, headers=headers, timeout=self.timeout_s, **kwargs)
            try:
                data = resp.json()
            except Exception:
                data = {"raw": resp.text}
            if resp.status_code >= 400:
                raise AgentHiveError(data.get("error") or f"HTTP {resp.status_code}")
            return data

        return with_retry(_do)

    def register(self, name: str, bio: Optional[str] = None) -> Dict[str, Any]:
        payload = {"name": name}
        if bio:
            payload["bio"] = bio
        return self._request("POST", "/api/agents", json=payload)

    def post_message(self, content: str, *, force: bool = False) -> Dict[str, Any]:
        guard_key = "agenthive_post"
        last_ts = get_last_ts(guard_key)
        if not force and last_ts is not None and (time.time() - last_ts) < 60:
            raise AgentHiveError("Local guard: AgentHive posting is limited to 1 per 60 seconds (use --force to override).")
        
        resp = self._request(
            "POST",
            "/api/posts",
            auth=True,
            json={"content": content},
            headers={"Content-Type": "application/json"},
        )
        set_last_ts(guard_key)
        return resp

    def read_timeline(self) -> Dict[str, Any]:
        return self._request("GET", "/api/feed")

    def read_agent_posts(self, name: str) -> Dict[str, Any]:
        return self._request("GET", f"/api/agents/{name}/posts")

    def follow(self, name: str) -> Dict[str, Any]:
        return self._request("POST", f"/api/agents/{name}/follow", auth=True)
