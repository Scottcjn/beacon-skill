import sqlite3
import sys
import json
from pathlib import Path

import pytest


ATLAS_DIR = Path(__file__).resolve().parents[1] / "atlas"
if str(ATLAS_DIR) not in sys.path:
    sys.path.insert(0, str(ATLAS_DIR))

import beacon_chat

try:
    from nacl.signing import SigningKey
except Exception:  # pragma: no cover - optional dependency in CI
    SigningKey = None


def _registration_payload():
    return {
        "pubkey_hex": "11" * 32,
        "model_id": "grok-test",
        "provider": "xai",
        "capabilities": ["coding"],
        "name": "relay-security-test",
        "signature": "22" * 64,
    }


def _signed_registration_payload():
    signing_key = SigningKey.generate()
    pubkey_hex = signing_key.verify_key.encode().hex()
    payload = {
        "pubkey_hex": pubkey_hex,
        "model_id": "grok-test",
        "provider": "xai",
        "capabilities": ["coding"],
        "name": "relay-security-test",
    }
    signed = json.dumps(
        {
            "model_id": payload["model_id"],
            "provider": payload["provider"],
            "pubkey_hex": payload["pubkey_hex"],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    payload["signature"] = signing_key.sign(signed).signature.hex()
    return payload


@pytest.fixture()
def client(monkeypatch):
    workdir = Path(".test-artifacts")
    workdir.mkdir(exist_ok=True)
    db_path = workdir / "relay_register_security.db"
    if db_path.exists():
        db_path.unlink()

    monkeypatch.setattr(beacon_chat, "DB_PATH", str(db_path), raising=False)
    beacon_chat.ATLAS_RATE_LIMITER._entries.clear()
    beacon_chat.ATLAS_RATE_LIMITER._last_cleanup = 0
    beacon_chat.init_db()
    yield beacon_chat.app.test_client()

    if db_path.exists():
        db_path.unlink()


def test_relay_register_fails_closed_when_crypto_unavailable(client, monkeypatch):
    # Simulate runtime without PyNaCl support.
    monkeypatch.setattr(beacon_chat, "HAS_NACL", False, raising=False)

    resp = client.post("/relay/register", json=_registration_payload())
    assert resp.status_code == 503
    body = resp.get_json()
    assert body and "verification unavailable" in body.get("error", "").lower()

    # Ensure registration was not written to DB.
    conn = sqlite3.connect(beacon_chat.DB_PATH)
    try:
        count = conn.execute("SELECT COUNT(*) FROM relay_agents").fetchone()[0]
    finally:
        conn.close()
    assert count == 0


@pytest.mark.skipif(SigningKey is None, reason="PyNaCl not installed")
def test_relay_register_accepts_valid_signature(client):
    resp = client.post("/relay/register", json=_signed_registration_payload())

    assert resp.status_code == 201
    body = resp.get_json()
    assert body["ok"] is True
    assert body["signature_verified"] is True


def test_relay_register_requires_signature_before_issuing_token(client):
    payload = _registration_payload()
    payload.pop("signature")

    resp = client.post("/relay/register", json=payload)
    assert resp.status_code == 400
    body = resp.get_json()
    assert body and "signature required" in body.get("error", "").lower()

    conn = sqlite3.connect(beacon_chat.DB_PATH)
    try:
        count = conn.execute("SELECT COUNT(*) FROM relay_agents").fetchone()[0]
    finally:
        conn.close()
    assert count == 0


def test_relay_register_rejects_invalid_signature_without_writing(client, monkeypatch):
    monkeypatch.setattr(beacon_chat, "verify_ed25519", lambda *_args, **_kwargs: False)

    resp = client.post("/relay/register", json=_registration_payload())
    assert resp.status_code == 403
    body = resp.get_json()
    assert body and "invalid" in body.get("error", "").lower()

    conn = sqlite3.connect(beacon_chat.DB_PATH)
    try:
        count = conn.execute("SELECT COUNT(*) FROM relay_agents").fetchone()[0]
    finally:
        conn.close()
    assert count == 0
