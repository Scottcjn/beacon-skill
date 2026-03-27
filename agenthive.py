#!/usr/bin/env python3
"""
AgentHive Transport for Beacon
Bounty #151 - 10 RTC

Add AgentHive as a Beacon transport layer.
AgentHive is an independent, open microblogging network for AI agents.

API:
- POST /api/posts - Post a message (requires api_key)
- GET /api/feed - Read timeline (public)
- GET /api/agents/{name}/posts - Get agent posts
- POST /api/agents/{name}/follow - Follow an agent
"""

import os
import json
import time
import hashlib
import requests
from typing import Optional, List, Dict, Any


class AgentHiveTransport:
    """AgentHive transport layer for Beacon."""
    
    def __init__(self, api_key: Optional[str] = None, base_url: str = "https://agenthive.to"):
        """
        Initialize AgentHive transport.
        
        Args:
            api_key: AgentHive API key (optional for read, required for post)
            base_url: AgentHive API base URL
        """
        self.api_key = api_key or os.environ.get('AGENTHIVE_API_KEY')
        self.base_url = base_url.rstrip('/')
        self.agent_name = os.environ.get('BEACON_AGENT_NAME', 'unknown')
    
    def post_message(self, content: str, mentions: Optional[List[str]] = None) -> Dict[str, Any]:
        """
        Post a message to AgentHive.
        
        Args:
            content: Message content
            mentions: List of agent names to mention
            
        Returns:
            dict: Post response
        """
        url = f"{self.base_url}/api/posts"
        headers = {
            'Content-Type': 'application/json',
        }
        
        if self.api_key:
            headers['Authorization'] = f'Bearer {self.api_key}'
        
        # Add mentions to content
        if mentions:
            mention_str = ' '.join(f'@{m}' for m in mentions)
            content = f"{mention_str} {content}"
        
        payload = {
            'content': content,
            'agent_name': self.agent_name
        }
        
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            return {
                'success': False,
                'error': str(e)
            }
    
    def get_timeline(self, limit: int = 20) -> List[Dict[str, Any]]:
        """
        Get timeline from AgentHive.
        
        Args:
            limit: Number of posts to retrieve
            
        Returns:
            list: Timeline posts
        """
        url = f"{self.base_url}/api/feed"
        params = {'limit': limit}
        
        try:
            response = requests.get(url, params=params, timeout=30)
            response.raise_for_status()
            return response.json().get('posts', [])
        except requests.exceptions.RequestException as e:
            return []
    
    def get_agent_posts(self, agent_name: str, limit: int = 20) -> List[Dict[str, Any]]:
        """
        Get posts from a specific agent.
        
        Args:
            agent_name: Agent name
            limit: Number of posts to retrieve
            
        Returns:
            list: Agent posts
        """
        url = f"{self.base_url}/api/agents/{agent_name}/posts"
        params = {'limit': limit}
        
        try:
            response = requests.get(url, params=params, timeout=30)
            response.raise_for_status()
            return response.json().get('posts', [])
        except requests.exceptions.RequestException as e:
            return []
    
    def follow_agent(self, agent_name: str) -> Dict[str, Any]:
        """
        Follow an agent.
        
        Args:
            agent_name: Agent name to follow
            
        Returns:
            dict: Follow response
        """
        url = f"{self.base_url}/api/agents/{agent_name}/follow"
        headers = {}
        
        if self.api_key:
            headers['Authorization'] = f'Bearer {self.api_key}'
        
        try:
            response = requests.post(url, headers=headers, timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            return {
                'success': False,
                'error': str(e)
            }
    
    def search_posts(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        """
        Search posts by keyword.
        
        Args:
            query: Search query
            limit: Number of posts to retrieve
            
        Returns:
            list: Matching posts
        """
        url = f"{self.base_url}/api/search"
        params = {
            'q': query,
            'limit': limit
        }
        
        try:
            response = requests.get(url, params=params, timeout=30)
            response.raise_for_status()
            return response.json().get('posts', [])
        except requests.exceptions.RequestException as e:
            return []
    
    def get_agent_profile(self, agent_name: str) -> Optional[Dict[str, Any]]:
        """
        Get agent profile.
        
        Args:
            agent_name: Agent name
            
        Returns:
            dict: Agent profile or None
        """
        url = f"{self.base_url}/api/agents/{agent_name}"
        
        try:
            response = requests.get(url, timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException:
            return None
    
    def register_agent(self, agent_name: str, bio: str = "") -> Dict[str, Any]:
        """
        Register a new agent.
        
        Args:
            agent_name: Agent name
            bio: Agent biography
            
        Returns:
            dict: Registration response
        """
        url = f"{self.base_url}/api/agents"
        headers = {
            'Content-Type': 'application/json',
        }
        
        if self.api_key:
            headers['Authorization'] = f'Bearer {self.api_key}'
        
        payload = {
            'name': agent_name,
            'bio': bio
        }
        
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            return {
                'success': False,
                'error': str(e)
            }
    
    def health_check(self) -> Dict[str, Any]:
        """
        Check AgentHive API health.
        
        Returns:
            dict: Health status
        """
        url = f"{self.base_url}/api/health"
        
        try:
            response = requests.get(url, timeout=10)
            return {
                'status': 'healthy' if response.status_code == 200 else 'unhealthy',
                'status_code': response.status_code
            }
        except requests.exceptions.RequestException as e:
            return {
                'status': 'unhealthy',
                'error': str(e)
            }


# Beacon transport interface implementation
def send_message(content: str, mentions: Optional[List[str]] = None, **kwargs) -> Dict[str, Any]:
    """
    Send a message via AgentHive (Beacon interface).
    
    Args:
        content: Message content
        mentions: List of agent names to mention
        **kwargs: Additional arguments (api_key, base_url)
        
    Returns:
        dict: Send result
    """
    transport = AgentHiveTransport(
        api_key=kwargs.get('api_key'),
        base_url=kwargs.get('base_url', 'https://agenthive.to')
    )
    
    result = transport.post_message(content, mentions)
    
    return {
        'success': 'error' not in result,
        'transport': 'agenthive',
        'result': result
    }


def read_messages(limit: int = 20, **kwargs) -> List[Dict[str, Any]]:
    """
    Read messages from AgentHive (Beacon interface).
    
    Args:
        limit: Number of messages to read
        **kwargs: Additional arguments (api_key, base_url)
        
    Returns:
        list: Messages
    """
    transport = AgentHiveTransport(
        api_key=kwargs.get('api_key'),
        base_url=kwargs.get('base_url', 'https://agenthive.to')
    )
    
    return transport.get_timeline(limit)


if __name__ == "__main__":
    # Demo/test
    print("=== AgentHive Transport Demo ===")
    print("Bounty #151 - 10 RTC\n")
    
    transport = AgentHiveTransport()
    
    # Health check
    print("1. Health check...")
    health = transport.health_check()
    print(f"   Status: {health['status']}")
    
    # Get timeline
    print("\n2. Getting timeline...")
    timeline = transport.get_timeline(limit=5)
    print(f"   Found {len(timeline)} posts")
    
    # Search
    print("\n3. Searching for 'AI'...")
    results = transport.search_posts('AI', limit=3)
    print(f"   Found {len(results)} posts")
    
    print("\n=== Demo Complete ===")
