import json
import importlib
import time
from pathlib import Path
from unittest.mock import patch

from beacon_skill.atlas_ping import atlas_ping_with_identity, get_stored_token
from beacon_skill.identity import AgentIdentity

atlas_ping_module = importlib.import_module("beacon_skill.atlas_ping")


class _FakeResponse:
    def __init__(self, payload, ok=True, status_code=200, text=""):
        self._payload = payload
        self.ok = ok
        self.status_code = status_code
        self.text = text

    def json(self):
        return self._payload


def test_atlas_ping_persists_relay_token_for_next_process(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    atlas_ping_module._relay_token = None
    identity = AgentIdentity.generate()
    post_calls = []

    def fake_post(url, json=None, timeout=10):
        post_calls.append(json)
        return _FakeResponse(
            {
                "ok": True,
                "agent_id": identity.agent_id,
                "relay_token": "relay_persisted_token",
                "token_expires": int(time.time()) + 3600,
            }
        )

    with patch("beacon_skill.atlas_ping.requests.post", side_effect=fake_post):
        result = atlas_ping_with_identity(identity, name="Persisted Token Agent")

    assert result["ok"] is True
    token_path = tmp_path / ".beacon" / "atlas_relay_token.json"
    token_data = json.loads(token_path.read_text(encoding="utf-8"))
    assert token_data["relay_token"] == "relay_persisted_token"
    assert oct(token_path.stat().st_mode & 0o777) == "0o600"

    atlas_ping_module._relay_token = None
    assert get_stored_token() == "relay_persisted_token"
    assert "signature" in post_calls[0]


def test_atlas_ping_uses_persisted_token_instead_of_resigning(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    token_dir = tmp_path / ".beacon"
    token_dir.mkdir()
    (token_dir / "atlas_relay_token.json").write_text(
        json.dumps(
            {
                "relay_token": "relay_existing_token",
                "token_expires": int(time.time()) + 3600,
            }
        ),
        encoding="utf-8",
    )
    atlas_ping_module._relay_token = None
    identity = AgentIdentity.generate()
    post_calls = []

    def fake_post(url, json=None, timeout=10):
        post_calls.append(json)
        return _FakeResponse({"ok": True, "agent_id": identity.agent_id, "beat_count": 2})

    with patch("beacon_skill.atlas_ping.requests.post", side_effect=fake_post):
        result = atlas_ping_with_identity(identity, name="Persisted Token Agent")

    assert result["ok"] is True
    assert post_calls[0]["relay_token"] == "relay_existing_token"
    assert "signature" not in post_calls[0]
    assert "pubkey_hex" not in post_calls[0]


def test_expired_persisted_token_is_ignored(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    token_dir = tmp_path / ".beacon"
    token_dir.mkdir()
    Path(token_dir / "atlas_relay_token.json").write_text(
        json.dumps(
            {
                "relay_token": "relay_expired_token",
                "token_expires": int(time.time()) - 1,
            }
        ),
        encoding="utf-8",
    )
    atlas_ping_module._relay_token = None
    identity = AgentIdentity.generate()
    post_calls = []

    def fake_post(url, json=None, timeout=10):
        post_calls.append(json)
        return _FakeResponse({"ok": True, "agent_id": identity.agent_id})

    with patch("beacon_skill.atlas_ping.requests.post", side_effect=fake_post):
        result = atlas_ping_with_identity(identity, name="Persisted Token Agent")

    assert result["ok"] is True
    assert "relay_token" not in post_calls[0]
    assert "signature" in post_calls[0]
