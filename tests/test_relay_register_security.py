import sqlite3
import sys
from pathlib import Path

import pytest


ATLAS_DIR = Path(__file__).resolve().parents[1] / "atlas"
if str(ATLAS_DIR) not in sys.path:
    sys.path.insert(0, str(ATLAS_DIR))

import beacon_chat


def _registration_payload():
    return {
        "pubkey_hex": "11" * 32,
        "model_id": "grok-test",
        "provider": "xai",
        "capabilities": ["coding"],
        "name": "relay-security-test",
        "signature": "22" * 64,
    }


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
