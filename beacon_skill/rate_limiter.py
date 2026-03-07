# SPDX-License-Identifier: MIT
"""Per-endpoint API rate limiting for the Beacon webhook server.

Provides a simple in-memory token-bucket rate limiter with configurable
per-endpoint limits. Designed for the stdlib HTTPServer — no Flask required.

Usage:
    limiter = RateLimiter(default_rpm=60)
    limiter.set_limit("/api/miners", rpm=30)

    # In request handler:
    if not limiter.allow(path, client_ip):
        send_429_response()
"""

from __future__ import annotations

import time
import threading
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple


@dataclass
class _Bucket:
    """Token bucket for a single (endpoint, client) pair."""
    tokens: float
    max_tokens: float
    refill_rate: float  # tokens per second
    last_refill: float = field(default_factory=time.monotonic)

    def consume(self) -> bool:
        """Try to consume one token. Returns True if allowed."""
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(self.max_tokens, self.tokens + elapsed * self.refill_rate)
        self.last_refill = now

        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False

    @property
    def retry_after_s(self) -> float:
        """Seconds until the next token is available."""
        if self.tokens >= 1.0:
            return 0.0
        return max(0.0, (1.0 - self.tokens) / self.refill_rate)


class RateLimiter:
    """In-memory per-endpoint, per-client rate limiter.

    Args:
        default_rpm: Default requests per minute for any /api/* endpoint.
        burst: Burst allowance (extra tokens above steady-state).
        cleanup_interval_s: How often to prune expired buckets.
    """

    def __init__(
        self,
        default_rpm: int = 60,
        burst: int = 10,
        cleanup_interval_s: float = 300.0,
    ):
        self.default_rpm = default_rpm
        self.burst = burst
        self.cleanup_interval_s = cleanup_interval_s

        self._endpoint_limits: Dict[str, int] = {}  # path -> rpm
        self._buckets: Dict[Tuple[str, str], _Bucket] = {}  # (path, ip) -> bucket
        self._lock = threading.Lock()
        self._last_cleanup = time.monotonic()

    def set_limit(self, path: str, rpm: int) -> None:
        """Set a custom rate limit for a specific endpoint.

        Args:
            path: The endpoint path (e.g., "/api/miners").
            rpm: Requests per minute allowed per client IP.
        """
        self._endpoint_limits[path] = rpm

    def get_limit(self, path: str) -> int:
        """Get the effective RPM limit for a path."""
        # Exact match first
        if path in self._endpoint_limits:
            return self._endpoint_limits[path]
        # Prefix match for nested paths
        for prefix, rpm in self._endpoint_limits.items():
            if path.startswith(prefix):
                return rpm
        return self.default_rpm

    def allow(self, path: str, client_ip: str) -> Tuple[bool, float]:
        """Check if a request is allowed.

        Args:
            path: Request path.
            client_ip: Client IP address.

        Returns:
            (allowed, retry_after_seconds) tuple.
        """
        # Only rate-limit /api/* endpoints
        if not path.startswith("/api/"):
            return True, 0.0

        rpm = self.get_limit(path)
        refill_rate = rpm / 60.0  # tokens per second
        max_tokens = float(rpm + self.burst)
        key = (path, client_ip)

        with self._lock:
            self._maybe_cleanup()

            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = _Bucket(
                    tokens=max_tokens,
                    max_tokens=max_tokens,
                    refill_rate=refill_rate,
                )
                self._buckets[key] = bucket

            allowed = bucket.consume()
            retry_after = 0.0 if allowed else bucket.retry_after_s
            return allowed, retry_after

    def _maybe_cleanup(self) -> None:
        """Remove stale buckets to prevent memory leaks."""
        now = time.monotonic()
        if now - self._last_cleanup < self.cleanup_interval_s:
            return

        self._last_cleanup = now
        stale_keys = []
        for key, bucket in self._buckets.items():
            # If bucket is full (no activity), it's stale
            if now - bucket.last_refill > self.cleanup_interval_s:
                stale_keys.append(key)

        for key in stale_keys:
            del self._buckets[key]

    def reset(self) -> None:
        """Clear all buckets (for testing)."""
        with self._lock:
            self._buckets.clear()

    @property
    def active_buckets(self) -> int:
        """Number of active rate limit buckets."""
        with self._lock:
            return len(self._buckets)
