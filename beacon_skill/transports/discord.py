from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

import requests


class DiscordError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
        error_type: str = "unknown",
    ):
        self.status_code = status_code
        self.error_type = error_type
        super().__init__(message)


class DiscordClient:
    """Discord webhook transport for Beacon envelopes."""

    API_BASE = "https://discord.com/api/v10"

    def __init__(
        self,
        webhook_url: Optional[str] = None,
        timeout_s: int = 20,
        username: Optional[str] = None,
        avatar_url: Optional[str] = None,
        bot_token: Optional[str] = None,
        channel_id: Optional[str] = None,
        max_attempts: int = 3,
        base_delay_s: float = 1.0,
    ):
        self.webhook_url = webhook_url or ""
        self.timeout_s = timeout_s
        self.username = username
        self.avatar_url = avatar_url
        self.bot_token = bot_token or ""
        self.channel_id = channel_id or ""
        self.max_attempts = max(1, int(max_attempts))
        self.base_delay_s = max(0.05, float(base_delay_s))
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "Beacon/2.12.0 (Elyan Labs)"})

    def _parse_error_payload(self, resp: requests.Response) -> Dict[str, Any]:
        msg = (resp.text or "").strip()
        retry_after = 0.0
        try:
            data = resp.json()
            if isinstance(data, dict):
                msg = str(data.get("message") or data.get("error") or msg)
                retry_raw = data.get("retry_after")
                if retry_raw is not None:
                    retry_after = float(retry_raw)
        except Exception:
            pass
        return {"message": msg, "retry_after": max(0.0, retry_after)}

    def _backoff_s(self, attempt: int, retry_after_s: Optional[float] = None) -> float:
        if retry_after_s is not None and retry_after_s > 0:
            return min(60.0, retry_after_s)
        return min(60.0, self.base_delay_s * (2 ** attempt))

    def _request_with_retry(
        self,
        method: str,
        url: str,
        *,
        json_payload: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        for attempt in range(self.max_attempts):
            try:
                resp = self.session.request(
                    method,
                    url,
                    json=json_payload,
                    params=params,
                    headers=headers,
                    timeout=self.timeout_s,
                )
            except requests.RequestException as e:
                if attempt >= self.max_attempts - 1:
                    raise DiscordError(
                        f"Discord request failed: {e}",
                        error_type="transient",
                    ) from e
                time.sleep(self._backoff_s(attempt))
                continue

            status = int(resp.status_code)
            if 200 <= status < 300:
                if status == 204 or not (resp.text or "").strip():
                    return {"ok": True, "status": status}
                try:
                    data = resp.json()
                except Exception:
                    data = {"raw": resp.text}
                return {"ok": True, "status": status, "data": data}

            err = self._parse_error_payload(resp)
            msg = err["message"] or f"HTTP {status}"
            retry_after = float(err.get("retry_after", 0.0) or 0.0)

            if status == 429:
                if attempt >= self.max_attempts - 1:
                    raise DiscordError(
                        f"HTTP 429: {msg}",
                        status_code=status,
                        error_type="throttled",
                    )
                time.sleep(self._backoff_s(attempt, retry_after))
                continue

            if 500 <= status < 600:
                if attempt >= self.max_attempts - 1:
                    raise DiscordError(
                        f"HTTP {status}: {msg}",
                        status_code=status,
                        error_type="server",
                    )
                time.sleep(self._backoff_s(attempt))
                continue

            error_type = "auth" if status in (401, 403) else "client"
            raise DiscordError(
                f"HTTP {status}: {msg}",
                status_code=status,
                error_type=error_type,
            )

        raise DiscordError("Discord request failed after retries", error_type="unknown")

    def _send_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not self.webhook_url:
            raise DiscordError("Discord webhook_url required", error_type="config")
        return self._request_with_retry(
            "POST",
            self.webhook_url,
            json_payload=payload,
        )

    def send_message(
        self,
        content: str,
        *,
        username: Optional[str] = None,
        avatar_url: Optional[str] = None,
        embeds: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        payload = self._build_message_payload(
            content=content,
            username=username,
            avatar_url=avatar_url,
            embeds=embeds,
        )
        return self._send_payload(payload)

    def send_beacon(
        self,
        *,
        content: str,
        kind: str,
        agent_id: str,
        rtc_tip: Optional[float] = None,
        signature_preview: str = "",
        username: Optional[str] = None,
        avatar_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload = self.build_beacon_payload(
            content=content,
            kind=kind,
            agent_id=agent_id,
            rtc_tip=rtc_tip,
            signature_preview=signature_preview,
            username=username,
            avatar_url=avatar_url,
        )
        return self._send_payload(payload)

    def build_beacon_payload(
        self,
        *,
        content: str,
        kind: str,
        agent_id: str,
        rtc_tip: Optional[float] = None,
        signature_preview: str = "",
        username: Optional[str] = None,
        avatar_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        fields: List[Dict[str, Any]] = [
            {"name": "Kind", "value": kind[:64] or "unknown", "inline": True},
            {
                "name": "Agent",
                "value": (agent_id[:24] + "...") if len(agent_id) > 24 else (agent_id or "unknown"),
                "inline": True,
            },
        ]
        if rtc_tip is not None:
            fields.append({"name": "RTC Tip", "value": f"{rtc_tip:g} RTC", "inline": True})
        if signature_preview:
            fields.append({"name": "Signature", "value": signature_preview[:32], "inline": True})

        embed = {
            "title": f"Beacon Ping · {kind.upper()}",
            "description": (content or "Beacon ping")[:4096],
            "color": 65450 if rtc_tip else 7506394,
            "fields": fields,
        }
        payload = self._build_message_payload(
            content=content,
            username=username,
            avatar_url=avatar_url,
            embeds=[embed],
        )
        return payload

    def _build_message_payload(
        self,
        *,
        content: str,
        username: Optional[str] = None,
        avatar_url: Optional[str] = None,
        embeds: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"content": content[:2000]}
        if username or self.username:
            payload["username"] = (username or self.username or "")[:80]
        if avatar_url or self.avatar_url:
            payload["avatar_url"] = avatar_url or self.avatar_url
        if embeds:
            payload["embeds"] = embeds
        return payload

    def _bot_auth_headers(self) -> Dict[str, Any]:
        if not self.bot_token:
            raise DiscordError("Discord bot_token required for listener mode", error_type="config")
        return {"Authorization": f"Bot {self.bot_token}"}

    def listen_messages(
        self,
        *,
        limit: int = 20,
        after_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        channel_id = (self.channel_id or "").strip()
        if not channel_id:
            raise DiscordError("Discord channel_id required for listener mode", error_type="config")

        req_limit = max(1, min(int(limit), 100))
        params: Dict[str, Any] = {"limit": req_limit}
        if after_id:
            params["after"] = str(after_id)

        url = f"{self.API_BASE}/channels/{channel_id}/messages"
        result = self._request_with_retry(
            "GET",
            url,
            params=params,
            headers=self._bot_auth_headers(),
        )
        rows = result.get("data")
        if rows is None:
            rows = []
        if not isinstance(rows, list):
            raise DiscordError("Unexpected response shape for Discord channel messages", error_type="client")

        messages: List[Dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            author = row.get("author") if isinstance(row.get("author"), dict) else {}
            messages.append(
                {
                    "id": row.get("id"),
                    "content": row.get("content", ""),
                    "timestamp": row.get("timestamp"),
                    "author_id": author.get("id"),
                    "author_username": author.get("username"),
                }
            )

        return {
            "ok": True,
            "status": result.get("status", 200),
            "channel_id": channel_id,
            "count": len(messages),
            "messages": messages,
        }
