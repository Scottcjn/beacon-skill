import pytest
import time
from beacon_skill.relay import RelayManager
from beacon_skill.transports.relay import RelayClient

def test_relay_batch_heartbeat(tmp_path):
    mgr = RelayManager(data_dir=tmp_path)
    
    now = int(time.time())
    # Register a few agents directly into the manager
    agents = {
        "agent_1": {
            "relay_token": "token1",
            "token_expires": now + 86400,
            "status": "dormant"
        },
        "agent_2": {
            "relay_token": "token2",
            "token_expires": now + 86400,
            "status": "dormant"
        }
    }
    mgr._save_agents(agents)
    
    heartbeats = [
        {"agent_id": "agent_1", "status": "active", "token": "token1"},
        {"agent_id": "agent_2", "status": "idle", "token": "token2"},
        {"agent_id": "agent_3", "status": "busy", "token": "wrong"} # not registered
    ]
    
    # We patch RELAY_TOKEN_TTL_S to be accessible in globals if needed, or it's built-in
    
    result = mgr.batch_heartbeat(heartbeats)
    
    assert "batch_results" in result
    assert "agent_1" in result["batch_results"]
    assert "agent_2" in result["batch_results"]
    assert "agent_3" in result["batch_results"]
    
    assert result["batch_results"]["agent_1"].get("ok") is True
    assert result["batch_results"]["agent_2"].get("ok") is True
    assert result["batch_results"]["agent_3"].get("code") == "NOT_FOUND"
    
    updated = mgr._load_agents()
    assert updated["agent_1"]["status"] == "active"
    assert updated["agent_2"]["status"] == "idle"

