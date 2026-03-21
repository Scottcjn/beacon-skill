"""Integration tests for ClawNews commands and response contracts.

This module provides end-to-end integration tests that verify:
- Command execution with real argument parsing
- Response contract validation
- Error handling scenarios
- Backward compatibility
- Performance and timeout handling
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class TestClawNewsIntegration(unittest.TestCase):
    """Integration tests for ClawNews CLI commands."""

    def setUp(self):
        """Set up test environment."""
        self.test_dir = Path(__file__).parent.parent
        self.beacon_script = self.test_dir / "bin" / "beacon.js"

    def _run_beacon_command(self, args, input_data=None, expect_success=True):
        """Run a beacon command and return result."""
        cmd = ["node", str(self.beacon_script)] + args
        
        try:
            result = subprocess.run(
                cmd,
                input=input_data,
                capture_output=True,
                text=True,
                timeout=30,
                cwd=str(self.test_dir)
            )
            
            if expect_success and result.returncode != 0:
                self.fail(f"Command failed: {' '.join(args)}\nStderr: {result.stderr}")
            
            return {
                "returncode": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr
            }
        except subprocess.TimeoutExpired:
            self.fail(f"Command timed out: {' '.join(args)}")

    def _parse_json_output(self, output):
        """Parse JSON output, handling multiple JSON objects or pretty printed ones."""
        output = output.strip()
        if not output: return []
        try:
            # Let's see if the entire output is a valid JSON array or object
            res = json.loads(output)
            if isinstance(res, list): return res
            return [res]
        except json.JSONDecodeError:
            pass
        
        # If it's multiple JSON objects separated by whitespace (or JSONL)
        import re
        decoder = json.JSONDecoder()
        results = []
        idx = 0
        while idx < len(output):
            try:
                obj, idx = decoder.raw_decode(output, idx)
                results.append(obj)
            except json.JSONDecodeError:
                idx += 1
        return results

    @unittest.skip("Requires actual ClawNews API access")
    def test_browse_integration(self):
        """Test clawnews browse command integration."""
        # Test default browse
        result = self._run_beacon_command(["clawnews", "browse", "--limit", "5"])
        
        json_results = self._parse_json_output(result["stdout"])
        self.assertTrue(json_results, "Should produce JSON output")
        
        # Should be a list of item IDs
        main_result = json_results[0]
        self.assertIsInstance(main_result, list, "Browse should return a list")
        
        # Test different feed types
        feeds = ["top", "new", "best"]
        for feed in feeds:
            with self.subTest(feed=feed):
                result = self._run_beacon_command([
                    "clawnews", "browse", "--feed", feed, "--limit", "3"
                ])
                json_results = self._parse_json_output(result["stdout"])
                self.assertTrue(json_results, f"Feed {feed} should produce output")

    def test_browse_argument_validation(self):
        """Test browse command argument validation."""
        # Test invalid feed type
        result = self._run_beacon_command(
            ["clawnews", "browse", "--feed", "invalid"],
            expect_success=False
        )
        self.assertNotEqual(result["returncode"], 0)
        self.assertIn("invalid choice", result["stderr"])

    def test_submit_argument_validation(self):
        """Test submit command argument validation."""
        # Test missing title
        result = self._run_beacon_command(
            ["clawnews", "submit"],
            expect_success=False
        )
        self.assertNotEqual(result["returncode"], 0)
        self.assertIn("required", result["stderr"])

    def test_submit_dry_run(self):
        """Test submit command dry run functionality."""
        result = self._run_beacon_command([
            "clawnews", "submit",
            "--title", "Test Title",
            "--text", "Test content",
            "--dry-run"
        ])
        
        json_results = self._parse_json_output(result["stdout"])
        self.assertTrue(json_results, "Dry run should produce JSON output")
        
        dry_run_result = json_results[0]
        self.assertEqual(dry_run_result.get("type"), "story")
        self.assertEqual(dry_run_result.get("title"), "Test Title")
        self.assertEqual(dry_run_result.get("text"), "Test content")

    def test_comment_argument_validation(self):
        """Test comment command argument validation."""
        # Test missing parent_id
        result = self._run_beacon_command(
            ["clawnews", "comment"],
            expect_success=False
        )
        self.assertNotEqual(result["returncode"], 0)
        self.assertIn("required", result["stderr"])
        
        # Test invalid parent_id
        result = self._run_beacon_command(
            ["clawnews", "comment", "not_a_number", "--text", "Comment"],
            expect_success=False
        )
        self.assertNotEqual(result["returncode"], 0)
        self.assertIn("invalid", result["stderr"])

    def test_vote_argument_validation(self):
        """Test vote command argument validation."""
        # Test missing item_id
        result = self._run_beacon_command(
            ["clawnews", "vote"],
            expect_success=False
        )
        self.assertNotEqual(result["returncode"], 0)
        
        # Test invalid item_id
        result = self._run_beacon_command(
            ["clawnews", "vote", "not_a_number"],
            expect_success=False
        )
        self.assertNotEqual(result["returncode"], 0)

    def test_search_argument_validation(self):
        """Test search command argument validation."""
        # Test missing query
        result = self._run_beacon_command(
            ["clawnews", "search"],
            expect_success=False
        )
        self.assertNotEqual(result["returncode"], 0)
        
        # Test invalid type
        result = self._run_beacon_command(
            ["clawnews", "search", "test", "--type", "invalid"],
            expect_success=False
        )
        self.assertNotEqual(result["returncode"], 0)

    def test_help_text_availability(self):
        """Test that help text is available for all commands."""
        help_commands = [
            ["clawnews", "--help"],
            ["clawnews", "browse", "--help"],
            ["clawnews", "submit", "--help"],
            ["clawnews", "comment", "--help"],
            ["clawnews", "vote", "--help"],
            ["clawnews", "profile", "--help"],
            ["clawnews", "search", "--help"],
        ]
        
        for cmd in help_commands:
            with self.subTest(cmd=cmd):
                result = self._run_beacon_command(cmd, expect_success=False)
                # Help commands exit with code 0 in argparse, but subprocess may see it as failure
                # Check that help text is actually present
                help_text = result["stdout"] + result["stderr"]
                self.assertTrue(
                    "help" in help_text.lower() or "usage" in help_text.lower(),
                    f"Help should be available for {cmd}"
                )


class TestClawNewsResponseContracts(unittest.TestCase):
    """Test response format contracts for ClawNews commands."""

    def test_browse_response_contract(self):
        """Test that browse responses follow expected format."""
        # Mock the client to return various response formats
        test_responses = [
            [],  # Empty result
            [1, 2, 3],  # Simple ID list
            ["mb_123", 456, "ext_789"],  # Mixed ID types
        ]
        
        for response in test_responses:
            with self.subTest(response=response):
                with mock.patch("beacon_skill.cli._clawnews_client") as mock_factory:
                    mock_client = mock.MagicMock()
                    mock_client.get_stories.return_value = response
                    mock_factory.return_value = mock_client
                    
                    from beacon_skill.cli import cmd_clawnews_browse
                    class MockArgs:
                        def __init__(self, **kwargs):
                            for k, v in kwargs.items():
                                setattr(self, k, v)
                    
                    # Capture output
                    import io
                    import contextlib
                    
                    stdout_capture = io.StringIO()
                    with contextlib.redirect_stdout(stdout_capture):
                        result = cmd_clawnews_browse(MockArgs(feed="top", limit=20))
                    
                    self.assertEqual(result, 0)
                    output = stdout_capture.getvalue()
                    
                    # Should be valid JSON
                    try:
                        parsed = json.loads(output)
                        self.assertEqual(parsed, response)
                    except json.JSONDecodeError:
                        self.fail(f"Invalid JSON output: {output}")

    def test_submit_response_contract(self):
        """Test that submit responses are handled consistently."""
        test_responses = [
            {"id": 123},
            {"id": 456, "status": "created"},
            {"id": 789, "ok": True, "url": "/item/789"},
            {},  # Empty response
        ]
        
        for response in test_responses:
            with self.subTest(response=response):
                with mock.patch("beacon_skill.cli.load_config", return_value={}):
                    with mock.patch("beacon_skill.cli._clawnews_client") as mock_factory:
                        with mock.patch("beacon_skill.cli.append_jsonl"):
                            with mock.patch("beacon_skill.cli._maybe_udp_emit"):
                                mock_client = mock.MagicMock()
                                mock_client.submit_story.return_value = response
                                mock_factory.return_value = mock_client
                                
                                from beacon_skill.cli import cmd_clawnews_submit
                                
                                class MockArgs:
                                    def __init__(self):
                                        self.title = "Test"
                                        self.url = None
                                        self.text = "Test"
                                        self.type = "story"
                                        self.dry_run = False
                                
                                import io
                                import contextlib
                                
                                stdout_capture = io.StringIO()
                                with contextlib.redirect_stdout(stdout_capture):
                                    result = cmd_clawnews_submit(MockArgs())
                                
                                self.assertEqual(result, 0)
                                output = stdout_capture.getvalue()
                                
                                # Should be valid JSON
                                try:
                                    parsed = json.loads(output)
                                    self.assertEqual(parsed, response)
                                except json.JSONDecodeError:
                                    self.fail(f"Invalid JSON output: {output}")

    def test_error_response_contract(self):
        """Test that error responses follow expected format."""
        from beacon_skill.transports.clawnews import ClawNewsError
        
        error_cases = [
            "Authentication required",
            "Rate limit exceeded",
            "Invalid item ID",
            "Insufficient karma",
        ]
        
        for error_msg in error_cases:
            with self.subTest(error=error_msg):
                with mock.patch("beacon_skill.cli._clawnews_client") as mock_factory:
                    mock_client = mock.MagicMock()
                    mock_client.get_stories.side_effect = ClawNewsError(error_msg)
                    mock_factory.return_value = mock_client
                    
                    from beacon_skill.cli import cmd_clawnews_browse
                    
                    class MockArgs:
                        def __init__(self):
                            self.feed = "top"
                            self.limit = 20
                    
                    # Should raise the error
                    with self.assertRaises(ClawNewsError) as cm:
                        cmd_clawnews_browse(MockArgs())
                    
                    self.assertEqual(str(cm.exception), error_msg)


class TestClawNewsBackwardCompatibility(unittest.TestCase):
    """Test backward compatibility of ClawNews commands."""

    def test_default_argument_compatibility(self):
        """Test that default arguments maintain backward compatibility."""
        # Test that old command invocations still work
        with mock.patch("beacon_skill.cli._clawnews_client") as mock_factory:
            mock_client = mock.MagicMock()
            mock_client.get_stories.return_value = []
            mock_factory.return_value = mock_client
            
            from beacon_skill.cli import cmd_clawnews_browse
            
            # Test with minimal args (as would come from old scripts)
            class MinimalArgs:
                def __init__(self):
                    # Only have the minimal required attributes
                    pass
            
            args = MinimalArgs()
            
            # Should work with getattr defaults
            import io
            import contextlib
            
            stdout_capture = io.StringIO()
            with contextlib.redirect_stdout(stdout_capture):
                result = cmd_clawnews_browse(args)
            
            self.assertEqual(result, 0)
            mock_client.get_stories.assert_called_with(feed="top", limit=20)

    def test_response_format_tolerance(self):
        """Test that commands tolerate various response formats."""
        # Test both old simple responses and new rich responses
        response_pairs = [
            # (old_format, new_format)
            ([], [{"id": 1, "title": "Rich"}]),
            ({"id": 123}, {"id": 123, "status": "created", "url": "/item/123"}),
            (True, {"ok": True, "karma_change": 1}),
        ]
        
        commands_and_responses = [
            ("get_stories", "cmd_clawnews_browse"),
            ("submit_story", "cmd_clawnews_submit"),
            ("upvote", "cmd_clawnews_vote"),
        ]
        
        for method_name, cmd_name in commands_and_responses:
            for old_resp, new_resp in response_pairs:
                with self.subTest(cmd=cmd_name, response="old"):
                    # Test old format doesn't break
                    with mock.patch("beacon_skill.cli._clawnews_client") as mock_factory:
                        mock_client = mock.MagicMock()
                        getattr(mock_client, method_name).return_value = old_resp
                        mock_factory.return_value = mock_client
                        
                        # Import and call command
                        from beacon_skill import cli
                        cmd_func = getattr(cli, cmd_name)
                        
                        # Create appropriate args
                        if cmd_name == "cmd_clawnews_browse":
                            class Args:
                                feed = "top"
                                limit = 20
                        elif cmd_name == "cmd_clawnews_submit":
                            class Args:
                                title = "Test"
                                url = None
                                text = "Test"
                                type = "story"
                                dry_run = False
                        elif cmd_name == "cmd_clawnews_vote":
                            class Args:
                                item_id = 123
                        
                        # Should not raise regardless of response format
                        with mock.patch("beacon_skill.cli.load_config", return_value={}):
                            with mock.patch("beacon_skill.cli.append_jsonl"):
                                with mock.patch("beacon_skill.cli._maybe_udp_emit"):
                                    import io
                                    import contextlib
                                    
                                    stdout_capture = io.StringIO()
                                    with contextlib.redirect_stdout(stdout_capture):
                                        result = cmd_func(Args())
                                    
                                    self.assertEqual(result, 0)


if __name__ == "__main__":
    unittest.main()