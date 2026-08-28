import sys
from pathlib import Path

import pytest


ATLAS_DIR = Path(__file__).resolve().parents[1] / "atlas"
if str(ATLAS_DIR) not in sys.path:
    sys.path.insert(0, str(ATLAS_DIR))

import beacon_chat


@pytest.fixture()
def client(monkeypatch, tmp_path):
    db_path = tmp_path / "beacon_join_routes.db"
    monkeypatch.setattr(beacon_chat, "DB_PATH", str(db_path), raising=False)
    beacon_chat.ATLAS_RATE_LIMITER._entries.clear()
    beacon_chat.ATLAS_RATE_LIMITER._last_cleanup = 0
    monkeypatch.setattr(beacon_chat, "HAS_NACL", True, raising=False)
    monkeypatch.setattr(beacon_chat, "verify_ed25519", lambda *_args, **_kwargs: True)
    beacon_chat.init_db()
    beacon_chat.app.config["TESTING"] = True
    yield beacon_chat.app.test_client()


def _join_payload(**overrides):
    payload = {
        "pubkey_hex": "11" * 32,
        "model_id": "codex-route-probe",
        "provider": "beacon",
        "capabilities": ["devops", "infra"],
        "name": "Route Runner",
        "signature": "22" * 64,
    }
    payload.update(overrides)
    return payload


def test_beacon_join_registers_new_agent(client):
    response = client.post("/beacon/join", json=_join_payload())

    assert response.status_code == 201
    body = response.get_json()
    assert body["ok"] is True
    assert body["joined_via"] == "/beacon/join"
    assert body["agent_id"] == beacon_chat.agent_id_from_pubkey_hex("11" * 32)
    assert body["signature_verified"] is True
    assert body["upserted"] is False


def test_beacon_join_duplicate_agent_upserts_not_errors(client):
    first = client.post("/beacon/join", json=_join_payload(name="Route Runner"))
    second = client.post(
        "/beacon/join",
        json=_join_payload(name="Route Runner Updated", capabilities=["routing"]),
    )

    assert first.status_code == 201
    assert second.status_code == 201
    body = second.get_json()
    assert body["agent_id"] == beacon_chat.agent_id_from_pubkey_hex("11" * 32)
    assert body["upserted"] is True

    with beacon_chat.app.app_context():
        db = beacon_chat.get_db()
        rows = db.execute("SELECT name, capabilities FROM relay_agents").fetchall()
    assert len(rows) == 1
    assert rows[0]["name"] == "Route Runner Updated"
    assert rows[0]["capabilities"] == '["routing"]'


def test_beacon_join_invalid_pubkey_returns_400_without_write(client):
    response = client.post("/beacon/join", json=_join_payload(pubkey_hex="not-hex"))

    assert response.status_code == 400
    assert "pubkey_hex" in response.get_json()["error"]
    with beacon_chat.app.app_context():
        db = beacon_chat.get_db()
        count = db.execute("SELECT COUNT(*) FROM relay_agents").fetchone()[0]
    assert count == 0


def test_beacon_join_rejects_mismatched_claimed_agent_id(client):
    response = client.post(
        "/beacon/join",
        json=_join_payload(agent_id="bcn_wrong000000"),
    )

    assert response.status_code == 400
    body = response.get_json()
    assert "agent_id mismatch" in body["error"]
    assert body["expected"] == beacon_chat.agent_id_from_pubkey_hex("11" * 32)


def test_api_agents_returns_native_agents_after_route_removal(client):
    response = client.get("/api/agents")

    assert response.status_code == 200
    agents = response.get_json()
    native_agent_ids = {agent["agent_id"] for agent in agents if not agent["relay"]}
    assert "bcn_sophia_elya" in native_agent_ids


def test_beacon_atlas_alias_returns_native_and_joined_agents(client):
    joined = client.post("/beacon/join", json=_join_payload())
    response = client.get("/beacon/atlas")

    assert joined.status_code == 201
    assert response.status_code == 200
    agents = response.get_json()
    agent_ids = {agent["agent_id"] for agent in agents}
    assert "bcn_sophia_elya" in agent_ids
    assert beacon_chat.agent_id_from_pubkey_hex("11" * 32) in agent_ids


def test_nginx_config_preserves_beacon_prefix():
    config_path = Path(__file__).resolve().parents[1] / "atlas" / "nginx_rustchain_org.conf"
    config = config_path.read_text(encoding="utf-8")

    assert "proxy_pass http://127.0.0.1:8071;" in config
    assert "proxy_pass http://127.0.0.1:8071/;" not in config
