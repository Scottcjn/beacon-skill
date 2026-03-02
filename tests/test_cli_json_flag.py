import json
import sys
import unittest
from io import StringIO
from unittest import mock

from beacon_skill import __version__
from beacon_skill.cli import main


class TestCliJsonFlag(unittest.TestCase):
    def setUp(self) -> None:
        self._stdout = sys.stdout
        self._stderr = sys.stderr

    def tearDown(self) -> None:
        sys.stdout = self._stdout
        sys.stderr = self._stderr

    def _run(self, argv):
        sys.stdout = StringIO()
        sys.stderr = StringIO()
        try:
            main(argv)
            code = 0
        except SystemExit as e:
            code = e.code
        return code, sys.stdout.getvalue(), sys.stderr.getvalue()

    def test_version_json_output(self):
        code, stdout, _ = self._run(["--version", "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout), {"version": __version__})

    @mock.patch("beacon_skill.identity.AgentIdentity.load")
    def test_identity_show_accepts_json_flag(self, mock_load):
        mock_load.return_value = mock.Mock(to_dict=mock.Mock(return_value={
            "agent_id": "bcn_test123456789",
            "public_key_hex": "ab" * 32,
        }))
        code, stdout, _ = self._run(["identity", "show", "--json"])
        self.assertEqual(code, 0)
        parsed = json.loads(stdout)
        self.assertEqual(parsed["agent_id"], "bcn_test123456789")
        self.assertEqual(parsed["public_key_hex"], "ab" * 32)


if __name__ == "__main__":
    unittest.main()
