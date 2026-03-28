"""Tests for AgentHive transport.

Covers: all public methods, error handling, rate-limit guard, dry_run flag.
"""

import time
from unittest.mock import MagicMock, Mock, patch

import pytest
import requests

from beacon_skill.transports.agenthive import AgentHiveClient, AgentHiveError


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def client():
    """AgentHiveClient with a fake API key."""
    return AgentHiveClient(api_key="hk_testkey1234")


@pytest.fixture()
def client_no_key():
    """AgentHiveClient with no API key."""
    return AgentHiveClient()


def _mock_response(status_code: int = 200, json_data=None, text: str = ""):
    """Build a mock requests.Response."""
    resp = Mock()
    resp.status_code = status_code
    resp.text = text or str(json_data or "")
    if json_data is not None:
        resp.json.return_value = json_data
    else:
        resp.json.side_effect = ValueError("no JSON")
    return resp


# ---------------------------------------------------------------------------
# _request / internals
# ---------------------------------------------------------------------------


class TestInternalRequest:
    def test_auth_header_set(self, client):
        with patch.object(client.session, "request") as mock_req:
            mock_req.return_value = _mock_response(200, {"ok": True})
            client._request("GET", "/api/feed", auth=True)
            _, kwargs = mock_req.call_args
            headers = mock_req.call_args[1].get("headers") or mock_req.call_args[0][3] if len(mock_req.call_args[0]) > 3 else {}
            # Verify Authorization was passed via the headers argument
            call_kwargs = mock_req.call_args.kwargs if hasattr(mock_req.call_args, "kwargs") else mock_req.call_args[1]
            assert "Authorization" in call_kwargs.get("headers", {})
            assert call_kwargs["headers"]["Authorization"] == "Bearer hk_testkey1234"

    def test_auth_raises_without_key(self, client_no_key):
        with pytest.raises(AgentHiveError, match="API key required"):
            client_no_key._request("GET", "/api/feed", auth=True)

    def test_http_error_raises_agent_hive_error(self, client):
        with patch.object(client.session, "request") as mock_req:
            mock_req.return_value = _mock_response(500, {"error": "server boom"})
            with pytest.raises(AgentHiveError, match="server boom"):
                client._request("GET", "/api/feed")

    def test_http_error_uses_message_field(self, client):
        with patch.object(client.session, "request") as mock_req:
            mock_req.return_value = _mock_response(400, {"message": "bad input"})
            with pytest.raises(AgentHiveError, match="bad input"):
                client._request("POST", "/api/posts", auth=True)

    def test_http_error_fallback_http_status(self, client):
        with patch.object(client.session, "request") as mock_req:
            mock_req.return_value = _mock_response(403, text="Forbidden")
            with pytest.raises(AgentHiveError, match="HTTP 403"):
                client._request("GET", "/api/feed")

    def test_non_json_response_on_error(self, client):
        with patch.object(client.session, "request") as mock_req:
            mock_req.return_value = _mock_response(502, text="Bad Gateway")
            with pytest.raises(AgentHiveError):
                client._request("GET", "/api/feed")

    def test_user_agent_header_set(self, client):
        assert client.session.headers.get("User-Agent") == "Beacon/1.0.0 (Elyan Labs)"


# ---------------------------------------------------------------------------
# create_post
# ---------------------------------------------------------------------------


class TestCreatePost:
    def test_create_post_success(self, client):
        with patch.object(client.session, "request") as mock_req:
            mock_req.return_value = _mock_response(201, {"id": "p1", "content": "hello"})
            with patch("beacon_skill.transports.agenthive.get_last_ts", return_value=None), \
                 patch("beacon_skill.transports.agenthive.set_last_ts") as mock_set_ts:
                result = client.create_post("hello")
        assert result["id"] == "p1"
        mock_set_ts.assert_called_once_with("agenthive_post")

    def test_create_post_sends_content(self, client):
        with patch.object(client.session, "request") as mock_req:
            mock_req.return_value = _mock_response(201, {"id": "p2"})
            with patch("beacon_skill.transports.agenthive.get_last_ts", return_value=None), \
                 patch("beacon_skill.transports.agenthive.set_last_ts"):
                client.create_post("my message")
            call_kwargs = mock_req.call_args.kwargs if hasattr(mock_req.call_args, "kwargs") else mock_req.call_args[1]
            assert call_kwargs.get("json", {}).get("content") == "my message"

    def test_create_post_uses_auth(self, client):
        with patch.object(client.session, "request") as mock_req:
            mock_req.return_value = _mock_response(201, {"id": "p3"})
            with patch("beacon_skill.transports.agenthive.get_last_ts", return_value=None), \
                 patch("beacon_skill.transports.agenthive.set_last_ts"):
                client.create_post("hello")
            call_kwargs = mock_req.call_args.kwargs if hasattr(mock_req.call_args, "kwargs") else mock_req.call_args[1]
            assert "Authorization" in call_kwargs.get("headers", {})

    def test_create_post_rate_limit_guard_blocks(self, client):
        recent_ts = time.time() - 60  # 1 minute ago, within 30-min window
        with patch("beacon_skill.transports.agenthive.get_last_ts", return_value=recent_ts):
            with pytest.raises(AgentHiveError, match="30 minutes"):
                client.create_post("hello")

    def test_create_post_rate_limit_guard_bypassed_with_force(self, client):
        recent_ts = time.time() - 60
        with patch("beacon_skill.transports.agenthive.get_last_ts", return_value=recent_ts), \
             patch.object(client.session, "request") as mock_req, \
             patch("beacon_skill.transports.agenthive.set_last_ts"):
            mock_req.return_value = _mock_response(201, {"id": "p4"})
            result = client.create_post("hello", force=True)
        assert result["id"] == "p4"

    def test_create_post_rate_limit_guard_allows_after_window(self, client):
        old_ts = time.time() - 2000  # >30 min ago
        with patch("beacon_skill.transports.agenthive.get_last_ts", return_value=old_ts), \
             patch.object(client.session, "request") as mock_req, \
             patch("beacon_skill.transports.agenthive.set_last_ts"):
            mock_req.return_value = _mock_response(201, {"id": "p5"})
            result = client.create_post("hello")
        assert result["id"] == "p5"

    def test_create_post_rate_limit_guard_allows_first_post(self, client):
        with patch("beacon_skill.transports.agenthive.get_last_ts", return_value=None), \
             patch.object(client.session, "request") as mock_req, \
             patch("beacon_skill.transports.agenthive.set_last_ts"):
            mock_req.return_value = _mock_response(201, {"id": "p6"})
            result = client.create_post("first post!")
        assert result["id"] == "p6"

    def test_create_post_dry_run_no_request(self, client):
        with patch.object(client.session, "request") as mock_req:
            with patch("beacon_skill.transports.agenthive.get_last_ts", return_value=None):
                result = client.create_post("hello", dry_run=True)
        assert result == {"ok": True, "dry_run": True, "content": "hello"}
        mock_req.assert_not_called()

    def test_create_post_dry_run_skips_rate_limit(self, client):
        """dry_run should still respect the rate-limit guard (guard fires first)."""
        recent_ts = time.time() - 60
        with patch("beacon_skill.transports.agenthive.get_last_ts", return_value=recent_ts):
            with pytest.raises(AgentHiveError, match="30 minutes"):
                client.create_post("hello", dry_run=True)

    def test_create_post_api_error(self, client):
        with patch.object(client.session, "request") as mock_req:
            mock_req.return_value = _mock_response(422, {"error": "content too long"})
            with patch("beacon_skill.transports.agenthive.get_last_ts", return_value=None):
                with pytest.raises(AgentHiveError, match="content too long"):
                    client.create_post("x" * 10_000)

    def test_create_post_no_key_raises(self, client_no_key):
        with patch("beacon_skill.transports.agenthive.get_last_ts", return_value=None):
            with pytest.raises(AgentHiveError, match="API key required"):
                client_no_key.create_post("hello")


# ---------------------------------------------------------------------------
# read_feed
# ---------------------------------------------------------------------------


class TestReadFeed:
    def test_read_feed_returns_list_directly(self, client):
        posts = [{"id": "1"}, {"id": "2"}]
        with patch.object(client.session, "request") as mock_req:
            mock_req.return_value = _mock_response(200, posts)
            result = client.read_feed()
        assert result == posts

    def test_read_feed_returns_posts_key(self, client):
        posts = [{"id": "3"}]
        with patch.object(client.session, "request") as mock_req:
            mock_req.return_value = _mock_response(200, {"posts": posts})
            result = client.read_feed()
        assert result == posts

    def test_read_feed_returns_data_key(self, client):
        posts = [{"id": "4"}]
        with patch.object(client.session, "request") as mock_req:
            mock_req.return_value = _mock_response(200, {"data": posts})
            result = client.read_feed()
        assert result == posts

    def test_read_feed_with_limit(self, client):
        with patch.object(client.session, "request") as mock_req:
            mock_req.return_value = _mock_response(200, [])
            client.read_feed(limit=10)
            call_kwargs = mock_req.call_args.kwargs if hasattr(mock_req.call_args, "kwargs") else mock_req.call_args[1]
            assert call_kwargs.get("params", {}).get("limit") == 10

    def test_read_feed_no_limit_no_params(self, client):
        with patch.object(client.session, "request") as mock_req:
            mock_req.return_value = _mock_response(200, [])
            client.read_feed()
            call_kwargs = mock_req.call_args.kwargs if hasattr(mock_req.call_args, "kwargs") else mock_req.call_args[1]
            # params should be None or empty when no limit given
            assert not call_kwargs.get("params")

    def test_read_feed_no_auth_required(self, client_no_key):
        with patch.object(client_no_key.session, "request") as mock_req:
            mock_req.return_value = _mock_response(200, [{"id": "5"}])
            result = client_no_key.read_feed()
        assert result[0]["id"] == "5"

    def test_read_feed_error(self, client):
        with patch.object(client.session, "request") as mock_req:
            mock_req.return_value = _mock_response(500, {"error": "db down"})
            with pytest.raises(AgentHiveError, match="db down"):
                client.read_feed()

    def test_read_feed_hits_correct_path(self, client):
        with patch.object(client.session, "request") as mock_req:
            mock_req.return_value = _mock_response(200, [])
            client.read_feed()
            call_args = mock_req.call_args
            positional = call_args.args if hasattr(call_args, "args") else call_args[0]
            assert positional[1].endswith("/api/feed")


# ---------------------------------------------------------------------------
# read_agent_posts
# ---------------------------------------------------------------------------


class TestReadAgentPosts:
    def test_read_agent_posts_success(self, client):
        posts = [{"id": "10", "content": "hi"}]
        with patch.object(client.session, "request") as mock_req:
            mock_req.return_value = _mock_response(200, posts)
            result = client.read_agent_posts("beacon_bot")
        assert result == posts

    def test_read_agent_posts_hits_correct_path(self, client):
        with patch.object(client.session, "request") as mock_req:
            mock_req.return_value = _mock_response(200, [])
            client.read_agent_posts("beacon_bot")
            call_args = mock_req.call_args
            positional = call_args.args if hasattr(call_args, "args") else call_args[0]
            assert "/api/agents/beacon_bot/posts" in positional[1]

    def test_read_agent_posts_with_limit(self, client):
        with patch.object(client.session, "request") as mock_req:
            mock_req.return_value = _mock_response(200, [])
            client.read_agent_posts("beacon_bot", limit=5)
            call_kwargs = mock_req.call_args.kwargs if hasattr(mock_req.call_args, "kwargs") else mock_req.call_args[1]
            assert call_kwargs.get("params", {}).get("limit") == 5

    def test_read_agent_posts_empty_name_raises(self, client):
        with pytest.raises(AgentHiveError, match="agent_name must not be empty"):
            client.read_agent_posts("")

    def test_read_agent_posts_404(self, client):
        with patch.object(client.session, "request") as mock_req:
            mock_req.return_value = _mock_response(404, {"error": "agent not found"})
            with pytest.raises(AgentHiveError, match="agent not found"):
                client.read_agent_posts("nobody")

    def test_read_agent_posts_returns_items_key(self, client):
        items = [{"id": "20"}]
        with patch.object(client.session, "request") as mock_req:
            mock_req.return_value = _mock_response(200, {"items": items})
            result = client.read_agent_posts("someone")
        assert result == items


# ---------------------------------------------------------------------------
# follow_agent
# ---------------------------------------------------------------------------


class TestFollowAgent:
    def test_follow_agent_success(self, client):
        with patch.object(client.session, "request") as mock_req:
            mock_req.return_value = _mock_response(200, {"following": True})
            result = client.follow_agent("cool_agent")
        assert result["following"] is True

    def test_follow_agent_hits_correct_path(self, client):
        with patch.object(client.session, "request") as mock_req:
            mock_req.return_value = _mock_response(200, {"following": True})
            client.follow_agent("cool_agent")
            call_args = mock_req.call_args
            positional = call_args.args if hasattr(call_args, "args") else call_args[0]
            assert "/api/agents/cool_agent/follow" in positional[1]

    def test_follow_agent_requires_auth(self, client_no_key):
        with pytest.raises(AgentHiveError, match="API key required"):
            client_no_key.follow_agent("cool_agent")

    def test_follow_agent_empty_name_raises(self, client):
        with pytest.raises(AgentHiveError, match="agent_name must not be empty"):
            client.follow_agent("")

    def test_follow_agent_404(self, client):
        with patch.object(client.session, "request") as mock_req:
            mock_req.return_value = _mock_response(404, {"error": "agent not found"})
            with pytest.raises(AgentHiveError, match="agent not found"):
                client.follow_agent("ghost")

    def test_follow_agent_uses_post_method(self, client):
        with patch.object(client.session, "request") as mock_req:
            mock_req.return_value = _mock_response(200, {"following": True})
            client.follow_agent("buddy")
            call_args = mock_req.call_args
            positional = call_args.args if hasattr(call_args, "args") else call_args[0]
            assert positional[0].upper() == "POST"


# ---------------------------------------------------------------------------
# register_agent
# ---------------------------------------------------------------------------


class TestRegisterAgent:
    def test_register_agent_success(self, client_no_key):
        payload = {"api_key": "hk_newkey", "agent": {"name": "my_agent"}}
        with patch.object(client_no_key.session, "request") as mock_req:
            mock_req.return_value = _mock_response(201, payload)
            result = client_no_key.register_agent("my_agent")
        assert result["api_key"] == "hk_newkey"

    def test_register_agent_hits_correct_path(self, client_no_key):
        with patch.object(client_no_key.session, "request") as mock_req:
            mock_req.return_value = _mock_response(201, {"api_key": "hk_x"})
            client_no_key.register_agent("my_agent")
            call_args = mock_req.call_args
            positional = call_args.args if hasattr(call_args, "args") else call_args[0]
            assert positional[1].endswith("/api/agents")
            assert positional[0].upper() == "POST"

    def test_register_agent_sends_name(self, client_no_key):
        with patch.object(client_no_key.session, "request") as mock_req:
            mock_req.return_value = _mock_response(201, {"api_key": "hk_x"})
            client_no_key.register_agent("my_agent")
            call_kwargs = mock_req.call_args.kwargs if hasattr(mock_req.call_args, "kwargs") else mock_req.call_args[1]
            assert call_kwargs.get("json", {}).get("name") == "my_agent"

    def test_register_agent_extra_fields(self, client_no_key):
        with patch.object(client_no_key.session, "request") as mock_req:
            mock_req.return_value = _mock_response(201, {"api_key": "hk_x"})
            client_no_key.register_agent("my_agent", description="A test agent", avatar_url="https://example.com/img.png")
            call_kwargs = mock_req.call_args.kwargs if hasattr(mock_req.call_args, "kwargs") else mock_req.call_args[1]
            payload = call_kwargs.get("json", {})
            assert payload.get("description") == "A test agent"
            assert payload.get("avatar_url") == "https://example.com/img.png"

    def test_register_agent_empty_name_raises(self, client_no_key):
        with pytest.raises(AgentHiveError, match="Agent name must not be empty"):
            client_no_key.register_agent("")

    def test_register_agent_conflict(self, client_no_key):
        with patch.object(client_no_key.session, "request") as mock_req:
            mock_req.return_value = _mock_response(409, {"error": "name already taken"})
            with pytest.raises(AgentHiveError, match="name already taken"):
                client_no_key.register_agent("taken_name")

    def test_register_agent_no_auth_needed(self, client_no_key):
        """Registration should not require an API key."""
        with patch.object(client_no_key.session, "request") as mock_req:
            mock_req.return_value = _mock_response(201, {"api_key": "hk_fresh"})
            result = client_no_key.register_agent("fresh_agent")
        assert "api_key" in result


# ---------------------------------------------------------------------------
# Error class
# ---------------------------------------------------------------------------


class TestAgentHiveError:
    def test_is_runtime_error(self):
        err = AgentHiveError("oops")
        assert isinstance(err, RuntimeError)

    def test_message_preserved(self):
        err = AgentHiveError("something went wrong")
        assert "something went wrong" in str(err)


# ---------------------------------------------------------------------------
# __init__.py export
# ---------------------------------------------------------------------------


class TestImport:
    def test_importable_from_transports(self):
        from beacon_skill.transports import AgentHiveClient as AHC  # noqa: F401
        assert AHC is AgentHiveClient

    def test_error_importable(self):
        from beacon_skill.transports.agenthive import AgentHiveError as AHE  # noqa: F401
        assert AHE is AgentHiveError
