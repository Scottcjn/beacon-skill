import tempfile
import unittest
from pathlib import Path

from beacon_skill.trust import TrustManager


class TestTrust(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.data_dir = Path(self.tmpdir)

    def _mgr(self):
        return TrustManager(data_dir=self.data_dir)

    def test_record_and_score(self):
        mgr = self._mgr()
        mgr.record("bcn_alice", "in", "hello", outcome="ok")
        mgr.record("bcn_alice", "in", "bounty", outcome="ok")
        mgr.record("bcn_alice", "out", "pay", outcome="paid", rtc=10.0)
        s = mgr.score("bcn_alice")
        self.assertEqual(s["total"], 3)
        self.assertGreater(s["score"], 0)
        self.assertEqual(s["rtc_volume"], 10.0)

    def test_negative_outcomes_weigh_more(self):
        mgr = self._mgr()
        mgr.record("bcn_bad", "in", "ad", outcome="spam")
        mgr.record("bcn_bad", "in", "hello", outcome="ok")
        s = mgr.score("bcn_bad")
        # 1 ok, 1 spam: (1 - 1*3)/2 = -1.0
        self.assertLess(s["score"], 0)

    def test_scores_all_agents(self):
        mgr = self._mgr()
        mgr.record("bcn_a", "in", "hello", outcome="ok")
        mgr.record("bcn_b", "in", "hello", outcome="spam")
        all_scores = mgr.scores()
        self.assertEqual(len(all_scores), 2)
        # bcn_a should have higher score
        self.assertGreater(all_scores[0]["score"], all_scores[1]["score"])

    def test_block_unblock(self):
        mgr = self._mgr()
        self.assertFalse(mgr.is_blocked("bcn_x"))
        mgr.block("bcn_x", reason="test")
        self.assertTrue(mgr.is_blocked("bcn_x"))
        blocked = mgr.blocked_list()
        self.assertIn("bcn_x", blocked)
        mgr.unblock("bcn_x")
        self.assertFalse(mgr.is_blocked("bcn_x"))

    def test_block_persistence(self):
        mgr1 = self._mgr()
        mgr1.block("bcn_persist", reason="bad")
        mgr2 = self._mgr()
        self.assertTrue(mgr2.is_blocked("bcn_persist"))

    def test_hold_release_review_flow(self):
        mgr = self._mgr()
        mgr.hold("bcn_hold", reason="needs coaching")
        self.assertEqual(mgr.review_status("bcn_hold"), "needs_review")
        self.assertFalse(mgr.is_blocked("bcn_hold"))
        ok, reason = mgr.can_interact("bcn_hold")
        self.assertFalse(ok)
        self.assertEqual(reason, "needs_review")

        review = mgr.review_list()
        self.assertEqual(review["bcn_hold"]["reason"], "needs coaching")

        mgr.release("bcn_hold", reviewer_note="manual release")
        self.assertEqual(mgr.review_status("bcn_hold"), "ok")
        ok, reason = mgr.can_interact("bcn_hold")
        self.assertTrue(ok)
        self.assertEqual(reason, "")

    def test_empty_score(self):
        mgr = self._mgr()
        s = mgr.score("bcn_unknown")
        self.assertEqual(s["score"], 0)
        self.assertEqual(s["total"], 0)

    def test_score_clamped(self):
        mgr = self._mgr()
        # Many spam reports should clamp to -1.0
        for _ in range(20):
            mgr.record("bcn_spammer", "in", "ad", outcome="scam")
        s = mgr.score("bcn_spammer")
        self.assertEqual(s["score"], -1.0)


if __name__ == "__main__":
    unittest.main()
