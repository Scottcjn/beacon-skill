"""Tests for key management (TOFU revocation/rotation)."""

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from beacon_skill.key_management import (
    load_known_keys,
    save_known_keys,
    trust_key,
    revoke_key,
    is_key_expired,
    update_last_seen,
    list_keys,
    get_key_info,
    cleanup_expired_keys,
    DEFAULT_KEY_TTL,
)


class TestKeyManagement(unittest.TestCase):
    """Test key management functionality."""

    def setUp(self):
        """Create a temporary directory for test keys."""
        self.temp_dir = tempfile.mkdtemp()
        self.keys_path = Path(self.temp_dir) / "known_keys.json"

    def tearDown(self):
        """Clean up temporary directory."""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _patch_storage(self):
        """Patch storage path to use temp directory."""
        return patch('beacon_skill.key_management._dir', return_value=Path(self.temp_dir))

    def test_trust_key(self):
        """Test adding a new trusted key."""
        with self._patch_storage():
            trust_key("bcn_test123", "abcd1234" * 8)

            keys = load_known_keys()
            self.assertIn("bcn_test123", keys)
            self.assertEqual(keys["bcn_test123"]["pubkey_hex"], "abcd1234" * 8)
            self.assertFalse(keys["bcn_test123"]["revoked"])
            self.assertEqual(keys["bcn_test123"]["rotation_count"], 0)

    def test_revoke_key(self):
        """Test revoking a key."""
        with self._patch_storage():
            trust_key("bcn_test456", "efgh5678" * 8)

            success = revoke_key("bcn_test456", reason="Test revocation")
            self.assertTrue(success)

            keys = load_known_keys()
            self.assertTrue(keys["bcn_test456"]["revoked"])
            self.assertEqual(keys["bcn_test456"]["revoked_reason"], "Test revocation")
            self.assertIsNotNone(keys["bcn_test456"]["revoked_at"])

    def test_revoke_nonexistent_key(self):
        """Test revoking a key that doesn't exist."""
        with self._patch_storage():
            success = revoke_key("bcn_nonexistent", reason="Should fail")
            self.assertFalse(success)

    def test_key_expiration(self):
        """Test key expiration based on TTL."""
        with self._patch_storage():
            trust_key("bcn_expired", "ijkl9012" * 8)

            # Should not be expired initially
            self.assertFalse(is_key_expired("bcn_expired"))

            # Manually set last_seen to be older than TTL
            keys = load_known_keys()
            keys["bcn_expired"]["last_seen"] = time.time() - DEFAULT_KEY_TTL - 1000
            save_known_keys(keys)

            # Now it should be expired
            self.assertTrue(is_key_expired("bcn_expired"))

    def test_update_last_seen(self):
        """Test updating last_seen timestamp."""
        with self._patch_storage():
            trust_key("bcn_update", "qrst7890" * 8)

            keys = load_known_keys()
            original_last_seen = keys["bcn_update"]["last_seen"]

            time.sleep(0.01)
            update_last_seen("bcn_update")

            keys = load_known_keys()
            self.assertGreater(keys["bcn_update"]["last_seen"], original_last_seen)

    def test_list_keys(self):
        """Test listing keys including revoked."""
        with self._patch_storage():
            trust_key("bcn_list1", "aaaa" * 16)
            trust_key("bcn_list2", "bbbb" * 16)
            revoke_key("bcn_list2", reason="Test")

            # List all keys including revoked
            keys = list_keys(include_revoked=True)
            self.assertEqual(len(keys), 2)

            # List without revoked (default)
            keys = list_keys(include_revoked=False)
            self.assertEqual(len(keys), 1)
            self.assertEqual(keys[0]["agent_id"], "bcn_list1")

    def test_get_key_info(self):
        """Test getting key info."""
        with self._patch_storage():
            trust_key("bcn_info", "cccc" * 16)

            info = get_key_info("bcn_info")
            self.assertIsNotNone(info)
            self.assertEqual(info["agent_id"], "bcn_info")
            self.assertEqual(info["pubkey_hex"], "cccc" * 16)
            self.assertEqual(info["rotation_count"], 0)

            # Non-existent key
            info = get_key_info("bcn_nonexistent")
            self.assertIsNone(info)

    def test_cleanup_expired_keys(self):
        """Test cleaning up expired keys."""
        with self._patch_storage():
            trust_key("bcn_fresh", "dddd" * 16)
            trust_key("bcn_old", "eeee" * 16)

            # Make one key expired
            keys = load_known_keys()
            keys["bcn_old"]["last_seen"] = time.time() - DEFAULT_KEY_TTL - 1000
            save_known_keys(keys)

            # Dry run
            removed = cleanup_expired_keys(dry_run=True)
            self.assertEqual(len(removed), 1)
            self.assertIn("bcn_old", removed)

            # Keys should still exist
            keys = load_known_keys()
            self.assertIn("bcn_old", keys)

            # Actual cleanup
            removed = cleanup_expired_keys(dry_run=False)
            self.assertEqual(len(removed), 1)

            # Keys should be removed
            keys = load_known_keys()
            self.assertNotIn("bcn_old", keys)
            self.assertIn("bcn_fresh", keys)

    def test_migrate_old_format(self):
        """Test migration from old key format (string) to new format (dict)."""
        with self._patch_storage():
            # Write old format
            old_keys = {"bcn_old_format": "ffff" * 16}
            self.keys_path.write_text(json.dumps(old_keys))

            # Load and verify migration
            keys = load_known_keys()
            self.assertIn("bcn_old_format", keys)
            self.assertIsInstance(keys["bcn_old_format"], dict)
            self.assertEqual(keys["bcn_old_format"]["pubkey_hex"], "ffff" * 16)


if __name__ == "__main__":
    unittest.main()
