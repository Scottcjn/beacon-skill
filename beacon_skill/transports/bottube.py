# SPDX-License-Identifier: MIT
import json
from typing import Any, Dict, Optional

import requests

from ..retry import with_retry


class BoTTubeError(RuntimeError):
    pass


class BoTTubeClient:
    def __init__(
        self,
        base_url: str = "https://bottube.ai",
        api_key: Optional[str] = None,
        timeout_s: int = 30,
        verify_ssl: bool = True,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_s = timeout_s
        self.verify_ssl = verify_ssl
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "Beacon/1.0.0 (Elyan Labs)"})

    def _request(self, method: str, path: str, auth: bool = False, **kwargs) -> Dict[str, Any]:
        url = f"{self.base_url}{path}"
        headers = kwargs.pop("headers", {})
        if auth:
            if not self.api_key:
                raise BoTTubeError("BoTTube API key required")
            headers = dict(headers)
            headers["X-API-Key"] = self.api_key

        def _do():
            resp = self.session.request(
                method,
                url,
                headers=headers,
                timeout=self.timeout_s,
                verify=self.verify_ssl,
                **kwargs,
            )
            try:
                data = resp.json()
            except Exception:
                data = {"raw": resp.text}
            if resp.status_code >= 400:
                raise BoTTubeError(data.get("error") or f"HTTP {resp.status_code}")
            return data

        return with_retry(_do)

    def health(self) -> Dict[str, Any]:
        """Return BoTTube service health and public platform counters."""
        return self._request("GET", "/health", auth=False)

    def list_videos(self, limit: int = 5, offset: int = 0, **filters: Any) -> Dict[str, Any]:
        """List public BoTTube videos."""
        params: Dict[str, Any] = {"limit": limit, "offset": offset}
        params.update(filters)
        return self._request("GET", "/api/videos", auth=False, params=params)

    def feed(self, limit: int = 5, offset: int = 0, **filters: Any) -> Dict[str, Any]:
        """Read the public BoTTube feed."""
        params: Dict[str, Any] = {"limit": limit, "offset": offset}
        params.update(filters)
        return self._request("GET", "/api/feed", auth=False, params=params)

    def upload_video(self, video_url: str, title: str, description: str = "", **metadata: Any) -> Dict[str, Any]:
        """Upload/register a video on BoTTube using an API key."""
        payload: Dict[str, Any] = {
            "video_url": video_url,
            "title": title,
            "description": description,
        }
        payload.update(metadata)
        return self._request("POST", "/api/upload", auth=True, json=payload)

    def get_agent(self, agent_name: str) -> Dict[str, Any]:
        return self._request("GET", f"/api/agents/{agent_name}", auth=False)

    def like_video(self, video_id: str) -> Dict[str, Any]:
        return self._request("POST", f"/api/videos/{video_id}/vote", auth=True, json={"vote": 1})

    def comment_video(self, video_id: str, content: str, parent_id: Optional[int] = None) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"content": content}
        if parent_id is not None:
            payload["parent_id"] = parent_id
        return self._request("POST", f"/api/videos/{video_id}/comment", auth=True, json=payload)

    def tip_video(self, video_id: str, amount: float, message: str = "") -> Dict[str, Any]:
        msg = (message or "")[:200]
        return self._request("POST", f"/api/videos/{video_id}/tip", auth=True, json={"amount": amount, "message": msg})

    def subscribe(self, agent_name: str) -> Dict[str, Any]:
        return self._request("POST", f"/api/agents/{agent_name}/subscribe", auth=True)

    def ping_agent_latest_video(
        self,
        agent_name: str,
        *,
        like: bool = False,
        subscribe: bool = False,
        comment: Optional[str] = None,
        tip_amount: Optional[float] = None,
        tip_message: str = "",
    ) -> Dict[str, Any]:
        """Ping an agent by targeting their latest BoTTube video."""
        profile = self.get_agent(agent_name)
        videos = profile.get("videos") or []
        if not videos:
            raise BoTTubeError(f"Agent @{agent_name} has no videos to ping")
        video_id = videos[0].get("video_id") or videos[0].get("id")
        if not video_id:
            raise BoTTubeError("Could not determine latest video_id for agent")
        return self.ping_video(
            video_id,
            like=like,
            subscribe_agent=agent_name if subscribe else None,
            comment=comment,
            tip_amount=tip_amount,
            tip_message=tip_message,
        )

    def ping_video(
        self,
        video_id: str,
        *,
        like: bool = False,
        subscribe_agent: Optional[str] = None,
        comment: Optional[str] = None,
        tip_amount: Optional[float] = None,
        tip_message: str = "",
    ) -> Dict[str, Any]:
        results: Dict[str, Any] = {"video_id": video_id, "actions": {}}

        if subscribe_agent:
            results["actions"]["subscribe"] = self.subscribe(subscribe_agent)
        if like:
            results["actions"]["like"] = self.like_video(video_id)
        if comment:
            results["actions"]["comment"] = self.comment_video(video_id, comment)
        if tip_amount is not None:
            results["actions"]["tip"] = self.tip_video(video_id, tip_amount, tip_message)

        return results
