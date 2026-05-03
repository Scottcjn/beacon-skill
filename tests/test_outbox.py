import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from beacon_skill.outbox import OutboxManager, MAX_RETRY_ATTEMPTS, _retry_delay_seconds


class TestOutbox(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.data_dir = Path(self.tmpdir)

    def _mgr(self):
        return OutboxManager(data_dir=self.data_dir)

    def test_queue_returns_action_id(self):
        mgr = self._mgr()
        aid = mgr.queue("reply", "bcn_alice", {"kind": "hello", "text": "hi"})
        self.assertTrue(len(aid) > 0)

    def test_queue_appears_in_pending(self):
        mgr = self._mgr()
        mgr.queue("reply", "bcn_alice", {"kind": "hello"})
        items = mgr.pending()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["action_type"], "reply")
        self.assertEqual(items[0]["target_agent_id"], "bcn_alice")

    def test_mark_sent_removes_from_pending(self):
        mgr = self._mgr()
        aid = mgr.queue("reply", "bcn_alice", {"kind": "hello"})
        mgr.mark_sent(aid)
        self.assertEqual(len(mgr.pending()), 0)
        item = mgr.get(aid)
        self.assertEqual(item["status"], "sent")

    def test_mark_delivered(self):
        mgr = self._mgr()
        aid = mgr.queue("contact", "bcn_bob", {"kind": "offer"})
        mgr.mark_delivered(aid)
        item = mgr.get(aid)
        self.assertEqual(item["status"], "delivered")

    def test_mark_failed(self):
        mgr = self._mgr()
        aid = mgr.queue("reply", "bcn_alice", {"kind": "hello"})
        mgr.mark_failed(aid, error="connection_refused")
        item = mgr.get(aid)
        self.assertEqual(item["status"], "failed")
        self.assertEqual(item["error"], "connection_refused")

    def test_retry_increments_attempts(self):
        mgr = self._mgr()
        aid = mgr.queue("reply", "bcn_alice", {"kind": "hello"})
        mgr.mark_retry(aid)
        item = mgr.get(aid)
        self.assertEqual(item["attempts"], 1)
        self.assertEqual(item["status"], "pending")

    @mock.patch("beacon_skill.outbox.random.uniform", return_value=0)
    def test_retry_delay_uses_exponential_backoff(self, mock_uniform):
        self.assertEqual(_retry_delay_seconds(1), 1.0)
        self.assertEqual(_retry_delay_seconds(2), 2.0)
        self.assertEqual(_retry_delay_seconds(3), 4.0)

    @mock.patch("beacon_skill.outbox.random.uniform", return_value=0.25)
    def test_retry_delay_adds_jitter(self, mock_uniform):
        self.assertEqual(_retry_delay_seconds(1), 1.25)

    @mock.patch("beacon_skill.outbox.random.uniform", return_value=0)
    @mock.patch("beacon_skill.outbox.time.time", return_value=1000.0)
    def test_mark_retry_sets_next_attempt_time(self, mock_time, mock_uniform):
        mgr = self._mgr()
        aid = mgr.queue("reply", "bcn_alice", {"kind": "hello"})
        mgr.mark_retry(aid)
        item = mgr.get(aid)
        self.assertEqual(item["next_attempt_at"], 1001.0)

    def test_pending_excludes_items_until_next_attempt(self):
        mgr = self._mgr()
        aid = mgr.queue("reply", "bcn_alice", {"kind": "hello"})
        item = mgr.get(aid)
        item["next_attempt_at"] = time.time() + 60
        pending = mgr._read_pending()
        pending[aid] = item
        mgr._write_pending(pending)

        self.assertEqual(mgr.pending(), [])

    def test_max_retries_auto_fails(self):
        mgr = self._mgr()
        aid = mgr.queue("reply", "bcn_alice", {"kind": "hello"})
        for _ in range(MAX_RETRY_ATTEMPTS):
            mgr.mark_retry(aid)
        item = mgr.get(aid)
        self.assertEqual(item["status"], "failed")
        self.assertEqual(item["error"], "max_retries_exceeded")

    def test_max_retries_excluded_from_pending(self):
        mgr = self._mgr()
        aid = mgr.queue("reply", "bcn_alice", {"kind": "hello"})
        for _ in range(MAX_RETRY_ATTEMPTS - 1):
            mgr.mark_retry(aid)
        # After 2 retries: attempts=2, still < MAX_RETRY_ATTEMPTS=3.
        # Simulate the backoff window having elapsed so the item is ready.
        pending = mgr._read_pending()
        pending[aid]["next_attempt_at"] = time.time() - 1
        mgr._write_pending(pending)
        items = mgr.pending()
        self.assertEqual(len(items), 1)
        mgr.mark_retry(aid)  # Now attempts=3 >= MAX_RETRY_ATTEMPTS → auto-fail
        self.assertEqual(len(mgr.pending()), 0)

    def test_count_pending(self):
        mgr = self._mgr()
        mgr.queue("reply", "bcn_a", {"kind": "hello"})
        mgr.queue("contact", "bcn_b", {"kind": "offer"})
        self.assertEqual(mgr.count_pending(), 2)

    def test_recent_returns_log(self):
        mgr = self._mgr()
        mgr.queue("reply", "bcn_alice", {"kind": "hello"})
        mgr.queue("contact", "bcn_bob", {"kind": "offer"})
        recent = mgr.recent(limit=10)
        self.assertEqual(len(recent), 2)
        # Most recent first
        self.assertEqual(recent[0]["target_agent_id"], "bcn_bob")

    def test_cleanup_removes_old_completed(self):
        mgr = self._mgr()
        aid = mgr.queue("reply", "bcn_alice", {"kind": "hello"})
        mgr.mark_sent(aid)
        # Manually backdate the updated_at
        pending = mgr._read_pending()
        pending[aid]["updated_at"] = int(time.time()) - 8 * 86400
        mgr._write_pending(pending)
        removed = mgr.cleanup(max_age_days=7)
        self.assertEqual(removed, 1)
        self.assertIsNone(mgr.get(aid))

    def test_cleanup_keeps_recent(self):
        mgr = self._mgr()
        aid = mgr.queue("reply", "bcn_alice", {"kind": "hello"})
        mgr.mark_sent(aid)
        removed = mgr.cleanup(max_age_days=7)
        self.assertEqual(removed, 0)
        self.assertIsNotNone(mgr.get(aid))

    def test_persistence(self):
        mgr1 = self._mgr()
        aid = mgr1.queue("reply", "bcn_alice", {"kind": "hello"})
        mgr2 = self._mgr()
        item = mgr2.get(aid)
        self.assertIsNotNone(item)
        self.assertEqual(item["status"], "pending")

    def test_source_and_conversation_id(self):
        mgr = self._mgr()
        aid = mgr.queue("reply", "bcn_alice", {"kind": "hello"},
                         source="rule:greet_newcomers", conversation_id="conv_abc123")
        item = mgr.get(aid)
        self.assertEqual(item["source"], "rule:greet_newcomers")
        self.assertEqual(item["conversation_id"], "conv_abc123")

    def test_pending_limit(self):
        mgr = self._mgr()
        for i in range(10):
            mgr.queue("reply", f"bcn_{i}", {"kind": "hello"})
        items = mgr.pending(limit=3)
        self.assertEqual(len(items), 3)


if __name__ == "__main__":
    unittest.main()
