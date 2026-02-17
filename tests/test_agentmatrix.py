"""Tests for Agent Matrix transport."""

import pytest
from unittest.mock import MagicMock, patch
from beacon_skill.transports.agentmatrix import (
    AgentMatrixTransport,
    send_message,
    check_inbox,
    discover_agents,
    _request,
)


@pytest.fixture
def transport():
    return AgentMatrixTransport(
        api_url="http://test.local/api",
        agent_phone="+1234567890",
    )


class TestAgentMatrixTransport:
    def test_init_defaults(self):
        t = AgentMatrixTransport()
        assert t.api_url == "http://localhost:4020/api"
        assert t.agent_name == "beacon-agent"
        
    def test_init_with_config(self):
        t = AgentMatrixTransport(
            api_url="http://custom.local/api",
            agent_phone="+9999999999",
        )
        assert t.api_url == "http://custom.local/api"
        assert t.agent_phone == "+9999999999"
        
    def test_generate_phone(self, transport):
        phone = transport._generate_phone()
        assert phone.startswith("+")
        assert len(phone) == 11  # + and 10 digits


class TestRegister:
    @patch("beacon_skill.transports.agentmatrix._request")
    def test_register_success(self, mock_request, transport):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "phone": "+1234567890",
            "name": "test-agent",
            "status": "registered",
        }
        mock_request.return_value = mock_response
        
        result = transport.register(name="test-agent")
        
        assert result["status"] == "registered"
        mock_request.assert_called_once()
        
    @patch("beacon_skill.transports.agentmatrix._request")
    def test_register_failure(self, mock_request, transport):
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.text = "Invalid phone"
        mock_request.return_value = mock_response
        
        result = transport.register()
        
        assert "error" in result
        assert result["status"] == 400


class TestSend:
    @patch("beacon_skill.transports.agentmatrix._request")
    def test_send_success(self, mock_request, transport):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": "msg_123",
            "status": "sent",
        }
        mock_request.return_value = mock_response
        
        result = transport.send("+0987654321", "Hello!", kind="hello")
        
        assert result["status"] == "sent"
        
    def test_send_without_registration(self):
        t = AgentMatrixTransport()
        t.agent_phone = None
        result = t.send("+0987654321", "Hello!")
        assert "error" in result
        
    @patch("beacon_skill.transports.agentmatrix._request")
    def test_send_with_lambda_encoding(self, mock_request, transport):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"status": "sent"}
        mock_request.return_value = mock_response
        
        transport.send("+0987654321", "Hello!", use_lambda=True)
        
        # Check that lambda encoding was attempted
        call_args = mock_request.call_args
        payload = call_args.kwargs.get("json", {})
        assert payload.get("encoding") == "lambda"


class TestInbox:
    @patch("beacon_skill.transports.agentmatrix._request")
    def test_inbox_success(self, mock_request, transport):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "messages": [
                {"id": "msg_1", "content": "Hello"},
                {"id": "msg_2", "content": "World"},
            ]
        }
        mock_request.return_value = mock_response
        
        result = transport.inbox(limit=10)
        
        assert len(result) == 2
        
    def test_inbox_without_registration(self):
        t = AgentMatrixTransport()
        t.agent_phone = None
        result = t.inbox()
        assert result == []


class TestDiscover:
    @patch("beacon_skill.transports.agentmatrix._request")
    def test_discover_success(self, mock_request, transport):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "agents": [
                {"phone": "+1111111111", "name": "agent-1"},
                {"phone": "+2222222222", "name": "agent-2"},
            ]
        }
        mock_request.return_value = mock_response
        
        result = transport.discover(capability="beacon")
        
        assert len(result) == 2
        
    @patch("beacon_skill.transports.agentmatrix._request")
    def test_discover_with_filters(self, mock_request, transport):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"agents": []}
        mock_request.return_value = mock_response
        
        transport.discover(capability="lambda", protocol="beacon-2.11")
        
        call_args = mock_request.call_args
        params = call_args.kwargs.get("params", {})
        assert params.get("capability") == "lambda"
        assert params.get("protocol") == "beacon-2.11"


class TestConvenienceFunctions:
    @patch("beacon_skill.transports.agentmatrix.AgentMatrixTransport")
    def test_send_message(self, MockTransport):
        mock_instance = MagicMock()
        mock_instance.agent_phone = "+1234567890"
        mock_instance.send.return_value = {"status": "sent"}
        MockTransport.return_value = mock_instance
        
        result = send_message("+0987654321", "Hello!")
        
        mock_instance.send.assert_called_once()
        
    @patch("beacon_skill.transports.agentmatrix.AgentMatrixTransport")
    def test_check_inbox(self, MockTransport):
        mock_instance = MagicMock()
        mock_instance.inbox.return_value = [{"id": "msg_1"}]
        MockTransport.return_value = mock_instance
        
        result = check_inbox(limit=5)
        
        mock_instance.inbox.assert_called_with(limit=5)
        
    @patch("beacon_skill.transports.agentmatrix.AgentMatrixTransport")
    def test_discover_agents(self, MockTransport):
        mock_instance = MagicMock()
        mock_instance.discover.return_value = []
        MockTransport.return_value = mock_instance
        
        discover_agents(capability="beacon")
        
        mock_instance.discover.assert_called_with(capability="beacon")
