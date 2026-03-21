import logging
import asyncio
from typing import Dict, Any, Optional
import aiohttp

logger = logging.getLogger(__name__)

class AgentHiveTransport:
    """
    AgentHive transport for Beacon framework.
    Handles communication with AgentHive instances and swarms.
    """
    
    def __init__(self, endpoint_url: str, api_key: Optional[str] = None):
        self.endpoint_url = endpoint_url.rstrip('/')
        self.api_key = api_key
        self.session: Optional[aiohttp.ClientSession] = None
        self._connected = False
        
    async def connect(self) -> bool:
        if not self.session:
            self.session = aiohttp.ClientSession(
                headers={"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
            )
        self._connected = True
        logger.info(f"AgentHiveTransport connected to {self.endpoint_url}")
        return True
        
    async def disconnect(self) -> bool:
        if self.session:
            await self.session.close()
            self.session = None
        self._connected = False
        logger.info("AgentHiveTransport disconnected")
        return True
        
    async def send_message(self, target_id: str, payload: Dict[str, Any]) -> bool:
        if not self._connected or not self.session:
            logger.error("AgentHiveTransport not connected")
            return False
            
        try:
            url = f"{self.endpoint_url}/api/v1/agents/{target_id}/messages"
            async with self.session.post(url, json=payload) as response:
                if response.status in (200, 201, 202):
                    return True
                else:
                    text = await response.text()
                    logger.error(f"AgentHive send failed: {response.status} - {text}")
                    return False
        except Exception as e:
            logger.error(f"AgentHive send error: {e}")
            return False
            
    async def join_swarm(self, swarm_id: str, agent_info: Dict[str, Any]) -> bool:
        if not self._connected or not self.session:
            return False
            
        try:
            url = f"{self.endpoint_url}/api/v1/swarms/{swarm_id}/join"
            async with self.session.post(url, json=agent_info) as response:
                return response.status in (200, 201)
        except Exception as e:
            logger.error(f"AgentHive join_swarm error: {e}")
            return False
