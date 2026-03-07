# SPDX-License-Identifier: MIT
"""Tests for the per-endpoint API rate limiter."""

import time
import unittest
from unittest.mock import patch

from beacon_skill.rate_limiter import RateLimiter, _Bucket


class TestBucket(unittest.TestCase):
    """Token bucket internals."""

    def test_consume_when_tokens_available(self):
        b = _Bucket(tokens=5.0, max_tokens=10.0, refill_rate=1.0)
        self.assertTrue(b.consume())
        self.assertAlmostEqual(b.tokens, 4.0, delta=0.1)

    def test_consume_when_empty(self):
        b = _Bucket(tokens=0.0, max_tokens=10.0, refill_rate=1.0)
        b.last_refill = time.monotonic()
        self.assertFalse(b.consume())

    def test_refill_over_time(self):
        b = _Bucket(tokens=0.0, max_tokens=10.0, refill_rate=100.0)
        b.last_refill = time.monotonic() - 1.0  # 1 second ago
        self.assertTrue(b.consume())  # Should have refilled

    def test_tokens_capped_at_max(self):
        b = _Bucket(tokens=10.0, max_tokens=10.0, refill_rate=100.0)
        b.last_refill = time.monotonic() - 10.0
        b.consume()
        self.assertLessEqual(b.tokens, b.max_tokens)

    def test_retry_after_when_empty(self):
        b = _Bucket(tokens=0.0, max_tokens=10.0, refill_rate=1.0)
        b.last_refill = time.monotonic()
        self.assertGreater(b.retry_after_s, 0)

    def test_retry_after_when_available(self):
        b = _Bucket(tokens=5.0, max_tokens=10.0, refill_rate=1.0)
        self.assertEqual(b.retry_after_s, 0.0)


class TestRateLimiter(unittest.TestCase):
    """Rate limiter public API."""

    def test_non_api_paths_always_allowed(self):
        limiter = RateLimiter(default_rpm=1)
        allowed, _ = limiter.allow("/beacon/health", "1.2.3.4")
        self.assertTrue(allowed)

    def test_api_path_allowed_within_limit(self):
        limiter = RateLimiter(default_rpm=60, burst=10)
        allowed, _ = limiter.allow("/api/miners", "1.2.3.4")
        self.assertTrue(allowed)

    def test_api_path_blocked_after_burst(self):
        limiter = RateLimiter(default_rpm=1, burst=0)
        # First request: uses the initial token (rpm + burst = 1)
        allowed1, _ = limiter.allow("/api/miners", "1.2.3.4")
        self.assertTrue(allowed1)
        # Second request: should be blocked
        allowed2, retry = limiter.allow("/api/miners", "1.2.3.4")
        self.assertFalse(allowed2)
        self.assertGreater(retry, 0)

    def test_different_clients_independent(self):
        limiter = RateLimiter(default_rpm=1, burst=0)
        limiter.allow("/api/miners", "1.1.1.1")
        # Different IP should still be allowed
        allowed, _ = limiter.allow("/api/miners", "2.2.2.2")
        self.assertTrue(allowed)

    def test_different_endpoints_independent(self):
        limiter = RateLimiter(default_rpm=1, burst=0)
        limiter.allow("/api/miners", "1.1.1.1")
        # Different endpoint should still be allowed
        allowed, _ = limiter.allow("/api/nodes", "1.1.1.1")
        self.assertTrue(allowed)

    def test_custom_endpoint_limit(self):
        limiter = RateLimiter(default_rpm=100, burst=0)
        limiter.set_limit("/api/miners", rpm=1)
        limiter.allow("/api/miners", "1.1.1.1")
        allowed, _ = limiter.allow("/api/miners", "1.1.1.1")
        self.assertFalse(allowed)
        # Other endpoints still use default
        allowed2, _ = limiter.allow("/api/nodes", "1.1.1.1")
        self.assertTrue(allowed2)

    def test_get_limit_default(self):
        limiter = RateLimiter(default_rpm=60)
        self.assertEqual(limiter.get_limit("/api/unknown"), 60)

    def test_get_limit_custom(self):
        limiter = RateLimiter(default_rpm=60)
        limiter.set_limit("/api/miners", rpm=30)
        self.assertEqual(limiter.get_limit("/api/miners"), 30)

    def test_reset_clears_buckets(self):
        limiter = RateLimiter(default_rpm=60)
        limiter.allow("/api/test", "1.1.1.1")
        self.assertGreater(limiter.active_buckets, 0)
        limiter.reset()
        self.assertEqual(limiter.active_buckets, 0)

    def test_cleanup_removes_stale_buckets(self):
        limiter = RateLimiter(default_rpm=60, cleanup_interval_s=0)
        limiter.allow("/api/test", "1.1.1.1")
        # Manually age the bucket
        with limiter._lock:
            for bucket in limiter._buckets.values():
                bucket.last_refill = time.monotonic() - 999
            limiter._last_cleanup = 0
        # Next call triggers cleanup
        limiter.allow("/api/other", "2.2.2.2")
        # Stale bucket should be cleaned
        self.assertEqual(limiter.active_buckets, 1)

    def test_prefix_match_for_nested_paths(self):
        limiter = RateLimiter(default_rpm=100)
        limiter.set_limit("/api/miners", rpm=5)
        self.assertEqual(limiter.get_limit("/api/miners/active"), 5)


class TestWebhookIntegration(unittest.TestCase):
    """Test rate limiter integration with webhook handler."""

    def test_rate_limiter_parameter_accepted(self):
        """WebhookServer should accept rate_limiter parameter."""
        from beacon_skill.transports.webhook import WebhookServer
        limiter = RateLimiter(default_rpm=30)
        server = WebhookServer(port=0, rate_limiter=limiter)
        self.assertIs(server.rate_limiter, limiter)


if __name__ == "__main__":
    unittest.main()
