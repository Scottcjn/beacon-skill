import sys
from pathlib import Path

import pytest


ATLAS_DIR = Path(__file__).resolve().parents[1] / "atlas"
if str(ATLAS_DIR) not in sys.path:
    sys.path.insert(0, str(ATLAS_DIR))

import beacon_chat


@pytest.fixture()
def client(monkeypatch, tmp_path):
    db_path = tmp_path / "beacon_join_retired.db"
    monkeypatch.setattr(beacon_chat, "DB_PATH", str(db_path), raising=False)
    beacon_chat.ATLAS_RATE_LIMITER._entries.clear()
    beacon_chat.ATLAS_RATE_LIMITER._last_cleanup = 0
    beacon_chat.init_db()
    beacon_chat.app.config["TESTING"] = True
    yield beacon_chat.app.test_client()


def test_legacy_beacon_join_route_is_retired(client):
    response = client.post(
        "/beacon/join",
        json={"agent_id": "bcn_attacker001", "pubkey_hex": "11" * 32},
    )

    assert response.status_code == 404


def test_api_agents_returns_native_agents_after_route_removal(client):
    response = client.get("/api/agents")

    assert response.status_code == 200
    agents = response.get_json()
    native_agent_ids = {agent["agent_id"] for agent in agents if not agent["relay"]}
    assert "bcn_sophia_elya" in native_agent_ids
