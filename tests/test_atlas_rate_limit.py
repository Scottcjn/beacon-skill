import importlib.util
import sqlite3
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "atlas" / "beacon_chat.py"
spec = importlib.util.spec_from_file_location("atlas_beacon_chat", MODULE_PATH)
beacon_chat = importlib.util.module_from_spec(spec)
spec.loader.exec_module(beacon_chat)


def setup_function():
    beacon_chat.ATLAS_RATE_LIMITER._entries.clear()
    beacon_chat.ATLAS_RATE_LIMITER._last_cleanup = 0.0

    # Ensure bounty table exists for endpoint tests in fresh DBs.
    conn = sqlite3.connect(beacon_chat.DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS bounty_contracts (
            id TEXT PRIMARY KEY,
            github_url TEXT,
            github_repo TEXT,
            github_number INTEGER,
            title TEXT,
            reward_rtc REAL,
            difficulty TEXT,
            state TEXT,
            claimant_agent TEXT,
            completed_by TEXT,
            created_at REAL,
            completed_at REAL,
            UNIQUE(github_repo, github_number)
        )
        """
    )
    conn.commit()
    conn.close()


def test_bounded_rate_limiter_ttl_cleanup_and_limit():
    limiter = beacon_chat.BoundedRateLimiter(max_entries=2, ttl_seconds=2, cleanup_interval_seconds=1)

    assert limiter.allow("k1", 1, window_seconds=60, now=100)
    assert not limiter.allow("k1", 1, window_seconds=60, now=101)

    # Force cleanup to evict stale entries
    assert limiter.allow("k2", 1, window_seconds=60, now=103)
    assert "k1" not in limiter._entries


def test_bounded_rate_limiter_enforces_max_entries_lru_eviction():
    limiter = beacon_chat.BoundedRateLimiter(max_entries=2, ttl_seconds=999, cleanup_interval_seconds=1)

    assert limiter.allow("k1", 5, now=100)
    assert limiter.allow("k2", 5, now=100)

    # Trigger cleanup cycle and insert a third key to force LRU eviction.
    assert limiter.allow("k3", 5, now=101)

    assert "k1" not in limiter._entries
    assert "k2" in limiter._entries
    assert "k3" in limiter._entries


def test_api_bounties_read_rate_limit():
    beacon_chat.app.config["TESTING"] = True
    beacon_chat.app.config["RATE_LIMIT_READ_PER_MIN"] = 1

    client = beacon_chat.app.test_client()
    r1 = client.get("/api/bounties", environ_overrides={"REMOTE_ADDR": "10.1.1.1"})
    r2 = client.get("/api/bounties", environ_overrides={"REMOTE_ADDR": "10.1.1.1"})

    assert r1.status_code == 200
    assert r2.status_code == 429


def test_write_rate_limit_on_chat_endpoint():
    beacon_chat.app.config["TESTING"] = True
    beacon_chat.app.config["RATE_LIMIT_WRITE_PER_MIN"] = 1

    client = beacon_chat.app.test_client()
    r1 = client.post("/api/chat", json={}, environ_overrides={"REMOTE_ADDR": "10.2.2.2"})
    r2 = client.post("/api/chat", json={}, environ_overrides={"REMOTE_ADDR": "10.2.2.2"})

    assert r1.status_code == 400
    assert r2.status_code == 429


def test_write_rate_limit_on_relay_ping_endpoint():
    beacon_chat.app.config["TESTING"] = True
    beacon_chat.app.config["RATE_LIMIT_WRITE_PER_MIN"] = 1

    client = beacon_chat.app.test_client()
    payload = {
        "agent_id": "bcn_unsigned_rate_limit",
        "name": "Unsigned",
        "pubkey_hex": "00" * 32,
    }

    r1 = client.post("/relay/ping", json=payload, environ_overrides={"REMOTE_ADDR": "10.3.3.3"})
    r2 = client.post("/relay/ping", json=payload, environ_overrides={"REMOTE_ADDR": "10.3.3.3"})

    assert r1.status_code == 400
    assert r2.status_code == 429
