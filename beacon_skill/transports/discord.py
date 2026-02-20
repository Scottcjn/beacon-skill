"""Discord transport with hardened error handling and listener mode."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from enum import Enum

import requests

from ..retry import with_retry, RETRYABLE_STATUS_CODES

logger = logging.getLogger(__name__)


class DiscordError(RuntimeError):
    """Base exception for Discord transport errors."""
    pass


class DiscordRateLimitError(DiscordError):
    """Raised when rate limited by Discord."""
    
    def __init__(self, retry_after: float, message: str = "Rate limited"):
        self.retry_after = retry_after
        super().__init__(message)


class DiscordClientError(DiscordError):
    """Raised for 4xx errors (client errors)."""
    
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        super().__init__(f"HTTP {status_code}: {message}")


class DiscordServerError(DiscordError):
    """Raised for 5xx errors (server errors)."""
    
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        super().__init__(f"HTTP {status_code}: {message}")


class DiscordTransport:
    """Discord webhook transport for Beacon envelopes with hardened error handling."""

    def __init__(
        self,
        webhook_url: Optional[str] = None,
        timeout_s: int = 20,
        username: Optional[str] = None,
        avatar_url: Optional[str] = None,
        max_retries: int = 5,
        base_delay: float = 1.0,
    ):
        self.webhook_url = webhook_url or ""
        self.timeout_s = timeout_s
        self.username = username
        self.avatar_url = avatar_url
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "Beacon/2.12.0 (Elyan Labs)"})

    def _parse_response_error(self, response: requests.Response) -> DiscordError:
        """Parse HTTP error response and return appropriate exception."""
        status = response.status_code
        
        # Try to parse JSON error message
        message = f"HTTP {status}"
        try:
            data = response.json()
            if isinstance(data, dict):
                message = data.get("message", message)
        except Exception:
            message = response.text[:200] if response.text else message
        
        # 4xx client errors
        if 400 <= status < 500:
            if status == 429:
                # Rate limited - try to get retry_after
                retry_after = float(response.headers.get("Retry-After", 1))
                return DiscordRateLimitError(retry_after, message)
            return DiscordClientError(status, message)
        
        # 5xx server errors
        if status >= 500:
            return DiscordServerError(status, message)
        
        return DiscordError(message)

    def _calculate_backoff(self, attempt: int, retry_after: Optional[float] = None) -> float:
        """Calculate exponential backoff delay with jitter."""
        if retry_after:
            return retry_after
        
        delay = self.base_delay * (2 ** attempt)
        # Add jitter (0.5 to 1.5 of base delay)
        jitter = 0.5 + random.random()
        return delay * jitter

    def _send_payload(self, payload: Dict[str, Any], dry_run: bool = False) -> Dict[str, Any]:
        """Send payload with retry logic for rate limits and server errors."""
        if not self.webhook_url:
            raise DiscordError("Discord webhook_url required")

        def _do() -> Dict[str, Any]:
            if dry_run:
                logger.info(f"DRY RUN: Would send to Discord: {payload}")
                return {"ok": True, "status": 200, "dry_run": True}
            
            resp = self.session.post(
                self.webhook_url, 
                json=payload, 
                timeout=self.timeout_s
            )
            
            # Check for errors
            if resp.status_code >= 400:
                error = self._parse_response_error(resp)
                raise error

            # Success
            if resp.status_code == 204 or not resp.text.strip():
                return {"ok": True, "status": resp.status_code}

            try:
                data = resp.json()
            except Exception:
                data = {"raw": resp.text}
            return {"ok": True, "status": resp.status_code, "data": data}

        # Custom retry with proper 429 handling
        last_error: Optional[Exception] = None
        for attempt in range(self.max_retries):
            try:
                return _do()
            except DiscordRateLimitError as e:
                # Special handling for 429 - respect Retry-After header
                last_error = e
                if attempt == self.max_retries - 1:
                    break
                delay = self._calculate_backoff(attempt, e.retry_after)
                logger.warning(f"Rate limited. Retry after {delay:.1f}s (attempt {attempt + 1}/{self.max_retries})")
                time.sleep(delay)
            except (DiscordServerError, requests.RequestException) as e:
                # Retry on server errors and network issues
                last_error = e
                if attempt == self.max_retries - 1:
                    break
                delay = self._calculate_backoff(attempt)
                logger.warning(f"Server error/network issue. Retry in {delay:.1f}s (attempt {attempt + 1}/{self.max_retries})")
                time.sleep(delay)
            except DiscordClientError as e:
                # Don't retry 4xx client errors (except 429)
                raise

        if isinstance(last_error, DiscordRateLimitError):
            raise last_error
        if isinstance(last_error, (DiscordServerError, requests.RequestException)):
            raise DiscordError(f"Failed after {self.max_retries} attempts: {last_error}") from last_error
        raise DiscordError(f"Failed after {self.max_retries} attempts: {last_error}")

    def send_message(
        self,
        content: str,
        *,
        username: Optional[str] = None,
        avatar_url: Optional[str] = None,
        embeds: Optional[List[Dict[str, Any]]] = None,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """Send a message via Discord webhook."""
        payload: Dict[str, Any] = {"content": content[:2000]}
        if username or self.username:
            payload["username"] = (username or self.username or "")[:80]
        if avatar_url or self.avatar_url:
            payload["avatar_url"] = avatar_url or self.avatar_url
        if embeds:
            payload["embeds"] = embeds
        return self._send_payload(payload, dry_run=dry_run)

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
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """Send a Beacon envelope as a rich Discord embed."""
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
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }
        return self.send_message(
            content=content,
            username=username,
            avatar_url=avatar_url,
            embeds=[embed],
            dry_run=dry_run,
        )

    def ping(self, dry_run: bool = False) -> Dict[str, Any]:
        """Test webhook connectivity."""
        return self.send_message(
            content="🔔 Beacon Discord transport is operational!",
            dry_run=dry_run,
        )


class DiscordListener:
    """Lightweight listener for Discord webhook events (polling mode)."""

    def __init__(
        self,
        webhook_url: Optional[str] = None,
        poll_interval: int = 30,
        state_file: Optional[str] = None,
        max_backlog: int = 100,
    ):
        self.webhook_url = webhook_url or ""
        self.poll_interval = poll_interval
        self.state_file = state_file
        self.max_backlog = max_backlog
        self._running = False
        self._last_event_id: Optional[str] = None
        self._callback: Optional[Callable[[Dict[str, Any]], None]] = None
        
        # Load state if exists
        if state_file and Path(state_file).exists():
            try:
                state = json.loads(Path(state_file).read_text())
                self._last_event_id = state.get("last_event_id")
            except Exception as e:
                logger.warning(f"Failed to load listener state: {e}")

    def _save_state(self) -> None:
        """Save listener state to file."""
        if not self.state_file:
            return
        try:
            state = {
                "last_event_id": self._last_event_id,
                "last_updated": datetime.utcnow().isoformat() + "Z",
            }
            Path(self.state_file).write_text(json.dumps(state, indent=2))
        except Exception as e:
            logger.error(f"Failed to save listener state: {e}")

    async def start(self, callback: Callable[[Dict[str, Any]], None]) -> None:
        """Start listening for Discord events."""
        self._running = True
        self._callback = callback
        logger.info(f"Starting Discord listener (polling every {self.poll_interval}s)")

        while self._running:
            try:
                events = await self._poll_events()
                for event in events:
                    if self._callback:
                        await self._callback(event)
            except Exception as e:
                logger.error(f"Error in listener loop: {e}")
                await asyncio.sleep(5)  # Brief backoff before retry

            await asyncio.sleep(self.poll_interval)

    def stop(self) -> None:
        """Stop the listener gracefully."""
        logger.info("Stopping Discord listener")
        self._running = False
        self._save_state()

    async def _poll_events(self) -> List[Dict[str, Any]]:
        """Poll for new events. Override in subclass for custom polling logic."""
        # Default implementation - returns empty list
        # In production, this could poll Discord API or a message queue
        return []

    def run_sync(self, callback: Callable[[Dict[str, Any]], None]) -> None:
        """Run listener in synchronous mode (blocking)."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        try:
            loop.run_until_complete(self.start(callback))
        except KeyboardInterrupt:
            self.stop()
        finally:
            loop.close()


# Backwards compatibility - keep old class name
class DiscordClient(DiscordTransport):
    """Backwards-compatible Discord client (alias for DiscordTransport)."""
    pass
