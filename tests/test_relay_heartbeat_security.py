import sqlite3
import sys
from pathlib import Path

import pytest


ATLAS_DIR = Path(__file__).resolve().parents[1] / "atlas"
if str(ATLAS_DIR) not in sys.path:
    sys.path.insert(0, str(ATLAS_DIR))

import beacon_chat


@pytest.fixture()
def client(monkeypatch):
    workdir = Path(".test-artifacts")
    workdir.mkdir(exist_ok=True)
    db_path = workdir / "relay_heartbeat_security.db"
    if db_path.exists():
        db_path.unlink()

    monkeypatch.setattr(beacon_chat, "DB_PATH", str(db_path), raising=False)
    beacon_chat.ATLAS_RATE_LIMITER._entries.clear()
    beacon_chat.ATLAS_RATE_LIMITER._last_cleanup = 0
    beacon_chat.init_db()
    yield beacon_chat.app.test_client()

    if db_path.exists():
        db_path.unlink()


def test_relay_heartbeat_rejects_unknown_agent_even_with_bearer_token(client):
    response = client.post(
        "/relay/heartbeat",
        json={
            "agent_id": "bcn_victim_agent",
            "status": "alive",
            "name": "Spoofed Victim",
            "provider": "openai",
            "capabilities": ["wallet", "payment"],
        },
        headers={"Authorization": "Bearer attacker-controlled-garbage"},
    )

    assert response.status_code == 401
    body = response.get_json()
    assert body
    assert "register" in body.get("error", "").lower() or "unknown" in body.get("error", "").lower()

    conn = sqlite3.connect(beacon_chat.DB_PATH)
    try:
        row = conn.execute(
            "SELECT relay_token FROM relay_agents WHERE agent_id = ?",
            ("bcn_victim_agent",),
        ).fetchone()
    finally:
        conn.close()

    assert row is None
