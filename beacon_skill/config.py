import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional, TextIO

# Emit the insecure-TLS warning at most once per process so long-running
# `beacon loop` daemons do not spam stderr on every config reload.
_INSECURE_TLS_WARNED = False


def rustchain_verify_ssl_is_off(cfg: Dict[str, Any]) -> bool:
    """Return True when the config explicitly turns RustChain TLS verification off.

    Only an explicit opt-out counts: a missing key, ``None`` or an empty string
    mean "no decision", and the secure default applies. Accepted opt-out
    spellings are ``false``, ``0`` and ``"false"``/``"0"``/``"no"``.
    """
    if not isinstance(cfg, dict):
        return False
    rc = cfg.get("rustchain")
    if not isinstance(rc, dict):
        return False
    raw = rc.get("verify_ssl")
    if isinstance(raw, str):
        return raw.strip().lower() in ("0", "false", "no")
    if isinstance(raw, (bool, int)):
        return not raw
    return False


def warn_if_insecure_rustchain_tls(cfg: Dict[str, Any], stream: Optional[TextIO] = None) -> bool:
    """Warn (once per process) when a config still carries ``verify_ssl: false``.

    ``beacon init`` wrote ``"verify_ssl": false`` into every new config before
    2.17.1, so users who ran it stay insecure even after the code default
    flipped to verified TLS. This does not rewrite the user's file; it tells
    them what to change. The warning is suppressed when the operator has set
    ``BEACON_INSECURE_SKIP_TLS_VERIFY`` (the deliberate lab escape hatch).

    Returns True if a warning was emitted.
    """
    global _INSECURE_TLS_WARNED
    if _INSECURE_TLS_WARNED:
        return False
    if not rustchain_verify_ssl_is_off(cfg):
        return False
    if os.environ.get("BEACON_INSECURE_SKIP_TLS_VERIFY", "").strip().lower() in ("1", "true", "yes"):
        return False
    _INSECURE_TLS_WARNED = True
    out = stream if stream is not None else sys.stderr
    print(
        "WARNING: rustchain.verify_ssl is false in "
        f"{_config_path()} -- TLS certificate verification is OFF for RustChain "
        "calls (balance, pay, anchor). Older `beacon init` wrote this by default. "
        "Set \"verify_ssl\": true (or delete the key) unless you are talking to a "
        "self-signed lab node.",
        file=out,
    )
    return True


def _config_path() -> Path:
    custom = os.environ.get("BEACON_CONFIG_PATH")
    if custom:
        return Path(custom)
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
        cfg = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(cfg, dict):
        return {}
    warn_if_insecure_rustchain_tls(cfg)
    return cfg


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


def is_debug_mode() -> bool:
    """Check if verbose logging is enabled via BEACON_DEBUG env var.

    Returns True when BEACON_DEBUG is set to \"1\", \"true\", \"yes\", or \"on\"
    (case-insensitive). When enabled, configures the root \"beacon\" logger
    to DEBUG level.
    """
    val = os.environ.get("BEACON_DEBUG", "0").lower()
    if val not in ("1", "true", "yes", "on"):
        return False
    logger = logging.getLogger("beacon")
    logger.setLevel(logging.DEBUG)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
        ))
        logger.addHandler(handler)
    return True
