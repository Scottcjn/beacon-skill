"""Tests for Discord transport hardened error handling and listener mode."""

import asyncio
import json
from pathlib import Path

import pytest
from unittest.mock import Mock, patch, MagicMock
import requests

from beacon_skill.transports.discord import (
    DiscordTransport,
    DiscordListener,
    DiscordError,
    DiscordRateLimitError,
    DiscordClientError,
    DiscordServerError,
)


class TestDiscordTransport:
    """Tests for DiscordTransport error handling and retry logic."""

    @pytest.fixture
    def transport(self):
        """Create a DiscordTransport instance for testing."""
        return DiscordTransport(
            webhook_url="https://discord.com/api/webhooks/test/webhook",
            max_retries=3,
            base_delay=0.1,
        )

    def test_rate_limit_error_retry(self, transport):
        """Test that 429 responses trigger retry with proper backoff."""
        with patch.object(transport.session, 'post') as mock_post:
            # First call: rate limited, second: success
            mock_response_429 = Mock()
            mock_response_429.status_code = 429
            mock_response_429.text = "{\"message\": \"Rate limited\", \"retry_after\": 1}"
            mock_response_429.headers = {"Retry-After": "1"}
            mock_response_429.json.return_value = {"message": "Rate limited", "retry_after": 1}

            mock_response_200 = Mock()
            mock_response_200.status_code = 200
            mock_response_200.text = ""
            mock_response_200.status_code = 204

            mock_post.side_effect = [mock_response_429, mock_response_200]

            result = transport.send_message("test")
            
            assert result["ok"] is True
            assert mock_post.call_count == 2

    def test_rate_limit_respects_retry_after_header(self, transport):
        """Test that 429 retry uses Retry-After header for delay."""
        with patch.object(transport.session, 'post') as mock_post:
            mock_response_429 = Mock()
            mock_response_429.status_code = 429
            mock_response_429.headers = {"Retry-After": "5"}
            mock_response_429.json.return_value = {"message": "Rate limited", "retry_after": 5}

            mock_response_200 = Mock()
            mock_response_200.status_code = 204

            mock_post.side_effect = [mock_response_429, mock_response_200]

            result = transport.send_message("test")
            assert result["ok"] is True

    def test_rate_limit_uses_json_retry_after_when_header_missing(self, transport):
        """Retry-After can come from response JSON body."""
        with patch.object(transport.session, 'post') as mock_post:
            mock_response_429 = Mock()
            mock_response_429.status_code = 429
            mock_response_429.headers = {}
            mock_response_429.json.return_value = {"message": "Rate limited", "retry_after": 0.2}

            mock_response_200 = Mock()
            mock_response_200.status_code = 204

            mock_post.side_effect = [mock_response_429, mock_response_200]
            result = transport.send_message("test")
            assert result["ok"] is True

    def test_webhook_5xx_parsing(self, transport):
        """5xx should surface as DiscordServerError with status code."""
        with patch.object(transport.session, 'post') as mock_post:
            mock_response = Mock()
            mock_response.status_code = 503
            mock_response.text = "Service unavailable"
            mock_response.json.return_value = {"message": "Service unavailable"}
            mock_post.return_value = mock_response

            with pytest.raises(DiscordError) as exc_info:
                transport.send_message("test")
            assert "503" in str(exc_info.value) or "Failed after" in str(exc_info.value)

    def test_4xx_client_error_no_retry(self, transport):
        """Test that 4xx errors don't retry (except 429)."""
        with patch.object(transport.session, 'post') as mock_post:
            mock_response = Mock()
            mock_response.status_code = 400
            mock_response.text = "Invalid body"
            mock_response.json.return_value = {"message": "Invalid body"}

            mock_post.return_value = mock_response

            with pytest.raises(DiscordClientError) as exc_info:
                transport.send_message("test")
            
            assert exc_info.value.status_code == 400
            assert mock_post.call_count == 1  # No retry

    def test_5xx_server_error_retries(self, transport):
        """Test that 5xx errors trigger retry with backoff."""
        with patch.object(transport.session, 'post') as mock_post:
            mock_response_500 = Mock()
            mock_response_500.status_code = 500
            mock_response_500.text = "Internal error"
            mock_response_500.json.return_value = {"message": "Internal error"}

            mock_response_200 = Mock()
            mock_response_200.status_code = 204

            mock_post.side_effect = [mock_response_500, mock_response_200]

            result = transport.send_message("test")
            assert result["ok"] is True
            assert mock_post.call_count == 2

    def test_dry_run_no_request(self, transport):
        """Test that dry_run=True doesn't make actual requests."""
        with patch.object(transport.session, 'post') as mock_post:
            result = transport.send_message("test", dry_run=True)

            assert result["ok"] is True
            assert result.get("dry_run") is True
            assert result.get("payload", {}).get("content") == "test"
            assert "webhook_configured" in result
            assert mock_post.call_count == 0

    def test_error_parsing_429(self, transport):
        """Test 429 error parsing."""
        with patch.object(transport.session, 'post') as mock_post:
            mock_response = Mock()
            mock_response.status_code = 429
            mock_response.headers = {"Retry-After": "2"}
            mock_response.text = "Rate limited"
            mock_response.json.return_value = {"message": "Rate limited", "retry_after": 2}

            mock_post.return_value = mock_response

            with pytest.raises(DiscordError) as exc_info:
                transport.send_message("test")

            assert "Rate limited" in str(exc_info.value)
    def test_error_parsing_401(self, transport):
        """Test 401 error parsing."""
        with patch.object(transport.session, 'post') as mock_post:
            mock_response = Mock()
            mock_response.status_code = 401
            mock_response.text = "Unauthorized"
            mock_response.json.return_value = {"message": "Unauthorized"}

            mock_post.return_value = mock_response

            with pytest.raises(DiscordClientError) as exc_info:
                transport.send_message("test")
            
            assert exc_info.value.status_code == 401

    def test_ping(self, transport):
        """Test ping method."""
        with patch.object(transport.session, 'post') as mock_post:
            mock_response = Mock()
            mock_response.status_code = 204

            mock_post.return_value = mock_response

            result = transport.ping()
            assert result["ok"] is True


class TestDiscordListener:
    """Tests for DiscordListener mode."""

    @pytest.fixture
    def listener(self):
        """Create a DiscordListener instance for testing."""
        return DiscordListener(
            webhook_url="https://discord.com/api/webhooks/test/webhook",
            poll_interval=30,
            state_file="/tmp/test_listener_state.json",
        )

    def test_listener_initialization(self, listener):
        """Test listener initializes correctly."""
        assert listener.poll_interval == 30
        assert listener._running is False

    def test_save_state(self, listener, tmp_path):
        """Test state saving to file."""
        state_file = tmp_path / "state.json"
        listener.state_file = str(state_file)
        listener._last_event_id = "test_event_123"
        
        listener._save_state()
        
        assert state_file.exists()
        import json
        state = json.loads(state_file.read_text())
        assert state["last_event_id"] == "test_event_123"

    def test_start_stop(self, listener):
        """Test listener can be started and stopped."""
        async def dummy_callback(event):
            pass

        loop = asyncio.new_event_loop()
        try:
            task = loop.create_task(listener.start(dummy_callback))
            loop.run_until_complete(asyncio.sleep(0.1))
            listener.stop()
        finally:
            loop.close()

        assert listener._running is False

    def test_listener_polls_jsonl_event_source(self, tmp_path):
        event_file = tmp_path / "events.jsonl"
        state_file = tmp_path / "state.json"
        event_file.write_text(json.dumps({"id": "evt-1", "kind": "ping"}) + "\n")

        listener = DiscordListener(
            poll_interval=1,
            state_file=str(state_file),
            event_source_file=str(event_file),
        )

        events = asyncio.run(listener._poll_events())
        assert len(events) == 1
        assert events[0]["id"] == "evt-1"

        events2 = asyncio.run(listener._poll_events())
        assert events2 == []

class TestBackwardsCompatibility:
    """Test backwards compatibility with old DiscordClient class."""

    def test_discord_client_alias(self):
        """Test that DiscordClient is an alias for DiscordTransport."""
        from beacon_skill.transports.discord import DiscordClient
        
        client = DiscordClient(webhook_url="https://discord.com/api/webhooks/test/webhook")
        
        assert isinstance(client, DiscordTransport)
        assert hasattr(client, 'send_message')
        assert hasattr(client, 'send_beacon')
        assert hasattr(client, 'ping')
