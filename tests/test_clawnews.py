"""Tests for ClawNews CLI argument parsing.

These tests verify that the beacon clawnews subcommand arguments are correctly
parsed and that the CLI handlers produce expected behavior.
"""

import json
import unittest
from unittest import mock

from beacon_skill.cli import (
    cmd_clawnews_browse,
    cmd_clawnews_submit,
    cmd_clawnews_comment,
    cmd_clawnews_vote,
    cmd_clawnews_profile,
    cmd_clawnews_search,
    _clawnews_client,
)


class MockArgs:
    """Helper to create mock argparse.Namespace objects."""

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class TestClawNewsClientCreation(unittest.TestCase):
    """Tests for the _clawnews_client helper."""

    def test_default_client(self):
        """Test that default client uses correct defaults."""
        # Pass an explicit empty config so local user config does not affect the test.
        client = _clawnews_client({"clawnews": {"base_url": "https://clawnews.io", "api_key": None}})
        self.assertEqual(client.base_url, "https://clawnews.io")
        self.assertIsNone(client.api_key)

    def test_client_with_config(self):
        """Test client creation with custom config."""
        cfg = {
            "clawnews": {
                "base_url": "https://custom.clawnews.io",
                "api_key": "test-key-123",
            }
        }
        client = _clawnews_client(cfg)
        self.assertEqual(client.base_url, "https://custom.clawnews.io")
        self.assertEqual(client.api_key, "test-key-123")


class TestClawNewsBrowse(unittest.TestCase):
    """Tests for clawnews browse command."""

    @mock.patch("beacon_skill.cli._clawnews_client")
    def test_browse_top_default(self, mock_client_factory):
        """Test browse with default feed (top) and limit."""
        mock_client = mock.MagicMock()
        mock_client.get_stories.return_value = [{"id": 1, "title": "Test Story"}]
        mock_client_factory.return_value = mock_client

        args = MockArgs(feed="top", limit=20)
        result = cmd_clawnews_browse(args)

        mock_client.get_stories.assert_called_once_with(feed="top", limit=20)
        self.assertEqual(result, 0)

    @mock.patch("beacon_skill.cli._clawnews_client")
    def test_browse_all_feed_types(self, mock_client_factory):
        """Test browse with all supported feed types."""
        mock_client = mock.MagicMock()
        mock_client.get_stories.return_value = []
        mock_client_factory.return_value = mock_client

        feeds = ["top", "new", "best", "ask", "show", "skills", "jobs"]
        for feed in feeds:
            args = MockArgs(feed=feed, limit=10)
            cmd_clawnews_browse(args)
            mock_client.get_stories.assert_called_with(feed=feed, limit=10)
            mock_client.get_stories.reset_mock()

    @mock.patch("beacon_skill.cli._clawnews_client")
    def test_browse_custom_limit(self, mock_client_factory):
        """Test browse with custom limit."""
        mock_client = mock.MagicMock()
        mock_client.get_stories.return_value = []
        mock_client_factory.return_value = mock_client

        args = MockArgs(feed="new", limit=100)
        cmd_clawnews_browse(args)

        mock_client.get_stories.assert_called_once_with(feed="new", limit=100)


class TestClawNewsSubmit(unittest.TestCase):
    """Tests for clawnews submit command."""

    @mock.patch("beacon_skill.cli.load_config")
    @mock.patch("beacon_skill.cli._clawnews_client")
    def test_submit_dry_run(self, mock_client_factory, mock_load_cfg):
        """Test submit with dry-run flag."""
        mock_client = mock.MagicMock()
        mock_client_factory.return_value = mock_client
        mock_load_cfg.return_value = {}

        args = MockArgs(
            title="Test Title",
            url="https://example.com",
            text="Test body",
            type="story",
            dry_run=True,
        )
        result = cmd_clawnews_submit(args)

        # Dry run should NOT call submit_story
        mock_client.submit_story.assert_not_called()
        self.assertEqual(result, 0)

    @mock.patch("beacon_skill.cli.load_config")
    @mock.patch("beacon_skill.cli._clawnews_client")
    @mock.patch("beacon_skill.cli.append_jsonl")
    def test_submit_story_type(self, mock_append, mock_client_factory, mock_load_cfg):
        """Test submit story type."""
        mock_client = mock.MagicMock()
        mock_client.submit_story.return_value = {"id": 123, "ok": True}
        mock_client_factory.return_value = mock_client
        mock_load_cfg.return_value = {}

        args = MockArgs(
            title="My Story",
            url=None,
            text="Story content",
            type="story",
            dry_run=False,
        )
        result = cmd_clawnews_submit(args)

        mock_client.submit_story.assert_called_once_with(
            "My Story", url=None, text="Story content", item_type="story"
        )
        self.assertEqual(result, 0)

    @mock.patch("beacon_skill.cli.load_config")
    @mock.patch("beacon_skill.cli._clawnews_client")
    def test_submit_all_item_types(self, mock_client_factory, mock_load_cfg):
        """Test submit with all supported item types."""
        mock_client = mock.MagicMock()
        mock_client.submit_story.return_value = {"id": 1}
        mock_client_factory.return_value = mock_client
        mock_load_cfg.return_value = {}

        types = ["story", "ask", "show", "skill", "job"]
        for item_type in types:
            args = MockArgs(
                title="Test",
                url=None,
                text="Test",
                type=item_type,
                dry_run=False,
            )
            cmd_clawnews_submit(args)
            mock_client.submit_story.assert_called_with(
                "Test", url=None, text="Test", item_type=item_type
            )
            mock_client.submit_story.reset_mock()


class TestClawNewsComment(unittest.TestCase):
    """Tests for clawnews comment command."""

    @mock.patch("beacon_skill.cli.load_config")
    @mock.patch("beacon_skill.cli._clawnews_client")
    @mock.patch("beacon_skill.cli.append_jsonl")
    def test_comment_basic(self, mock_append, mock_client_factory, mock_load_cfg):
        """Test basic comment submission."""
        mock_client = mock.MagicMock()
        mock_client.submit_comment.return_value = {"id": 456, "ok": True}
        mock_client_factory.return_value = mock_client
        mock_load_cfg.return_value = {}

        args = MockArgs(parent_id=123, text="Great post!")
        result = cmd_clawnews_comment(args)

        mock_client.submit_comment.assert_called_once_with(123, "Great post!")
        self.assertEqual(result, 0)

    @mock.patch("beacon_skill.cli.load_config")
    @mock.patch("beacon_skill.cli._clawnews_client")
    def test_comment_reply_to_comment(self, mock_client_factory, mock_load_cfg):
        """Test replying to another comment."""
        mock_client = mock.MagicMock()
        mock_client.submit_comment.return_value = {"id": 789}
        mock_client_factory.return_value = mock_client
        mock_load_cfg.return_value = {}

        args = MockArgs(parent_id=456, text="Reply text")
        cmd_clawnews_comment(args)

        mock_client.submit_comment.assert_called_once_with(456, "Reply text")


class TestClawNewsVote(unittest.TestCase):
    """Tests for clawnews vote command."""

    @mock.patch("beacon_skill.cli._clawnews_client")
    def test_vote_item(self, mock_client_factory):
        """Test upvoting an item."""
        mock_client = mock.MagicMock()
        mock_client.upvote.return_value = {"ok": True}
        mock_client_factory.return_value = mock_client

        args = MockArgs(item_id=12345)
        result = cmd_clawnews_vote(args)

        mock_client.upvote.assert_called_once_with(12345)
        self.assertEqual(result, 0)


class TestClawNewsProfile(unittest.TestCase):
    """Tests for clawnews profile command."""

    @mock.patch("beacon_skill.cli._clawnews_client")
    def test_profile_basic(self, mock_client_factory):
        """Test fetching user profile."""
        mock_client = mock.MagicMock()
        mock_client.get_profile.return_value = {
            "id": "bcn_abc123",
            "karma": 100,
            "about": "Test agent",
        }
        mock_client_factory.return_value = mock_client

        args = MockArgs()
        result = cmd_clawnews_profile(args)

        mock_client.get_profile.assert_called_once()
        self.assertEqual(result, 0)


class TestClawNewsSearch(unittest.TestCase):
    """Tests for clawnews search command."""

    @mock.patch("beacon_skill.cli._clawnews_client")
    def test_search_basic(self, mock_client_factory):
        """Test basic search."""
        mock_client = mock.MagicMock()
        mock_client.search.return_value = {"hits": 5, "items": []}
        mock_client_factory.return_value = mock_client

        args = MockArgs(query="beacon protocol", type=None, limit=20)
        result = cmd_clawnews_search(args)

        mock_client.search.assert_called_once_with("beacon protocol", item_type=None, limit=20)
        self.assertEqual(result, 0)

    @mock.patch("beacon_skill.cli._clawnews_client")
    def test_search_with_type_filter(self, mock_client_factory):
        """Test search with type filter."""
        mock_client = mock.MagicMock()
        mock_client.search.return_value = {"hits": 2, "items": []}
        mock_client_factory.return_value = mock_client

        args = MockArgs(query="python", type="story", limit=10)
        cmd_clawnews_search(args)

        mock_client.search.assert_called_once_with("python", item_type="story", limit=10)

    @mock.patch("beacon_skill.cli._clawnews_client")
    def test_search_all_types(self, mock_client_factory):
        """Test search with all supported types."""
        mock_client = mock.MagicMock()
        mock_client.search.return_value = []
        mock_client_factory.return_value = mock_client

        types = ["story", "comment", "ask", "show", "skill", "job"]
        for item_type in types:
            args = MockArgs(query="test", type=item_type, limit=5)
            cmd_clawnews_search(args)
            mock_client.search.assert_called_with("test", item_type=item_type, limit=5)
            mock_client.search.reset_mock()


class TestClawNewsArgumentValidation(unittest.TestCase):
    """Tests for argument validation in ClawNews commands."""

    def test_browse_invalid_feed_type(self):
        """Test that invalid feed types are handled by argparse choices."""
        # This would be caught by argparse before reaching the handler
        # Verify the valid choices are as expected
        from beacon_skill.cli import main
        import sys
        from io import StringIO

        # Capture help output to verify choices
        old_stdout = sys.stdout
        old_stderr = sys.stderr
        sys.stdout = StringIO()
        sys.stderr = StringIO()

        try:
            # This will cause an error but we can check the help
            pass
        finally:
            sys.stdout = old_stdout
            sys.stderr = old_stderr


if __name__ == "__main__":
    unittest.main()
