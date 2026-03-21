import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from beacon_skill.transports.agenthive import AgentHiveTransport

@pytest.fixture
def agent_hive():
    return AgentHiveTransport(endpoint_url="https://api.agenthive.test", api_key="test_key")

@pytest.mark.asyncio
async def test_connect_disconnect(agent_hive):
    assert await agent_hive.connect() is True
    assert agent_hive._connected is True
    assert agent_hive.session is not None
    
    assert await agent_hive.disconnect() is True
    assert agent_hive._connected is False
    assert agent_hive.session is None

@pytest.mark.asyncio
async def test_send_message_not_connected(agent_hive):
    result = await agent_hive.send_message("agent_123", {"text": "hello"})
    assert result is False

@pytest.mark.asyncio
async def test_send_message_success(agent_hive):
    await agent_hive.connect()
    
    mock_post = MagicMock()
    mock_response = AsyncMock()
    mock_response.status = 200
    mock_post.return_value.__aenter__.return_value = mock_response
    
    agent_hive.session.post = mock_post
    
    result = await agent_hive.send_message("agent_123", {"text": "hello"})
    
    assert result is True
    mock_post.assert_called_once_with(
        "https://api.agenthive.test/api/v1/agents/agent_123/messages",
        json={"text": "hello"}
    )
    
    await agent_hive.disconnect()

@pytest.mark.asyncio
async def test_join_swarm_success(agent_hive):
    await agent_hive.connect()
    
    mock_post = MagicMock()
    mock_response = AsyncMock()
    mock_response.status = 200
    mock_post.return_value.__aenter__.return_value = mock_response
    
    agent_hive.session.post = mock_post
    
    result = await agent_hive.join_swarm("swarm_123", {"name": "test_agent"})
    
    assert result is True
    mock_post.assert_called_once_with(
        "https://api.agenthive.test/api/v1/swarms/swarm_123/join",
        json={"name": "test_agent"}
    )
    
    await agent_hive.disconnect()
