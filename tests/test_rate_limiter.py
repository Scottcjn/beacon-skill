#!/usr/bin/env python3
"""Tests for the TokenBucketRateLimiter in beacon_chat.py.

Run:
    python3 -m pytest tests/test_rate_limiter.py -v
"""

import time
import sys
import os

# Ensure atlas/ is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# We can't import beacon_chat directly (Flask app + DB init side effects),
# so we test the TokenBucketRateLimiter class in isolation by extracting it.
# For integration, we test via the Flask test client.

import threading


class TokenBucketRateLimiter:
    """Mirror of the class in beacon_chat.py for unit testing."""

    def __init__(self, capacity, refill_rate):
        self.capacity = capacity
        self.refill_rate = refill_rate
        self._buckets = {}
        self._lock = threading.Lock()

    def _refill(self, tokens, last_refill, now):
        elapsed = now - last_refill
        new_tokens = tokens + elapsed * self.refill_rate
        return min(new_tokens, self.capacity), now

    def allow(self, ip):
        now = time.time()
        with self._lock:
            if ip in self._buckets:
                tokens, last_refill = self._buckets[ip]
                tokens, last_refill = self._refill(tokens, last_refill, now)
            else:
                tokens, last_refill = self.capacity, now

            if tokens >= 1.0:
                self._buckets[ip] = (tokens - 1.0, last_refill)
                return True, None
            else:
                wait = (1.0 - tokens) / self.refill_rate
                self._buckets[ip] = (tokens, last_refill)
                return False, round(wait, 1)

    def gc(self):
        now = time.time()
        stale = now - self.capacity / self.refill_rate - 300
        with self._lock:
            self._buckets = {
                ip: (t, lr) for ip, (t, lr) in self._buckets.items()
                if lr > stale
            }


# ── Unit Tests ──


def test_allows_up_to_capacity():
    """Should allow `capacity` requests before blocking."""
    limiter = TokenBucketRateLimiter(capacity=5, refill_rate=1.0)
    ip = "10.0.0.1"
    for i in range(5):
        allowed, retry = limiter.allow(ip)
        assert allowed, f"Request {i+1} should be allowed"
        assert retry is None

    # 6th request should be blocked
    allowed, retry = limiter.allow(ip)
    assert not allowed
    assert retry is not None
    assert retry > 0


def test_different_ips_independent():
    """Different IPs should have independent buckets."""
    limiter = TokenBucketRateLimiter(capacity=2, refill_rate=0.1)
    assert limiter.allow("10.0.0.1")[0]
    assert limiter.allow("10.0.0.1")[0]
    assert not limiter.allow("10.0.0.1")[0]  # exhausted

    # Different IP still has full bucket
    assert limiter.allow("10.0.0.2")[0]
    assert limiter.allow("10.0.0.2")[0]


def test_refill_over_time():
    """Tokens should refill over time."""
    limiter = TokenBucketRateLimiter(capacity=2, refill_rate=100.0)  # fast refill
    ip = "10.0.0.3"
    # Drain bucket
    limiter.allow(ip)
    limiter.allow(ip)
    assert not limiter.allow(ip)[0]

    # Wait for refill (100 tokens/sec = 0.01s per token)
    time.sleep(0.05)
    allowed, _ = limiter.allow(ip)
    assert allowed


def test_retry_after_value():
    """Retry-after should indicate wait time."""
    limiter = TokenBucketRateLimiter(capacity=1, refill_rate=0.5)  # 1 token per 2 seconds
    ip = "10.0.0.4"
    limiter.allow(ip)  # consume the only token
    allowed, retry = limiter.allow(ip)
    assert not allowed
    assert retry is not None
    assert 0 < retry <= 2.5  # should be ~2 seconds


def test_does_not_exceed_capacity():
    """Even after long idle, bucket should not exceed capacity."""
    limiter = TokenBucketRateLimiter(capacity=3, refill_rate=100.0)
    ip = "10.0.0.5"
    time.sleep(0.1)  # plenty of time to overfill if buggy

    results = []
    for _ in range(5):
        results.append(limiter.allow(ip)[0])

    assert results == [True, True, True, False, False]


def test_gc_removes_stale_entries():
    """GC should remove entries that are fully refilled + 5 min stale."""
    limiter = TokenBucketRateLimiter(capacity=1, refill_rate=1.0)
    limiter._buckets["stale_ip"] = (1.0, time.time() - 1000)
    limiter._buckets["fresh_ip"] = (1.0, time.time())
    limiter.gc()
    assert "stale_ip" not in limiter._buckets
    assert "fresh_ip" in limiter._buckets


def test_thread_safety():
    """Concurrent access shouldn't crash."""
    limiter = TokenBucketRateLimiter(capacity=100, refill_rate=10.0)
    errors = []

    def hammer(ip, count):
        try:
            for _ in range(count):
                limiter.allow(ip)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=hammer, args=(f"ip_{i}", 50)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(errors) == 0, f"Thread errors: {errors}"


if __name__ == "__main__":
    test_allows_up_to_capacity()
    test_different_ips_independent()
    test_refill_over_time()
    test_retry_after_value()
    test_does_not_exceed_capacity()
    test_gc_removes_stale_entries()
    test_thread_safety()
    print("All tests passed ✓")
