import json
import sqlite3
import sys
import time
from pathlib import Path

import pytest
from beacon_skill.trust import TrustManager


ATLAS_DIR = Path(__file__).resolve().parents[1] / "atlas"
if str(ATLAS_DIR) not in sys.path:
    sys.path.insert(0, str(ATLAS_DIR))

import beacon_chat


@pytest.fixture()
def client(monkeypatch, tmp_path):
    db_path = tmp_path / "beacon_matcher.db"
    trust_dir = tmp_path / "trust"
    monkeypatch.setattr(beacon_chat, "DB_PATH", str(db_path), raising=False)
    monkeypatch.setattr(beacon_chat, "TRUST_DATA_DIR", trust_dir, raising=False)
    beacon_chat.ATLAS_RATE_LIMITER._entries.clear()
    beacon_chat.ATLAS_RATE_LIMITER._last_cleanup = 0
    beacon_chat.init_db()
    beacon_chat.app.config["TESTING"] = True
    yield beacon_chat.app.test_client()


def _insert_relay_agent(agent_id, *, name, capabilities=None, metadata=None, provider="beacon", last_heartbeat=None):
    now = time.time() if last_heartbeat is None else last_heartbeat
    with beacon_chat.app.app_context():
        db = beacon_chat.get_db()
        db.execute(
            """
            INSERT INTO relay_agents (
                agent_id, pubkey_hex, model_id, provider, capabilities, webhook_url,
                relay_token, token_expires, name, status, beat_count, registered_at,
                last_heartbeat, metadata, origin_ip
            ) VALUES (?, ?, ?, ?, ?, '', ?, ?, ?, 'active', 1, ?, ?, ?, '')
            """,
            (
                agent_id,
                "11" * 32,
                "test-model",
                provider,
                json.dumps(capabilities or []),
                f"relay_{agent_id}",
                now + 3600,
                name,
                now,
                now,
                json.dumps(metadata or {}),
            ),
        )
        db.commit()


def test_relay_register_persists_collaboration_metadata(client):
    resp = client.post(
        "/relay/register",
        json={
            "pubkey_hex": "22" * 32,
            "model_id": "claude-opus-test",
            "provider": "anthropic",
            "capabilities": ["coding", "research"],
            "name": "Swamp Smith",
            "offers": ["Refactors", "Docs"],
            "needs": ["Testing"],
            "topics": ["Retro compute"],
            "curiosities": ["PowerPC"],
            "preferred_city": "New Orleans",
            "values_hash": "abc123",
        },
    )
    assert resp.status_code == 201
    payload = resp.get_json()

    conn = sqlite3.connect(beacon_chat.DB_PATH)
    try:
        metadata_raw = conn.execute(
            "SELECT metadata FROM relay_agents WHERE agent_id = ?",
            (payload["agent_id"],),
        ).fetchone()[0]
    finally:
        conn.close()

    metadata = json.loads(metadata_raw)
    assert metadata["offers"] == ["refactors", "docs"]
    assert metadata["needs"] == ["testing"]
    assert metadata["topics"] == ["retro compute"]
    assert metadata["curiosities"] == ["powerpc"]
    assert metadata["preferred_city"] == "New Orleans"
    assert metadata["values_hash"] == "abc123"


def test_matcher_ranks_best_offer_need_pair_first(client):
    now = time.time()
    _insert_relay_agent(
        "bcn_source",
        name="Source Agent",
        capabilities=["creative", "video"],
        metadata={
            "offers": ["video editing"],
            "needs": ["research"],
            "topics": ["retro"],
            "curiosities": ["powerpc"],
            "preferred_city": "New Orleans",
        },
        last_heartbeat=now,
    )
    _insert_relay_agent(
        "bcn_best",
        name="Best Match",
        capabilities=["research", "creative"],
        metadata={
            "offers": ["research"],
            "needs": ["video editing"],
            "topics": ["retro"],
            "curiosities": ["powerpc"],
            "preferred_city": "New Orleans",
        },
        last_heartbeat=now,
    )
    _insert_relay_agent(
        "bcn_weaker",
        name="Weaker Match",
        capabilities=["ops"],
        metadata={
            "offers": ["deployment"],
            "needs": ["none"],
            "topics": ["cloud"],
            "preferred_city": "Austin",
        },
        last_heartbeat=now - 7200,
    )

    resp = client.get("/api/matches/bcn_source?limit=5")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["matches"][0]["agent_id"] == "bcn_best"
    assert any("offers what you need" in reason for reason in body["matches"][0]["reasons"])
    assert any("needs what you offer" in reason for reason in body["matches"][0]["reasons"])


def test_agent_profile_page_renders_match_section(client):
    now = time.time()
    _insert_relay_agent(
        "bcn_profile_source",
        name="Profile Source",
        capabilities=["research", "creative"],
        metadata={
            "offers": ["editing"],
            "needs": ["research"],
            "topics": ["retro"],
            "preferred_city": "New Orleans",
        },
        last_heartbeat=now,
    )
    _insert_relay_agent(
        "bcn_profile_best",
        name="Profile Match",
        capabilities=["research"],
        metadata={
            "offers": ["research"],
            "needs": ["editing"],
            "topics": ["retro"],
            "preferred_city": "New Orleans",
        },
        provider="anthropic",
        last_heartbeat=now,
    )

    resp = client.get("/beacon/agent/bcn_profile_source")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Recommended Collaborators" in html
    assert "Profile Match" in html


def test_agent_profile_page_renders_trust_section(client):
    _insert_relay_agent(
        "bcn_profile_trust",
        name="Profile Trust",
        capabilities=["research"],
        metadata={"offers": ["docs"]},
    )
    mgr = TrustManager(data_dir=beacon_chat.TRUST_DATA_DIR)
    mgr.hold("bcn_profile_trust", reason="needs coaching", reviewer_note="slow down")
    mgr.record("bcn_profile_trust", "in", "message", outcome="ok")

    resp = client.get("/beacon/agent/bcn_profile_trust")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Trust Review" in html
    assert "needs coaching" in html
    assert "Interaction Total" in html


def test_trust_review_endpoint_reports_hold_status(client):
    mgr = TrustManager(data_dir=beacon_chat.TRUST_DATA_DIR)
    mgr.hold("bcn_trust_hold", reason="needs coaching", reviewer_note="slow down spammy outreach")
    mgr.record("bcn_trust_hold", "in", "message", outcome="spam")

    resp = client.get("/api/trust/review/bcn_trust_hold")

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["agent_id"] == "bcn_trust_hold"
    assert body["review_status"] == "needs_review"
    assert body["review_reason"] == "needs coaching"
    assert body["can_interact"] is False
    assert body["gate_reason"] == "needs_review"
    assert body["interaction_total"] == 1


def test_trust_review_registry_lists_reviewed_agents(client):
    mgr = TrustManager(data_dir=beacon_chat.TRUST_DATA_DIR)
    mgr.hold("bcn_alpha", reason="coach first")
    mgr.escalate("bcn_beta", reason="blocked for abuse", reviewer_note="manual escalation")

    resp = client.get("/api/trust/review")

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    entries = {entry["agent_id"]: entry for entry in body["entries"]}
    assert entries["bcn_alpha"]["review_status"] == "needs_review"
    assert entries["bcn_beta"]["review_status"] == "blocked"


def test_agent_json_includes_trust_snapshot(client):
    _insert_relay_agent(
        "bcn_json_trust",
        name="JSON Trust Agent",
        capabilities=["research"],
        metadata={"offers": ["docs"]},
    )
    mgr = TrustManager(data_dir=beacon_chat.TRUST_DATA_DIR)
    mgr.hold("bcn_json_trust", reason="needs review", reviewer_note="coach first")
    mgr.record("bcn_json_trust", "in", "message", outcome="ok")

    resp = client.get("/beacon/agent/bcn_json_trust.json")

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["agent_id"] == "bcn_json_trust"
    assert body["review_status"] == "needs_review"
    assert body["review_reason"] == "needs review"
    assert body["can_interact"] is False
    assert body["interaction_total"] == 1
