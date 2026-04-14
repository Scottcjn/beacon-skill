"""FastAPI production-grade Webhook transport for Beacon."""

import json
import time
import asyncio
from typing import Any, Dict, List, Optional
from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from pydantic import BaseModel
import uvicorn

from ..codec import decode_envelopes, verify_envelope
from ..guard import check_envelope_window
from ..identity import AgentIdentity
from ..inbox import _learn_key_from_envelope, load_known_keys, save_known_keys
from ..storage import append_jsonl

class BeaconEnvelope(BaseModel):
    """Pydantic model for individual Beacon envelopes."""
    kind: str
    agent_id: Optional[str] = None
    nonce: Optional[str] = None
    ts: Optional[int] = None
    sig: Optional[str] = None
    pubkey: Optional[str] = None
    text: Optional[str] = None

class WebhookResponse(BaseModel):
    ok: bool
    received: int
    results: List[Dict[str, Any]]
    error: Optional[str] = None

class FastAPIWebhookServer:
    """Production-grade Webhook server using FastAPI and Uvicorn."""
    
    def __init__(
        self,
        port: int = 8402,
        host: str = "0.0.0.0",
        identity: Optional[AgentIdentity] = None,
        agent_card: Optional[Dict[str, Any]] = None,
    ):
        self.port = port
        self.host = host
        self.identity = identity
        self.agent_card = agent_card
        self.app = FastAPI(title="Beacon Webhook Server")
        self._setup_routes()

    def _setup_routes(self):
        @self.app.get("/beacon/health")
        async def health():
            data = {"ok": True, "beacon_version": "1.0.0", "engine": "FastAPI"}
            if self.identity:
                data["agent_id"] = self.identity.agent_id
            return data

        @self.app.get("/.well-known/beacon.json")
        async def well_known():
            if self.agent_card:
                return self.agent_card
            raise HTTPException(status_code=404, detail="No agent card configured")

        @self.app.post("/beacon/inbox", response_model=WebhookResponse)
        async def inbox(request: Request):
            # 1. Parse payload
            try:
                body = await request.body()
                text = body.decode("utf-8")
                # Support both pure JSON envelope and wrapped text
                try:
                    data = json.loads(text)
                    if isinstance(data, dict) and "kind" in data:
                        envelopes = [data]
                    elif isinstance(data, list):
                        envelopes = data
                    else:
                        envelopes = decode_envelopes(text)
                except json.JSONDecodeError:
                    envelopes = decode_envelopes(text)
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"Invalid payload: {str(e)}")

            if not envelopes:
                return WebhookResponse(ok=False, received=0, results=[], error="no_beacon_envelopes_found")

            # 2. Process envelopes
            known_keys = load_known_keys()
            results = []
            accepted_count = 0

            for env in envelopes:
                _learn_key_from_envelope(env, known_keys)
                verified = verify_envelope(env, known_keys={k: v["pubkey_hex"] for k, v in known_keys.items()})
                
                accepted_env = False
                reason = "ok"
                
                # Security Gates
                if env.get("sig"):
                    if verified is False:
                        reason = "signature_invalid"
                    elif verified is None:
                        reason = "signature_unverifiable"
                    else:
                        ok, reason = check_envelope_window(env)
                        accepted_env = ok
                else:
                    accepted_env = True
                    reason = "legacy_unsigned"

                if accepted_env:
                    record = {
                        "platform": "webhook-fastapi",
                        "from": request.client.host if request.client else "unknown",
                        "received_at": time.time(),
                        "envelope": env,
                    }
                    append_jsonl("inbox.jsonl", record)
                    accepted_count += 1

                results.append({
                    "nonce": env.get("nonce", ""),
                    "kind": env.get("kind", ""),
                    "verified": verified,
                    "accepted": accepted_env,
                    "reason": reason
                })

            save_known_keys(known_keys)
            
            if accepted_count == 0:
                raise HTTPException(status_code=400, detail={"ok": False, "results": results, "error": "no_valid_envelopes"})

            return WebhookResponse(ok=True, received=accepted_count, results=results)

    def run(self):
        """Blocking run."""
        uvicorn.run(self.app, host=self.host, port=self.port)

    async def start_async(self):
        """Non-blocking start for integration into other loops."""
        config = uvicorn.Config(self.app, host=self.host, port=self.port, log_level="info")
        server = uvicorn.Server(config)
        await server.serve()
