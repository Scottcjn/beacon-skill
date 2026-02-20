"""Webhook transport: HTTP endpoint for receiving/sending beacons over the internet."""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, Optional

import requests

from ..codec import decode_envelopes, verify_envelope
from ..guard import check_envelope_window
from ..identity import AgentIdentity
from ..inbox import _learn_key_from_envelope, load_known_keys, save_known_keys
from ..storage import append_jsonl


class WebhookHandler(BaseHTTPRequestHandler):
    """HTTP handler for the Beacon webhook server."""

    def log_message(self, format, *args):
        # Suppress default stderr logging.
        pass

    def _send_json(self, status: int, data: Dict[str, Any]) -> None:
        body = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/beacon/health":
            identity = self.server.beacon_identity
            data = {"ok": True, "beacon_version": "1.0.0"}
            if identity:
                data["agent_id"] = identity.agent_id
            self._send_json(200, data)
            return

        if self.path == "/.well-known/beacon.json":
            card = getattr(self.server, "beacon_agent_card", None)
            if card:
                self._send_json(200, card)
            else:
                self._send_json(404, {"error": "No agent card configured"})
            return

        self._send_json(404, {"error": "Not found"})

    def do_POST(self) -> None:
        if self.path != "/beacon/inbox":
            self._send_json(404, {"error": "Not found"})
            return

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8", errors="replace")

        # Try to parse as JSON (envelope or raw text).
        envelopes = []
        try:
            data = json.loads(body)
            if isinstance(data, dict) and "kind" in data:
                # Single envelope object.
                envelopes = [data]
            elif isinstance(data, dict) and "text" in data:
                # Wrapped text with embedded envelopes.
                envelopes = decode_envelopes(data["text"])
            elif isinstance(data, list):
                envelopes = data
        except json.JSONDecodeError:
            # Try raw text with embedded envelopes.
            envelopes = decode_envelopes(body)

        if not envelopes:
            self._send_json(400, {"error": "No beacon envelopes found"})
            return

        known_keys = load_known_keys()
        results = []
        accepted = 0

        for env in envelopes:
            _learn_key_from_envelope(env, known_keys)
            verified = verify_envelope(env, known_keys=known_keys)

            nonce = str(env.get("nonce") or "")
            kind = str(env.get("kind") or "")
            signed = bool(env.get("sig"))

            accepted_env = False
            reason = "ok"

            if signed:
                # Signed envelopes must be verifiable before any nonce/time checks.
                if verified is False:
                    reason = "signature_invalid"
                elif verified is None:
                    reason = "signature_unverifiable"
                else:
                    ok, reason = check_envelope_window(env)
                    accepted_env = ok
            else:
                # Legacy unsigned envelopes are still accepted for backward compatibility.
                accepted_env = True
                reason = "legacy_unsigned"

            if accepted_env:
                record = {
                    "platform": "webhook",
                    "from": self.client_address[0],
                    "received_at": time.time(),
                    "text": body,
                    "envelopes": [env],
                }
                append_jsonl("inbox.jsonl", record)
                accepted += 1

            results.append(
                {
                    "nonce": nonce,
                    "kind": kind,
                    "verified": verified,
                    "accepted": accepted_env,
                    "reason": reason,
                }
            )

        save_known_keys(known_keys)

        if accepted == 0:
            self._send_json(400, {"ok": False, "received": 0, "results": results, "error": "no_valid_envelopes"})
            return

        self._send_json(200, {"ok": True, "received": accepted, "results": results})


class WebhookServer:
    """Beacon webhook HTTP server using stdlib."""

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
        self._server: Optional[HTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    def start(self, blocking: bool = True) -> None:
        """Start the webhook server."""
        self._server = HTTPServer((self.host, self.port), WebhookHandler)
        self._server.beacon_identity = self.identity
        self._server.beacon_agent_card = self.agent_card

        if blocking:
            self._server.serve_forever()
        else:
            self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
            self._thread.start()

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._server = None


def webhook_send(
    url: str,
    envelope: Dict[str, Any],
    *,
    identity: Optional[AgentIdentity] = None,
    timeout_s: int = 15,
) -> Dict[str, Any]:
    """Send a beacon envelope to a webhook endpoint."""
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Beacon/1.0.0 (Elyan Labs)",
    }
    resp = requests.post(url, json=envelope, headers=headers, timeout=timeout_s)
    try:
        return resp.json()
    except Exception:
        return {"raw": resp.text, "status": resp.status_code}
