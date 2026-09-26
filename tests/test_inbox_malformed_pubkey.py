"""Regression tests: a malformed pubkey in one stored envelope must not make
the whole inbox unreadable.

Before the fix, _learn_key_from_envelope() called bytes.fromhex() on the
envelope's attacker-controlled ``pubkey``. Any peer that delivered one
envelope with a non-hex pubkey (persisted to inbox.jsonl by the UDP/webhook
listeners) made every later read_inbox() — and so `beacon inbox list`,
inbox_count(), get_entry_by_nonce() — raise ValueError.
"""

import json
import os
import shutil
import tempfile
import time
import unittest
from unittest import mock

from beacon_skill.codec import encode_envelope
from beacon_skill.identity import AgentIdentity
from beacon_skill.inbox import _learn_key_from_envelope


class TestLearnKeyMalformed(unittest.TestCase):
    def test_non_hex_pubkey_ignored(self):
        keys = _learn_key_from_envelope({"agent_id": "bcn_x", "pubkey": "zz"}, {})
        self.assertEqual(keys, {})

    def test_wrong_length_pubkey_ignored(self):
        keys = _learn_key_from_envelope({"agent_id": "bcn_x", "pubkey": "ab" * 5}, {})
        self.assertEqual(keys, {})

    def test_non_string_fields_ignored(self):
        self.assertEqual(_learn_key_from_envelope({"agent_id": "bcn_x", "pubkey": 12345}, {}), {})
        self.assertEqual(_learn_key_from_envelope({"agent_id": {"a": 1}, "pubkey": "ab" * 32}, {}), {})

    def test_valid_pubkey_still_learned(self):
        ident = AgentIdentity.generate()
        keys = _learn_key_from_envelope(
            {"agent_id": ident.agent_id, "pubkey": ident.public_key_hex}, {}
        )
        self.assertEqual(keys[ident.agent_id]["pubkey_hex"], ident.public_key_hex)


class TestReadInboxSurvivesMalformedEntry(unittest.TestCase):
    def setUp(self):
        self._tmp_home = tempfile.mkdtemp(prefix="beacon_inbox_bad_")
        self._env = mock.patch.dict(os.environ, {"HOME": self._tmp_home}, clear=False)
        self._env.start()
        os.environ.pop("BEACON_INBOX_PATH", None)

    def tearDown(self):
        self._env.stop()
        shutil.rmtree(self._tmp_home, ignore_errors=True)

    def test_good_entries_still_returned(self):
        from beacon_skill.inbox import inbox_count, read_inbox
        from beacon_skill.storage import _inbox_path

        ident = AgentIdentity.generate()
        good = encode_envelope({"kind": "hello", "ts": int(time.time())},
                               version=2, identity=ident, include_pubkey=True)
        bad_env = {"kind": "hello", "agent_id": "bcn_attacker", "pubkey": "not-hex",
                   "nonce": "badnonce0001", "sig": "00" * 64}
        lines = [
            {"platform": "udp", "received_at": time.time(), "envelopes": [bad_env]},
            {"platform": "udp", "received_at": time.time(), "text": good},
        ]
        _inbox_path().write_text("".join(json.dumps(l) + "\n" for l in lines), encoding="utf-8")

        entries = read_inbox()
        self.assertEqual(len(entries), 2)
        by_agent = {e["envelope"].get("agent_id"): e for e in entries}
        self.assertIs(by_agent["bcn_attacker"]["verified"], False)
        self.assertIs(by_agent[ident.agent_id]["verified"], True)
        self.assertEqual(inbox_count(), 2)


if __name__ == "__main__":
    unittest.main()
