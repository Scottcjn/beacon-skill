import tempfile
import unittest
from pathlib import Path
from unittest import mock

from beacon_skill.storage import _safe_path, read_jsonl


class TestStorage(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.dir_patcher = mock.patch("beacon_skill.storage._dir", return_value=Path(self.tmpdir))
        self.dir_patcher.start()

    def tearDown(self):
        self.dir_patcher.stop()
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_read_jsonl_returns_empty_for_missing_file(self):
        self.assertEqual(read_jsonl("missing.jsonl"), [])

    def test_read_jsonl_skips_blank_invalid_and_non_object_lines(self):
        path = Path(self.tmpdir) / "events.jsonl"
        path.write_text(
            '\n'.join([
                '{"kind": "ok", "value": 1}',
                '',
                'not-json',
                '[1, 2, 3]',
                '"hello"',
                '{"kind": "ok", "value": 2}',
            ]) + '\n',
            encoding="utf-8",
        )

        self.assertEqual(
            read_jsonl("events.jsonl"),
            [
                {"kind": "ok", "value": 1},
                {"kind": "ok", "value": 2},
            ],
        )

    def test_safe_path_rejects_traversal_and_hidden_names(self):
        for name in ("../state.json", "..\\state.json", ".hidden", "nested/file.json"):
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    _safe_path(name)

    def test_safe_path_uses_beacon_inbox_path_override(self):
        override = Path(self.tmpdir) / "nested" / "custom-inbox.jsonl"
        with mock.patch.dict("os.environ", {"BEACON_INBOX_PATH": str(override)}, clear=False):
            self.assertEqual(_safe_path("inbox.jsonl"), override)
            self.assertTrue(override.parent.exists())


if __name__ == "__main__":
    unittest.main()
