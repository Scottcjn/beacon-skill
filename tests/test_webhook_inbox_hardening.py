# SPDX-License-Identifier: MIT
"""Regression tests for FastAPI/stdlib webhook inbox storage, wrapped payloads, and body bounds."""

from __future__ import annotations

import asyncio
import io
import json
import time
from types import SimpleNamespace
from typing import Any

import pytest

from beacon_skill import storage
from beacon_skill.codec import encode_envelope
from beacon_skill.guard import clear_nonce_cache
from beacon_skill.identity import AgentIdentity
from beacon_skill.inbox import read_inbox
from beacon_skill.transports.webhook import WebhookHandler


class _FakeHeaders(dict):
    def get(self, key: str, default: Any = None) -> Any:
        key_l = key.lower()
        for k, v in self.items():
            if k.lower() == key_l:
                return v
        return default


class _FakeRequest:
    def __init__(self, body_bytes: bytes, headers: dict[str, str] | None = None) -> None:
        self._body = body_bytes
        self.headers = _FakeHeaders(headers or {"Content-Length": str(len(body_bytes))})
        self.client = SimpleNamespace(host="127.0.0.1")

    async def body(self) -> bytes:
        return self._body


def _fastapi_inbox_endpoint():
    fastapi = pytest.importorskip("fastapi")
    pytest.importorskip("pydantic")
    from beacon_skill.transports.webhook_fastapi import (
        MAX_BODY_SIZE,
        FastAPIWebhookServer,
    )

    server = FastAPIWebhookServer(host="127.0.0.1", port=0)
    for route in server.app.routes:
        if getattr(route, "path", None) == "/beacon/inbox" and "POST" in (getattr(route, "methods", None) or set()):
            return route.endpoint, fastapi.HTTPException, MAX_BODY_SIZE
    raise AssertionError("POST /beacon/inbox route not registered")


@pytest.fixture
def isolated_beacon_dir(tmp_path, monkeypatch):
    beacon_dir = tmp_path / ".beacon"
    beacon_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.delenv("BEACON_INBOX_PATH", raising=False)
    monkeypatch.setattr(storage, "_dir", lambda: beacon_dir)
    clear_nonce_cache()
    return beacon_dir


def test_fastapi_signed_envelope_is_readable_via_read_inbox(isolated_beacon_dir):
    endpoint, _, _ = _fastapi_inbox_endpoint()
    ident = AgentIdentity.generate()
    signed_text = encode_envelope(
        {"kind": "hello", "text": "hi via fastapi", "ts": int(time.time()), "nonce": "fa_inbox_01"},
        version=2,
        identity=ident,
        include_pubkey=True,
    )

    resp = asyncio.run(
        endpoint(_FakeRequest(signed_text.encode("utf-8"), {"Content-Type": "text/plain"}))
    )
    assert resp.ok is True
    assert resp.received == 1
    assert resp.results[0]["verified"] is True

    entries = read_inbox(kind="hello", agent_id=ident.agent_id)
    assert len(entries) == 1
    assert entries[0]["verified"] is True
    assert entries[0]["envelope"]["nonce"] == "fa_inbox_01"
    assert entries[0]["envelope"]["text"] == "hi via fastapi"


def test_fastapi_unwraps_wrapped_text_json_and_rejects_oversized_or_malformed(isolated_beacon_dir):
    endpoint, http_exc_cls, max_body_size = _fastapi_inbox_endpoint()
    ident = AgentIdentity.generate()
    wrapped = {
        "text": encode_envelope(
            {"kind": "want", "text": "need help", "ts": int(time.time()), "nonce": "fa_wrap_01"},
            version=2,
            identity=ident,
            include_pubkey=True,
        )
    }

    resp = asyncio.run(
        endpoint(_FakeRequest(json.dumps(wrapped).encode("utf-8"), {"Content-Type": "application/json"}))
    )
    assert resp.ok is True
    entries = read_inbox(kind="want", agent_id=ident.agent_id)
    assert len(entries) == 1
    assert entries[0]["envelope"]["nonce"] == "fa_wrap_01"

    with pytest.raises(http_exc_cls) as bad_text:
        asyncio.run(endpoint(_FakeRequest(json.dumps({"text": 123}).encode("utf-8"))))
    assert bad_text.value.status_code == 400

    with pytest.raises(http_exc_cls) as bad_list:
        asyncio.run(endpoint(_FakeRequest(json.dumps(["not-a-dict"]).encode("utf-8"))))
    assert bad_list.value.status_code == 400
    assert bad_list.value.detail["results"][0]["reason"] == "invalid_envelope"

    with pytest.raises(http_exc_cls) as bad_header_len:
        asyncio.run(endpoint(_FakeRequest(b"{}", {"Content-Length": "-5"})))
    assert bad_header_len.value.status_code == 400

    with pytest.raises(http_exc_cls) as too_big:
        asyncio.run(endpoint(_FakeRequest(b"x" * (max_body_size + 1))))
    assert too_big.value.status_code == 413


def _invoke_stdlib_post(path: str, headers: dict, body_bytes: bytes):
    handler = object.__new__(WebhookHandler)
    handler.path = path
    handler.headers = headers
    handler.client_address = ("127.0.0.1", 12345)
    handler.server = type("FakeServer", (), {"beacon_rate_limiter": None})()
    handler.rfile = io.BytesIO(body_bytes)
    handler.wfile = io.BytesIO()
    sent = {}

    def _send_json(status, data):
        sent["status"] = status
        sent["data"] = data

    handler._send_json = _send_json
    handler.do_POST()
    return sent["status"], sent["data"]


@pytest.mark.parametrize("bad_len", ["-1", "abc"])
def test_stdlib_webhook_rejects_invalid_content_length(isolated_beacon_dir, bad_len):
    status, data = _invoke_stdlib_post(
        "/beacon/inbox",
        {"Content-Length": bad_len},
        b"{}",
    )
    assert status == 400
    assert "Content-Length" in data["error"]


def test_stdlib_webhook_rejects_non_string_text_and_non_dict_list_items(isolated_beacon_dir):
    status1, data1 = _invoke_stdlib_post(
        "/beacon/inbox",
        {"Content-Length": "13"},
        json.dumps({"text": 42}).encode("utf-8"),
    )
    assert status1 == 400
    assert "text" in data1["error"]

    raw_list = json.dumps(["bad"]).encode("utf-8")
    status2, data2 = _invoke_stdlib_post(
        "/beacon/inbox",
        {"Content-Length": str(len(raw_list))},
        raw_list,
    )
    assert status2 == 400
    assert data2["results"][0]["reason"] == "invalid_envelope"
