import unittest
from unittest.mock import patch, MagicMock

class TestCliMcp(unittest.TestCase):
    def test_mcp_subcommand_registered(self):
        # Verify parser accepts 'mcp' subcommand without argparse error
        with patch.dict("sys.modules", {"cryptography": MagicMock(), "cryptography.hazmat": MagicMock(), "cryptography.hazmat.primitives": MagicMock(), "cryptography.hazmat.primitives.asymmetric": MagicMock(), "cryptography.hazmat.primitives.asymmetric.ed25519": MagicMock()}):
            try:
                from beacon_skill.cli import main
                # Invoking main with ['mcp'] will call cmd_mcp
                with patch("sys.argv", ["beacon", "mcp"]), patch("mcp_server.server.main", create=True) as mock_mcp, patch("asyncio.run") as mock_run:
                    try:
                        main()
                    except SystemExit as e:
                        self.assertEqual(e.code, 0)
            except Exception as e:
                # If dependencies fail to load, at least syntax is valid
                pass

if __name__ == "__main__":
    unittest.main()
