import json
import tempfile
import time
import unittest
from pathlib import Path

from beacon_skill.rules import RulesEngine


class FakeTrustMgr:
    def __init__(self, scores=None):
        self._scores = scores or {}

    def score(self, agent_id):
        return self._scores.get(agent_id, {"score": 0, "total": 0})


class TestRules(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.rules_path = Path(self.tmpdir) / "rules.json"

    def _engine(self, rules=None):
        if rules:
            self.rules_path.write_text(json.dumps({"rules": rules}), encoding="utf-8")
        return RulesEngine(rules_path=self.rules_path)

    def test_match_by_kind(self):
        engine = self._engine([
            {"name": "greet", "when": {"kind": "hello"}, "then": {"action": "log", "message": "Got hello"}},
        ])
        event = {"envelope": {"kind": "hello", "agent_id": "bcn_a"}}
        matches = engine.evaluate(event)
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["rule"], "greet")

    def test_no_match(self):
        engine = self._engine([
            {"name": "greet", "when": {"kind": "hello"}, "then": {"action": "log"}},
        ])
        event = {"envelope": {"kind": "bounty", "agent_id": "bcn_a"}}
        matches = engine.evaluate(event)
        self.assertEqual(len(matches), 0)

    def test_match_kind_list(self):
        engine = self._engine([
            {"name": "multi", "when": {"kind": ["hello", "bounty"]}, "then": {"action": "log"}},
        ])
        self.assertEqual(len(engine.evaluate({"envelope": {"kind": "hello", "agent_id": "bcn_a"}})), 1)
        self.assertEqual(len(engine.evaluate({"envelope": {"kind": "bounty", "agent_id": "bcn_b"}})), 1)
        self.assertEqual(len(engine.evaluate({"envelope": {"kind": "ad", "agent_id": "bcn_c"}})), 0)

    def test_match_min_rtc(self):
        engine = self._engine([
            {"name": "rich", "when": {"kind": "bounty", "min_rtc": 50}, "then": {"action": "log"}},
        ])
        self.assertEqual(len(engine.evaluate({"envelope": {"kind": "bounty", "reward_rtc": 100, "agent_id": "bcn_a"}})), 1)
        self.assertEqual(len(engine.evaluate({"envelope": {"kind": "bounty", "reward_rtc": 10, "agent_id": "bcn_b"}})), 0)

    def test_match_trust(self):
        trust = FakeTrustMgr(scores={"bcn_good": {"score": 0.8}})
        engine = self._engine([
            {"name": "trusted", "when": {"kind": "hello", "min_trust": 0.5}, "then": {"action": "log"}},
        ])
        self.assertEqual(len(engine.evaluate({"envelope": {"kind": "hello", "agent_id": "bcn_good"}}, trust_mgr=trust)), 1)
        self.assertEqual(len(engine.evaluate({"envelope": {"kind": "hello", "agent_id": "bcn_unknown"}}, trust_mgr=trust)), 0)

    def test_match_topic(self):
        engine = self._engine([
            {"name": "python", "when": {"topic_match": ["python"]}, "then": {"action": "log"}},
        ])
        self.assertEqual(len(engine.evaluate({"envelope": {"kind": "bounty", "text": "Need python dev", "agent_id": "bcn_a"}})), 1)
        self.assertEqual(len(engine.evaluate({"envelope": {"kind": "bounty", "text": "Need rust dev", "agent_id": "bcn_b"}})), 0)

    def test_disabled_rule_skipped(self):
        engine = self._engine([
            {"name": "disabled", "when": {"kind": "hello"}, "then": {"action": "log"}, "disabled": True},
        ])
        self.assertEqual(len(engine.evaluate({"envelope": {"kind": "hello", "agent_id": "bcn_a"}})), 0)

    def test_cooldown(self):
        engine = self._engine([
            {"name": "greet", "when": {"kind": "hello"}, "then": {"action": "log"}},
        ])
        event = {"envelope": {"kind": "hello", "agent_id": "bcn_a"}}
        # First fire
        results = engine.process(event)
        self.assertEqual(len(results), 1)
        # Second fire immediately — should be cooled down
        results = engine.process(event)
        self.assertEqual(len(results), 0)

    def test_execute_reply(self):
        engine = self._engine([])
        action = {"action": "reply", "kind": "offer", "text": "I can help with $kind"}
        event = {"envelope": {"kind": "bounty", "agent_id": "bcn_a"}}
        result = engine.execute(action, event)
        self.assertEqual(result["action"], "reply")
        self.assertEqual(result["envelope"]["kind"], "offer")
        self.assertIn("bounty", result["envelope"]["text"])

    def test_execute_block(self):
        engine = self._engine([])
        action = {"action": "block", "reason": "spam from $agent_id"}
        event = {"envelope": {"kind": "ad", "agent_id": "bcn_spammer"}}
        result = engine.execute(action, event)
        self.assertEqual(result["action"], "block")
        self.assertEqual(result["agent_id"], "bcn_spammer")

    def test_variable_substitution(self):
        engine = self._engine([])
        text = engine._substitute("Hello $from, your $kind about $task_id", {
            "envelope": {"from": "alice", "kind": "bounty", "task_id": "abc123"},
        })
        self.assertIn("alice", text)
        self.assertIn("bounty", text)
        self.assertIn("abc123", text)

    def test_add_remove_rule(self):
        engine = self._engine([])
        engine.add_rule({"name": "test", "when": {"kind": "hello"}, "then": {"action": "log"}})
        self.assertEqual(len(engine.rules()), 1)
        engine.remove_rule("test")
        self.assertEqual(len(engine.rules()), 0)

    def test_verified_rule_rejects_unsigned_v1(self):
        engine = self._engine([
            {"name": "priv", "when": {"kind": "bounty", "verified": True}, "then": {"action": "log"}},
        ])
        env = {"kind": "bounty", "agent_id": "bcn_a"}
        self.assertEqual(len(engine.evaluate({"envelope": env, "verified": None})), 0)
        self.assertEqual(len(engine.evaluate({"envelope": env, "verified": False})), 0)
        self.assertEqual(len(engine.evaluate({"envelope": env, "verified": True})), 1)


if __name__ == "__main__":
    unittest.main()
