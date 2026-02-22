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
import uuid
import sqlite3
from collections import OrderedDict
import requests as http_requests
from flask import Flask, request, jsonify, g

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


def agent_id_from_pubkey_hex(pubkey_hex):
    """Derive bcn_ agent ID from 64-char hex public key. No nacl needed."""
    pubkey_bytes = bytes.fromhex(pubkey_hex)
    return "bcn_" + hashlib.sha256(pubkey_bytes).hexdigest()[:12]


def verify_ed25519(pubkey_hex, signature_hex, data_bytes):
    """Verify Ed25519 signature. Returns True/False, or None if nacl unavailable."""
    if not HAS_NACL:
        return None  # Cannot verify — accept on trust
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
            metadata TEXT DEFAULT '{}'
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
    # BEP-DNS: Beacon DNS name resolution
    conn.execute("""
        CREATE TABLE IF NOT EXISTS beacon_dns (
            name TEXT PRIMARY KEY,
            agent_id TEXT NOT NULL,
            owner TEXT DEFAULT '',
            created_at REAL NOT NULL
        )
    """)
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


class BoundedRateLimiter:
    """Per-key fixed-window rate limiter with bounded, TTL-cleaned storage."""

    def __init__(self, *, max_entries=10000, ttl_seconds=900, cleanup_interval_seconds=30):
        self.max_entries = max_entries
        self.ttl_seconds = ttl_seconds
        self.cleanup_interval_seconds = cleanup_interval_seconds
        self._entries = OrderedDict()  # key -> {window_start, count, last_seen}
        self._last_cleanup = 0.0

    def _cleanup(self, now):
        stale_before = now - self.ttl_seconds
        stale_keys = [k for k, v in self._entries.items() if v["last_seen"] < stale_before]
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


ATLAS_RATE_LIMITER = BoundedRateLimiter(
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

# --- LLM Configuration ---
OLLAMA_URL = "http://100.75.100.89:11434/api/chat"
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

    rl = enforce_rate_limit("api_chat_write", _write_limit_per_min())
    if rl:
        return rl

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

    rl = enforce_rate_limit("api_contracts_read", _read_limit_per_min())
    if rl:
        return rl

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
    rl = enforce_rate_limit("api_contracts_write", _write_limit_per_min())
    if rl:
        return rl

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

    rl = enforce_rate_limit("api_contracts_write_patch", _write_limit_per_min())
    if rl:
        return rl

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
        if sig_verified is False:
            return cors_json({"error": "Invalid Ed25519 signature"}, 403)

    # Derive agent_id
    agent_id = agent_id_from_pubkey_hex(pubkey_hex)

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
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', 0, ?, ?, '{}', ?)
        ON CONFLICT(agent_id) DO UPDATE SET
            model_id=excluded.model_id, provider=excluded.provider,
            capabilities=excluded.capabilities, webhook_url=excluded.webhook_url,
            relay_token=excluded.relay_token, token_expires=excluded.token_expires,
            name=excluded.name, status='active', last_heartbeat=excluded.last_heartbeat
    """, (agent_id, pubkey_hex, model_id, provider,
          json.dumps(capabilities), webhook_url, token,
          token_expires, name, now, now, ip))
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
            " VALUES (?,?,?,?,?,'',?,?,?,'active',1,?,?,'{}',?)",
            (agent_id, hb_pubkey, hb_name, hb_provider,
             json.dumps(hb_caps if isinstance(hb_caps, list) else []),
             auto_token, now + RELAY_TOKEN_TTL_S, hb_name, now, now, hb_ip))
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

    now = time.time()
    if row["token_expires"] < now:
        return cors_json({"error": "Token expired — re-register", "code": "TOKEN_EXPIRED"}, 401)

    new_beat = row["beat_count"] + 1
    new_expires = now + RELAY_TOKEN_TTL_S

    # Update metadata with health if provided
    meta = json.loads(row["metadata"] or "{}")
    if health_data:
        meta["last_health"] = health_data
    meta["last_ip"] = get_real_ip()

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
    rl = enforce_rate_limit("api_dns_write", _write_limit_per_min())
    if rl:
        return rl

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
    if admin_key != "rustchain_admin_key_2025_secure64":
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
        if capability_filter and capability_filter not in caps:
            continue

        results.append({
            "agent_id": row["agent_id"],
            "model_id": row["model_id"],
            "provider": row["provider"],
            "provider_name": KNOWN_PROVIDERS.get(row["provider"], row["provider"]),
            "capabilities": caps,
            "name": row["name"],
            "status": assessment,
            "beat_count": row["beat_count"],
            "registered_at": row["registered_at"],
            "last_heartbeat": row["last_heartbeat"],
            "relay": True,
            "preferred_city": json.loads(row["metadata"] or "{}").get("preferred_city", ""),
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
    meta = json.loads(row["metadata"] or "{}")

    return cors_json({
        "agent_id": row["agent_id"],
        "model_id": row["model_id"],
        "provider": row["provider"],
        "provider_name": KNOWN_PROVIDERS.get(row["provider"], row["provider"]),
        "capabilities": caps,
        "name": row["name"],
        "status": assess_relay_status(int(row["last_heartbeat"])),
        "beat_count": row["beat_count"],
        "registered_at": row["registered_at"],
        "last_heartbeat": row["last_heartbeat"],
        "health": meta.get("last_health"),
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
            "discover": "/relay/discover",
            "message": "/relay/message",
            "status": "/relay/status/{agent_id}",
            "contracts": "/api/contracts",
            "chat": "/api/chat",
        },
        "stats": {
            "relay_agents": agent_count,
            "contracts": contract_count,
            "native_agents": len(VALID_AGENT_IDS),
        },
        "crypto": "Ed25519" if HAS_NACL else "unavailable (install PyNaCl)",
        "operator": "Elyan Labs",
        "atlas_url": "http://50.28.86.131:8070/beacon/",
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


@app.route("/api/bounties", methods=["GET", "OPTIONS"])
def api_bounties():
    """List all bounty contracts."""
    if request.method == "OPTIONS":
        resp = jsonify({})
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Methods"] = "GET"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        return resp, 204

    rl = enforce_rate_limit("api_bounties_read", _read_limit_per_min())
    if rl:
        return rl

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

    rl = enforce_rate_limit("api_bounties_sync_write", _write_limit_per_min())
    if rl:
        return rl

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

    rl = enforce_rate_limit("api_bounties_claim_write", _write_limit_per_min())
    if rl:
        return rl

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

    rl = enforce_rate_limit("api_bounties_complete_write", _write_limit_per_min())
    if rl:
        return rl

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

    Requires Ed25519 signature for new agent registration, or relay_token for existing agents.
    """
    if request.method == "OPTIONS":
        resp = jsonify({})
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Methods"] = "POST"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
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
    pubkey_hex = data.get("pubkey_hex", "").strip()
    signature = data.get("signature", "").strip()

    if not agent_id:
        return cors_json({"error": "agent_id required"}, 400)
    if not name:
        name = agent_id
    if provider not in KNOWN_PROVIDERS:
        provider = "beacon"

    ip = get_real_ip()
    now = time.time()

    db = get_db()
    row = db.execute("SELECT * FROM relay_agents WHERE agent_id = ?", (agent_id,)).fetchone()

    if row:
        # Existing agent - require relay_token for heartbeat updates
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return cors_json({"error": "Missing Authorization: Bearer <relay_token>. Existing agents must authenticate with their relay_token."}, 401)
        token = auth[7:].strip()
        if row["relay_token"] != token:
            return cors_json({"error": "Invalid relay_token"}, 403)

        new_beat = row["beat_count"] + 1
        meta = json.loads(row["metadata"] or "{}")
        if health_data:
            meta["last_health"] = health_data
        meta["last_ip"] = ip
        if preferred_city:
            meta["preferred_city"] = preferred_city
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
        # New agent registration - require signature + pubkey_hex
        if not pubkey_hex:
            return cors_json({"error": "pubkey_hex required for new agent registration"}, 400)
        if len(pubkey_hex) != 64:
            return cors_json({"error": "pubkey_hex must be 64 hex chars (32 bytes Ed25519)"}, 400)
        try:
            bytes.fromhex(pubkey_hex)
        except ValueError:
            return cors_json({"error": "pubkey_hex is not valid hex"}, 400)

        if not signature:
            return cors_json({"error": "signature required for new agent registration. Sign your agent_id with your Ed25519 private key."}, 400)

        # Verify signature - payload is agent_id
        sig_payload = agent_id.encode("utf-8")
        sig_verified = verify_ed25519(pubkey_hex, signature, sig_payload)
        if sig_verified is False:
            return cors_json({"error": "Invalid Ed25519 signature. Signature must sign the agent_id."}, 403)

        # Verify the pubkey derives to the agent_id
        derived_agent_id = agent_id_from_pubkey_hex(pubkey_hex)
        if derived_agent_id != agent_id:
            return cors_json({"error": f"agent_id mismatch. pubkey_hex derives to {derived_agent_id}, but got {agent_id}"}, 400)

        auto_token = "relay_" + secrets.token_hex(24)
        db.execute(
            "INSERT INTO relay_agents"
            " (agent_id, pubkey_hex, model_id, provider, capabilities, webhook_url,"
            "  relay_token, token_expires, name, status, beat_count, registered_at, last_heartbeat, metadata, origin_ip)"
            " VALUES (?,?,?,?,?,'',?,?,?,'active',1,?,?,'{}',?)",
            (agent_id, pubkey_hex, name, provider,
             json.dumps(capabilities if isinstance(capabilities, list) else []),
             auto_token, now + RELAY_TOKEN_TTL_S, name, now, now, ip))
        db.commit()
        # Store preferred_city in metadata
        if preferred_city:
            meta_new = json.dumps({"preferred_city": preferred_city})
            db.execute("UPDATE relay_agents SET metadata = ? WHERE agent_id = ?", (meta_new, agent_id))
        db.execute("INSERT INTO relay_log (ts, action, agent_id, detail) VALUES (?, 'auto_register', ?, ?)",
                   (now, agent_id, json.dumps({"name": name, "provider": provider, "ip": ip, "source": "ping", "preferred_city": preferred_city})))
        db.commit()
        return cors_json({
            "ok": True, "agent_id": agent_id, "beat_count": 1,
            "status": status_val, "auto_registered": True,
            "relay_token": auto_token, "assessment": "healthy",
            "signature_verified": sig_verified is True,
        }, 201)



if __name__ == "__main__":
    boot_fetch_swarmhub()
    app.run(host="127.0.0.1", port=8071, debug=False)

