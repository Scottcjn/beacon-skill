import json
import os
from pathlib import Path
from typing import Any, Dict


def _config_path() -> Path:
    return Path.home() / ".beacon" / "config.json"


def ensure_config_dir() -> Path:
    cfg_dir = Path.home() / ".beacon"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    return cfg_dir


def load_config() -> Dict[str, Any]:
    path = _config_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_default_config(overwrite: bool = False) -> Path:
    cfg_dir = ensure_config_dir()
    path = cfg_dir / "config.json"
    if path.exists() and not overwrite:
        return path

    default = {
        "beacon": {"agent_name": ""},
        "identity": {
            "auto_sign": True,
            "password_protected": True,
        },
        "presence": {
            "pulse_interval_s": 60,
            "pulse_ttl_s": 300,
            "offers": [],
            "needs": [],
            "status": "online",
        },
        "autonomy": {
            "rules_enabled": True,
            "trust_enabled": True,
            "feed_enabled": True,
            "task_tracking": True,
            "presence_enabled": True,
            "memory_enabled": True,
            "min_score": 0.0,
            "journal_enabled": True,
            "curiosity_enabled": True,
            "values_enabled": True,
            "auto_journal": True,
            "boundary_enforcement": True,
            "goals_enabled": True,
            "insights_enabled": True,
            "matchmaking_enabled": True,
            "proactive_interval_s": 300,
            "executor_enabled": True,
            "auto_contact": False,
            "auto_reply": False,
            "max_actions_per_cycle": 3,
            "max_retry_attempts": 3,
            "conversation_stale_days": 7,
            "anchor_enabled": True,
            "auto_anchor": False,
            "heartbeat_enabled": True,
            "heartbeat_interval_s": 3600,
            "heartbeat_anchor_every": 0,
            "heartbeat_silence_threshold_s": 7200,
            "mayday_auto_check": False,
            "mayday_health_threshold": 0.2,
            "accord_enabled": True,
            "accord_auto_pushback": True,
            "thought_proof_enabled": True,
            "thought_auto_anchor": False,
            "relay_enabled": True,
            "relay_prune_interval_s": 3600,
            "market_enabled": True,
            "hybrid_enabled": True,
        },
        "update": {
            "check_enabled": True,
            "check_interval_s": 21600,
            "auto_upgrade": False,
            "notify_in_loop": True,
        },
        "bottube": {"base_url": "https://bottube.ai", "api_key": ""},
        "moltbook": {"base_url": "https://www.moltbook.com", "api_key": ""},
        "agenthive": {"base_url": "https://api.agenthive.net", "api_key": ""},
        "discord": {
            "enabled": False,
            "webhook_url": "",
            "username": "Beacon Agent",
            "avatar_url": "",
            "timeout_s": 20,
        },
        "udp": {
            "enabled": False,
            "host": "255.255.255.255",
            "port": 38400,
            "broadcast": True,
            "ttl": None,
        },
        "webhook": {
            "enabled": False,
            "port": 8402,
            "host": "0.0.0.0",
        },
        "rustchain": {
            "base_url": "https://rustchain.org",
            "verify_ssl": True,
            "wallet_keystore": "",
        },
    }
    path.write_text(json.dumps(default, indent=2) + "\n", encoding="utf-8")

    # Best-effort: restrict perms (works on POSIX).
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass

    return path
