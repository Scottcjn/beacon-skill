import argparse
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import Optional, Tuple
from unittest.mock import patch

from beacon_skill.cli import cmd_identity_restore
from beacon_skill.identity import AgentIdentity


BIP39_TEST_PHRASE = "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about"


class TestIdentityRestoreCli(unittest.TestCase):
    def _run_restore(
        self,
        *,
        phrase: str = BIP39_TEST_PHRASE,
        legacy: bool = False,
        expect_agent_id: Optional[str] = None,
        password: Optional[str] = None,
    ) -> Tuple[int, Optional[dict], str]:
        args = argparse.Namespace(
            mnemonic_phrase=phrase,
            legacy=legacy,
            expect_agent_id=expect_agent_id,
            password=password,
        )
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            rc = cmd_identity_restore(args)
        raw_stdout = stdout.getvalue().strip()
        data = json.loads(raw_stdout) if raw_stdout else None
        return rc, data, stderr.getvalue()

    def test_restore_legacy_flag_preserves_pre_bip39_agent_id(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            identity_path = str(Path(td) / "agent.key")
            with patch.dict(os.environ, {"BEACON_IDENTITY_PATH": identity_path}):
                rc, data, err = self._run_restore(legacy=True)

        legacy = AgentIdentity.from_legacy_mnemonic(BIP39_TEST_PHRASE)
        self.assertEqual(rc, 0)
        self.assertIsNotNone(data)
        assert data is not None
        self.assertEqual(data["agent_id"], legacy.agent_id)
        self.assertEqual(data["derivation"], "legacy_sha256")
        self.assertIn("pre-BIP39", err)

    def test_restore_expect_agent_id_auto_selects_legacy_match(self) -> None:
        legacy = AgentIdentity.from_legacy_mnemonic(BIP39_TEST_PHRASE)

        with tempfile.TemporaryDirectory() as td:
            identity_path = str(Path(td) / "agent.key")
            with patch.dict(os.environ, {"BEACON_IDENTITY_PATH": identity_path}):
                rc, data, err = self._run_restore(expect_agent_id=legacy.agent_id)

        self.assertEqual(rc, 0)
        self.assertIsNotNone(data)
        assert data is not None
        self.assertEqual(data["agent_id"], legacy.agent_id)
        self.assertEqual(data["derivation"], "legacy_sha256")
        self.assertIn("matched the legacy", err)

    def test_restore_expected_agent_id_mismatch_refuses_to_save(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            identity_path = Path(td) / "agent.key"
            with patch.dict(os.environ, {"BEACON_IDENTITY_PATH": str(identity_path)}):
                rc, data, err = self._run_restore(expect_agent_id="bcn_deadbeefcafe")
                self.assertFalse(identity_path.exists())

        self.assertEqual(rc, 1)
        self.assertIsNone(data)
        self.assertIn("does not restore", err)

    def test_restore_legacy_flag_still_honors_expected_agent_id(self) -> None:
        modern = AgentIdentity.from_mnemonic(BIP39_TEST_PHRASE)

        with tempfile.TemporaryDirectory() as td:
            identity_path = Path(td) / "agent.key"
            with patch.dict(os.environ, {"BEACON_IDENTITY_PATH": str(identity_path)}):
                rc, data, err = self._run_restore(
                    legacy=True,
                    expect_agent_id=modern.agent_id,
                )
                self.assertFalse(identity_path.exists())

        self.assertEqual(rc, 1)
        self.assertIsNone(data)
        self.assertIn("does not restore", err)

    def test_restore_auto_keeps_existing_legacy_keystore_agent_id(self) -> None:
        legacy = AgentIdentity.from_legacy_mnemonic(BIP39_TEST_PHRASE)

        with tempfile.TemporaryDirectory() as td:
            identity_path = str(Path(td) / "agent.key")
            with patch.dict(os.environ, {"BEACON_IDENTITY_PATH": identity_path}):
                legacy.save()
                rc, data, err = self._run_restore()

        self.assertEqual(rc, 0)
        self.assertIsNotNone(data)
        assert data is not None
        self.assertEqual(data["agent_id"], legacy.agent_id)
        self.assertEqual(data["derivation"], "legacy_sha256")
        self.assertIn("existing keystore matches", err)


if __name__ == "__main__":
    unittest.main()
