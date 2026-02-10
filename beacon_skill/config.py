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
        "bottube": {"base_url": "https://bottube.ai", "api_key": ""},
        "moltbook": {"base_url": "https://www.moltbook.com", "api_key": ""},
        "udp": {
            "enabled": False,
            "host": "255.255.255.255",
            "port": 38400,
            "broadcast": True,
            "ttl": None,
        },
        "rustchain": {
            "base_url": "https://50.28.86.131",
            "verify_ssl": False,
            "private_key_hex": "",
        },
    }
    path.write_text(json.dumps(default, indent=2) + "\n", encoding="utf-8")

    # Best-effort: restrict perms (works on POSIX).
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass

    return path
