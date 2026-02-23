"""Rate limiting with TTL-based cleanup and configurable limits."""

import time
from collections import defaultdict, OrderedDict
from typing import Dict, Optional, Tuple


class RateLimiter:
    """Simple in-memory rate limiter with TTL-based cleanup.
    
    Uses sliding window approach with timestamp tracking.
    Automatically cleans up stale entries to prevent memory growth.
    """
    
    def __init__(
        self,
        requests_per_minute: int = 30,
        window_seconds: int = 60,
        max_entries: int = 10000,
        cleanup_threshold: int = 1000,
    ):
        self.requests_per_minute = requests_per_minute
        self.window_seconds = window_seconds
        self.max_entries = max_entries
        self.cleanup_threshold = cleanup_threshold
        
        # Track request timestamps per key: key -> [timestamp1, timestamp2, ...]
        self._requests: Dict[str, list] = defaultdict(list)
        self._last_cleanup = time.time()
    
    def _cleanup_stale(self) -> None:
        """Remove stale entries older than window_seconds."""
        now = time.time()
        cutoff = now - self.window_seconds
        
        # Only cleanup periodically to avoid overhead
        if len(self._requests) < self.cleanup_threshold:
            return
        
        # Clean up old timestamps
        for key in list(self._requests.keys()):
            self._requests[key] = [ts for ts in self._requests[key] if ts > cutoff]
            if not self._requests[key]:
                del self._requests[key]
        
        # If still too many keys, evict oldest keys
        if len(self._requests) > self.max_entries:
            # Keep most recent entries
            keys_to_remove = list(self._requests.keys())[:-self.max_entries]
            for key in keys_to_remove:
                del self._requests[key]
        
        self._last_cleanup = now
    
    def is_allowed(self, key: str) -> Tuple[bool, Dict]:
        """Check if request is allowed.
        
        Returns (allowed, info) where info contains:
            - remaining: remaining requests in window
            - reset_seconds: seconds until window resets
        """
        self._cleanup_stale()
        
        now = time.time()
        cutoff = now - self.window_seconds
        
        # Get timestamps within window
        timestamps = [ts for ts in self._requests[key] if ts > cutoff]
        
        if len(timestamps) >= self.requests_per_minute:
            # Rate limited
            reset_seconds = int(timestamps[0] + self.window_seconds - now)
            return False, {"remaining": 0, "reset_seconds": reset_seconds}
        
        # Allow request
        timestamps.append(now)
        self._requests[key] = timestamps
        
        remaining = self.requests_per_minute - len(timestamps)
        return True, {"remaining": remaining, "reset_seconds": self.window_seconds}
    
    def get_remaining(self, key: str) -> int:
        """Get remaining requests for key."""
        now = time.time()
        cutoff = now - self.window_seconds
        timestamps = [ts for ts in self._requests[key] if ts > cutoff]
        return max(0, self.requests_per_minute - len(timestamps))
    
    def reset(self, key: Optional[str] = None) -> None:
        """Reset rate limit for key, or all if key is None."""
        if key:
            self._requests.pop(key, None)
        else:
            self._requests.clear()


# Pre-configured limiters for different endpoint types
READ_RATE_LIMITER = RateLimiter(
    requests_per_minute=30,
    window_seconds=60,
    max_entries=10000,
)

WRITE_RATE_LIMITER = RateLimiter(
    requests_per_minute=10,
    window_seconds=60,
    max_entries=10000,
)


def check_rate_limit(ip: str, is_write: bool = False) -> Tuple[bool, Dict]:
    """Check if IP is allowed to make a request.
    
    Args:
        ip: Client IP address
        is_write: If True, use stricter write limits
        
    Returns:
        (allowed, info) tuple
    """
    limiter = WRITE_RATE_LIMITER if is_write else READ_RATE_LIMITER
    return limiter.is_allowed(ip)
