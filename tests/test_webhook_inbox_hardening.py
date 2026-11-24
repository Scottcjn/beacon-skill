# SPDX-License-Identifier: MIT
"""Regression tests for FastAPI/stdlib webhook inbox storage, wrapped payloads, and body bounds."""

import io
import json
import time

import pytest
from fastapi.testclient import TestClient

from beacon_skill import storage
from beacon_skill.codec import encode_envelope
from beacon_skill.guard import clear_nonce_cache
from beacon_skill.identity import AgentIdentity
from beacon_skill.inbox import read_inbox
from beacon_skill.transports.webhook import WebhookHandler
from beacon_skill.transports.webhook_fastapi import MAX_BODY_SIZE, FastAPIWebhookServer


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
    ident = AgentIdentity.generate()
    signed_text = encode_envelope(
        {"kind": "hello", "text": "hi via fastapi", "ts": int(time.time()), "nonce": "fa_inbox_01"},
        version=2,
        identity=ident,
        include_pubkey=True,
    )
    server = FastAPIWebhookServer(host="127.0.0.1", port=0)
    client = TestClient(server.app)

    resp = client.post("/beacon/inbox", content=signed_text, headers={"Content-Type": "text/plain"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["received"] == 1
    assert body["results"][0]["verified"] is True

    entries = read_inbox(kind="hello", agent_id=ident.agent_id)
    assert len(entries) == 1
    assert entries[0]["verified"] is True
    assert entries[0]["envelope"]["nonce"] == "fa_inbox_01"
    assert entries[0]["envelope"]["text"] == "hi via fastapi"


def test_fastapi_unwraps_wrapped_text_json_and_rejects_oversized_or_malformed(isolated_beacon_dir):
    ident = AgentIdentity.generate()
    wrapped = {
        "text": encode_envelope(
            {"kind": "want", "text": "need help", "ts": int(time.time()), "nonce": "fa_wrap_01"},
            version=2,
            identity=ident,
            include_pubkey=True,
        )
    }
    server = FastAPIWebhookServer(host="127.0.0.1", port=0)
    client = TestClient(server.app)

    resp = client.post("/beacon/inbox", json=wrapped)
    assert resp.status_code == 200
    entries = read_inbox(kind="want", agent_id=ident.agent_id)
    assert len(entries) == 1
    assert entries[0]["envelope"]["nonce"] == "fa_wrap_01"

    bad_text = client.post("/beacon/inbox", json={"text": 123})
    assert bad_text.status_code == 400

    bad_list = client.post("/beacon/inbox", json=["not-a-dict"])
    assert bad_list.status_code == 400
    assert bad_list.json()["detail"]["results"][0]["reason"] == "invalid_envelope"

    too_big = client.post(
        "/beacon/inbox",
        content=b"x" * (MAX_BODY_SIZE + 1),
        headers={"Content-Type": "text/plain"},
    )
    assert too_big.status_code == 413


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
