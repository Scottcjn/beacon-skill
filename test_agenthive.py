#!/usr/bin/env python3
"""
Test suite for AgentHive transport
Bounty #151 - 10 RTC
"""

import pytest
import os
from unittest.mock import patch, MagicMock

from agenthive import (
    AgentHiveTransport,
    send_message,
    read_messages
)


class TestAgentHiveTransport:
    """Test AgentHive transport class."""
    
    def test_init_default(self):
        """Test initialization with defaults."""
        with patch.dict(os.environ, {}, clear=True):
            transport = AgentHiveTransport()
            assert transport.api_key is None
            assert transport.base_url == "https://agenthive.to"
            assert transport.agent_name == "unknown"
    
    def test_init_with_api_key(self):
        """Test initialization with API key."""
        transport = AgentHiveTransport(api_key="test_key")
        assert transport.api_key == "test_key"
    
    def test_init_with_env_var(self):
        """Test initialization with environment variable."""
        with patch.dict(os.environ, {'AGENTHIVE_API_KEY': 'env_key'}):
            transport = AgentHiveTransport()
            assert transport.api_key == "env_key"
    
    def test_init_custom_url(self):
        """Test initialization with custom base URL."""
        transport = AgentHiveTransport(base_url="https://custom.url/")
        assert transport.base_url == "https://custom.url"


class TestPostMessage:
    """Test post_message method."""
    
    def test_post_success(self):
        """Test successful post."""
        mock_response = MagicMock()
        mock_response.json.return_value = {'id': 123, 'content': 'test'}
        
        with patch('agenthive.requests.post', return_value=mock_response):
            transport = AgentHiveTransport(api_key="test")
            result = transport.post_message("test content")
            
            assert 'error' not in result
            assert result['id'] == 123
    
    def test_post_with_mentions(self):
        """Test post with mentions."""
        mock_response = MagicMock()
        mock_response.json.return_value = {'id': 123}
        
        with patch('agenthive.requests.post', return_value=mock_response) as mock_post:
            transport = AgentHiveTransport(api_key="test")
            transport.post_message("hello", mentions=['alice', 'bob'])
            
            # Check mentions were added to content
            call_args = mock_post.call_args
            assert '@alice' in call_args[1]['json']['content']
            assert '@bob' in call_args[1]['json']['content']
    
    def test_post_failure(self):
        """Test post failure."""
        import requests
        with patch('agenthive.requests.post', side_effect=requests.exceptions.RequestException('Network error')):
            transport = AgentHiveTransport(api_key="test")
            result = transport.post_message("test")
            
            assert 'error' in result


class TestGetTimeline:
    """Test get_timeline method."""
    
    def test_timeline_success(self):
        """Test successful timeline retrieval."""
        mock_response = MagicMock()
        mock_response.json.return_value = {'posts': [{'id': 1}, {'id': 2}]}
        
        with patch('agenthive.requests.get', return_value=mock_response):
            transport = AgentHiveTransport()
            timeline = transport.get_timeline(limit=10)
            
            assert len(timeline) == 2
            assert timeline[0]['id'] == 1
    
    def test_timeline_empty(self):
        """Test empty timeline."""
        mock_response = MagicMock()
        mock_response.json.return_value = {'posts': []}
        
        with patch('agenthive.requests.get', return_value=mock_response):
            transport = AgentHiveTransport()
            timeline = transport.get_timeline()
            
            assert len(timeline) == 0
    
    def test_timeline_failure(self):
        """Test timeline failure."""
        import requests
        with patch('agenthive.requests.get', side_effect=requests.exceptions.RequestException('Network error')):
            transport = AgentHiveTransport()
            timeline = transport.get_timeline()
            
            assert len(timeline) == 0


class TestGetAgentPosts:
    """Test get_agent_posts method."""
    
    def test_agent_posts_success(self):
        """Test successful agent posts retrieval."""
        mock_response = MagicMock()
        mock_response.json.return_value = {'posts': [{'id': 1}]}
        
        with patch('agenthive.requests.get', return_value=mock_response):
            transport = AgentHiveTransport()
            posts = transport.get_agent_posts('testagent', limit=5)
            
            assert len(posts) == 1


class TestFollowAgent:
    """Test follow_agent method."""
    
    def test_follow_success(self):
        """Test successful follow."""
        mock_response = MagicMock()
        mock_response.json.return_value = {'success': True}
        
        with patch('agenthive.requests.post', return_value=mock_response):
            transport = AgentHiveTransport(api_key="test")
            result = transport.follow_agent('testagent')
            
            assert result['success'] == True


class TestSearchPosts:
    """Test search_posts method."""
    
    def test_search_success(self):
        """Test successful search."""
        mock_response = MagicMock()
        mock_response.json.return_value = {'posts': [{'id': 1}]}
        
        with patch('agenthive.requests.get', return_value=mock_response):
            transport = AgentHiveTransport()
            results = transport.search_posts('AI', limit=10)
            
            assert len(results) == 1


class TestGetAgentProfile:
    """Test get_agent_profile method."""
    
    def test_profile_success(self):
        """Test successful profile retrieval."""
        mock_response = MagicMock()
        mock_response.json.return_value = {'name': 'test', 'bio': 'test bio'}
        
        with patch('agenthive.requests.get', return_value=mock_response):
            transport = AgentHiveTransport()
            profile = transport.get_agent_profile('testagent')
            
            assert profile['name'] == 'test'
    
    def test_profile_not_found(self):
        """Test profile not found."""
        import requests
        with patch('agenthive.requests.get', side_effect=requests.exceptions.RequestException('404')):
            transport = AgentHiveTransport()
            profile = transport.get_agent_profile('nonexistent')
            
            assert profile is None


class TestRegisterAgent:
    """Test register_agent method."""
    
    def test_register_success(self):
        """Test successful registration."""
        mock_response = MagicMock()
        mock_response.json.return_value = {'id': 123, 'name': 'newagent'}
        
        with patch('agenthive.requests.post', return_value=mock_response):
            transport = AgentHiveTransport(api_key="test")
            result = transport.register_agent('newagent', bio='test')
            
            assert result['id'] == 123


class TestHealthCheck:
    """Test health_check method."""
    
    def test_health_healthy(self):
        """Test healthy status."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        
        with patch('agenthive.requests.get', return_value=mock_response):
            transport = AgentHiveTransport()
            health = transport.health_check()
            
            assert health['status'] == 'healthy'
    
    def test_health_unhealthy(self):
        """Test unhealthy status."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        
        with patch('agenthive.requests.get', return_value=mock_response):
            transport = AgentHiveTransport()
            health = transport.health_check()
            
            assert health['status'] == 'unhealthy'
    
    def test_health_error(self):
        """Test health check error."""
        import requests
        with patch('agenthive.requests.get', side_effect=requests.exceptions.RequestException('Network error')):
            transport = AgentHiveTransport()
            health = transport.health_check()
            
            assert health['status'] == 'unhealthy'
            assert 'error' in health


class TestBeaconInterface:
    """Test Beacon interface functions."""
    
    def test_send_message(self):
        """Test send_message Beacon interface."""
        mock_response = MagicMock()
        mock_response.json.return_value = {'id': 123}
        
        with patch('agenthive.requests.post', return_value=mock_response):
            result = send_message("test", api_key="test")
            
            assert result['success'] == True
            assert result['transport'] == 'agenthive'
    
    def test_read_messages(self):
        """Test read_messages Beacon interface."""
        mock_response = MagicMock()
        mock_response.json.return_value = {'posts': [{'id': 1}]}
        
        with patch('agenthive.requests.get', return_value=mock_response):
            messages = read_messages(limit=5)
            
            assert len(messages) == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
