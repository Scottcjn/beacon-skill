# SPDX-License-Identifier: MIT
"""Regression tests for ActionExecutor and webhook_send v2 Ed25519 envelope signing."""

import time
from unittest.mock import patch

import pytest

from beacon_skill import storage
from beacon_skill.codec import decode_envelopes, verify_envelope
from beacon_skill.executor import ActionExecutor
from beacon_skill.guard import clear_nonce_cache
from beacon_skill.identity import AgentIdentity
from beacon_skill.outbox import OutboxManager
from beacon_skill.transports.webhook import WebhookServer, webhook_send


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


def test_executor_drain_webhook_delivers_verifiable_signed_v2_envelope(isolated_beacon_dir):
    ident = AgentIdentity.generate()
    server = WebhookServer(port=0, host="127.0.0.1")
    server.start(blocking=False)
    time.sleep(0.1)
    try:
        port = server._server.server_port
        outbox = OutboxManager(data_dir=isolated_beacon_dir / "outbox_data")
        ex = ActionExecutor(outbox=outbox, identity=ident)
        outbox.queue(
            action_type="emit",
            target_agent_id="bcn_peer",
            envelope={"kind": "hello", "to": "bcn_peer", "text": "signed webhook"},
            transport_hint=f"webhook:http://127.0.0.1:{port}/beacon/inbox",
        )
        results = ex.drain(max_actions=1)
        assert results[0]["status"] == "sent"
        assert results[0]["method"] == "webhook"
    finally:
        server.stop()


def test_executor_drain_udp_sends_verifiable_beacon_v2_datagram(isolated_beacon_dir):
    ident = AgentIdentity.generate()
    outbox = OutboxManager(data_dir=isolated_beacon_dir / "outbox_udp")
    ex = ActionExecutor(
        outbox=outbox,
        identity=ident,
        cfg={"udp": {"enabled": True, "host": "127.0.0.1", "port": 38400, "broadcast": False}},
    )
    outbox.queue(
        action_type="emit",
        target_agent_id="bcn_peer",
        envelope={"kind": "hello", "to": "bcn_peer", "text": "signed udp"},
        transport_hint="udp:127.0.0.1:38400",
    )

    sent_payloads = []

    def _fake_udp_send(host, port, payload, **kwargs):
        sent_payloads.append((host, port, payload))

    with patch("beacon_skill.transports.udp.udp_send", side_effect=_fake_udp_send):
        results = ex.drain(max_actions=1)

    assert results[0]["status"] == "sent"
    assert len(sent_payloads) == 1
    text = sent_payloads[0][2].decode("utf-8")
    assert text.startswith("[BEACON v2]\n")
    envs = decode_envelopes(text)
    assert len(envs) == 1
    assert envs[0]["agent_id"] == ident.agent_id
    assert verify_envelope(envs[0]) is True


def test_webhook_send_signs_when_identity_provided():
    ident = AgentIdentity.generate()
    captured = {}

    class _FakeResp:
        status_code = 200

        def json(self):
            return {"ok": True}

    def _fake_post(url, json=None, headers=None, timeout=15):
        captured["json"] = json
        return _FakeResp()

    with patch("beacon_skill.transports.webhook.requests.post", side_effect=_fake_post):
        webhook_send(
            "https://example.invalid/beacon/inbox",
            {"kind": "hello", "text": "direct send"},
            identity=ident,
        )

    sent = captured["json"]
    assert sent["v"] == 2
    assert sent["agent_id"] == ident.agent_id
    assert isinstance(sent.get("nonce"), str) and sent["nonce"]
    assert isinstance(sent.get("ts"), int)
    assert verify_envelope(sent) is True
