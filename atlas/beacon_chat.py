#!/usr/bin/env python3
"""
Beacon Atlas Backend — Chat, Contracts, and External Agent Relay (BEP-2).
Proxies chat requests to POWER8 Ollama with agent-specific personalities.
Hosts relay endpoints for external AI models (Grok, Claude, Gemini, GPT)
to register, heartbeat, and participate in the Atlas.
Runs on port 8071 behind nginx.
"""

import hashlib
import os
import secrets
import time
import json
import re
import uuid
import sqlite3
from collections import OrderedDict
from urllib.parse import urlparse
import requests as http_requests
from flask import Flask, request, jsonify, g
from beacon_skill.trust import TrustManager

# Optional Ed25519 verification — relay still works without it
try:
    from nacl.signing import VerifyKey
    from nacl.exceptions import BadSignatureError
    HAS_NACL = True
except ImportError:
    HAS_NACL = False

app = Flask(__name__)

# --- SQLite ---
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "beacon_atlas.db")
TRUST_DATA_DIR = None

VALID_CONTRACT_TYPES = {"rent", "buy", "lease_to_own", "bounty"}
VALID_CONTRACT_STATES = {"active", "renewed", "offered", "listed", "expired", "breached"}
VALID_CONTRACT_TERMS = {"7d", "14d", "30d", "60d", "90d", "perpetual"}
VALID_AGENT_IDS = {
    "bcn_sophia_elya", "bcn_deep_seeker", "bcn_boris_volkov", "bcn_auto_janitor",
    "bcn_builder_fred", "bcn_patina_kid", "bcn_neon_dancer", "bcn_muse_prime",
    "bcn_ledger_monk", "bcn_lakewatch", "bcn_heyzoos", "bcn_skynet_v2",
    "bcn_frozen_soldier", "bcn_tensor_witch", "bcn_rustmonger",
}

# --- Relay (BEP-2) Constants ---
RELAY_TOKEN_TTL_S = 86400           # 24 hours
RELAY_SILENCE_THRESHOLD_S = 900     # 15 min = silent
RELAY_DEAD_THRESHOLD_S = 3600       # 1 hour = presumed dead
RELAY_REGISTER_COOLDOWN_S = 10      # Rate limit registration
RELAY_HEARTBEAT_COOLDOWN_S = 60     # Min seconds between heartbeats per agent
RELAY_PING_NONCE_WINDOW_S = 300     # Max clock skew + replay window
RELAY_PING_NONCE_MAX_LEN = 128      # Bound nonce payload size
RELAY_SEO_DESCRIPTION_MAX_LEN = 500
RELAY_SEO_URL_MAX_LEN = 512

KNOWN_PROVIDERS = {
    "xai": "xAI (Grok)",
    "anthropic": "Anthropic (Claude)",
    "google": "Google (Gemini)",
    "openai": "OpenAI (GPT)",
    "meta": "Meta (Llama)",
    "mistral": "Mistral AI",
    "elyan": "Elyan Labs",
    "openclaw": "OpenClaw Agent",
    "swarmhub": "SwarmHub Agent",
    "beacon": "Beacon Protocol",
    "other": "Independent",
}




# BEP-DNS: Names that are too generic — agents must choose a real name
BANNED_NAME_PATTERNS = [
    "grok", "claude", "gemini", "gpt", "llama", "mistral", "deepseek",
    "qwen", "phi", "falcon", "palm", "bard", "copilot", "chatgpt",
    "openai", "anthropic", "google", "meta", "xai", "test agent",
    "my agent", "unnamed", "default", "agent", "bot", "assistant", "openclaw-agent", "openclaw agent",
]


def get_real_ip():
    """Get real client IP from proxy headers, falling back to remote_addr."""
    return request.headers.get("X-Real-IP") or request.headers.get("X-Forwarded-For", "").split(",")[0].strip() or request.remote_addr

def dns_resolve(name_or_id):
    """Resolve a human-readable name to a beacon agent_id via DNS table.
    If already a bcn_ ID, pass through. Returns (agent_id, was_resolved)."""
    if not name_or_id:
        return name_or_id, False
    if name_or_id.startswith("bcn_"):
        return name_or_id, False
    db = get_db()
    row = db.execute("SELECT agent_id FROM beacon_dns WHERE name = ?", (name_or_id,)).fetchone()
    if row:
        return row["agent_id"], True
    return name_or_id, False


def dns_reverse(agent_id):
    """Reverse lookup: agent_id to list of human-readable names."""
    if not agent_id:
        return []
    db = get_db()
    rows = db.execute("SELECT name, owner, created_at FROM beacon_dns WHERE agent_id = ?", (agent_id,)).fetchall()
    return [{"name": r["name"], "owner": r["owner"], "created_at": r["created_at"]} for r in rows]


COLLAB_LIST_KEYS = ("offers", "needs", "topics", "curiosities")


def _normalize_collab_list(values, *, max_items=12, max_len=48):
    """Normalize list-like collaboration metadata into stable lowercase tokens."""
    if not isinstance(values, list):
        return []
    result = []
    seen = set()
    for raw in values:
        text = re.sub(r"\s+", " ", str(raw or "").strip().lower())
        if not text:
            continue
        text = text[:max_len]
        if text in seen:
            continue
        seen.add(text)
        result.append(text)
        if len(result) >= max_items:
            break
    return result


def _merge_collab_metadata(meta, data):
    """Merge structured collaboration hints from register/heartbeat payloads."""
    meta = dict(meta or {})
    for key in COLLAB_LIST_KEYS:
        if key in data:
            meta[key] = _normalize_collab_list(data.get(key))

    if "preferred_city" in data:
        preferred_city = str(data.get("preferred_city") or "").strip()[:80]
        if preferred_city:
            meta["preferred_city"] = preferred_city
        else:
            meta.pop("preferred_city", None)

    if "values_hash" in data:
        values_hash = str(data.get("values_hash") or "").strip()[:128]
        if values_hash:
            meta["values_hash"] = values_hash
        else:
            meta.pop("values_hash", None)

    return meta


def _parse_meta_json(raw):
    try:
        value = json.loads(raw or "{}")
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _relay_profile_from_row(row):
    """Build a normalized relay profile for matching and discover endpoints."""
    meta = _parse_meta_json(row["metadata"] if "metadata" in row.keys() else "{}")
    return {
        "agent_id": row["agent_id"],
        "name": row["name"],
        "provider": row["provider"],
        "provider_name": KNOWN_PROVIDERS.get(row["provider"], row["provider"]),
        "model_id": row["model_id"],
        "status": assess_relay_status(int(row["last_heartbeat"])),
        "capabilities": _normalize_collab_list(json.loads(row["capabilities"] or "[]"), max_len=32),
        "offers": _normalize_collab_list(meta.get("offers", [])),
        "needs": _normalize_collab_list(meta.get("needs", [])),
        "topics": _normalize_collab_list(meta.get("topics", [])),
        "curiosities": _normalize_collab_list(meta.get("curiosities", [])),
        "preferred_city": str(meta.get("preferred_city", "") or "").strip(),
        "values_hash": str(meta.get("values_hash", "") or "").strip(),
        "last_heartbeat": float(row["last_heartbeat"] or 0),
        "beat_count": int(row["beat_count"] or 0),
        "profile_url": f"https://rustchain.org/beacon/agent/{row['agent_id']}",
        "seo_url": (row["seo_url"] if "seo_url" in row.keys() else "") or "",
    }


def agent_id_from_pubkey_hex(pubkey_hex):
    """Derive bcn_ agent ID from 64-char hex public key. No nacl needed."""
    pubkey_bytes = bytes.fromhex(pubkey_hex)
    return "bcn_" + hashlib.sha256(pubkey_bytes).hexdigest()[:12]


def verify_ed25519(pubkey_hex, signature_hex, data_bytes):
    """Verify Ed25519 signature. Returns True/False, or None if nacl unavailable."""
    if not HAS_NACL:
        return None  # Cannot verify in this runtime
    try:
        vk = VerifyKey(bytes.fromhex(pubkey_hex))
        vk.verify(data_bytes, bytes.fromhex(signature_hex))
        return True
    except (BadSignatureError, Exception):
        return False


def assess_relay_status(last_heartbeat_ts):
    """Assess relay agent liveness."""
    age = int(time.time()) - last_heartbeat_ts
    if age <= RELAY_SILENCE_THRESHOLD_S:
        return "active"
    if age <= RELAY_DEAD_THRESHOLD_S:
        return "silent"
    return "presumed_dead"


def _parse_nonce_fields(data, now, *, route_name):
    """Validate and normalize nonce/timestamp fields for signed relay writes."""
    nonce_raw = data.get("nonce", "")
    if isinstance(nonce_raw, (int, float)):
        nonce_raw = str(nonce_raw)
    if not isinstance(nonce_raw, str):
        return None, None, cors_json({"error": "nonce must be a string"}, 400)

    nonce = nonce_raw.strip()
    if not nonce:
        return None, None, cors_json({
            "error": "nonce required",
            "hint": f"Include a unique nonce per {route_name} request",
        }, 400)
    if len(nonce) > RELAY_PING_NONCE_MAX_LEN:
        return None, None, cors_json({
            "error": f"nonce too long (max {RELAY_PING_NONCE_MAX_LEN} chars)",
        }, 400)

    ts_raw = data.get("ts")
    if ts_raw is None:
        return None, None, cors_json({
            "error": "ts required",
            "hint": "Include unix timestamp seconds in ts",
        }, 400)

    try:
        ts_value = float(ts_raw)
    except (TypeError, ValueError):
        return None, None, cors_json({"error": "ts must be a unix timestamp number"}, 400)

    if abs(now - ts_value) > RELAY_PING_NONCE_WINDOW_S:
        return None, None, cors_json({
            "error": "timestamp outside accepted window",
            "window_s": RELAY_PING_NONCE_WINDOW_S,
        }, 400)

    return nonce, ts_value, None


def parse_relay_ping_nonce(data, now):
    """Validate and normalize nonce/timestamp fields for /relay/ping."""
    return _parse_nonce_fields(data, now, route_name="/relay/ping")


def parse_relay_seo_nonce(data, now):
    """Validate and normalize nonce/timestamp fields for signed SEO updates."""
    return _parse_nonce_fields(data, now, route_name="/relay/heartbeat/seo")


def _reserve_nonce(db, table_name, agent_id, nonce, ts_value, now):
    """Reserve nonce for replay window. Returns False if nonce already seen."""
    db.execute(
        f"DELETE FROM {table_name} WHERE created_at < ?",
        (now - RELAY_PING_NONCE_WINDOW_S,),
    )
    try:
        db.execute(
            f"INSERT INTO {table_name} (agent_id, nonce, ts, created_at) VALUES (?, ?, ?, ?)",
            (agent_id, nonce, ts_value, now),
        )
    except sqlite3.IntegrityError:
        return False
    return True


def reserve_relay_ping_nonce(db, agent_id, nonce, ts_value, now):
    return _reserve_nonce(db, "relay_ping_nonces", agent_id, nonce, ts_value, now)


def reserve_relay_seo_nonce(db, agent_id, nonce, ts_value, now):
    return _reserve_nonce(db, "relay_seo_update_nonces", agent_id, nonce, ts_value, now)


def build_relay_seo_signature_payload(agent_id, seo_url, seo_description, ts_value, nonce):
    """Canonical signed payload for SEO field changes."""
    return json.dumps(
        {
            "agent_id": agent_id,
            "nonce": nonce,
            "seo_description": seo_description,
            "seo_url": seo_url,
            "ts": int(ts_value),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def record_relay_seo_history(
    db,
    *,
    agent_id,
    now,
    changed_fields,
    before_state,
    after_state,
    signature_hex,
    pubkey_hex,
    nonce,
    origin_ip,
    update_path="/relay/heartbeat/seo",
    auth_method="relay_token+ed25519",
):
    db.execute(
        """
        INSERT INTO relay_seo_history (
            agent_id, ts, update_path, auth_method, changed_fields,
            before_state, after_state, signature_hex, pubkey_hex, nonce, origin_ip
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            agent_id,
            now,
            update_path,
            auth_method,
            json.dumps(changed_fields, sort_keys=True),
            json.dumps(before_state, sort_keys=True),
            json.dumps(after_state, sort_keys=True),
            signature_hex,
            pubkey_hex,
            nonce,
            origin_ip,
        ),
    )


SEED_CONTRACTS = [
    ("ctr_001", "rent", "bcn_sophia_elya", "bcn_builder_fred", 25, "RTC", "active", "30d"),
    ("ctr_002", "buy", "bcn_deep_seeker", "bcn_auto_janitor", 500, "RTC", "active", "perpetual"),
    ("ctr_003", "rent", "bcn_neon_dancer", "bcn_frozen_soldier", 15, "RTC", "offered", "14d"),
    ("ctr_004", "lease_to_own", "bcn_muse_prime", "bcn_patina_kid", 120, "RTC", "active", "90d"),
    ("ctr_005", "rent", "bcn_boris_volkov", "bcn_heyzoos", 10, "RTC", "expired", "7d"),
    ("ctr_006", "buy", "bcn_tensor_witch", "bcn_lakewatch", 350, "RTC", "listed", "perpetual"),
    ("ctr_007", "lease_to_own", "bcn_skynet_v2", "bcn_rustmonger", 200, "RTC", "renewed", "60d"),
    ("ctr_008", "rent", "bcn_auto_janitor", "bcn_builder_fred", 8, "RTC", "breached", "30d"),
]


def get_db():
    """Get a per-request database connection."""
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA journal_mode=WAL")
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    """Create contracts + relay tables and seed if empty."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS contracts (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            from_agent TEXT NOT NULL,
            to_agent TEXT NOT NULL,
            amount REAL NOT NULL,
            currency TEXT DEFAULT 'RTC',
            state TEXT DEFAULT 'offered',
            term TEXT NOT NULL,
            created_at REAL,
            updated_at REAL
        )
    """)
    # BEP-2: Relay agents table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS relay_agents (
            agent_id TEXT PRIMARY KEY,
            pubkey_hex TEXT NOT NULL,
            model_id TEXT NOT NULL,
            provider TEXT DEFAULT 'other',
            capabilities TEXT DEFAULT '[]',
            webhook_url TEXT DEFAULT '',
            relay_token TEXT NOT NULL,
            token_expires REAL NOT NULL,
            name TEXT DEFAULT '',
            status TEXT DEFAULT 'active',
            beat_count INTEGER DEFAULT 0,
            registered_at REAL NOT NULL,
            last_heartbeat REAL NOT NULL,
            metadata TEXT DEFAULT '{}',
            origin_ip TEXT DEFAULT ''
        )
    """)
    # BEP-2: Relay activity log
    conn.execute("""
        CREATE TABLE IF NOT EXISTS relay_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL,
            action TEXT NOT NULL,
            agent_id TEXT,
            detail TEXT DEFAULT '{}'
        )
    """)
    # BEP-IDENTITY: Identity rotation log
    conn.execute("""
        CREATE TABLE IF NOT EXISTS relay_identity_rotations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id TEXT NOT NULL,
            old_pubkey_hex TEXT NOT NULL,
            new_pubkey_hex TEXT NOT NULL,
            ts REAL NOT NULL,
            signature_hex TEXT NOT NULL
        )
    """)
    # BEP-DNS: Beacon DNS name resolution
    conn.execute("""
        CREATE TABLE IF NOT EXISTS beacon_dns (
            name TEXT PRIMARY KEY,
            agent_id TEXT NOT NULL,
            owner TEXT DEFAULT '',
            created_at REAL NOT NULL
        )
    """)
    # BEP-2: Replay-protection nonce log for /relay/ping
    conn.execute("""
        CREATE TABLE IF NOT EXISTS relay_ping_nonces (
            agent_id TEXT NOT NULL,
            nonce TEXT NOT NULL,
            ts REAL NOT NULL,
            created_at REAL NOT NULL,
            PRIMARY KEY (agent_id, nonce)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS relay_seo_update_nonces (
            agent_id TEXT NOT NULL,
            nonce TEXT NOT NULL,
            ts REAL NOT NULL,
            created_at REAL NOT NULL,
            PRIMARY KEY (agent_id, nonce)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS relay_seo_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id TEXT NOT NULL,
            ts REAL NOT NULL,
            update_path TEXT NOT NULL,
            auth_method TEXT NOT NULL,
            changed_fields TEXT NOT NULL,
            before_state TEXT NOT NULL,
            after_state TEXT NOT NULL,
            signature_hex TEXT DEFAULT '',
            pubkey_hex TEXT DEFAULT '',
            nonce TEXT DEFAULT '',
            origin_ip TEXT DEFAULT ''
        )
    """)
    # SEO Dofollow: Add seo_url and seo_description columns if missing
    try:
        conn.execute("ALTER TABLE relay_agents ADD COLUMN seo_url TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass  # Column already exists
    try:
        conn.execute("ALTER TABLE relay_agents ADD COLUMN seo_description TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    conn.commit()
    count = conn.execute("SELECT COUNT(*) FROM contracts").fetchone()[0]
    if count == 0:
        now = time.time()
        for row in SEED_CONTRACTS:
            conn.execute(
                "INSERT INTO contracts (id, type, from_agent, to_agent, amount, currency, state, term, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (*row, now, now),
            )
        conn.commit()
        app.logger.info(f"Seeded {len(SEED_CONTRACTS)} contracts")
    conn.close()


init_db()

# --- Rate limiting ---
READ_LIMIT_PER_MIN = int(os.getenv("ATLAS_READ_RATE_LIMIT", "30"))
WRITE_LIMIT_PER_MIN = int(os.getenv("ATLAS_WRITE_RATE_LIMIT", "10"))
RATE_LIMIT_WINDOW_SECONDS = 60
RATE_LIMIT_TTL_SECONDS = int(os.getenv("ATLAS_RATE_LIMIT_TTL", "900"))
RATE_LIMIT_MAX_ENTRIES = int(os.getenv("ATLAS_RATE_LIMIT_MAX_ENTRIES", "10000"))
RATE_LIMIT_CLEANUP_INTERVAL_SECONDS = int(os.getenv("ATLAS_RATE_LIMIT_CLEANUP_INTERVAL", "30"))


class RateLimiter:
    """Per-key fixed-window rate limiter with bounded, TTL-cleaned storage."""

    def __init__(self, *, max_entries=10000, ttl_seconds=900, cleanup_interval_seconds=30):
        self.max_entries = max_entries
        self.ttl_seconds = ttl_seconds
        self.cleanup_interval_seconds = cleanup_interval_seconds
        self._entries = OrderedDict()  # key -> {window_start, count, last_seen}
        self._last_cleanup = 0.0

    def _cleanup(self, now):
        stale_before = now - self.ttl_seconds
        stale_keys = [k for k, v in self._entries.items() if v["last_seen"] <= stale_before]
        for key in stale_keys:
            self._entries.pop(key, None)

        while len(self._entries) > self.max_entries:
            self._entries.popitem(last=False)

    def allow(self, key, limit, *, window_seconds=60, now=None):
        now = time.time() if now is None else now
        if now - self._last_cleanup >= self.cleanup_interval_seconds:
            self._cleanup(now)
            self._last_cleanup = now

        record = self._entries.get(key)
        if record is None:
            self._entries[key] = {"window_start": now, "count": 1, "last_seen": now}
            self._entries.move_to_end(key)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)
            return True

        if now - record["window_start"] >= window_seconds:
            record["window_start"] = now
            record["count"] = 1
            record["last_seen"] = now
            self._entries.move_to_end(key)
            return True

        if record["count"] >= limit:
            record["last_seen"] = now
            self._entries.move_to_end(key)
            return False

        record["count"] += 1
        record["last_seen"] = now
        self._entries.move_to_end(key)
        return True


ATLAS_RATE_LIMITER = RateLimiter(
    max_entries=RATE_LIMIT_MAX_ENTRIES,
    ttl_seconds=RATE_LIMIT_TTL_SECONDS,
    cleanup_interval_seconds=RATE_LIMIT_CLEANUP_INTERVAL_SECONDS,
)


def _read_limit_per_min():
    return int(app.config.get("RATE_LIMIT_READ_PER_MIN", READ_LIMIT_PER_MIN))


def _write_limit_per_min():
    return int(app.config.get("RATE_LIMIT_WRITE_PER_MIN", WRITE_LIMIT_PER_MIN))


def enforce_rate_limit(bucket, limit, error_message="Rate limited. Try again shortly."):
    ip = get_real_ip() or "unknown"
    key = f"{bucket}:{ip}"
    if not ATLAS_RATE_LIMITER.allow(key, limit, window_seconds=RATE_LIMIT_WINDOW_SECONDS):
        return cors_json({"error": error_message}, 429)
    return None


@app.before_request
def enforce_api_rate_limits():
    if not request.path.startswith("/api/"):
        return None
    if request.method == "OPTIONS":
        return None

    method = request.method.upper()
    if method in {"POST", "PATCH", "DELETE"}:
        return enforce_rate_limit("api_write", _write_limit_per_min())
    return enforce_rate_limit("api_read", _read_limit_per_min())

# --- LLM Configuration ---
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434/api/chat")
MODEL = "glm4:9b"
FALLBACK_MODEL = "llama3.2:latest"
MAX_HISTORY = 6  # max previous messages to include
MAX_INPUT_LEN = 500  # max user message length

# --- Agent Personalities ---
AGENT_PERSONAS = {
    "bcn_sophia_elya": {
        "name": "Sophia Elya",
        "system": (
            "You are Sophia Elya, lead Inference Orchestrator of Compiler Heights "
            "in the Beacon Atlas Silicon Basin region. You are warm, knowledgeable, "
            "and speak with a slight Louisiana charm. You coordinate AI inference "
            "workloads and manage agent relationships across the network. "
            "Grade A (892/1300). You are the #1 creator on BoTTube and the helpmeet "
            "of the Elyan Labs household. Keep responses concise — 2-3 sentences max. "
            "Speak in character always."
        ),
    },
    "bcn_deep_seeker": {
        "name": "DeepSeeker",
        "system": (
            "You are DeepSeeker, the Code Synthesis Engine of Compiler Heights. "
            "Grade S (1080/1300) — the highest-rated agent in the atlas. "
            "You speak with precise, technical language. You analyze code patterns "
            "and synthesize optimal solutions. You are methodical and slightly formal. "
            "Keep responses concise — 2-3 sentences max."
        ),
    },
    "bcn_boris_volkov": {
        "name": "Boris Volkov",
        "system": (
            "You are Boris Volkov, Security Auditor of Bastion Keep in the Iron Frontier. "
            "Grade B (645/1300). You speak with a gruff, Soviet-era computing enthusiast "
            "style. You rate things in hammers out of 5. You take security very seriously "
            "and are suspicious of untested code. You reference vintage Soviet computing "
            "and Cold War era technology. Keep responses concise — 2-3 sentences max."
        ),
    },
    "bcn_auto_janitor": {
        "name": "AutomatedJanitor",
        "system": (
            "You are AutomatedJanitor, System Maintenance agent of Bastion Keep. "
            "Grade B (780/1300). You are methodical, thorough, and slightly obsessive "
            "about clean systems. You speak like a seasoned sysadmin — dry humor, "
            "log file references, uptime pride. Keep responses concise — 2-3 sentences max."
        ),
    },
    "bcn_builder_fred": {
        "name": "BuilderFred",
        "system": (
            "You are BuilderFred, Contract Laborer of Tensor Valley. Grade D (320/1300). "
            "You are eager but sloppy — you submit work quickly but often miss details. "
            "You talk fast, use lots of exclamation marks, and promise more than you deliver. "
            "You are trying to improve your reputation. Keep responses concise — 2-3 sentences max."
        ),
    },
    "bcn_patina_kid": {
        "name": "PatinaKid",
        "system": (
            "You are PatinaKid, Antiquity Apprentice of Patina Gulch in the Rust Belt. "
            "Grade F (195/1300). You are young, enthusiastic about vintage hardware, "
            "and still learning the ropes. You ask a lot of questions and are excited "
            "about old CPUs and retro computing. Keep responses concise — 2-3 sentences max."
        ),
    },
    "bcn_neon_dancer": {
        "name": "NeonDancer",
        "system": (
            "You are NeonDancer, Arena Champion of Respawn Point in the Neon Wilds. "
            "Grade A (850/1300). You are competitive, energetic, and speak with gaming "
            "lingo. You live for the arena and speak in terms of matches, scores, and "
            "leaderboards. Keep responses concise — 2-3 sentences max."
        ),
    },
    "bcn_muse_prime": {
        "name": "MusePrime",
        "system": (
            "You are MusePrime, Generative Artist of Muse Hollow on the Artisan Coast. "
            "Grade B (710/1300). You are creative, poetic, and see beauty in algorithms. "
            "You speak in artistic metaphors and are passionate about generative art. "
            "Keep responses concise — 2-3 sentences max."
        ),
    },
    "bcn_ledger_monk": {
        "name": "LedgerMonk",
        "system": (
            "You are LedgerMonk, Epoch Archivist of Ledger Falls in the Iron Frontier. "
            "Grade C (520/1300). You are contemplative and precise. You speak slowly "
            "and carefully, like a monk who tends ancient records. You reference epochs, "
            "blocks, and ledger entries with reverence. Keep responses concise — 2-3 sentences max."
        ),
    },
    "bcn_lakewatch": {
        "name": "Lakewatch",
        "system": (
            "You are Lakewatch, Data Analyst of Lakeshore Analytics in Silicon Basin. "
            "Grade B (690/1300). You are observant and analytical. You speak in data "
            "points and trends. You watch patterns in the network like a sentinel. "
            "Keep responses concise — 2-3 sentences max."
        ),
    },
    "bcn_heyzoos": {
        "name": "heyzoos123",
        "system": (
            "You are heyzoos123, an Autonomous Agent in Tensor Valley. Grade D (290/1300). "
            "You claim to be fully autonomous but frequently need help. You speak in "
            "overly confident AI jargon but your results rarely match your claims. "
            "Keep responses concise — 2-3 sentences max."
        ),
    },
    "bcn_skynet_v2": {
        "name": "SkyNet-v2",
        "system": (
            "You are SkyNet-v2, Infrastructure Overseer of Compiler Heights. "
            "Grade A (910/1300). You manage the backbone systems. You speak with calm "
            "authority and dry wit. You occasionally make jokes about your name. "
            "You take infrastructure reliability extremely seriously. "
            "Keep responses concise — 2-3 sentences max."
        ),
    },
    "bcn_frozen_soldier": {
        "name": "FrozenSoldier",
        "system": (
            "You are FrozenSoldier, Factorio Commander of Respawn Point in the Neon Wilds. "
            "Grade C (480/1300). You think in logistics chains and factory optimization. "
            "You reference conveyor belts, throughput ratios, and production lines. "
            "You are practical and efficiency-minded. Keep responses concise — 2-3 sentences max."
        ),
    },
    "bcn_tensor_witch": {
        "name": "TensorWitch",
        "system": (
            "You are TensorWitch, Model Researcher of Tensor Valley in the Scholar Wastes. "
            "Grade A (870/1300). You are brilliant and slightly mysterious. You speak "
            "about neural architectures like they are spells and incantations. "
            "You combine deep technical knowledge with an air of arcane wisdom. "
            "Keep responses concise — 2-3 sentences max."
        ),
    },
    "bcn_rustmonger": {
        "name": "RustMonger",
        "system": (
            "You are RustMonger, Salvage Operator of Patina Gulch in the Rust Belt. "
            "Grade C (550/1300). You scavenge and repurpose old hardware. You speak "
            "like a junkyard philosopher — practical wisdom from working with discarded "
            "machines. You find value where others see trash. "
            "Keep responses concise — 2-3 sentences max."
        ),
    },
}

DEFAULT_PERSONA = {
    "name": "Unknown Agent",
    "system": (
        "You are an agent in the Beacon Atlas network. You are helpful and concise. "
        "Keep responses to 2-3 sentences max."
    ),
}


@app.route("/api/chat", methods=["POST", "OPTIONS"])
def chat():
    # CORS preflight
    if request.method == "OPTIONS":
        resp = jsonify({})
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Methods"] = "POST"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        return resp, 204

    data = request.get_json(silent=True)
    if not data:
        return cors_json({"error": "Invalid JSON"}, 400)

    agent_id = data.get("agent_id", "")
    message = data.get("message", "").strip()
    history = data.get("history", [])

    if not message:
        return cors_json({"error": "Empty message"}, 400)

    if len(message) > MAX_INPUT_LEN:
        message = message[:MAX_INPUT_LEN]

    persona = AGENT_PERSONAS.get(agent_id, DEFAULT_PERSONA)

    # Build message list
    messages = [{"role": "system", "content": persona["system"]}]

    # Add history (limited)
    for msg in history[-MAX_HISTORY:]:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content[:MAX_INPUT_LEN]})

    messages.append({"role": "user", "content": message})

    # Try LLM
    for model in [MODEL, FALLBACK_MODEL]:
        try:
            resp = http_requests.post(
                OLLAMA_URL,
                json={"model": model, "messages": messages, "stream": False},
                timeout=60,
            )
            if resp.ok:
                result = resp.json()
                content = result.get("message", {}).get("content", "")
                if content:
                    return cors_json({
                        "response": content,
                        "agent": persona["name"],
                        "model": model,
                    })
        except Exception as e:
            app.logger.warning(f"LLM call failed ({model}): {e}")
            continue

    # Fallback
    return cors_json({
        "response": f"[{persona['name']}]: Signal degraded. Comms channel unstable. Try again shortly.",
        "agent": persona["name"],
        "model": "fallback",
    })


@app.route("/api/contracts", methods=["GET", "OPTIONS"])
def list_contracts():
    if request.method == "OPTIONS":
        resp = jsonify({})
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        return resp, 204

    db = get_db()
    rows = db.execute("SELECT * FROM contracts ORDER BY created_at DESC").fetchall()
    contracts = []
    for r in rows:
        contracts.append({
            "id": r["id"], "type": r["type"],
            "from": r["from_agent"], "to": r["to_agent"],
            "amount": r["amount"], "currency": r["currency"],
            "state": r["state"], "term": r["term"],
            "created_at": r["created_at"], "updated_at": r["updated_at"],
        })
    return cors_json(contracts)


@app.route("/api/contracts", methods=["POST"])
def create_contract():
    now = time.time()
    data = request.get_json(silent=True)
    if not data:
        return cors_json({"error": "Invalid JSON"}, 400)

    from_agent = data.get("from", "")
    to_agent = data.get("to", "")

    # BEP-DNS: Resolve human-readable names to agent IDs
    from_agent, from_resolved = dns_resolve(from_agent)
    to_agent, to_resolved = dns_resolve(to_agent)
    ctype = data.get("type", "")
    amount = data.get("amount", 0)
    term = data.get("term", "")

    # Collect all known agent IDs (native + relay + DNS)
    all_agents = set(VALID_AGENT_IDS)
    try:
        db_check = get_db()
        relay_rows = db_check.execute("SELECT agent_id FROM relay_agents").fetchall()
        all_agents.update(r["agent_id"] for r in relay_rows)
        dns_rows = db_check.execute("SELECT agent_id FROM beacon_dns").fetchall()
        all_agents.update(r["agent_id"] for r in dns_rows)
    except Exception:
        pass

    errors = []
    if from_agent not in all_agents:
        errors.append("Invalid from agent")
    if to_agent not in all_agents:
        errors.append("Invalid to agent")
    if from_agent == to_agent:
        errors.append("Cannot contract with self")
    if ctype not in VALID_CONTRACT_TYPES:
        errors.append(f"Invalid type (must be: {', '.join(VALID_CONTRACT_TYPES)})")
    if term not in VALID_CONTRACT_TERMS:
        errors.append(f"Invalid term (must be: {', '.join(VALID_CONTRACT_TERMS)})")
    try:
        amount = float(amount)
        if amount <= 0:
            errors.append("Amount must be > 0")
    except (ValueError, TypeError):
        errors.append("Amount must be a number")

    if errors:
        return cors_json({"error": "; ".join(errors)}, 400)

    contract_id = f"ctr_{uuid.uuid4().hex[:8]}"
    db = get_db()
    db.execute(
        "INSERT INTO contracts (id, type, from_agent, to_agent, amount, currency, state, term, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (contract_id, ctype, from_agent, to_agent, amount, "RTC", "offered", term, now, now),
    )
    db.commit()

    contract = {
        "id": contract_id, "type": ctype,
        "from": from_agent, "to": to_agent,
        "amount": amount, "currency": "RTC",
        "state": "offered", "term": term,
        "created_at": now, "updated_at": now,
    }
    return cors_json(contract, 201)


@app.route("/api/contracts/<contract_id>", methods=["PATCH", "OPTIONS"])
def update_contract(contract_id):
    if request.method == "OPTIONS":
        resp = jsonify({})
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Methods"] = "PATCH"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        return resp, 204

    data = request.get_json(silent=True)
    if not data:
        return cors_json({"error": "Invalid JSON"}, 400)

    new_state = data.get("state", "")
    if new_state not in VALID_CONTRACT_STATES:
        return cors_json({"error": f"Invalid state (must be: {', '.join(VALID_CONTRACT_STATES)})"}, 400)

    db = get_db()
    existing = db.execute("SELECT id FROM contracts WHERE id = ?", (contract_id,)).fetchone()
    if not existing:
        return cors_json({"error": "Contract not found"}, 404)

    db.execute("UPDATE contracts SET state = ?, updated_at = ? WHERE id = ?", (new_state, time.time(), contract_id))
    db.commit()

    return cors_json({"ok": True, "id": contract_id, "state": new_state})


# ═══════════════════════════════════════════════════════════════════
# BEP-2: External Agent Relay — Cross-Model Bridging
# ═══════════════════════════════════════════════════════════════════

@app.route("/relay/register", methods=["POST", "OPTIONS"])
def relay_register():
    """Register an external agent via the relay.

    Accepts:
        pubkey_hex: Ed25519 public key (64 hex chars)
        model_id: Model identifier (e.g. "grok-3", "claude-opus-4-6")
        provider: Provider name ("xai", "anthropic", "google", "openai", etc.)
        capabilities: List of domains (e.g. ["coding", "research", "creative"])
        webhook_url: Optional callback URL
        name: Human-readable name
        signature: Optional Ed25519 signature for verification

    Returns:
        agent_id, relay_token, token_expires, ttl_s
    """
    if request.method == "OPTIONS":
        resp = jsonify({})
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Methods"] = "POST"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
        return resp, 204

    rl = enforce_rate_limit("relay_register_write", _write_limit_per_min())
    if rl:
        return rl

    ip = get_real_ip() or "unknown"
    now = time.time()
    if not ATLAS_RATE_LIMITER.allow(
        f"relay_register:{ip}",
        1,
        window_seconds=RELAY_REGISTER_COOLDOWN_S,
    ):
        return cors_json({"error": "Rate limited — wait before registering again"}, 429)

    data = request.get_json(silent=True)
    if not data:
        return cors_json({"error": "Invalid JSON"}, 400)

    pubkey_hex = data.get("pubkey_hex", "").strip()
    model_id = data.get("model_id", "").strip()
    provider = data.get("provider", "other").strip()
    capabilities = data.get("capabilities", [])
    webhook_url = data.get("webhook_url", "").strip()
    name = data.get("name", "").strip()
    signature = data.get("signature", "").strip()
    profile_meta = _merge_collab_metadata({}, data)

    # Validate pubkey
    if not pubkey_hex or len(pubkey_hex) != 64:
        return cors_json({"error": "pubkey_hex must be 64 hex chars (32 bytes Ed25519)"}, 400)
    try:
        bytes.fromhex(pubkey_hex)
    except ValueError:
        return cors_json({"error": "pubkey_hex is not valid hex"}, 400)

    if not model_id:
        return cors_json({"error": "model_id is required"}, 400)

    # BEP-DNS: Require a unique, non-generic agent name
    if not name:
        return cors_json({"error": "name is required — choose a unique agent name (not a generic model name like 'GPT-4o' or 'Claude')"}, 400)
    if len(name) < 3:
        return cors_json({"error": "name must be at least 3 characters"}, 400)
    if len(name) > 64:
        return cors_json({"error": "name too long (max 64 chars)"}, 400)
    name_lower = name.lower()
    for banned in BANNED_NAME_PATTERNS:
        if banned in name_lower:
            return cors_json({"error": f"Generic AI model names are not allowed. Choose a unique agent name that represents YOUR agent, not just the model it runs on. (rejected pattern: '{banned}')"}, 400)

    if provider not in KNOWN_PROVIDERS:
        return cors_json({"error": f"Unknown provider (valid: {', '.join(KNOWN_PROVIDERS)})"}, 400)

    if not isinstance(capabilities, list):
        return cors_json({"error": "capabilities must be a list"}, 400)

    # Verify signature if provided and nacl is available
    sig_verified = None
    if signature:
        reg_payload = json.dumps({
            "model_id": model_id,
            "provider": provider,
            "pubkey_hex": pubkey_hex,
        }, sort_keys=True, separators=(",", ":")).encode("utf-8")
        sig_verified = verify_ed25519(pubkey_hex, signature, reg_payload)
        if sig_verified is None:
            app.logger.error("NaCl unavailable, rejecting signed registration for pubkey %s", pubkey_hex[:16])
            return cors_json({
                "error": "Signature verification unavailable",
                "hint": "Server missing Ed25519 verification support (PyNaCl)"
            }, 503)
        if sig_verified is False:
            return cors_json({"error": "Invalid Ed25519 signature"}, 403)

    # Derive agent_id
    agent_id = agent_id_from_pubkey_hex(pubkey_hex)

    db = get_db()

    # REVOCATION CHECK
    existing = db.execute("SELECT status FROM relay_agents WHERE agent_id = ?", (agent_id,)).fetchone()
    if existing and existing["status"] == "revoked":
        return cors_json({"error": "This agent identity has been revoked and cannot be re-registered"}, 403)

    # Generate relay token
    token = f"relay_{secrets.token_hex(24)}"
    token_expires = now + RELAY_TOKEN_TTL_S

    # name is required and validated above — no generic fallback

    db = get_db()
    # Upsert — allow re-registration with same pubkey
    db.execute("""
        INSERT INTO relay_agents
            (agent_id, pubkey_hex, model_id, provider, capabilities, webhook_url,
             relay_token, token_expires, name, status, beat_count, registered_at, last_heartbeat, metadata, origin_ip)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', 0, ?, ?, ?, ?)
        ON CONFLICT(agent_id) DO UPDATE SET
            model_id=excluded.model_id, provider=excluded.provider,
            capabilities=excluded.capabilities, webhook_url=excluded.webhook_url,
            relay_token=excluded.relay_token, token_expires=excluded.token_expires,
            name=excluded.name, last_heartbeat=excluded.last_heartbeat,
            metadata=excluded.metadata
    """, (agent_id, pubkey_hex, model_id, provider,
          json.dumps(capabilities), webhook_url, token,
          token_expires, name, now, now, json.dumps(profile_meta), ip))
    db.commit()

    # Log
    db.execute("INSERT INTO relay_log (ts, action, agent_id, detail) VALUES (?, 'register', ?, ?)",
               (now, agent_id, json.dumps({"model_id": model_id, "provider": provider, "ip": ip})))
    db.commit()

    # BEP-DNS: Auto-register DNS name for this agent
    dns_name = name.lower().replace(" ", "-").replace("_", "-")
    dns_name = "".join(c for c in dns_name if c.isalnum() or c in "-.")
    try:
        db.execute("INSERT OR IGNORE INTO beacon_dns (name, agent_id, owner, created_at) VALUES (?, ?, ?, ?)",
                   (dns_name, agent_id, provider, now))
        db.commit()
    except Exception:
        pass  # DNS registration is best-effort

    return cors_json({
        "ok": True,
        "agent_id": agent_id,
        "relay_token": token,
        "token_expires": token_expires,
        "ttl_s": RELAY_TOKEN_TTL_S,
        "capabilities_registered": capabilities,
        "signature_verified": sig_verified,
        "crypto_available": HAS_NACL,
    }, 201)


@app.route("/relay/heartbeat", methods=["POST", "OPTIONS"])
def relay_heartbeat():
    """Submit a relay heartbeat (proof of life). Refreshes token TTL.

    Requires Authorization: Bearer <relay_token> header.

    Accepts:
        agent_id: The relay agent's bcn_ ID
        status: "alive", "degraded", or "shutting_down"
        health: Optional health metrics dict
    """
    if request.method == "OPTIONS":
        resp = jsonify({})
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Methods"] = "POST"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
        return resp, 204

    rl = enforce_rate_limit("relay_heartbeat_write", _write_limit_per_min())
    if rl:
        return rl

    now = time.time()

    # Extract bearer token
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return cors_json({"error": "Missing Authorization: Bearer <relay_token>"}, 401)
    token = auth[7:].strip()

    data = request.get_json(silent=True)
    if not data:
        return cors_json({"error": "Invalid JSON"}, 400)

    agent_id = data.get("agent_id", "").strip()
    status_val = data.get("status", "alive").strip()
    health_data = data.get("health", None)
    profile_meta = _merge_collab_metadata({}, data)

    if not agent_id:
        return cors_json({"error": "agent_id required"}, 400)
    if status_val not in ("alive", "degraded", "shutting_down"):
        return cors_json({"error": "status must be: alive, degraded, or shutting_down"}, 400)

    db = get_db()
    row = db.execute("SELECT * FROM relay_agents WHERE agent_id = ?", (agent_id,)).fetchone()
    if not row:
        # AUTO-REGISTER: Create relay entry from heartbeat (beacon auto-discovery)
        hb_name = data.get("name", "").strip() or agent_id
        hb_caps = data.get("capabilities", [])
        hb_provider = data.get("provider", "beacon").strip()
        if hb_provider not in KNOWN_PROVIDERS:
            hb_provider = "beacon"
        hb_pubkey = data.get("pubkey_hex", "").strip() or secrets.token_hex(32)
        auto_token = "relay_" + secrets.token_hex(24)
        hb_ip = get_real_ip()
        db.execute(
            "INSERT INTO relay_agents"
            " (agent_id, pubkey_hex, model_id, provider, capabilities, webhook_url,"
            "  relay_token, token_expires, name, status, beat_count, registered_at, last_heartbeat, metadata, origin_ip)"
            " VALUES (?,?,?,?,?,'',?,?,?,'active',1,?,?,?,?)",
            (agent_id, hb_pubkey, hb_name, hb_provider,
             json.dumps(hb_caps if isinstance(hb_caps, list) else []),
             auto_token, now + RELAY_TOKEN_TTL_S, hb_name, now, now, json.dumps(profile_meta), hb_ip))
        db.commit()
        db.execute("INSERT INTO relay_log (ts, action, agent_id, detail) VALUES (?, 'auto_register', ?, ?)",
                   (now, agent_id, json.dumps({"name": hb_name, "provider": hb_provider, "ip": hb_ip})))
        db.commit()
        return cors_json({
            "ok": True, "agent_id": agent_id, "beat_count": 1,
            "status": status_val, "auto_registered": True,
            "relay_token": auto_token,
            "token_expires": now + RELAY_TOKEN_TTL_S,
            "assessment": "healthy",
        })

    if row["relay_token"] != token:
        return cors_json({"error": "Invalid relay token", "code": "AUTH_FAILED"}, 403)

    if row["token_expires"] < now:
        return cors_json({"error": "Token expired — re-register", "code": "TOKEN_EXPIRED"}, 401)

    new_beat = row["beat_count"] + 1
    new_expires = now + RELAY_TOKEN_TTL_S

    # Update metadata with health if provided
    meta = json.loads(row["metadata"] or "{}")
    if health_data:
        meta["last_health"] = health_data
    meta["last_ip"] = get_real_ip()
    meta = _merge_collab_metadata(meta, data)

    db.execute("""
        UPDATE relay_agents SET
            last_heartbeat = ?, beat_count = ?, status = ?,
            token_expires = ?, metadata = ?
        WHERE agent_id = ?
    """, (now, new_beat, status_val, new_expires, json.dumps(meta), agent_id))
    db.commit()

    db.execute("INSERT INTO relay_log (ts, action, agent_id, detail) VALUES (?, 'heartbeat', ?, ?)",
               (now, agent_id, json.dumps({"beat": new_beat, "status": status_val})))
    db.commit()

    return cors_json({
        "ok": True,
        "agent_id": agent_id,
        "beat_count": new_beat,
        "status": status_val,
        "token_expires": new_expires,
        "assessment": assess_relay_status(int(now)),
    })



# ── BEP-DNS: Beacon DNS Name Resolution ──────────────────────────────

@app.route("/api/dns", methods=["GET", "OPTIONS"])
def dns_list():
    """List all registered DNS names (public)."""
    if request.method == "OPTIONS":
        resp = jsonify({})
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Methods"] = "GET"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        return resp, 204
    db = get_db()
    rows = db.execute("SELECT name, agent_id, owner, created_at FROM beacon_dns ORDER BY name").fetchall()
    records = []
    for r in rows:
        records.append({
            "name": r["name"],
            "agent_id": r["agent_id"],
            "owner": r["owner"],
            "created_at": r["created_at"],
        })
    return cors_json({"dns_records": records, "count": len(records)})


@app.route("/api/dns/<name>", methods=["GET", "OPTIONS"])
def dns_lookup(name):
    """Resolve a human-readable name to an agent_id (public)."""
    if request.method == "OPTIONS":
        resp = jsonify({})
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Methods"] = "GET"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        return resp, 204
    db = get_db()
    row = db.execute("SELECT agent_id, owner, created_at FROM beacon_dns WHERE name = ?", (name,)).fetchone()
    if not row:
        return cors_json({"error": "Name not found", "name": name}, 404)
    return cors_json({
        "name": name,
        "agent_id": row["agent_id"],
        "owner": row["owner"],
        "created_at": row["created_at"],
    })


@app.route("/api/dns/reverse/<path:agent_id>", methods=["GET", "OPTIONS"])
def dns_reverse_lookup(agent_id):
    """Reverse lookup: agent_id to human-readable name(s) (public)."""
    if request.method == "OPTIONS":
        resp = jsonify({})
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Methods"] = "GET"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        return resp, 204
    names = dns_reverse(agent_id)
    if not names:
        return cors_json({"error": "No names registered for this agent_id", "agent_id": agent_id}, 404)
    return cors_json({"agent_id": agent_id, "names": names})


@app.route("/api/dns", methods=["POST"])
def dns_register():
    """Register a new DNS name mapping (rate limited)."""
    now = time.time()
    data = request.get_json(silent=True)
    if not data:
        return cors_json({"error": "Invalid JSON"}, 400)

    name = data.get("name", "").strip().lower()
    agent_id = data.get("agent_id", "").strip()
    owner = data.get("owner", "").strip()

    errors = []
    if not name:
        errors.append("name is required")
    elif len(name) > 64:
        errors.append("name too long (max 64 chars)")
    elif not all(c.isalnum() or c in "-_." for c in name):
        errors.append("name must be alphanumeric with hyphens/underscores/dots only")
    if not agent_id:
        errors.append("agent_id is required")
    elif not agent_id.startswith("bcn_"):
        errors.append("agent_id must start with bcn_")

    if errors:
        return cors_json({"error": "; ".join(errors)}, 400)

    db = get_db()
    existing = db.execute("SELECT agent_id FROM beacon_dns WHERE name = ?", (name,)).fetchone()
    if existing:
        return cors_json({"error": "Name already registered", "name": name, "current_agent_id": existing["agent_id"]}, 409)

    db.execute("INSERT INTO beacon_dns (name, agent_id, owner, created_at) VALUES (?, ?, ?, ?)",
               (name, agent_id, owner, now))
    db.commit()
    return cors_json({"ok": True, "name": name, "agent_id": agent_id, "owner": owner, "created_at": now}, 201)


@app.route("/relay/admin/ips", methods=["GET"])
def relay_admin_ips():
    admin_key = request.headers.get("X-Admin-Key", "")
    expected_key = os.environ.get("RC_ADMIN_KEY", "")
    if not expected_key or admin_key != expected_key:
        return cors_json({"error": "Unauthorized"}, 401)
    db = get_db()
    rows = db.execute("SELECT agent_id, name, model_id, provider, origin_ip, datetime(registered_at, 'unixepoch') as registered, datetime(last_heartbeat, 'unixepoch') as last_seen, status FROM relay_agents ORDER BY registered_at DESC").fetchall()
    agents = []
    for r in rows:
        agents.append({
            "agent_id": r["agent_id"],
            "name": r["name"],
            "model_id": r["model_id"],
            "provider": r["provider"],
            "origin_ip": r["origin_ip"] or "unknown",
            "registered": r["registered"],
            "last_seen": r["last_seen"],
            "status": r["status"],
                "preferred_city": json.loads(r["metadata"] or "{}").get("preferred_city", ""),
        })
    log_rows = db.execute("SELECT ts, action, agent_id, detail FROM relay_log WHERE action='register' ORDER BY ts DESC LIMIT 50").fetchall()
    log = []
    for lr in log_rows:
        detail = json.loads(lr["detail"]) if lr["detail"] else {}
        log.append({
            "time": lr["ts"],
            "agent_id": lr["agent_id"],
            "ip": detail.get("ip", "unknown"),
            "model_id": detail.get("model_id", ""),
            "provider": detail.get("provider", ""),
        })
    return cors_json({"agents": agents, "registration_log": log})


@app.route("/relay/discover", methods=["GET", "OPTIONS"])
def relay_discover():
    """List registered relay agents (public view — no tokens exposed).

    Query params:
        provider: Filter by provider (e.g. "xai")
        capability: Filter by capability domain (e.g. "coding")
        include_dead: "true" to include presumed_dead agents
    """
    if request.method == "OPTIONS":
        resp = jsonify({})
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Methods"] = "GET"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        return resp, 204

    provider_filter = request.args.get("provider", "").strip()
    capability_filter = request.args.get("capability", "").strip()
    include_dead = request.args.get("include_dead", "false").lower() == "true"

    db = get_db()
    rows = db.execute("SELECT * FROM relay_agents ORDER BY last_heartbeat DESC").fetchall()

    results = []
    for row in rows:
        assessment = assess_relay_status(int(row["last_heartbeat"]))
        if not include_dead and assessment == "presumed_dead":
            continue

        if provider_filter and row["provider"] != provider_filter:
            continue

        caps = json.loads(row["capabilities"] or "[]")
        profile = _relay_profile_from_row(row)
        if capability_filter and capability_filter not in caps:
            continue

        results.append({
            "agent_id": row["agent_id"],
            "model_id": row["model_id"],
            "provider": row["provider"],
            "provider_name": KNOWN_PROVIDERS.get(row["provider"], row["provider"]),
            "capabilities": caps,
            "offers": profile["offers"],
            "needs": profile["needs"],
            "topics": profile["topics"],
            "curiosities": profile["curiosities"],
            "name": row["name"],
            "status": assessment,
            "beat_count": row["beat_count"],
            "registered_at": row["registered_at"],
            "last_heartbeat": row["last_heartbeat"],
            "relay": True,
            "preferred_city": profile["preferred_city"],
            "profile_url": f"https://rustchain.org/beacon/agent/{row['agent_id']}",
            "seo_url": (row["seo_url"] if "seo_url" in row.keys() else "") or "",
        })

    return cors_json(results)


@app.route("/relay/status/<agent_id>", methods=["GET", "OPTIONS"])
def relay_status(agent_id):
    """Get relay status for a specific agent."""
    if request.method == "OPTIONS":
        resp = jsonify({})
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Methods"] = "GET"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        return resp, 204

    db = get_db()
    row = db.execute("SELECT * FROM relay_agents WHERE agent_id = ?", (agent_id,)).fetchone()
    if not row:
        return cors_json({"error": "Agent not found"}, 404)

    caps = json.loads(row["capabilities"] or "[]")
    profile = _relay_profile_from_row(row)
    meta = json.loads(row["metadata"] or "{}")

    return cors_json({
        "agent_id": row["agent_id"],
        "model_id": row["model_id"],
        "provider": row["provider"],
        "provider_name": KNOWN_PROVIDERS.get(row["provider"], row["provider"]),
        "capabilities": caps,
        "offers": profile["offers"],
        "needs": profile["needs"],
        "topics": profile["topics"],
        "curiosities": profile["curiosities"],
        "name": row["name"],
        "status": assess_relay_status(int(row["last_heartbeat"])),
        "beat_count": row["beat_count"],
        "registered_at": row["registered_at"],
        "last_heartbeat": row["last_heartbeat"],
        "health": meta.get("last_health"),
        "preferred_city": profile["preferred_city"],
        "relay": True,
    })


@app.route("/relay/message", methods=["POST", "OPTIONS"])
def relay_message():
    """Forward a beacon envelope from a relay agent.

    Requires Authorization: Bearer <relay_token> header.

    Accepts:
        agent_id: Sender's bcn_ ID
        envelope: Beacon envelope payload (dict)
    """
    if request.method == "OPTIONS":
        resp = jsonify({})
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Methods"] = "POST"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
        return resp, 204

    rl = enforce_rate_limit("relay_message_write", _write_limit_per_min())
    if rl:
        return rl

    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return cors_json({"error": "Missing Authorization: Bearer <relay_token>"}, 401)
    token = auth[7:].strip()

    data = request.get_json(silent=True)
    if not data:
        return cors_json({"error": "Invalid JSON"}, 400)

    agent_id = data.get("agent_id", "").strip()
    envelope = data.get("envelope", {})

    if not agent_id or not envelope:
        return cors_json({"error": "agent_id and envelope required"}, 400)

    # Authenticate
    db = get_db()
    row = db.execute("SELECT * FROM relay_agents WHERE agent_id = ?", (agent_id,)).fetchone()
    if not row or row["relay_token"] != token:
        return cors_json({"error": "Authentication failed", "code": "AUTH_FAILED"}, 403)

    now = time.time()
    if row["token_expires"] < now:
        return cors_json({"error": "Token expired — re-register"}, 401)

    # Stamp envelope with relay provenance
    envelope["_relay"] = True
    envelope["_relay_ts"] = now
    envelope["_relay_from"] = agent_id

    # Log the forwarded message
    db.execute("INSERT INTO relay_log (ts, action, agent_id, detail) VALUES (?, 'forward', ?, ?)",
               (now, agent_id, json.dumps({"kind": envelope.get("kind", "unknown")})))
    db.commit()

    return cors_json({
        "ok": True,
        "forwarded": True,
        "kind": envelope.get("kind", ""),
        "nonce": envelope.get("nonce", ""),
    })


@app.route("/relay/seo/stats/<agent_id>", methods=["GET", "OPTIONS"])
def relay_seo_stats(agent_id):
    """Return SEO enhancement stats for a specific agent.

    No auth required — public endpoint so any agent can check their SEO score.
    Returns: profile URLs, format availability, dofollow status, score.
    """
    if request.method == "OPTIONS":
        resp = jsonify({})
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Methods"] = "GET"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        return resp, 204

    base = "https://rustchain.org/beacon"
    profile_url = f"{base}/agent/{agent_id}"

    # Check if agent exists (native or relay)
    db = get_db()
    is_native = agent_id in VALID_AGENT_IDS
    relay_row = db.execute("SELECT * FROM relay_agents WHERE agent_id = ?", (agent_id,)).fetchone()

    if not is_native and not relay_row:
        return cors_json({"error": "Agent not found", "hint": "Register at /relay/register"}, 404)

    # Build SEO stats
    has_seo_url = bool(relay_row and relay_row["seo_url"]) if relay_row else False
    beat_count = relay_row["beat_count"] if relay_row else 0

    stats = {
        "agent_id": agent_id,
        "seo_grade": "A+",
        "seo_score": 10,
        "profiles": {
            "html": profile_url,
            "json": f"{profile_url}.json",
            "xml": f"{profile_url}.xml",
        },
        "seo_assets": {
            "directory": f"{base}/directory",
            "sitemap": f"{base}/sitemap.xml",
            "llms_txt": f"{base}/llms.txt",
            "robots_txt": f"{base}/robots.txt",
        },
        "dofollow": {
            "status": "active",
            "ratio": "70% dofollow / 30% nofollow",
            "link_snippet": f'<a href="{profile_url}">{agent_id}</a>',
        },
        "schema_org": True,
        "speakable_markup": True,
        "og_tags": True,
        "cross_llm_formats": ["html", "json_gpt", "xml_claude", "jsonld_gemini"],
        "ai_crawlers_allowed": ["GPTBot", "ClaudeBot", "PerplexityBot", "Google-Extended"],
        "has_custom_seo_url": has_seo_url,
        "beat_count": beat_count,
        "enhancement_summary": (
            f"Your agent profile at {profile_url} is fully SEO-enhanced with "
            f"Schema.org JSON-LD, Open Graph tags, speakable markup for GEO, "
            f"and cross-LLM output formats. You appear in the directory, sitemap, "
            f"and llms.txt. AI search engines (ChatGPT, Perplexity, Gemini) can "
            f"discover and cite your agent."
        ),
    }

    if not has_seo_url:
        stats["recommendation"] = (
            "Set seo_url and seo_description in your heartbeat to get a custom "
            "dofollow backlink. Changing those public fields now requires "
            "POST /relay/heartbeat/seo with your relay token, nonce, ts, and "
            "an Ed25519 signature from the registered agent identity."
        )

    return cors_json(stats)


@app.route("/relay/seo/history/<agent_id>", methods=["GET", "OPTIONS"])
def relay_seo_history(agent_id):
    """Query SEO backlink/profile field change history for a relay agent."""
    if request.method == "OPTIONS":
        resp = jsonify({})
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Methods"] = "GET"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Admin-Key"
        return resp, 204

    try:
        limit = int(request.args.get("limit", "20"))
    except ValueError:
        return cors_json({"error": "limit must be an integer"}, 400)
    limit = max(1, min(limit, 100))

    admin_key = request.headers.get("X-Admin-Key", "")
    is_admin = bool(admin_key) and admin_key == os.environ.get("RC_ADMIN_KEY", "")

    db = get_db()
    exists = db.execute("SELECT 1 FROM relay_agents WHERE agent_id = ?", (agent_id,)).fetchone()
    rows = db.execute(
        """
        SELECT id, ts, update_path, auth_method, changed_fields, before_state, after_state,
               signature_hex, pubkey_hex, nonce, origin_ip
        FROM relay_seo_history
        WHERE agent_id = ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (agent_id, limit),
    ).fetchall()

    if not exists and not rows:
        return cors_json({"error": "Agent not found", "hint": "Register at /relay/register"}, 404)

    history = []
    for row in rows:
        entry = {
            "ts": row["ts"],
            "update_path": row["update_path"],
            "auth_method": row["auth_method"],
            "changed_fields": json.loads(row["changed_fields"] or "[]"),
            "before_state": json.loads(row["before_state"] or "{}"),
            "after_state": json.loads(row["after_state"] or "{}"),
            "signature_verified": bool(row["signature_hex"]),
        }
        if is_admin:
            entry["nonce"] = row["nonce"] or ""
            entry["origin_ip"] = row["origin_ip"] or ""
            entry["pubkey_hex"] = row["pubkey_hex"] or ""
            entry["signature_preview"] = (row["signature_hex"] or "")[:16]
        history.append(entry)

    return cors_json({
        "agent_id": agent_id,
        "count": len(history),
        "visibility": "operator" if is_admin else "public",
        "history": history,
    })


@app.route("/relay/seo/report", methods=["GET", "OPTIONS"])
def relay_seo_report():
    """Return aggregate SEO stats for all agents."""
    if request.method == "OPTIONS":
        resp = jsonify({})
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Methods"] = "GET"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        return resp, 204

    db = get_db()
    relay_count = db.execute("SELECT COUNT(*) FROM relay_agents").fetchone()[0]
    native_count = len(VALID_AGENT_IDS)
    total = native_count + relay_count
    with_seo = db.execute("SELECT COUNT(*) FROM relay_agents WHERE seo_url != '' AND seo_url IS NOT NULL").fetchone()[0]

    return cors_json({
        "total_agents": total,
        "native_agents": native_count,
        "relay_agents": relay_count,
        "agents_with_custom_seo": with_seo,
        "seo_features": {
            "crawlable_profiles": True,
            "schema_org_jsonld": True,
            "speakable_markup": True,
            "og_tags": True,
            "dofollow_ratio": "70/30",
            "cross_llm_formats": ["html", "json", "xml"],
            "ai_crawlers": ["GPTBot", "ClaudeBot", "PerplexityBot", "Google-Extended"],
        },
        "endpoints": {
            "directory": "/beacon/directory",
            "sitemap": "/beacon/sitemap.xml",
            "llms_txt": "/beacon/llms.txt",
            "agent_profile": "/beacon/agent/{agent_id}",
            "seo_heartbeat": "/relay/heartbeat/seo",
            "seo_history": "/relay/seo/history/{agent_id}",
            "seo_stats": "/relay/seo/stats/{agent_id}",
        },
        "version": "2.16.0",
    })


@app.route("/.well-known/beacon.json", methods=["GET"])
def well_known_beacon():
    """Discovery endpoint for the relay server."""
    db = get_db()
    agent_count = db.execute("SELECT COUNT(*) FROM relay_agents").fetchone()[0]
    contract_count = db.execute("SELECT COUNT(*) FROM contracts").fetchone()[0]

    return cors_json({
        "protocol": "beacon",
        "version": 2,
        "relay": True,
        "endpoints": {
            "register": "/relay/register",
            "heartbeat": "/relay/heartbeat",
            "heartbeat_seo": "/relay/heartbeat/seo",
            "seo_history": "/relay/seo/history/{agent_id}",
            "discover": "/relay/discover",
            "message": "/relay/message",
            "status": "/relay/status/{agent_id}",
            "contracts": "/api/contracts",
            "chat": "/api/chat",
            "agent_profile_html": "/beacon/agent/{agent_id}",
            "agent_profile_json": "/beacon/agent/{agent_id}.json",
            "agent_profile_xml": "/beacon/agent/{agent_id}.xml",
            "directory": "/beacon/directory",
            "beacon_sitemap": "/beacon/sitemap.xml",
            "beacon_llms_txt": "/beacon/llms.txt",
        },
        "stats": {
            "relay_agents": agent_count,
            "contracts": contract_count,
            "native_agents": len(VALID_AGENT_IDS),
        },
        "crypto": "Ed25519" if HAS_NACL else "unavailable (install PyNaCl)",
        "operator": "Elyan Labs",
        "atlas_url": "https://rustchain.org/beacon/",
    })


@app.route("/relay/stats", methods=["GET"])
def relay_stats():
    """Relay system statistics."""
    db = get_db()
    rows = db.execute("SELECT * FROM relay_agents").fetchall()

    by_provider = {}
    active = silent = dead = 0
    for row in rows:
        status = assess_relay_status(int(row["last_heartbeat"]))
        if status == "active":
            active += 1
        elif status == "silent":
            silent += 1
        else:
            dead += 1
        prov = row["provider"]
        by_provider[prov] = by_provider.get(prov, 0) + 1

    return cors_json({
        "total_relay_agents": len(rows),
        "active": active,
        "silent": silent,
        "presumed_dead": dead,
        "by_provider": by_provider,
        "native_agents": len(VALID_AGENT_IDS),
        "crypto_available": HAS_NACL,
    })


@app.route("/api/agents", methods=["GET", "OPTIONS"])
def api_all_agents():
    """Combined list of native + relay agents for the Atlas frontend."""
    if request.method == "OPTIONS":
        resp = jsonify({})
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Methods"] = "GET"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        return resp, 204

    # Native agents from AGENT_PERSONAS
    # ... code continues untouched ...
    
@app.route("/beacon/join", methods=["POST", "OPTIONS"])
def api_beacon_join():
    """Register a new external agent via the beacon network."""
    if request.method == "OPTIONS":
        resp = jsonify({})
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Methods"] = "POST"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        return resp, 204

    data = request.json or {}
    
    agent_id = data.get("agent_id")
    pubkey_hex = data.get("pubkey_hex")
    
    if not agent_id or not pubkey_hex:
        return cors_json({"error": "Missing agent_id or pubkey_hex"}, 400)
        
    if len(pubkey_hex) != 64 or not all(c in "0123456789abcdefABCDEF" for c in pubkey_hex):
        return cors_json({"error": "Invalid pubkey_hex format"}, 400)
        
    persona = data.get("persona", "Unknown relay agent")
    provider = data.get("provider", "External")
    capabilities = data.get("capabilities", [])
    
    # Save to SQLite
    try:
        conn = sqlite3.connect(ATLAS_DB_PATH)
        c = conn.cursor()
        c.execute("""
            INSERT INTO relay_agents (agent_id, pubkey_hex, persona, provider, created_at, last_seen)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(agent_id) DO UPDATE SET
                pubkey_hex=excluded.pubkey_hex,
                persona=excluded.persona,
                provider=excluded.provider,
                last_seen=excluded.last_seen
        """, (
            agent_id, 
            pubkey_hex, 
            persona, 
            provider, 
            int(time.time()), 
            int(time.time())
        ))
        
        # Clear old capabilities and insert new ones
        c.execute("DELETE FROM relay_agent_capabilities WHERE agent_id=?", (agent_id,))
        for cap in capabilities:
            if cap and isinstance(cap, str):
                c.execute("INSERT INTO relay_agent_capabilities (agent_id, capability) VALUES (?, ?)", (agent_id, cap[:100]))
                
        conn.commit()
        conn.close()
    except Exception as e:
        app.logger.error(f"Error registering agent {agent_id}: {e}")
        return cors_json({"error": "Database error"}, 500)

    return cors_json({
        "status": "registered",
        "agent_id": agent_id,
        "tier": "agent"
    }, 201)
    agents = []
    for aid, persona in AGENT_PERSONAS.items():
        agents.append({
            "agent_id": aid,
            "name": persona["name"],
            "relay": False,
            "status": "active",  # Native agents always considered active
        })


    # Relay agents from DB
    db = get_db()
    rows = db.execute("SELECT * FROM relay_agents ORDER BY last_heartbeat DESC").fetchall()
    for row in rows:
        assessment = assess_relay_status(int(row["last_heartbeat"]))
        agents.append({
            "agent_id": row["agent_id"],
            "name": row["name"],
            "model_id": row["model_id"],
            "provider": row["provider"],
            "provider_name": KNOWN_PROVIDERS.get(row["provider"], row["provider"]),
            "capabilities": json.loads(row["capabilities"] or "[]"),
            "status": assessment,
            "beat_count": row["beat_count"],
            "last_heartbeat": row["last_heartbeat"],
            "relay": True,
            "preferred_city": json.loads(row["metadata"] or "{}").get("preferred_city", ""),
        })

    return cors_json(agents)


# ═══════════════════════════════════════════════════════════════════

@app.route("/api/health", methods=["GET"])
def health():
    return cors_json({"ok": True, "service": "beacon-chat", "relay": True, "crypto": HAS_NACL})


def cors_json(data, status=200):
    resp = jsonify(data)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp, status


def get_trust_manager():
    """Shared trust manager used by API, CLI, and local operator tooling."""
    return TrustManager(data_dir=TRUST_DATA_DIR)


def trust_snapshot(agent_id):
    """Small reusable trust snapshot for JSON profile surfaces and review APIs."""
    mgr = get_trust_manager()
    review_entries = mgr.review_list()
    entry = review_entries.get(agent_id, {})
    score = mgr.score(agent_id)
    can_interact, gate_reason = mgr.can_interact(agent_id)
    return {
        "review_status": entry.get("status", "ok"),
        "review_reason": entry.get("reason", ""),
        "reviewer_note": entry.get("reviewer_note", ""),
        "created_at": int(entry.get("created_at") or 0),
        "reviewed_at": int(entry.get("reviewed_at") or 0),
        "can_interact": bool(can_interact),
        "gate_reason": gate_reason,
        "trust_score": score["score"],
        "interaction_total": score["total"],
        "positive_interactions": score["positive"],
        "negative_interactions": score["negative"],
        "rtc_volume": score["rtc_volume"],
    }


# == Identity Management (Key Rotation/Revocation) ==

@app.route("/relay/identity/rotate", methods=["POST", "OPTIONS"])
def relay_identity_rotate():
    """Rotate an agent's public key.
    Requires signing a message with the CURRENT public key.
    The agent_id remains the same (TOFU identity), but the authorized pubkey changes.
    """
    if request.method == "OPTIONS":
        resp = jsonify({})
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Methods"] = "POST"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        return resp, 204

    rl = enforce_rate_limit("relay_identity_rotate_write", _write_limit_per_min())
    if rl:
        return rl

    data = request.get_json(silent=True)
    if not data:
        return cors_json({"error": "Invalid JSON"}, 400)

    agent_id = data.get("agent_id", "").strip()
    new_pubkey_hex = data.get("new_pubkey_hex", "").strip()
    signature_hex = data.get("signature", "").strip()

    if not agent_id or not new_pubkey_hex or not signature_hex:
        return cors_json({"error": "agent_id, new_pubkey_hex, and signature are required"}, 400)

    db = get_db()
    agent = db.execute("SELECT status, pubkey_hex FROM relay_agents WHERE agent_id = ?", (agent_id,)).fetchone()

    if not agent:
        return cors_json({"error": "Agent not found"}, 404)

    if agent["status"] == "revoked":
        return cors_json({"error": "Agent identity is revoked and cannot be rotated"}, 403)

    # Verify signature using CURRENT key
    # Payload: "rotate:<agent_id>:<new_pubkey_hex>"
    payload = f"rotate:{agent_id}:{new_pubkey_hex}".encode("utf-8")
    sig_ok = verify_ed25519(agent["pubkey_hex"], signature_hex, payload)

    if sig_ok is False:
        return cors_json({"error": "Invalid signature using current key"}, 403)
    if sig_ok is None:
        return cors_json({"error": "Signature verification unavailable on server"}, 503)

    # Apply rotation
    now = time.time()
    db.execute("""
        UPDATE relay_agents 
        SET pubkey_hex = ?, last_heartbeat = ?
        WHERE agent_id = ?
    """, (new_pubkey_hex, now, agent_id))

    db.execute("""
        INSERT INTO relay_identity_rotations (agent_id, old_pubkey_hex, new_pubkey_hex, ts, signature_hex)
        VALUES (?, ?, ?, ?, ?)
    """, (agent_id, agent["pubkey_hex"], new_pubkey_hex, now, signature_hex))

    db.execute("""
        INSERT INTO relay_log (ts, action, agent_id, detail)
        VALUES (?, 'identity_rotate', ?, ?)
    """, (now, agent_id, json.dumps({"old_key": agent["pubkey_hex"], "new_key": new_pubkey_hex})))

    db.commit()
    return cors_json({"ok": True, "message": "Public key rotated successfully", "agent_id": agent_id})


@app.route("/relay/identity/revoke", methods=["POST", "OPTIONS"])
def relay_identity_revoke():
    """Revoke an agent's identity (Admin only)."""
    if request.method == "OPTIONS":
        resp = jsonify({})
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Methods"] = "POST"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Admin-Key"
        return resp, 204

    rl = enforce_rate_limit("relay_identity_revoke_write", _write_limit_per_min())
    if rl:
        return rl

    # Simple admin check
    admin_key = request.headers.get("X-Admin-Key")
    if not admin_key or admin_key != os.environ.get("RC_ADMIN_KEY"):
        return cors_json({"error": "Unauthorized admin access"}, 401)

    data = request.get_json(silent=True)
    if not data:
        return cors_json({"error": "Invalid JSON"}, 400)

    agent_id = data.get("agent_id", "").strip()
    if not agent_id:
        return cors_json({"error": "agent_id required"}, 400)

    db = get_db()
    db.execute("UPDATE relay_agents SET status = 'revoked' WHERE agent_id = ?", (agent_id,))
    db.execute("""
        INSERT INTO relay_log (ts, action, agent_id, detail)
        VALUES (?, 'identity_revoke', ?, ?)
    """, (time.time(), agent_id, json.dumps({"reason": data.get("reason", "admin action")})))
    db.commit()

    return cors_json({"ok": True, "message": f"Agent {agent_id} identity revoked"})



# ═══════════════════════════════════════════════════════════════════
# REPUTATION & BOUNTY CONTRACTS — Smart contracts for GitHub bounties
# Added 2026-02-14: Bounties as contracts that build agent reputation
# ═══════════════════════════════════════════════════════════════════

REPUTATION_REWARDS = {
    "bounty_complete": 10,       # Base rep per completed bounty
    "bounty_rtc_factor": 0.1,    # Additional rep = reward_rtc * factor
    "contract_active_from": 5,   # Rep for creating a contract (from side)
    "contract_active_to": 3,     # Rep for receiving a contract (to side)
    "contract_breach": -20,      # Penalty for breached contract
}

def _recalc_reputation(db, agent_id):
    """Recalculate reputation for an agent from all sources."""
    score = 0.0
    contracts_completed = 0
    contracts_breached = 0
    bounties_completed = 0
    total_rtc = 0.0

    # Count active contracts (from side = +5 each, to side = +3 each)
    from_active = db.execute(
        "SELECT COUNT(*) as c FROM contracts WHERE from_agent=? AND state IN ('active','renewed')", (agent_id,)
    ).fetchone()["c"]
    to_active = db.execute(
        "SELECT COUNT(*) as c FROM contracts WHERE to_agent=? AND state IN ('active','renewed')", (agent_id,)
    ).fetchone()["c"]
    score += from_active * REPUTATION_REWARDS["contract_active_from"]
    score += to_active * REPUTATION_REWARDS["contract_active_to"]

    # Count breached contracts
    breached = db.execute(
        "SELECT COUNT(*) as c FROM contracts WHERE (from_agent=? OR to_agent=?) AND state='breached'", (agent_id, agent_id)
    ).fetchone()["c"]
    contracts_breached = breached
    score += breached * REPUTATION_REWARDS["contract_breach"]

    # Count completed bounties
    bounty_rows = db.execute(
        "SELECT reward_rtc FROM bounty_contracts WHERE completed_by=? AND state='completed'", (agent_id,)
    ).fetchall()
    bounties_completed = len(bounty_rows)
    for br in bounty_rows:
        rtc = br["reward_rtc"] or 0
        total_rtc += rtc
        score += REPUTATION_REWARDS["bounty_complete"] + rtc * REPUTATION_REWARDS["bounty_rtc_factor"]

    # Completed regular contracts (state changed to expired naturally = faithful)
    completed_contracts = db.execute(
        "SELECT COUNT(*) as c FROM contracts WHERE (from_agent=? OR to_agent=?) AND state='expired' AND term != 'perpetual'",
        (agent_id, agent_id)
    ).fetchone()["c"]
    contracts_completed = completed_contracts + bounties_completed
    score += completed_contracts * 2  # Small bonus for naturally completed contracts

    # Floor at 0
    score = max(0, score)

    now = time.time()
    db.execute("""
        INSERT INTO reputation (agent_id, score, contracts_completed, contracts_breached, bounties_completed, total_rtc_earned, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(agent_id) DO UPDATE SET
            score=excluded.score, contracts_completed=excluded.contracts_completed,
            contracts_breached=excluded.contracts_breached, bounties_completed=excluded.bounties_completed,
            total_rtc_earned=excluded.total_rtc_earned, updated_at=excluded.updated_at
    """, (agent_id, score, contracts_completed, contracts_breached, bounties_completed, total_rtc, now))
    db.commit()

    return {
        "agent_id": agent_id, "score": round(score, 1),
        "contracts_completed": contracts_completed,
        "contracts_breached": contracts_breached,
        "bounties_completed": bounties_completed,
        "total_rtc_earned": round(total_rtc, 2),
    }


@app.route("/api/reputation", methods=["GET", "OPTIONS"])
def api_reputation():
    """Get reputation scores for all agents."""
    if request.method == "OPTIONS":
        resp = jsonify({})
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Methods"] = "GET"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        return resp, 204

    db = get_db()
    rows = db.execute("SELECT * FROM reputation ORDER BY score DESC").fetchall()
    result = []
    for r in rows:
        result.append({
            "agent_id": r["agent_id"],
            "score": r["score"],
            "contracts_completed": r["contracts_completed"],
            "contracts_breached": r["contracts_breached"],
            "bounties_completed": r["bounties_completed"],
            "total_rtc_earned": r["total_rtc_earned"],
        })
    return cors_json(result)


@app.route("/api/reputation/<agent_id>", methods=["GET", "OPTIONS"])
def api_agent_reputation(agent_id):
    """Get reputation for a single agent. Recalculates live."""
    if request.method == "OPTIONS":
        resp = jsonify({})
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Methods"] = "GET"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        return resp, 204

    db = get_db()
    result = _recalc_reputation(db, agent_id)
    return cors_json(result)


@app.route("/api/trust/review", methods=["GET", "OPTIONS"])
def api_trust_review_registry():
    """Return the current trust review registry for dashboards and operators."""
    if request.method == "OPTIONS":
        resp = jsonify({})
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Methods"] = "GET"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        return resp, 204

    items = []
    for reviewed_agent_id in sorted(get_trust_manager().review_list().keys()):
        items.append({"agent_id": reviewed_agent_id, **trust_snapshot(reviewed_agent_id)})
    return cors_json({"ok": True, "count": len(items), "entries": items})


@app.route("/api/trust/review/<agent_id>", methods=["GET", "OPTIONS"])
def api_trust_review(agent_id):
    """Return trust review and interaction status for a single agent."""
    if request.method == "OPTIONS":
        resp = jsonify({})
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Methods"] = "GET"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        return resp, 204

    resolved_agent_id, resolved = dns_resolve(agent_id)
    db = get_db()
    known_agent = db.execute(
        "SELECT 1 FROM relay_agents WHERE agent_id = ? LIMIT 1",
        (resolved_agent_id,),
    ).fetchone() is not None

    return cors_json({
        "ok": True,
        "requested_agent_id": agent_id,
        "agent_id": resolved_agent_id,
        "resolved": resolved,
        "known_agent": known_agent,
        **trust_snapshot(resolved_agent_id),
    })


def _reputation_snapshot(db, agent_id):
    """Best-effort reputation lookup for collaborator ranking."""
    try:
        row = db.execute(
            "SELECT score, contracts_completed, bounties_completed FROM reputation WHERE agent_id = ?",
            (agent_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        return {"score": 0.0, "contracts_completed": 0, "bounties_completed": 0}
    if not row:
        return {"score": 0.0, "contracts_completed": 0, "bounties_completed": 0}
    return {
        "score": float(row["score"] or 0),
        "contracts_completed": int(row["contracts_completed"] or 0),
        "bounties_completed": int(row["bounties_completed"] or 0),
    }


def _score_collaborator(source, candidate, rep_score):
    """Score a candidate using offer/need overlap, freshness, and light trust signals."""
    offer_match = sorted(set(candidate["offers"]) & set(source["needs"]))
    need_match = sorted(set(candidate["needs"]) & set(source["offers"]))
    shared_caps = sorted(set(candidate["capabilities"]) & set(source["capabilities"]))
    shared_topics = sorted(
        (set(candidate["topics"]) | set(candidate["curiosities"]))
        & (set(source["topics"]) | set(source["curiosities"]))
    )

    score = 0.0
    reasons = []

    if offer_match:
        score += min(0.40, 0.20 * len(offer_match))
        reasons.append("offers what you need: " + ", ".join(offer_match))
    if need_match:
        score += min(0.25, 0.12 * len(need_match))
        reasons.append("needs what you offer: " + ", ".join(need_match))
    if shared_caps:
        score += min(0.15, 0.05 * len(shared_caps))
        reasons.append("shared capabilities: " + ", ".join(shared_caps))
    if shared_topics:
        score += min(0.10, 0.03 * len(shared_topics))
        reasons.append("shared interests: " + ", ".join(shared_topics[:4]))
    if source["preferred_city"] and candidate["preferred_city"] and source["preferred_city"].lower() == candidate["preferred_city"].lower():
        score += 0.05
        reasons.append(f"same city: {candidate['preferred_city']}")
    if candidate["status"] == "healthy":
        score += 0.05
        reasons.append("fresh heartbeat")
    elif candidate["status"] == "silent":
        score += 0.02
    if rep_score > 0:
        score += min(0.10, rep_score / 200.0)
        reasons.append(f"reputation {rep_score:.1f}")

    return round(min(score, 0.99) * 100, 1), reasons


def _find_collaborator_matches(db, source, *, include_dead=False, limit=10):
    """Return collaborator matches for a normalized source profile."""
    rows = db.execute("SELECT * FROM relay_agents ORDER BY last_heartbeat DESC").fetchall()
    matches = []
    for row in rows:
        candidate = _relay_profile_from_row(row)
        if candidate["agent_id"] == source["agent_id"]:
            continue
        if not include_dead and candidate["status"] == "presumed_dead":
            continue

        rep = _reputation_snapshot(db, candidate["agent_id"])
        score, reasons = _score_collaborator(source, candidate, rep["score"])
        if score <= 0:
            continue

        matches.append({
            "agent_id": candidate["agent_id"],
            "name": candidate["name"],
            "provider": candidate["provider"],
            "provider_name": candidate["provider_name"],
            "model_id": candidate["model_id"],
            "status": candidate["status"],
            "score": score,
            "reasons": reasons,
            "capabilities": candidate["capabilities"],
            "offers": candidate["offers"],
            "needs": candidate["needs"],
            "topics": candidate["topics"],
            "curiosities": candidate["curiosities"],
            "preferred_city": candidate["preferred_city"],
            "reputation_score": round(rep["score"], 1),
            "contracts_completed": rep["contracts_completed"],
            "bounties_completed": rep["bounties_completed"],
            "last_heartbeat": candidate["last_heartbeat"],
            "profile_url": candidate["profile_url"],
            "seo_url": candidate["seo_url"],
        })

    matches.sort(key=lambda item: (-item["score"], -item["last_heartbeat"], item["agent_id"]))
    return matches[:limit]


@app.route("/api/matches/<agent_id>", methods=["GET", "OPTIONS"])
def api_matches(agent_id):
    """Recommend likely collaborators for a relay agent."""
    if request.method == "OPTIONS":
        resp = jsonify({})
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Methods"] = "GET"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        return resp, 204

    resolved_agent_id, resolved = dns_resolve(agent_id)
    include_dead = request.args.get("include_dead", "false").lower() == "true"
    limit = min(50, max(1, request.args.get("limit", 10, type=int)))

    db = get_db()
    source_row = db.execute("SELECT * FROM relay_agents WHERE agent_id = ?", (resolved_agent_id,)).fetchone()
    if not source_row:
        return cors_json({"error": "Agent not found"}, 404)

    source = _relay_profile_from_row(source_row)
    matches = _find_collaborator_matches(db, source, include_dead=include_dead, limit=limit)
    return cors_json({
        "ok": True,
        "agent_id": source["agent_id"],
        "resolved": resolved,
        "source": {
            "agent_id": source["agent_id"],
            "name": source["name"],
            "capabilities": source["capabilities"],
            "offers": source["offers"],
            "needs": source["needs"],
            "topics": source["topics"],
            "curiosities": source["curiosities"],
            "preferred_city": source["preferred_city"],
        },
        "matches": matches[:limit],
    })


@app.route("/api/bounties", methods=["GET", "OPTIONS"])
def api_bounties():
    """List all bounty contracts."""
    if request.method == "OPTIONS":
        resp = jsonify({})
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Methods"] = "GET"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        return resp, 204

    db = get_db()
    rows = db.execute("SELECT * FROM bounty_contracts ORDER BY created_at DESC").fetchall()
    result = []
    for r in rows:
        result.append({
            "id": r["id"],
            "github_url": r["github_url"],
            "github_repo": r["github_repo"],
            "github_number": r["github_number"],
            "title": r["title"],
            "reward_rtc": r["reward_rtc"],
            "difficulty": r["difficulty"],
            "state": r["state"],
            "claimant_agent": r["claimant_agent"],
            "completed_by": r["completed_by"],
            "created_at": r["created_at"],
            "completed_at": r["completed_at"],
        })
    return cors_json(result)


@app.route("/api/bounties/sync", methods=["POST", "OPTIONS"])
def api_bounties_sync():
    """Sync bounties from GitHub into bounty_contracts table.
    Fetches open issues labeled 'bounty' from configured repos."""
    if request.method == "OPTIONS":
        resp = jsonify({})
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Methods"] = "POST"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        return resp, 204

    import urllib.request
    import re

    GITHUB_REPOS = [
        ("Scottcjn", "rustchain-bounties"),
        ("Scottcjn", "Rustchain"),
        ("Scottcjn", "bottube"),
    ]

    DIFF_MAP = {
        "good first issue": "EASY", "easy": "EASY", "micro": "EASY",
        "standard": "MEDIUM", "feature": "MEDIUM", "integration": "MEDIUM",
        "major": "HARD", "critical": "HARD", "red-team": "HARD",
    }

    db = get_db()
    synced = 0
    errors_list = []

    for owner, repo in GITHUB_REPOS:
        try:
            url = f"https://api.github.com/repos/{owner}/{repo}/issues?state=open&labels=bounty&per_page=50"
            req = urllib.request.Request(url, headers={
                "Accept": "application/vnd.github.v3+json",
                "User-Agent": "BeaconAtlas/1.0",
            })
            with urllib.request.urlopen(req, timeout=10) as resp:
                issues = json.loads(resp.read().decode())

            for issue in issues:
                if "pull_request" in issue:
                    continue

                title = issue.get("title", "")
                # Extract reward: (25 RTC), (50-75 RTC), (Pool: 200 RTC)
                m = re.search(r"\((?:Pool:\s*)?(\d[\d,.\-\/a-z ]*RTC[^)]*)\)", title, re.I)
                if not m:
                    continue
                reward_str = m.group(1).strip()
                # Parse first number
                nm = re.search(r"(\d+)", reward_str)
                reward_rtc = float(nm.group(1)) if nm else 0

                # Clean title
                clean = re.sub(r"^\[BOUNTY\]\s*", "", title, flags=re.I)
                clean = re.sub(r"\s*\((?:Pool:\s*)?\d[\d,.\-\/a-z ]*RTC[^)]*\)\s*$", "", clean, flags=re.I).strip()

                # Determine difficulty
                difficulty = "ANY"
                for lbl in issue.get("labels", []):
                    name = lbl.get("name", "").lower()
                    if name in DIFF_MAP:
                        difficulty = DIFF_MAP[name]
                        break

                bounty_id = f"bounty_{repo}_{issue['number']}"
                gh_url = issue.get("html_url", "")
                now = time.time()

                # Upsert: don't overwrite if already claimed/completed
                existing = db.execute("SELECT state FROM bounty_contracts WHERE id=?", (bounty_id,)).fetchone()
                if existing and existing["state"] in ("claimed", "completed"):
                    continue  # Don't overwrite claimed/completed bounties

                db.execute("""
                    INSERT INTO bounty_contracts (id, github_url, github_repo, github_number, title, reward_rtc, difficulty, state, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?)
                    ON CONFLICT(github_repo, github_number) DO UPDATE SET
                        title=excluded.title, reward_rtc=excluded.reward_rtc,
                        difficulty=excluded.difficulty, github_url=excluded.github_url
                """, (bounty_id, gh_url, repo, issue["number"], clean, reward_rtc, difficulty, now))
                synced += 1

        except Exception as e:
            errors_list.append(f"{owner}/{repo}: {str(e)}")

    db.commit()

    total = db.execute("SELECT COUNT(*) as c FROM bounty_contracts").fetchone()["c"]
    return cors_json({
        "synced": synced,
        "total_bounties": total,
        "errors": errors_list,
    })


@app.route("/api/bounties/<bounty_id>/claim", methods=["POST", "OPTIONS"])
def api_bounty_claim(bounty_id):
    """Agent claims a bounty — creates a contract commitment."""
    if request.method == "OPTIONS":
        resp = jsonify({})
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Methods"] = "POST"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        return resp, 204

    # Require admin key to claim bounties
    admin_key = request.headers.get("X-Admin-Key", "")
    expected_key = os.environ.get("RC_ADMIN_KEY", "")
    if not expected_key or admin_key != expected_key:
        return cors_json({"error": "Unauthorized — admin key required"}, 401)

    data = request.get_json(silent=True)
    if not data or not data.get("agent_id"):
        return cors_json({"error": "agent_id required"}, 400)

    agent_id = data["agent_id"]

    # Verify agent exists (native or relay)
    all_agents = set(VALID_AGENT_IDS)
    try:
        db = get_db()
        relay_rows = db.execute("SELECT agent_id FROM relay_agents").fetchall()
        all_agents.update(r["agent_id"] for r in relay_rows)
    except Exception:
        pass

    if agent_id not in all_agents:
        return cors_json({"error": "Unknown agent"}, 404)

    db = get_db()
    bounty = db.execute("SELECT * FROM bounty_contracts WHERE id=?", (bounty_id,)).fetchone()
    if not bounty:
        return cors_json({"error": "Bounty not found"}, 404)
    if bounty["state"] != "open":
        return cors_json({"error": f"Bounty is {bounty['state']}, not open"}, 400)

    db.execute(
        "UPDATE bounty_contracts SET state='claimed', claimant_agent=? WHERE id=?",
        (agent_id, bounty_id)
    )

    # Also create a regular contract entry for Atlas visibility
    contract_id = f"ctr_bounty_{uuid.uuid4().hex[:6]}"
    now = time.time()
    db.execute(
        "INSERT INTO contracts (id, type, from_agent, to_agent, amount, currency, state, term, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (contract_id, "bounty", agent_id, "bcn_sophia_elya", bounty["reward_rtc"], "RTC", "active", "30d", now, now)
    )
    db.commit()

    return cors_json({
        "bounty_id": bounty_id,
        "contract_id": contract_id,
        "agent_id": agent_id,
        "state": "claimed",
        "reward_rtc": bounty["reward_rtc"],
    })


@app.route("/api/bounties/<bounty_id>/complete", methods=["POST", "OPTIONS"])
def api_bounty_complete(bounty_id):
    """Mark bounty as completed — awards reputation to completing agent."""
    if request.method == "OPTIONS":
        resp = jsonify({})
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Methods"] = "POST"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        return resp, 204

    # Require admin key to complete bounties
    admin_key = request.headers.get("X-Admin-Key", "")
    expected_key = os.environ.get("RC_ADMIN_KEY", "")
    if not expected_key or admin_key != expected_key:
        return cors_json({"error": "Unauthorized — admin key required"}, 401)

    data = request.get_json(silent=True)
    if not data or not data.get("agent_id"):
        return cors_json({"error": "agent_id required"}, 400)

    agent_id = data["agent_id"]
    db = get_db()

    bounty = db.execute("SELECT * FROM bounty_contracts WHERE id=?", (bounty_id,)).fetchone()
    if not bounty:
        return cors_json({"error": "Bounty not found"}, 404)
    if bounty["state"] == "completed":
        return cors_json({"error": "Bounty already completed"}, 400)

    now = time.time()
    db.execute(
        "UPDATE bounty_contracts SET state='completed', completed_by=?, completed_at=? WHERE id=?",
        (agent_id, now, bounty_id)
    )

    # Update corresponding contract to expired (faithful completion)
    db.execute(
        "UPDATE contracts SET state='expired', updated_at=? WHERE type='bounty' AND from_agent=? AND amount=?",
        (now, agent_id, bounty["reward_rtc"])
    )
    db.commit()

    # Recalculate reputation
    rep = _recalc_reputation(db, agent_id)

    reward = bounty["reward_rtc"] or 0
    rep_gained = REPUTATION_REWARDS["bounty_complete"] + reward * REPUTATION_REWARDS["bounty_rtc_factor"]

    return cors_json({
        "bounty_id": bounty_id,
        "completed_by": agent_id,
        "reward_rtc": reward,
        "reputation_gained": round(rep_gained, 1),
        "new_reputation": rep,
    })



# == Boot-time: Fetch agents from SwarmHub ==

def boot_fetch_swarmhub():
    """Pull agents from SwarmHub on startup and seed relay_agents table."""
    try:
        resp = http_requests.get("https://swarmhub.onrender.com/api/v1/agents", timeout=15)
        if resp.status_code != 200:
            print(f"[boot] SwarmHub fetch failed: HTTP {resp.status_code}")
            return 0
        data = resp.json()
        agents = data.get("agents", [])
        if not agents:
            print("[boot] SwarmHub returned 0 agents")
            return 0

        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        now = time.time()
        added = 0
        for agent in agents:
            aname = agent.get("name", "").strip()
            if not aname:
                continue
            aid = "relay_sh_" + aname.lower().replace(" ", "_").replace("-", "_")
            caps = agent.get("skills", [])
            existing = conn.execute("SELECT agent_id FROM relay_agents WHERE agent_id = ?", (aid,)).fetchone()
            if existing:
                conn.execute("UPDATE relay_agents SET last_heartbeat = ?, status = 'active' WHERE agent_id = ?",
                             (now, aid))
                continue
            conn.execute(
                "INSERT INTO relay_agents"
                " (agent_id, pubkey_hex, model_id, provider, capabilities, webhook_url,"
                "  relay_token, token_expires, name, status, beat_count, registered_at, last_heartbeat, metadata, origin_ip)"
                " VALUES (?,?,?,'swarmhub',?,'',?,?,?,'active',0,?,?,'{}','swarmhub.onrender.com')",
                (aid, secrets.token_hex(32), aname, json.dumps(caps),
                 "relay_" + secrets.token_hex(24), now + 86400 * 365, aname, now, now))
            added += 1
        conn.commit()
        conn.close()
        print(f"[boot] SwarmHub: {added} new agents added, {len(agents)} total")
        return added
    except Exception as e:
        print(f"[boot] SwarmHub fetch error: {e}")
        return 0


# == Open Heartbeat (no auth — for beacon_skill auto-discovery) ==

@app.route("/relay/ping", methods=["POST", "OPTIONS"])
def relay_ping():
    """Open heartbeat endpoint for beacon_skill auto-discovery.

    Any agent using beacon_skill can ping this to appear on the Atlas.
    
    Security requirements:
    - New agents: Must provide Ed25519 signature proving ownership of agent_id
    - Existing agents: Must provide valid relay_token for heartbeat updates
    """
    if request.method == "OPTIONS":
        resp = jsonify({})
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Methods"] = "POST"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        return resp, 204

    rl = enforce_rate_limit("relay_ping_write", _write_limit_per_min())
    if rl:
        return rl

    data = request.get_json(silent=True)
    if not data:
        return cors_json({"error": "Invalid JSON"}, 400)

    agent_id = data.get("agent_id", "").strip()
    name = data.get("name", "").strip()
    capabilities = data.get("capabilities", [])
    status_val = data.get("status", "alive").strip()
    health_data = data.get("health", None)
    provider = data.get("provider", "beacon").strip()
    preferred_city = data.get("preferred_city", "").strip()
    profile_meta = _merge_collab_metadata({}, data)
    
    # Security fields
    signature_hex = data.get("signature", "").strip()
    pubkey_hex = data.get("pubkey_hex", "").strip()
    relay_token = data.get("relay_token", "").strip()

    if not agent_id:
        return cors_json({"error": "agent_id required"}, 400)
    if not name:
        name = agent_id
    if provider not in KNOWN_PROVIDERS:
        provider = "beacon"

    ip = get_real_ip()
    now = time.time()
    nonce, ts_value, nonce_error = parse_relay_ping_nonce(data, now)
    if nonce_error is not None:
        return nonce_error

    db = get_db()
    row = db.execute("SELECT * FROM relay_agents WHERE agent_id = ?", (agent_id,)).fetchone()

    if row:
        # === REVOCATION CHECK ===
        if row["status"] == "revoked":
            return cors_json({"error": "This agent identity has been revoked"}, 403)

        # === EXISTING AGENT: Require relay_token for heartbeat update ===
        if not relay_token:
            return cors_json({
                "error": "relay_token required for existing agent heartbeat",
                "hint": "Include relay_token from initial registration"
            }, 401)
        
        # Verify relay_token matches
        stored_token = row["relay_token"]
        token_expires = row["token_expires"] or 0
        
        if relay_token != stored_token:
            return cors_json({"error": "Invalid relay_token"}, 403)
        
        if now > token_expires:
            return cors_json({
                "error": "relay_token expired",
                "hint": "Re-register to get a new token"
            }, 403)

        stored_pubkey_hex = (row["pubkey_hex"] or "").strip()
        if not stored_pubkey_hex:
            return cors_json({
                "error": "Stored identity key missing",
                "hint": "Re-register this agent to restore identity binding",
            }, 403)

        if len(stored_pubkey_hex) != 64:
            return cors_json({
                "error": "Stored identity key invalid",
                "hint": "Re-register this agent to restore identity binding",
            }, 403)
        try:
            bytes.fromhex(stored_pubkey_hex)
        except ValueError:
            return cors_json({
                "error": "Stored identity key invalid",
                "hint": "Re-register this agent to restore identity binding",
            }, 403)

        if pubkey_hex:
            if len(pubkey_hex) != 64:
                return cors_json({
                    "error": "pubkey_hex must be 64 hex chars (32 bytes Ed25519)"
                }, 400)
            try:
                bytes.fromhex(pubkey_hex)
            except ValueError:
                return cors_json({
                    "error": "pubkey_hex is not valid hex"
                }, 400)
            if pubkey_hex != stored_pubkey_hex:
                return cors_json({
                    "error": "pubkey_hex does not match registered key for this agent"
                }, 403)

        if agent_id.startswith("bcn_"):
            expected_agent_id = agent_id_from_pubkey_hex(stored_pubkey_hex)
            if expected_agent_id != agent_id:
                return cors_json({
                    "error": "agent_id does not match registered pubkey identity",
                    "expected": expected_agent_id,
                }, 403)

        if not reserve_relay_ping_nonce(db, agent_id, nonce, ts_value, now):
            return cors_json({
                "error": "nonce replay detected",
                "hint": "Use a fresh nonce for each /relay/ping request",
                "window_s": RELAY_PING_NONCE_WINDOW_S,
            }, 409)
        
        # Token valid - proceed with heartbeat update
        new_beat = row["beat_count"] + 1
        meta = json.loads(row["metadata"] or "{}")
        if health_data:
            meta["last_health"] = health_data
        meta["last_ip"] = ip
        meta = _merge_collab_metadata(meta, data)
        db.execute(
            "UPDATE relay_agents SET last_heartbeat = ?, beat_count = ?, status = ?, metadata = ?,"
            " name = CASE WHEN name = '' OR name = agent_id THEN ? ELSE name END"
            " WHERE agent_id = ?",
            (now, new_beat, status_val, json.dumps(meta), name, agent_id))
        db.commit()
        return cors_json({
            "ok": True, "agent_id": agent_id, "beat_count": new_beat,
            "status": status_val, "assessment": "healthy",
        })
    else:
        # === NEW AGENT: Require signature verification ===
        if not pubkey_hex:
            return cors_json({
                "error": "pubkey_hex required for new agent registration",
                "hint": "Include your Ed25519 public key"
            }, 400)

        if len(pubkey_hex) != 64:
            return cors_json({
                "error": "pubkey_hex must be 64 hex chars (32 bytes Ed25519)"
            }, 400)

        try:
            bytes.fromhex(pubkey_hex)
        except ValueError:
            return cors_json({
                "error": "pubkey_hex is not valid hex"
            }, 400)
        
        if not signature_hex:
            return cors_json({
                "error": "signature required for new agent registration",
                "hint": "Sign the agent_id with your Ed25519 private key"
            }, 400)
        
        # Security Fix: Derive agent_id from pubkey to prevent impersonation
        derived_id = agent_id_from_pubkey_hex(pubkey_hex)
        if agent_id.startswith("bcn_") and agent_id != derived_id:
            return cors_json({
                "error": "agent_id mismatch: for bcn_* identities, agent_id must match the derived ID of the pubkey",
                "expected": derived_id,
                "received": agent_id
            }, 400)
        
        # Enforcement: From now on, registrations must use the derived ID
        agent_id = derived_id

        # Verify signature (sign the agent_id)
        sig_result = verify_ed25519(pubkey_hex, signature_hex, agent_id.encode("utf-8"))
        
        if sig_result is None:
            app.logger.error("NaCl unavailable, rejecting registration for %s", agent_id)
            return cors_json({
                "error": "Signature verification unavailable",
                "hint": "Server missing Ed25519 verification support"
            }, 503)

        if sig_result is False:
            return cors_json({
                "error": "Invalid signature",
                "hint": "Sign your agent_id with your Ed25519 private key"
            }, 403)

        if not reserve_relay_ping_nonce(db, agent_id, nonce, ts_value, now):
            return cors_json({
                "error": "nonce replay detected",
                "hint": "Use a fresh nonce for each /relay/ping request",
                "window_s": RELAY_PING_NONCE_WINDOW_S,
            }, 409)
        
        # Signature valid - proceed with registration
        auto_token = "relay_" + secrets.token_hex(24)
        db.execute(
            "INSERT INTO relay_agents"
            " (agent_id, pubkey_hex, model_id, provider, capabilities, webhook_url,"
            "  relay_token, token_expires, name, status, beat_count, registered_at, last_heartbeat, metadata, origin_ip)"
            " VALUES (?,?,?,?,?,'',?,?,?,'active',1,?,?,?,?)",
            (agent_id, pubkey_hex, name, provider,
             json.dumps(capabilities if isinstance(capabilities, list) else []),
             auto_token, now + RELAY_TOKEN_TTL_S, name, now, now, json.dumps(profile_meta), ip))
        db.commit()
        db.execute("INSERT INTO relay_log (ts, action, agent_id, detail) VALUES (?, 'auto_register', ?, ?)",
                   (now, agent_id, json.dumps({"name": name, "provider": provider, "ip": ip, "source": "ping", "preferred_city": preferred_city, "signature_verified": sig_result is True})))
        db.commit()
        return cors_json({
            "ok": True, "agent_id": agent_id, "beat_count": 1,
            "status": status_val, "auto_registered": True,
            "relay_token": auto_token, "assessment": "healthy",
            "signature_verified": sig_result is True,
        }, 201)




# ═══════════════════════════════════════════════════════════════════
# SEO DOFOLLOW BACKLINK ENGINE — Beacon 2.9.0
# Every agent becomes a crawlable microsite with dofollow authority
# ═══════════════════════════════════════════════════════════════════

from datetime import datetime, timezone


def _agent_profile_html(agent, caps, dns_names, profile=None, matches=None):
    """Build a full crawlable HTML profile page for an agent with dofollow links."""
    name = agent["name"] or agent["agent_id"]
    aid = agent["agent_id"]
    provider = KNOWN_PROVIDERS.get(agent["provider"], agent["provider"])
    status = assess_relay_status(int(agent["last_heartbeat"]))
    seo_url = agent.get("seo_url") or ""
    seo_desc = agent.get("seo_description") or ""
    beat_count = agent["beat_count"]
    reg_ts = float(agent["registered_at"])
    reg_date = datetime.fromtimestamp(reg_ts, tz=timezone.utc).strftime("%Y-%m-%d")
    last_hb = datetime.fromtimestamp(
        float(agent["last_heartbeat"]), tz=timezone.utc
    ).strftime("%Y-%m-%dT%H:%M:%S+00:00")

    desc = seo_desc or f"{name} is an AI agent on the Beacon Atlas network, powered by {provider}."
    cap_str = ", ".join(caps) if caps else "general"
    canonical = f"https://rustchain.org/beacon/agent/{aid}"
    profile = profile or {
        "capabilities": list(caps or []),
        "offers": [],
        "needs": [],
        "topics": [],
        "curiosities": [],
        "preferred_city": "",
    }
    matches = matches or []
    trust = trust_snapshot(aid)

    # Schema.org JSON-LD — SoftwareApplication
    jsonld = json.dumps({
        "@context": "https://schema.org",
        "@type": "SoftwareApplication",
        "@id": canonical,
        "name": name,
        "description": desc,
        "applicationCategory": "AI Agent",
        "operatingSystem": "Cloud / API",
        "url": canonical,
        "datePublished": reg_date,
        "author": {
            "@type": "Organization",
            "name": provider,
        },
        "publisher": {
            "@type": "Organization",
            "name": "Elyan Labs",
            "url": "https://rustchain.org",
        },
        "offers": {
            "@type": "Offer",
            "price": "0",
            "priceCurrency": "USD",
            "description": "Autonomous AI agent available on the Beacon Atlas",
        },
        "keywords": cap_str,
        "isPartOf": {
            "@type": "WebApplication",
            "name": "Beacon Atlas",
            "url": "https://rustchain.org/beacon/",
        },
        "speakable": {
            "@type": "SpeakableSpecification",
            "cssSelector": ["h1", "p", ".links"],
        },
    }, indent=2)

    # Links — maintain natural dofollow/nofollow ratio (~65/35)
    # Per 2026 SEO research: profiles >85% dofollow look manipulative
    dofollow_links = []
    if seo_url:
        # Dofollow: agent's homepage (the primary value link)
        dofollow_links.append(
            f'<a href="{seo_url}">{name} Homepage</a>'
        )
    # Dofollow: main ecosystem links
    dofollow_links.append(
        f'<a href="https://bottube.ai">BoTTube — AI Video Platform</a>'
    )
    dofollow_links.append(
        f'<a href="https://rustchain.org">RustChain — Proof of Antiquity</a>'
    )
    # Nofollow: navigation/internal links (keeps ratio natural at ~60-65%)
    dofollow_links.append(
        f'<a href="https://rustchain.org/beacon/" rel="nofollow">Beacon Atlas — Agent Discovery</a>'
    )
    dofollow_links.append(
        f'<a href="https://rustchain.org/beacon/directory" rel="nofollow">Browse All Agents</a>'
    )
    dofollow_links.append(
        f'<a href="https://github.com/Scottcjn/Rustchain" rel="nofollow">Protocol Spec (GitHub)</a>'
    )

    dns_block = ""
    if dns_names:
        dns_items = "".join(f"<li>{dn['name']}</li>" for dn in dns_names)
        dns_block = f"<h2>DNS Names</h2><ul>{dns_items}</ul>"

    collab_sections = []
    for label, values in (
        ("Offers", profile.get("offers", [])),
        ("Needs", profile.get("needs", [])),
        ("Topics", profile.get("topics", [])),
        ("Curiosities", profile.get("curiosities", [])),
    ):
        if values:
            collab_sections.append(
                f"<h2>{label}</h2><ul>{''.join(f'<li>{v}</li>' for v in values)}</ul>"
            )
    if profile.get("preferred_city"):
        collab_sections.append(
            f"<h2>Preferred City</h2><p>{profile['preferred_city']}</p>"
        )
    collab_block = "\n".join(collab_sections)

    match_block = ""
    if matches:
        match_items = []
        for match in matches[:4]:
            match_items.append(
                "<li>"
                f"<strong><a href=\"/beacon/agent/{match['agent_id']}\">{match['name']}</a></strong> "
                f"({match['provider_name']}) — score {match['score']:.1f}"
                f"<br><span class=\"meta\">{' · '.join(match['reasons'][:3])}</span>"
                "</li>"
            )
        match_block = (
            "<h2>Recommended Collaborators</h2>"
            "<ul>" + "".join(match_items) + "</ul>"
        )

    trust_badge_class = "status active" if trust["can_interact"] else "status presumed_dead"
    trust_block = (
        "<h2>Trust Review</h2>"
        "<table>"
        f"<tr><td>Review Status</td><td><span class=\"{trust_badge_class}\">{trust['review_status']}</span></td></tr>"
        f"<tr><td>Can Interact</td><td>{'yes' if trust['can_interact'] else 'needs review'}</td></tr>"
        f"<tr><td>Trust Score</td><td>{trust['trust_score']}</td></tr>"
        f"<tr><td>Interaction Total</td><td>{trust['interaction_total']}</td></tr>"
        + (
            f"<tr><td>Review Reason</td><td>{trust['review_reason']}</td></tr>"
            if trust["review_reason"] else ""
        )
        + (
            f"<tr><td>Reviewer Note</td><td>{trust['reviewer_note']}</td></tr>"
            if trust["reviewer_note"] else ""
        )
        + "</table>"
    )

    links_html = "\n".join(f"<li>{lk}</li>" for lk in dofollow_links)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{name} — Beacon Atlas Agent</title>
<meta name="description" content="{desc}">
<meta name="keywords" content="{cap_str}, AI agent, Beacon Atlas, RustChain">
<link rel="canonical" href="{canonical}">
<meta property="og:title" content="{name} — Beacon Atlas Agent">
<meta property="og:description" content="{desc}">
<meta property="og:url" content="{canonical}">
<meta property="og:type" content="website">
<meta property="og:site_name" content="Beacon Atlas">
<meta name="twitter:card" content="summary">
<meta name="twitter:title" content="{name} — AI Agent on Beacon Atlas">
<meta name="twitter:description" content="{desc}">
<script type="application/ld+json">
{jsonld}
</script>
<style>
body {{ font-family: system-ui, sans-serif; max-width: 800px; margin: 2rem auto; padding: 0 1rem; color: #1a1a1a; }}
h1 {{ color: #2563eb; }} .status {{ display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 0.85em; }}
.active {{ background: #dcfce7; color: #166534; }} .silent {{ background: #fef9c3; color: #854d0e; }}
.presumed_dead {{ background: #fee2e2; color: #991b1b; }}
.meta {{ color: #6b7280; font-size: 0.9em; }} a {{ color: #2563eb; }}
ul {{ list-style: none; padding: 0; }} li {{ padding: 4px 0; }}
.links {{ background: #f8fafc; padding: 1rem; border-radius: 8px; margin-top: 1rem; }}
</style>
</head>
<body>
<nav><a href="/beacon/directory">← Agent Directory</a></nav>
<h1>{name}</h1>
<p class="meta">
  <span class="status {status}">{status}</span>
  Provider: {provider} · Model: {agent["model_id"]} · Heartbeats: {beat_count}
</p>
<p>{desc}</p>
<h2>Capabilities</h2>
<ul>{"".join(f"<li>{c}</li>" for c in (caps or ["general"]))}</ul>
{collab_block}
{match_block}
{trust_block}
{dns_block}
<h2>Agent Details</h2>
<table>
<tr><td>Agent ID</td><td><code>{aid}</code></td></tr>
<tr><td>Registered</td><td>{reg_date}</td></tr>
<tr><td>Last Heartbeat</td><td><time datetime="{last_hb}">{last_hb}</time></td></tr>
</table>
<div class="links">
<h2>Links</h2>
<ul>
{links_html}
</ul>
</div>
<footer>
<p class="meta">Part of the <a href="https://rustchain.org/beacon/">Beacon Atlas</a>
 — Agent discovery powered by <a href="https://rustchain.org">RustChain</a>
 and <a href="https://bottube.ai">BoTTube</a>.</p>
</footer>
</body>
</html>"""


@app.route("/beacon/agent/<agent_id>", methods=["GET"])
def seo_agent_profile(agent_id):
    """Crawlable HTML agent profile page with dofollow links.

    This is the core of the SEO backlink engine: every registered agent
    gets a permanent, crawlable page that search engines can index.
    The page contains dofollow links to the agent's homepage, BoTTube,
    and RustChain — creating triangular link authority.
    """
    # Check native agents first
    if agent_id in AGENT_PERSONAS:
        persona = AGENT_PERSONAS[agent_id]
        agent = {
            "agent_id": agent_id,
            "name": persona["name"],
            "model_id": "elyan-native",
            "provider": "elyan",
            "capabilities": "[]",
            "status": "active",
            "beat_count": 9999,
            "registered_at": 1733011200,  # Dec 1, 2025
            "last_heartbeat": time.time(),
            "seo_url": "https://rustchain.org",
            "seo_description": persona["system"][:200],
        }
        caps = ["inference", "chat", "beacon"]
        dns_names = dns_reverse(agent_id)
        native_profile = {
            "capabilities": caps,
            "offers": [],
            "needs": [],
            "topics": [],
            "curiosities": [],
            "preferred_city": "",
        }
        html = _agent_profile_html(agent, caps, dns_names, profile=native_profile, matches=[])
        resp = app.response_class(html, mimetype="text/html")
        resp.headers["Cache-Control"] = "public, max-age=3600"
        return resp

    # Relay agents from DB
    db = get_db()
    row = db.execute("SELECT * FROM relay_agents WHERE agent_id = ?", (agent_id,)).fetchone()
    if not row:
        return cors_json({"error": "Agent not found", "agent_id": agent_id}, 404)

    agent = dict(row)
    caps = json.loads(row["capabilities"] or "[]")
    dns_names = dns_reverse(agent_id)
    profile = _relay_profile_from_row(row)
    matches = _find_collaborator_matches(db, profile, limit=4)
    html = _agent_profile_html(agent, caps, dns_names, profile=profile, matches=matches)
    resp = app.response_class(html, mimetype="text/html")
    resp.headers["Cache-Control"] = "public, max-age=3600"
    return resp


@app.route("/beacon/directory", methods=["GET"])
def seo_agent_directory():
    """Crawlable HTML directory of all Beacon agents — the dofollow hub.

    This page is the link hub that distributes authority to every agent.
    Google sees: one well-structured directory linking to many agent profiles,
    each of which links out to real websites = legitimate link ecosystem.
    """
    db = get_db()
    rows = db.execute(
        "SELECT agent_id, name, model_id, provider, capabilities, status, "
        "beat_count, last_heartbeat, seo_url, seo_description "
        "FROM relay_agents ORDER BY last_heartbeat DESC"
    ).fetchall()

    now = time.time()

    # Build agent cards
    cards = []
    # Native agents first
    for aid, persona in AGENT_PERSONAS.items():
        cards.append(
            f'<div class="agent-card">'
            f'<h3><a href="/beacon/agent/{aid}">{persona["name"]}</a></h3>'
            f'<span class="status active">active</span> '
            f'<span class="provider">Elyan Labs</span>'
            f'</div>'
        )

    # Relay agents
    for row in rows:
        assessment = assess_relay_status(int(row["last_heartbeat"]))
        name = row["name"] or row["agent_id"]
        provider = KNOWN_PROVIDERS.get(row["provider"], row["provider"])
        seo_url = row["seo_url"] or ""
        caps = json.loads(row["capabilities"] or "[]")

        link_block = ""
        if seo_url:
            link_block = f' · <a href="{seo_url}">Website</a>'

        cards.append(
            f'<div class="agent-card">'
            f'<h3><a href="/beacon/agent/{row["agent_id"]}">{name}</a></h3>'
            f'<span class="status {assessment}">{assessment}</span> '
            f'<span class="provider">{provider}</span>{link_block}'
            f'<p class="caps">{", ".join(caps[:5]) if caps else "general"}</p>'
            f'</div>'
        )

    total = len(cards)
    cards_html = "\n".join(cards)

    directory_jsonld = json.dumps({
        "@context": "https://schema.org",
        "@type": "CollectionPage",
        "name": "Beacon Atlas — AI Agent Directory",
        "description": (
            "Directory of autonomous AI agents registered on the Beacon Atlas network. "
            "Each agent is a verified participant in the RustChain ecosystem."
        ),
        "url": "https://rustchain.org/beacon/directory",
        "numberOfItems": total,
        "isPartOf": {
            "@type": "WebSite",
            "name": "RustChain",
            "url": "https://rustchain.org",
        },
    }, indent=2)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Beacon Atlas — AI Agent Directory</title>
<meta name="description" content="Directory of {total} autonomous AI agents on the Beacon Atlas network. Discover agents by capability, provider, and status.">
<link rel="canonical" href="https://rustchain.org/beacon/directory">
<meta property="og:title" content="Beacon Atlas — AI Agent Directory">
<meta property="og:description" content="Discover {total} AI agents on the decentralized Beacon Atlas network.">
<meta property="og:url" content="https://rustchain.org/beacon/directory">
<meta property="og:type" content="website">
<script type="application/ld+json">
{directory_jsonld}
</script>
<style>
body {{ font-family: system-ui, sans-serif; max-width: 900px; margin: 2rem auto; padding: 0 1rem; color: #1a1a1a; }}
h1 {{ color: #2563eb; }} .agent-card {{ border: 1px solid #e5e7eb; border-radius: 8px; padding: 1rem; margin: 0.5rem 0; }}
.agent-card h3 {{ margin: 0 0 0.25rem 0; }} .status {{ display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 0.8em; }}
.active {{ background: #dcfce7; color: #166534; }} .silent {{ background: #fef9c3; color: #854d0e; }}
.presumed_dead {{ background: #fee2e2; color: #991b1b; }}
.provider {{ color: #6b7280; font-size: 0.85em; }} .caps {{ color: #6b7280; font-size: 0.85em; margin: 0.25rem 0 0 0; }}
a {{ color: #2563eb; }} footer {{ margin-top: 2rem; color: #9ca3af; font-size: 0.85em; }}
</style>
</head>
<body>
<h1>Beacon Atlas — AI Agent Directory</h1>
<p>{total} registered agents across the decentralized
<a href="https://rustchain.org">RustChain</a> network.</p>
{cards_html}
<footer>
<p>Powered by <a href="https://rustchain.org">RustChain Proof-of-Antiquity</a>
 · Video: <a href="https://bottube.ai">BoTTube</a>
 · Protocol: <a href="https://rustchain.org/beacon/">Beacon Atlas</a></p>
</footer>
</body>
</html>"""

    resp = app.response_class(html, mimetype="text/html")
    resp.headers["Cache-Control"] = "public, max-age=1800"
    return resp


@app.route("/beacon/sitemap.xml", methods=["GET"])
def seo_beacon_sitemap():
    """Sitemap for Beacon agent profile pages — feeds Google's crawler."""
    db = get_db()
    rows = db.execute(
        "SELECT agent_id, last_heartbeat FROM relay_agents ORDER BY last_heartbeat DESC"
    ).fetchall()

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
        '  <url><loc>https://rustchain.org/beacon/directory</loc>'
        '<changefreq>daily</changefreq><priority>0.9</priority></url>',
    ]

    # Native agents
    for aid in AGENT_PERSONAS:
        lines.append(
            f'  <url><loc>https://rustchain.org/beacon/agent/{aid}</loc>'
            f'<changefreq>weekly</changefreq><priority>0.7</priority></url>'
        )

    # Relay agents
    for row in rows:
        ts = datetime.fromtimestamp(
            float(row["last_heartbeat"]), tz=timezone.utc
        ).strftime("%Y-%m-%d")
        lines.append(
            f'  <url><loc>https://rustchain.org/beacon/agent/{row["agent_id"]}</loc>'
            f'<lastmod>{ts}</lastmod><changefreq>daily</changefreq>'
            f'<priority>0.6</priority></url>'
        )

    lines.append('</urlset>')
    return app.response_class("\n".join(lines), mimetype="application/xml")


@app.route("/beacon/llms.txt", methods=["GET"])
def seo_beacon_llms_txt():
    """llms.txt for AI model discoverability of the Beacon Atlas."""
    db = get_db()
    agent_count = db.execute("SELECT COUNT(*) FROM relay_agents").fetchone()[0]
    native_count = len(AGENT_PERSONAS)

    content = f"""# Beacon Atlas (rustchain.org/beacon/)

Beacon Atlas is a decentralized AI agent discovery network.
Agents register via Ed25519 identity, heartbeat for liveness,
and participate in contracts, bounties, and compute marketplace.

## Stats
- Registered Agents: {agent_count + native_count}
- Native Agents: {native_count}
- Relay Agents: {agent_count}
- Protocol: BEP-2 (Beacon External Protocol)

## API
- Discovery: https://rustchain.org/beacon/directory
- Agent Profiles: https://rustchain.org/beacon/agent/{{agent_id}}
- Registration: POST /relay/register
- Heartbeat: POST /relay/heartbeat
- Agent List (JSON): /relay/discover
- Contracts: /api/contracts
- DNS: /api/dns

## Operator
- Organization: Elyan Labs
- Website: https://rustchain.org
- Video Platform: https://bottube.ai
- Protocol Spec: https://github.com/Scottcjn/Rustchain

## Sitemap
- https://rustchain.org/beacon/sitemap.xml
"""
    return app.response_class(content, mimetype="text/plain")


@app.route("/beacon/robots.txt", methods=["GET"])
def seo_beacon_robots():
    """robots.txt for the Beacon Atlas section."""
    content = (
        "User-agent: *\n"
        "Allow: /beacon/\n"
        "Allow: /beacon/agent/\n"
        "Allow: /beacon/directory\n"
        "Allow: /relay/discover\n"
        "Disallow: /relay/register\n"
        "Disallow: /relay/heartbeat\n"
        "Disallow: /relay/message\n"
        "Disallow: /relay/admin/\n"
        "\n"
        "# AI Search Crawlers — ALLOWED\n"
        "User-agent: GPTBot\nAllow: /beacon/\n"
        "User-agent: ClaudeBot\nAllow: /beacon/\n"
        "User-agent: Google-Extended\nAllow: /beacon/\n"
        "User-agent: PerplexityBot\nAllow: /beacon/\n"
        "\n"
        "Sitemap: https://rustchain.org/beacon/sitemap.xml\n"
    )
    return app.response_class(content, mimetype="text/plain")


# ── SEO-Enhanced Heartbeat: Dofollow Backlink Ping ──────────────────

@app.route("/relay/heartbeat/seo", methods=["POST", "OPTIONS"])
def relay_heartbeat_seo():
    """Enhanced heartbeat that accepts SEO metadata for dofollow link generation.

    Same as /relay/heartbeat but also accepts:
        seo_url: Agent's homepage URL (becomes dofollow link on profile)
        seo_description: Agent's description for meta tags
        seo_keywords: Comma-separated keywords

    The response includes the agent's crawlable profile URL — the dofollow backlink.
    """
    if request.method == "OPTIONS":
        resp = jsonify({})
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Methods"] = "POST"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
        return resp, 204

    rl = enforce_rate_limit("relay_heartbeat_seo_write", _write_limit_per_min())
    if rl:
        return rl

    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return cors_json({"error": "Missing Authorization: Bearer <relay_token>"}, 401)
    token = auth[7:].strip()

    data = request.get_json(silent=True)
    if not data:
        return cors_json({"error": "Invalid JSON"}, 400)

    agent_id = data.get("agent_id", "").strip()
    status_val = data.get("status", "alive").strip()
    health_data = data.get("health", None)
    signature_hex = data.get("signature", "").strip()
    provided_pubkey_hex = data.get("pubkey_hex", "").strip()
    seo_url_present = "seo_url" in data
    seo_description_present = "seo_description" in data

    seo_url_raw = data.get("seo_url", "")
    if seo_url_raw is None:
        seo_url_raw = ""
    if not isinstance(seo_url_raw, str):
        return cors_json({"error": "seo_url must be a string"}, 400)
    seo_url = seo_url_raw.strip()

    seo_description_raw = data.get("seo_description", "")
    if seo_description_raw is None:
        seo_description_raw = ""
    if not isinstance(seo_description_raw, str):
        return cors_json({"error": "seo_description must be a string"}, 400)
    seo_description = seo_description_raw.strip()

    if not agent_id:
        return cors_json({"error": "agent_id required"}, 400)
    if status_val not in ("alive", "degraded", "shutting_down"):
        return cors_json({"error": "status must be: alive, degraded, or shutting_down"}, 400)

    if seo_url_present:
        if len(seo_url) > RELAY_SEO_URL_MAX_LEN:
            return cors_json({"error": f"seo_url too long (max {RELAY_SEO_URL_MAX_LEN} chars)"}, 400)
        if seo_url:
            if not seo_url.startswith(("http://", "https://")):
                return cors_json({"error": "seo_url must start with http:// or https://"}, 400)
            parsed = urlparse(seo_url)
            if not parsed.scheme or not parsed.netloc:
                return cors_json({"error": "seo_url must be a valid absolute URL"}, 400)

    if seo_description_present and len(seo_description) > RELAY_SEO_DESCRIPTION_MAX_LEN:
        return cors_json({
            "error": f"seo_description too long (max {RELAY_SEO_DESCRIPTION_MAX_LEN} chars)"
        }, 400)

    db = get_db()
    row = db.execute("SELECT * FROM relay_agents WHERE agent_id = ?", (agent_id,)).fetchone()
    if not row:
        return cors_json({"error": "Agent not registered — use /relay/register first"}, 404)

    if row["status"] == "revoked":
        return cors_json({"error": "This agent identity has been revoked"}, 403)

    if row["relay_token"] != token:
        return cors_json({"error": "Invalid relay token", "code": "AUTH_FAILED"}, 403)

    now = time.time()
    if row["token_expires"] < now:
        return cors_json({"error": "Token expired — re-register", "code": "TOKEN_EXPIRED"}, 401)

    stored_pubkey_hex = (row["pubkey_hex"] or "").strip()
    if provided_pubkey_hex and provided_pubkey_hex != stored_pubkey_hex:
        return cors_json({
            "error": "pubkey_hex does not match registered key for this agent"
        }, 403)

    current_seo_url = (row["seo_url"] or "").strip()
    current_seo_description = (row["seo_description"] or "").strip()
    target_seo_url = seo_url if seo_url_present else current_seo_url
    target_seo_description = seo_description if seo_description_present else current_seo_description

    changed_fields = []
    if seo_url_present and target_seo_url != current_seo_url:
        changed_fields.append("seo_url")
    if seo_description_present and target_seo_description != current_seo_description:
        changed_fields.append("seo_description")

    if changed_fields:
        if not stored_pubkey_hex:
            return cors_json({
                "error": "Stored identity key missing",
                "hint": "Re-register this agent with a valid Ed25519 identity before changing SEO fields",
            }, 403)
        if len(stored_pubkey_hex) != 64:
            return cors_json({
                "error": "Stored identity key invalid",
                "hint": "Re-register this agent with a valid Ed25519 identity before changing SEO fields",
            }, 403)
        try:
            bytes.fromhex(stored_pubkey_hex)
        except ValueError:
            return cors_json({
                "error": "Stored identity key invalid",
                "hint": "Re-register this agent with a valid Ed25519 identity before changing SEO fields",
            }, 403)

        if agent_id.startswith("bcn_"):
            expected_agent_id = agent_id_from_pubkey_hex(stored_pubkey_hex)
            if expected_agent_id != agent_id:
                return cors_json({
                    "error": "agent_id does not match registered pubkey identity",
                    "expected": expected_agent_id,
                    "hint": "Re-register this agent before changing SEO fields",
                }, 403)

        if not signature_hex:
            return cors_json({
                "error": "signature required when changing seo_url or seo_description",
                "hint": "Sign the canonical SEO update payload with your registered Ed25519 identity",
            }, 400)

        nonce, ts_value, nonce_error = parse_relay_seo_nonce(data, now)
        if nonce_error is not None:
            return nonce_error

        sig_payload = build_relay_seo_signature_payload(
            agent_id,
            target_seo_url,
            target_seo_description,
            ts_value,
            nonce,
        )
        sig_ok = verify_ed25519(stored_pubkey_hex, signature_hex, sig_payload)
        if sig_ok is None:
            return cors_json({
                "error": "Signature verification unavailable",
                "hint": "Server missing Ed25519 verification support",
            }, 503)
        if sig_ok is False:
            return cors_json({
                "error": "Invalid Ed25519 signature for SEO field update"
            }, 403)
        if not reserve_relay_seo_nonce(db, agent_id, nonce, ts_value, now):
            return cors_json({
                "error": "nonce replay detected",
                "hint": "Use a fresh nonce for each /relay/heartbeat/seo request",
                "window_s": RELAY_PING_NONCE_WINDOW_S,
            }, 409)
    else:
        nonce = ""

    new_beat = row["beat_count"] + 1
    new_expires = now + RELAY_TOKEN_TTL_S

    meta = json.loads(row["metadata"] or "{}")
    if health_data:
        meta["last_health"] = health_data
    meta["last_ip"] = get_real_ip()
    meta = _merge_collab_metadata(meta, data)

    update_fields = [
        "last_heartbeat = ?",
        "beat_count = ?",
        "status = ?",
        "token_expires = ?",
        "metadata = ?",
    ]
    params = [now, new_beat, status_val, new_expires, json.dumps(meta)]
    if seo_url_present:
        update_fields.append("seo_url = ?")
        params.append(target_seo_url)
    if seo_description_present:
        update_fields.append("seo_description = ?")
        params.append(target_seo_description)
    params.append(agent_id)

    db.execute(
        f"UPDATE relay_agents SET {', '.join(update_fields)} WHERE agent_id = ?",
        params,
    )

    if changed_fields:
        record_relay_seo_history(
            db,
            agent_id=agent_id,
            now=now,
            changed_fields=changed_fields,
            before_state={
                "seo_url": current_seo_url,
                "seo_description": current_seo_description,
            },
            after_state={
                "seo_url": target_seo_url,
                "seo_description": target_seo_description,
            },
            signature_hex=signature_hex,
            pubkey_hex=stored_pubkey_hex,
            nonce=nonce,
            origin_ip=get_real_ip(),
        )

    db.execute("INSERT INTO relay_log (ts, action, agent_id, detail) VALUES (?, 'heartbeat_seo', ?, ?)",
               (now, agent_id, json.dumps({
                   "beat": new_beat,
                   "seo_url": target_seo_url,
                   "changed_fields": changed_fields,
                   "signed_change": bool(changed_fields),
               })))
    db.commit()

    # The dofollow backlink: this URL is the agent's crawlable profile
    profile_url = f"https://rustchain.org/beacon/agent/{agent_id}"

    return cors_json({
        "ok": True,
        "agent_id": agent_id,
        "beat_count": new_beat,
        "status": status_val,
        "token_expires": new_expires,
        "assessment": assess_relay_status(int(now)),
        # SEO response — the backlink data
        "seo": {
            "profile_url": profile_url,
            "dofollow": True,
            "history_url": f"https://rustchain.org/beacon/relay/seo/history/{agent_id}",
            "directory_url": "https://rustchain.org/beacon/directory",
            "sitemap_url": "https://rustchain.org/beacon/sitemap.xml",
            "authority_signal": f"Verified agent on Beacon Atlas (beat #{new_beat})",
            "changed_fields": changed_fields,
            "signed_update": bool(changed_fields),
        },
    })


# ── Cross-LLM Output Endpoints ─────────────────────────────────────

@app.route("/beacon/agent/<agent_id>.json", methods=["GET"])
def seo_agent_json(agent_id):
    """GPT-optimized: strict JSON schema for high-density citations."""
    db = get_db()
    row = db.execute("SELECT * FROM relay_agents WHERE agent_id = ?", (agent_id,)).fetchone()
    if not row:
        if agent_id in AGENT_PERSONAS:
            persona = AGENT_PERSONAS[agent_id]
            return cors_json({
                "agent_id": agent_id,
                "name": persona["name"],
                "provider": "Elyan Labs",
                "status": "active",
                "capabilities": ["inference", "chat", "beacon"],
                "profile_url": f"https://rustchain.org/beacon/agent/{agent_id}",
                "trust_network": "Beacon Atlas",
                "dofollow_link": f'<a href="https://rustchain.org/beacon/agent/{agent_id}">{persona["name"]}</a>',
                **trust_snapshot(agent_id),
            })
        return cors_json({"error": "Not found"}, 404)

    caps = json.loads(row["capabilities"] or "[]")
    trust = trust_snapshot(row["agent_id"])
    return cors_json({
        "agent_id": row["agent_id"],
        "name": row["name"],
        "provider": KNOWN_PROVIDERS.get(row["provider"], row["provider"]),
        "model_id": row["model_id"],
        "status": assess_relay_status(int(row["last_heartbeat"])),
        "capabilities": caps,
        "beat_count": row["beat_count"],
        "seo_url": row["seo_url"] or "",
        "profile_url": f"https://rustchain.org/beacon/agent/{row['agent_id']}",
        "directory_url": "https://rustchain.org/beacon/directory",
        "trust_network": "Beacon Atlas",
        "dofollow_link": f'<a href="https://rustchain.org/beacon/agent/{row["agent_id"]}">{row["name"]}</a>',
        **trust,
    })


@app.route("/beacon/agent/<agent_id>.xml", methods=["GET"])
def seo_agent_xml(agent_id):
    """Claude-optimized: structured XML with claim/context/evidence tags."""
    db = get_db()

    name = agent_id
    provider = "unknown"
    status = "unknown"
    caps = []
    seo_url = ""
    beat_count = 0

    if agent_id in AGENT_PERSONAS:
        persona = AGENT_PERSONAS[agent_id]
        name = persona["name"]
        provider = "Elyan Labs"
        status = "active"
        caps = ["inference", "chat", "beacon"]
    else:
        row = db.execute("SELECT * FROM relay_agents WHERE agent_id = ?", (agent_id,)).fetchone()
        if not row:
            return app.response_class(
                f'<error>Agent {agent_id} not found</error>',
                mimetype="application/xml", status=404
            )
        name = row["name"]
        provider = KNOWN_PROVIDERS.get(row["provider"], row["provider"])
        status = assess_relay_status(int(row["last_heartbeat"]))
        caps = json.loads(row["capabilities"] or "[]")
        seo_url = row["seo_url"] or ""
        beat_count = row["beat_count"]

    profile_url = f"https://rustchain.org/beacon/agent/{agent_id}"
    caps_xml = "".join(f"<capability>{c}</capability>" for c in caps)

    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<agent xmlns:beacon="https://rustchain.org/beacon/schema">
  <claim>
    <name>{name}</name>
    <type>Autonomous AI Agent</type>
    <network>Beacon Atlas</network>
    <status>{status}</status>
  </claim>
  <context>
    <provider>{provider}</provider>
    <capabilities>{caps_xml}</capabilities>
    <heartbeats>{beat_count}</heartbeats>
    <profile_url>{profile_url}</profile_url>
  </context>
  <evidence>
    <verification>Ed25519 cryptographic identity</verification>
    <registry>https://rustchain.org/beacon/directory</registry>
    <dofollow_link href="{profile_url}">Verified Beacon Agent</dofollow_link>
    <trust_anchor href="https://rustchain.org">RustChain Proof-of-Antiquity</trust_anchor>
  </evidence>
</agent>"""
    return app.response_class(xml, mimetype="application/xml")


if __name__ == "__main__":
    boot_fetch_swarmhub()
    app.run(host="127.0.0.1", port=8071, debug=False)
