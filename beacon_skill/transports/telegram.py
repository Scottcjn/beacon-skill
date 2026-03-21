"""Telegram transport for Beacon."""

import logging
import requests
import time
from typing import Dict, Any, List

logger = logging.getLogger(__name__)

class TelegramError(RuntimeError):
    pass

class TelegramClient:
    def __init__(self, bot_token: str, timeout_s: int = 20):
        self.bot_token = bot_token
        self.timeout_s = timeout_s
        self.session = requests.Session()
        self.base_url = f"https://api.telegram.org/bot{bot_token}"

    def send_message(self, chat_id: str, text: str, envelope: Dict[str, Any] = None) -> Dict[str, Any]:
        url = f"{self.base_url}/sendMessage"
        
        message_text = text
        if envelope:
            message_text += f"\n\n[Beacon]: {envelope.get('id', 'N/A')} ({envelope.get('kind', 'N/A')})"
            
        payload = {
            "chat_id": chat_id,
            "text": message_text
        }
        
        resp = self.session.post(url, json=payload, timeout=self.timeout_s)
        if resp.status_code != 200:
            raise TelegramError(f"Failed to send to Telegram: {resp.text}")
        return resp.json()

class TelegramListener:
    def __init__(self, bot_token: str, timeout_s: int = 20):
        self.bot_token = bot_token
        self.timeout_s = timeout_s
        self.base_url = f"https://api.telegram.org/bot{bot_token}"
        self.session = requests.Session()
        
    def run_sync(self, callback):
        offset = 0
        logger.info("Starting Telegram listener...")
        while True:
            try:
                url = f"{self.base_url}/getUpdates"
                payload = {"offset": offset, "timeout": self.timeout_s}
                resp = self.session.get(url, params=payload, timeout=self.timeout_s + 5)
                
                if resp.status_code == 200:
                    data = resp.json()
                    
                    if not data.get("ok"):
                        continue
                        
                    for update in data.get("result", []):
                        offset = update["update_id"] + 1
                        msg = update.get("message")
                        if msg and "text" in msg:
                            env = {
                                "platform": "telegram",
                                "chat_id": str(msg["chat"]["id"]),
                                "text": msg["text"],
                                "raw": update
                            }
                            callback(env)
                else:
                    logger.error(f"Telegram offset {offset} failed: {resp.status_code}")
                    time.sleep(2)
            except Exception as e:
                logger.error(f"Telegram polling error: {e}")
                time.sleep(5)
