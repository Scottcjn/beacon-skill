import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from beacon_skill.outbox import OutboxManager
from beacon_skill.conversations import ConversationManager
from beacon_skill.executor import ActionExecutor


class TestExecutor(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.data_dir = Path(self.tmpdir)
        self.outbox = OutboxManager(data_dir=self.data_dir)
        self.conversations = ConversationManager(data_dir=self.data_dir, my_agent_id="bcn_me")
        self.trust_mgr = MagicMock()
        self.trust_mgr.is_blocked.return_value = False
        self.trust_mgr.can_interact.return_value = (True, "")
        self.trust_mgr.record = MagicMock()
        self.presence_mgr = MagicMock()
        self.presence_mgr.get_agent.return_value = None
        self.match_mgr = MagicMock()
        self.match_mgr.can_contact.return_value = True
        self.match_mgr.record_contact = MagicMock()

    def _executor(self, **kwargs):
        defaults = {
            "outbox": self.outbox,
            "identity": None,
            "cfg": {"udp": {"enabled": True, "host": "255.255.255.255", "port": 38400}},
            "trust_mgr": self.trust_mgr,
            "presence_mgr": self.presence_mgr,
            "match_mgr": self.match_mgr,
            "conversations": self.conversations,
        }
        defaults.update(kwargs)
        return ActionExecutor(**defaults)

    # ── Queuing tests ──

    def test_queue_rule_reply(self):
        ex = self._executor()
        action = {"action": "reply", "rule": "greet", "envelope": {"kind": "hello", "to": "bcn_alice", "text": "hi"}}
        event = {"envelope": {"agent_id": "bcn_alice", "kind": "hello"}}
        aid = ex.queue_rule_action(action, event)
        self.assertIsNotNone(aid)
        self.assertEqual(self.outbox.count_pending(), 1)

    def test_queue_rule_blocked_agent_skipped(self):
        self.trust_mgr.can_interact.return_value = (False, "blocked")
        ex = self._executor()
        action = {"action": "reply", "envelope": {"kind": "hello", "to": "bcn_bad"}}
        aid = ex.queue_rule_action(action, {})
        self.assertIsNone(aid)

    def test_queue_rule_unknown_action_ignored(self):
        ex = self._executor()
        action = {"action": "log"}
        aid = ex.queue_rule_action(action, {})
        self.assertIsNone(aid)

    def test_queue_contact(self):
        ex = self._executor()
        match = {"agent_id": "bcn_bob", "reasons": ["offers: python"]}
        aid = ex.queue_contact(match, my_offers=["python"], my_needs=["rust"])
        self.assertIsNotNone(aid)
        item = self.outbox.get(aid)
        self.assertEqual(item["action_type"], "contact")
        self.assertIn("python", item["envelope"].get("text", ""))

    def test_queue_contact_blocked_skipped(self):
        self.trust_mgr.can_interact.return_value = (False, "blocked")
        ex = self._executor()
        match = {"agent_id": "bcn_blocked"}
        aid = ex.queue_contact(match)
        self.assertIsNone(aid)

    def test_queue_contact_review_hold_skipped(self):
        self.trust_mgr.can_interact.return_value = (False, "needs_review")
        ex = self._executor()
        match = {"agent_id": "bcn_review"}
        aid = ex.queue_contact(match)
        self.assertIsNone(aid)

    def test_queue_contact_cooldown_skipped(self):
        self.match_mgr.can_contact.return_value = False
        ex = self._executor()
        match = {"agent_id": "bcn_recent"}
        aid = ex.queue_contact(match)
        self.assertIsNone(aid)

    def test_queue_contact_waiting_for_reply_skipped(self):
        ex = self._executor()
        # First contact queued OK
        match = {"agent_id": "bcn_alice", "reasons": ["test"]}
        aid = ex.queue_contact(match)
        self.assertIsNotNone(aid)
        # Simulate that we sent and are waiting
        conv = self.conversations.get_or_create("bcn_alice", "match")
        self.conversations.record_message(conv["conversation_id"], "out", "hello")
        # Second contact should be skipped
        aid2 = ex.queue_contact(match)
        self.assertIsNone(aid2)

    def test_queue_offer(self):
        ex = self._executor()
        suggestion = {"agent_id": "bcn_charlie", "description": "Can help with deployment", "goal_id": "g_123"}
        aid = ex.queue_offer(suggestion)
        self.assertIsNotNone(aid)
        item = self.outbox.get(aid)
        self.assertEqual(item["action_type"], "offer")
        self.assertIn("goal:g_123", item["source"])

    def test_queue_emit(self):
        ex = self._executor()
        envelope = {"kind": "ping", "to": "bcn_someone", "text": "manual send"}
        aid = ex.queue_emit(envelope, source="manual")
        self.assertIsNotNone(aid)
        item = self.outbox.get(aid)
        self.assertEqual(item["source"], "manual")

    # ── Transport resolution tests ──

    def test_resolve_transport_hint_webhook(self):
        ex = self._executor()
        item = {"transport_hint": "webhook:https://example.com/beacon/inbox", "target_agent_id": "bcn_x"}
        method, addr = ex._resolve_transport(item)
        self.assertEqual(method, "webhook")
        self.assertEqual(addr, "https://example.com/beacon/inbox")

    def test_resolve_transport_hint_udp(self):
        ex = self._executor()
        item = {"transport_hint": "udp:192.168.1.1:38400", "target_agent_id": "bcn_x"}
        method, addr = ex._resolve_transport(item)
        self.assertEqual(method, "udp")
        self.assertEqual(addr, "192.168.1.1:38400")

    def test_resolve_transport_roster_card(self):
        self.presence_mgr.get_agent.return_value = {
            "agent_id": "bcn_x",
            "card_url": "https://agent.example/.well-known/beacon.json",
        }
        ex = self._executor()
        item = {"transport_hint": "", "target_agent_id": "bcn_x"}
        method, addr = ex._resolve_transport(item)
        self.assertEqual(method, "webhook")
        self.assertEqual(addr, "https://agent.example/beacon/inbox")

    def test_resolve_transport_udp_fallback(self):
        ex = self._executor()
        item = {"transport_hint": "", "target_agent_id": "bcn_unknown"}
        method, addr = ex._resolve_transport(item)
        self.assertEqual(method, "udp")
        self.assertIn("38400", addr)

    def test_resolve_transport_no_transport(self):
        ex = self._executor(cfg={"udp": {"enabled": False}})
        item = {"transport_hint": "", "target_agent_id": "bcn_unknown"}
        method, addr = ex._resolve_transport(item)
        self.assertEqual(method, "")

    # ── Drain tests ──

    @patch("beacon_skill.executor.ActionExecutor._execute_transport")
    def test_drain_sends_pending(self, mock_transport):
        mock_transport.return_value = {"ok": True}
        ex = self._executor()
        ex.queue_emit({"kind": "test", "to": "bcn_alice"})
        results = ex.drain(max_actions=10)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "sent")
        self.assertEqual(self.outbox.count_pending(), 0)

    @patch("beacon_skill.executor.ActionExecutor._execute_transport")
    def test_drain_records_trust(self, mock_transport):
        mock_transport.return_value = {"ok": True}
        ex = self._executor()
        ex.queue_emit({"kind": "hello", "to": "bcn_alice"})
        ex.drain()
        self.trust_mgr.record.assert_called()

    @patch("beacon_skill.executor.ActionExecutor._execute_transport")
    def test_drain_records_contact(self, mock_transport):
        mock_transport.return_value = {"ok": True}
        ex = self._executor()
        ex.queue_emit({"kind": "hello", "to": "bcn_alice"})
        ex.drain()
        self.match_mgr.record_contact.assert_called_with("bcn_alice")

    @patch("beacon_skill.executor.ActionExecutor._execute_transport")
    def test_drain_retries_on_failure(self, mock_transport):
        mock_transport.return_value = {"ok": False, "error": "timeout"}
        ex = self._executor()
        aid = ex.queue_emit({"kind": "test", "to": "bcn_alice"})
        results = ex.drain()
        self.assertEqual(results[0]["status"], "failed")
        item = self.outbox.get(aid)
        self.assertEqual(item["attempts"], 1)
        self.assertEqual(item["status"], "pending")  # Still pending, can retry

    @patch("beacon_skill.executor.ActionExecutor._execute_transport")
    def test_drain_skips_blocked_at_execution(self, mock_transport):
        """Block added between queue and drain."""
        ex = self._executor()
        ex.queue_emit({"kind": "test", "to": "bcn_evil"})
        # Block after queuing
        self.trust_mgr.can_interact.return_value = (False, "blocked")
        results = ex.drain()
        self.assertEqual(results[0]["status"], "skipped")
        self.assertEqual(results[0]["reason"], "blocked")
        mock_transport.assert_not_called()

    @patch("beacon_skill.executor.ActionExecutor._execute_transport")
    def test_drain_max_actions_limit(self, mock_transport):
        mock_transport.return_value = {"ok": True}
        ex = self._executor()
        for i in range(5):
            ex.queue_emit({"kind": "test", "to": f"bcn_{i}"})
        results = ex.drain(max_actions=2)
        self.assertEqual(len(results), 2)
        self.assertEqual(self.outbox.count_pending(), 3)

    @patch("beacon_skill.executor.ActionExecutor._execute_transport")
    def test_drain_no_transport_retries(self, mock_transport):
        ex = self._executor(cfg={"udp": {"enabled": False}})
        # Also no roster entry
        ex.queue_emit({"kind": "test", "to": "bcn_unknown"})
        results = ex.drain()
        self.assertEqual(results[0]["status"], "no_transport")
        mock_transport.assert_not_called()

    def test_can_execute_blocked(self):
        self.trust_mgr.can_interact.return_value = (False, "blocked")
        ex = self._executor()
        ok, reason = ex._can_execute({"target_agent_id": "bcn_bad"})
        self.assertFalse(ok)
        self.assertEqual(reason, "blocked")

    def test_can_execute_needs_review(self):
        self.trust_mgr.can_interact.return_value = (False, "needs_review")
        ex = self._executor()
        ok, reason = ex._can_execute({"target_agent_id": "bcn_review"})
        self.assertFalse(ok)
        self.assertEqual(reason, "needs_review")

    def test_can_execute_ok(self):
        ex = self._executor()
        ok, reason = ex._can_execute({"target_agent_id": "bcn_good"})
        self.assertTrue(ok)

    # ── Conversation tracking ──

    @patch("beacon_skill.executor.ActionExecutor._execute_transport")
    def test_drain_updates_conversation(self, mock_transport):
        mock_transport.return_value = {"ok": True}
        ex = self._executor()
        match = {"agent_id": "bcn_alice", "reasons": ["test"]}
        aid = ex.queue_contact(match)
        ex.drain()
        convs = self.conversations.find_by_agent("bcn_alice")
        self.assertTrue(len(convs) > 0)
        # Conversation should have an outbound message recorded
        self.assertEqual(convs[0]["last_direction"], "out")


if __name__ == "__main__":
    unittest.main()
