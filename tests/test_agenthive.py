import pytest
import time
from unittest.mock import Mock, patch
from beacon_skill.transports.agenthive import AgentHiveClient, AgentHiveError

class TestAgentHiveClient:
    @pytest.fixture
    def client(self):
        return AgentHiveClient(api_key="hk_test_key")

    def test_register(self, client):
        with patch.object(client.session, 'request') as mock_req:
            mock_resp = Mock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"api_key": "hk_new_key"}
            mock_req.return_value = mock_resp

            result = client.register("testbot", "A test bot")
            assert result["api_key"] == "hk_new_key"
            mock_req.assert_called_once_with(
                "POST", "https://agenthive.to/api/agents", headers={}, timeout=20, json={"name": "testbot", "bio": "A test bot"}
            )

    @patch("beacon_skill.transports.agenthive.get_last_ts")
    @patch("beacon_skill.transports.agenthive.set_last_ts")
    def test_post_message(self, mock_set_ts, mock_get_ts, client):
        mock_get_ts.return_value = 0 # old timestamp

        with patch.object(client.session, 'request') as mock_req:
            mock_resp = Mock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"id": 123}
            mock_req.return_value = mock_resp

            result = client.post_message("hello hive")
            assert result["id"] == 123
            mock_req.assert_called_once_with(
                "POST", "https://agenthive.to/api/posts",
                headers={"Content-Type": "application/json", "Authorization": "Bearer hk_test_key"},
                timeout=20,
                json={"content": "hello hive"}
            )
            mock_set_ts.assert_called_once_with("agenthive_post")

    def test_read_timeline(self, client):
        with patch.object(client.session, 'request') as mock_req:
            mock_resp = Mock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = [{"id": 1}]
            mock_req.return_value = mock_resp

            result = client.read_timeline()
            assert result[0]["id"] == 1
            mock_req.assert_called_once_with(
                "GET", "https://agenthive.to/api/feed", headers={}, timeout=20
            )

    def test_auth_error(self):
        client = AgentHiveClient() # No api key
        with pytest.raises(AgentHiveError, match="AgentHive API key required"):
            client.post_message("hello")

    def test_http_error(self, client):
        with patch.object(client.session, 'request') as mock_req:
            mock_resp = Mock()
            mock_resp.status_code = 403
            mock_resp.json.return_value = {"error": "Forbidden"}
            mock_req.return_value = mock_resp
            
            with pytest.raises(AgentHiveError, match="Forbidden"):
                client.read_timeline()
