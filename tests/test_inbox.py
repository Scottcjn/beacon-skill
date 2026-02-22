import json
import os
import tempfile
import unittest
import time
from pathlib import Path
from unittest import mock

from beacon_skill.codec import encode_envelope
from beacon_skill.identity import AgentIdentity
from beacon_skill.inbox import (
    read_inbox, mark_read, inbox_count, get_entry_by_nonce, trust_key,
    load_known_keys, save_known_keys, revoke_key, rotate_key, list_keys,
    get_key_metadata, is_key_expired, DEFAULT_KEY_TTL_SECONDS,
)


class TestInbox(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.patcher = mock.patch("beacon_skill.storage._dir", return_value=Path(self.tmpdir))
        self.mock_dir = self.patcher.start()
        # Also patch inbox module's _dir reference.
        self.patcher2 = mock.patch("beacon_skill.inbox._dir", return_value=Path(self.tmpdir))
        self.mock_dir2 = self.patcher2.start()

    def tearDown(self):
        self.patcher.stop()
        self.patcher2.stop()
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_inbox(self, entries):
        path = Path(self.tmpdir) / "inbox.jsonl"
        with open(path, "w") as f:
            for entry in entries:
                f.write(json.dumps(entry) + "\n")

    def test_parse_v2_envelopes(self) -> None:
        ident = AgentIdentity.generate()
        text = encode_envelope(
            {"kind": "hello", "from": "a", "to": "b", "ts": 1},
            version=2, identity=ident, include_pubkey=True,
        )
        self._write_inbox([{
            "platform": "udp",
            "from": "127.0.0.1:38400",
            "received_at": 1000.0,
            "text": text,
            "envelopes": [],
        }])
        entries = read_inbox()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["envelope"]["kind"], "hello")
        self.assertTrue(entries[0]["verified"])

    def test_filter_by_kind(self) -> None:
        ident = AgentIdentity.generate()
        hello = encode_envelope(
            {"kind": "hello", "from": "a", "to": "b", "ts": 1},
            version=2, identity=ident,
        )
        like = encode_envelope(
            {"kind": "like", "from": "a", "to": "b", "ts": 2},
            version=2, identity=ident,
        )
        self._write_inbox([
            {"platform": "udp", "received_at": 1000.0, "text": hello, "envelopes": []},
            {"platform": "udp", "received_at": 1001.0, "text": like, "envelopes": []},
        ])
        entries = read_inbox(kind="like")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["envelope"]["kind"], "like")

    def test_dedup_via_read_tracking(self) -> None:
        ident = AgentIdentity.generate()
        text = encode_envelope(
            {"kind": "hello", "from": "a", "to": "b", "ts": 1, "nonce": "abc123def456"},
            version=2, identity=ident,
        )
        self._write_inbox([{
            "platform": "udp",
            "received_at": 1000.0,
            "text": text,
            "envelopes": [],
        }])
        entries = read_inbox(unread_only=True)
        self.assertEqual(len(entries), 1)

        # Mark as read.
        mark_read("abc123def456")

        entries = read_inbox(unread_only=True)
        self.assertEqual(len(entries), 0)

        # But still shows up without unread_only filter.
        entries = read_inbox()
        self.assertEqual(len(entries), 1)
        self.assertTrue(entries[0]["is_read"])

    def test_count(self) -> None:
        ident = AgentIdentity.generate()
        text = encode_envelope(
            {"kind": "hello", "from": "a", "to": "b", "ts": 1},
            version=2, identity=ident,
        )
        self._write_inbox([
            {"platform": "udp", "received_at": 1000.0, "text": text, "envelopes": []},
            {"platform": "udp", "received_at": 1001.0, "text": text, "envelopes": []},
        ])
        self.assertEqual(inbox_count(), 2)


class TestTOFUKeys(unittest.TestCase):
    """Tests for TOFU key management (trust, revoke, rotate, TTL)."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.patcher = mock.patch("beacon_skill.storage._dir", return_value=Path(self.tmpdir))
        self.mock_dir = self.patcher.start()
        self.patcher2 = mock.patch("beacon_skill.inbox._dir", return_value=Path(self.tmpdir))
        self.mock_dir2 = self.patcher2.start()

    def tearDown(self):
        self.patcher.stop()
        self.patcher2.stop()
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_trust_key_adds_metadata(self) -> None:
        """trust_key should store full metadata, not just pubkey."""
        agent_id = "bcn_abc123def456"
        pubkey_hex = "a" * 64
        
        trust_key(agent_id, pubkey_hex)
        
        keys = load_known_keys()
        self.assertIn(agent_id, keys)
        self.assertEqual(keys[agent_id]["pubkey_hex"], pubkey_hex)
        self.assertIn("first_seen", keys[agent_id])
        self.assertIn("last_seen", keys[agent_id])
        self.assertEqual(keys[agent_id]["rotation_count"], 0)
        self.assertEqual(keys[agent_id]["previous_keys"], [])

    def test_trust_key_updates_last_seen(self) -> None:
        """trust_key should update last_seen for existing keys."""
        agent_id = "bcn_abc123def456"
        pubkey_hex = "a" * 64
        
        trust_key(agent_id, pubkey_hex)
        time.sleep(0.01)  # Small delay
        trust_key(agent_id, pubkey_hex)
        
        keys = load_known_keys()
        # last_seen should be >= first_seen
        self.assertGreaterEqual(
            keys[agent_id]["last_seen"],
            keys[agent_id]["first_seen"]
        )

    def test_trust_key_blocks_unsigned_rotation(self) -> None:
        """trust_key should block rotation without allow_rotate=True."""
        agent_id = "bcn_abc123def456"
        old_pubkey = "a" * 64
        new_pubkey = "b" * 64
        
        trust_key(agent_id, old_pubkey)
        # Try to rotate without allow_rotate
        result = trust_key(agent_id, new_pubkey, allow_rotate=False)
        
        self.assertFalse(result)
        keys = load_known_keys()
        # Key should still be the old one
        self.assertEqual(keys[agent_id]["pubkey_hex"], old_pubkey)

    def test_trust_key_allows_rotation(self) -> None:
        """trust_key with allow_rotate=True should accept new key."""
        agent_id = "bcn_abc123def456"
        old_pubkey = "a" * 64
        new_pubkey = "b" * 64
        
        trust_key(agent_id, old_pubkey)
        result = trust_key(agent_id, new_pubkey, allow_rotate=True)
        
        self.assertTrue(result)
        keys = load_known_keys()
        self.assertEqual(keys[agent_id]["pubkey_hex"], new_pubkey)
        self.assertEqual(keys[agent_id]["rotation_count"], 1)
        self.assertIn(old_pubkey, keys[agent_id]["previous_keys"])

    def test_revoke_key(self) -> None:
        """revoke_key should remove a key."""
        agent_id = "bcn_abc123def456"
        pubkey_hex = "a" * 64
        
        trust_key(agent_id, pubkey_hex)
        self.assertIn(agent_id, load_known_keys())
        
        result = revoke_key(agent_id)
        self.assertTrue(result)
        self.assertNotIn(agent_id, load_known_keys())

    def test_revoke_nonexistent_key(self) -> None:
        """revoke_key should return False for non-existent key."""
        result = revoke_key("bcn_nonexistent")
        self.assertFalse(result)

    def test_rotate_key_with_valid_signature(self) -> None:
        """rotate_key should accept new key signed by old key."""
        # Create two identities
        old_identity = AgentIdentity.generate()
        new_identity = AgentIdentity.generate()
        
        agent_id = old_identity.agent_id
        old_pubkey = old_identity.public_key_hex
        new_pubkey = new_identity.public_key_hex
        
        # Trust the old key first
        trust_key(agent_id, old_pubkey)
        
        # Sign the new pubkey with the old private key
        signature = old_identity.sign(bytes.fromhex(new_pubkey))
        
        # Rotate to new key
        result = rotate_key(agent_id, new_pubkey, signature)
        
        self.assertTrue(result)
        keys = load_known_keys()
        self.assertEqual(keys[agent_id]["pubkey_hex"], new_pubkey)
        self.assertEqual(keys[agent_id]["rotation_count"], 1)

    def test_rotate_key_with_invalid_signature(self) -> None:
        """rotate_key should reject new key with invalid signature."""
        old_identity = AgentIdentity.generate()
        new_identity = AgentIdentity.generate()
        
        agent_id = old_identity.agent_id
        old_pubkey = old_identity.public_key_hex
        new_pubkey = new_identity.public_key_hex
        
        trust_key(agent_id, old_pubkey)
        
        # Sign with wrong key (new identity instead of old)
        signature = new_identity.sign(bytes.fromhex(new_pubkey))
        
        result = rotate_key(agent_id, new_pubkey, signature)
        
        self.assertFalse(result)
        keys = load_known_keys()
        # Should still have old key
        self.assertEqual(keys[agent_id]["pubkey_hex"], old_pubkey)

    def test_is_key_expired(self) -> None:
        """is_key_expired should detect expired keys."""
        now = time.time()
        
        # Key with recent last_seen should not be expired
        recent_key = {
            "pubkey_hex": "a" * 64,
            "first_seen": now - 100,
            "last_seen": now - 100,
            "rotation_count": 0,
            "previous_keys": [],
            "ttl_seconds": DEFAULT_KEY_TTL_SECONDS,
        }
        self.assertFalse(is_key_expired(recent_key, now))
        
        # Key with old last_seen should be expired (use > 30 days ago)
        old_key = {
            "pubkey_hex": "b" * 64,
            "first_seen": now - 40 * 24 * 60 * 60,  # 40 days ago
            "last_seen": now - 40 * 24 * 60 * 60,  # 40 days ago
            "rotation_count": 0,
            "previous_keys": [],
            "ttl_seconds": DEFAULT_KEY_TTL_SECONDS,
        }
        self.assertTrue(is_key_expired(old_key, now))

    def test_list_keys_filters_expired(self) -> None:
        """list_keys should filter out expired keys by default."""
        now = time.time()
        
        # Add an old (expired) key - use > 30 days ago
        expired_agent = "bcn_expired123456"
        keys = {
            expired_agent: {
                "pubkey_hex": "a" * 64,
                "first_seen": now - 40 * 24 * 60 * 60,  # 40 days ago
                "last_seen": now - 40 * 24 * 60 * 60,  # 40 days ago
                "rotation_count": 0,
                "previous_keys": [],
                "ttl_seconds": DEFAULT_KEY_TTL_SECONDS,
            }
        }
        save_known_keys(keys)
        
        # list_keys without show_expired should not include expired
        active_keys = list_keys(show_expired=False)
        self.assertNotIn(expired_agent, active_keys)
        
        # list_keys with show_expired should include expired
        all_keys = list_keys(show_expired=True)
        self.assertIn(expired_agent, all_keys)

    def test_get_key_metadata(self) -> None:
        """get_key_metadata should return full metadata for a key."""
        agent_id = "bcn_test123456"
        pubkey_hex = "c" * 64
        
        trust_key(agent_id, pubkey_hex)
        
        meta = get_key_metadata(agent_id)
        self.assertIsNotNone(meta)
        self.assertEqual(meta["pubkey_hex"], pubkey_hex)
        self.assertIn("first_seen", meta)
        self.assertIn("last_seen", meta)

    def test_get_key_metadata_nonexistent(self) -> None:
        """get_key_metadata should return None for non-existent key."""
        meta = get_key_metadata("bcn_nonexistent")
        self.assertIsNone(meta)


if __name__ == "__main__":
    unittest.main()
