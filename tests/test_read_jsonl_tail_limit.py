# SPDX-License-Identifier: MIT
"""read_jsonl_tail must honor the "last N entries" contract at the boundary.

`lines[-limit:]` silently widens the result for two invalid inputs:
  limit == 0  ->  lines[-0:] == lines[:]   (returns EVERYTHING)
  limit  < 0  ->  lines[N:]                (returns a large slice)

Both contradict "read the last N entries" and expand every history/log path
that reuses the helper (relay, memory-market, hybrid-district,
proof-of-thought, rules log). Reported by @antoleod under #254.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from beacon_skill import storage


class ReadJsonlTailLimitTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self._patch = patch.object(storage, "_dir", return_value=self.tmp)
        self._patch.start()
        self.path = storage._safe_path("test_tail.jsonl")
        with self.path.open("w", encoding="utf-8") as fh:
            for i in range(5):
                fh.write(json.dumps({"i": i}) + "\n")

    def tearDown(self):
        self._patch.stop()
        self._tmp.cleanup()

    def test_zero_limit_returns_empty(self):
        # The bug: lines[-0:] returned the whole file. Must be [].
        self.assertEqual(storage.read_jsonl_tail("test_tail.jsonl", 0), [])

    def test_negative_limit_is_rejected(self):
        with self.assertRaises(ValueError):
            storage.read_jsonl_tail("test_tail.jsonl", -2)

    def test_positive_limit_returns_last_n(self):
        got = storage.read_jsonl_tail("test_tail.jsonl", 2)
        self.assertEqual([e["i"] for e in got], [3, 4])

    def test_limit_larger_than_file_returns_all(self):
        got = storage.read_jsonl_tail("test_tail.jsonl", 100)
        self.assertEqual([e["i"] for e in got], [0, 1, 2, 3, 4])


if __name__ == "__main__":
    unittest.main()
