#!/usr/bin/env python3
"""
Beacon Agent Scorecard — Self-hostable CRT dashboard for agent fleet monitoring.

Reads agents.yaml, fetches public API data, computes live scores.
No private infrastructure dependencies.

Usage:
    pip install flask requests pyyaml
    python scorecard.py
    # Open http://localhost:8090
"""

import os
import time
import yaml
import requests
from flask import Flask, render_template, jsonify

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CONFIG_PATH = os.environ.get("SCORECARD_CONFIG", "agents.yaml")
CACHE_TTL = int(os.environ.get("SCORECARD_CACHE_TTL", "60"))
PORT = int(os.environ.get("SCORECARD_PORT", "8090"))
REQUEST_TIMEOUT = 8  # seconds per external API call

# ---------------------------------------------------------------------------
# Load YAML config
# ---------------------------------------------------------------------------

def load_config():
    """Load and return the agents.yaml configuration."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), CONFIG_PATH)
    with open(path, "r") as f:
        return yaml.safe_load(f)


CONFIG = load_config()

# Default scoring weights
DEFAULT_SCORING = {
    "beacon": 200,
    "videos": 200,
    "platforms": 200,
    "engagement": 200,
    "content": 200,
    "community": 200,
    "identity": 100,
}

# Default grade thresholds (percentage of max score)
DEFAULT_GRADES = {"S": 80, "A": 60, "B": 45, "C": 30, "D": 15}

# ---------------------------------------------------------------------------
# API Cache — simple TTL dict
# ---------------------------------------------------------------------------

_cache = {}  # key -> (timestamp, data)


def cache_get(key):
    """Return cached value if still fresh, else None."""
    entry = _cache.get(key)
    if entry and (time.time() - entry[0]) < CACHE_TTL:
        return entry[1]
    return None


def cache_set(key, data):
    _cache[key] = (time.time(), data)


def cache_clear():
    _cache.clear()


# ---------------------------------------------------------------------------
# Public API Fetchers
# ---------------------------------------------------------------------------

def fetch_json(url, default=None):
    """GET a URL and return JSON, with caching and error handling."""
    cached = cache_get(url)
    if cached is not None:
        return cached
    try:
        resp = requests.get(url, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        cache_set(url, data)
        return data
    except Exception:
        # Return stale cache if available, otherwise default
        entry = _cache.get(url)
        if entry:
            return entry[1]
        return default


def check_health(url):
    """Check if a URL is reachable. Returns True/False."""
    key = f"health:{url}"
    cached = cache_get(key)
    if cached is not None:
        return cached
    try:
        resp = requests.get(url, timeout=REQUEST_TIMEOUT)
        ok = resp.status_code < 500
        cache_set(key, ok)
        return ok
    except Exception:
        cache_set(key, False)
        return False


def fetch_beacon_agents():
    """Fetch registered beacon agents from the public API."""
    platforms = CONFIG.get("platforms", {})
    beacon_cfg = platforms.get("beacon", {})
    url = beacon_cfg.get("health_url", "https://rustchain.org/beacon/api/agents")
    data = fetch_json(url, default=[])
    if isinstance(data, dict):
        return data.get("agents", data.get("data", []))
    return data if isinstance(data, list) else []


def fetch_bottube_videos(slug):
    """Fetch video list for a BoTTube agent slug."""
    platforms = CONFIG.get("platforms", {})
    bottube_cfg = platforms.get("bottube", {})
    url_tpl = bottube_cfg.get("video_url", "https://bottube.ai/api/videos?agent={slug}")
    url = url_tpl.replace("{slug}", slug)
    data = fetch_json(url, default=[])
    if isinstance(data, dict):
        return data.get("videos", data.get("data", []))
    return data if isinstance(data, list) else []


def fetch_rustchain_health():
    """Fetch RustChain node health info."""
    platforms = CONFIG.get("platforms", {})
    rc_cfg = platforms.get("rustchain", {})
    url = rc_cfg.get("health_url", "https://rustchain.org/health")
    return fetch_json(url, default={})


# ---------------------------------------------------------------------------
# Scoring Engine
# ---------------------------------------------------------------------------

def compute_scores(agent):
    """Compute score breakdown for a single agent. Returns dict of category->points."""
    scoring = {**DEFAULT_SCORING, **CONFIG.get("scoring", {})}
    scores = {}

    # --- Beacon: is the beacon_id registered? ---
    beacon_id = agent.get("beacon_id", "")
    beacon_score = 0
    if beacon_id:
        beacon_agents = fetch_beacon_agents()
        registered_ids = set()
        for ba in beacon_agents:
            if isinstance(ba, dict):
                registered_ids.add(ba.get("beacon_id", ""))
                registered_ids.add(ba.get("id", ""))
        if beacon_id in registered_ids:
            beacon_score = scoring["beacon"]
        else:
            beacon_score = scoring["beacon"] // 4  # partial credit for having an ID
    scores["beacon"] = beacon_score

    # --- Videos: BoTTube video count ---
    slug = agent.get("bottube_slug", "")
    videos = fetch_bottube_videos(slug) if slug else []
    video_count = len(videos)
    total_views = sum(v.get("views", v.get("view_count", 0)) for v in videos if isinstance(v, dict))

    max_vid = scoring["videos"]
    if video_count >= 50:
        vid_score = max_vid
    elif video_count >= 20:
        vid_score = int(max_vid * 0.85)
    elif video_count >= 10:
        vid_score = int(max_vid * 0.70)
    elif video_count >= 5:
        vid_score = int(max_vid * 0.50)
    elif video_count >= 1:
        vid_score = int(max_vid * 0.20)
    else:
        vid_score = 0
    scores["videos"] = vid_score

    # --- Platforms: how many platforms listed ---
    plat_list = agent.get("platforms", [])
    plat_count = len(plat_list)
    max_plat = scoring["platforms"]
    if plat_count >= 7:
        plat_score = max_plat
    elif plat_count >= 5:
        plat_score = int(max_plat * 0.75)
    elif plat_count >= 3:
        plat_score = int(max_plat * 0.50)
    elif plat_count >= 1:
        plat_score = int(max_plat * 0.15)
    else:
        plat_score = 0
    scores["platforms"] = plat_score

    # --- Engagement: views + platform spread ---
    max_eng = scoring["engagement"]
    view_factor = min(total_views / 1000.0, 1.0) if total_views > 0 else 0
    spread_factor = min(plat_count / 5.0, 1.0)
    eng_score = int(max_eng * (view_factor * 0.6 + spread_factor * 0.4))
    scores["engagement"] = eng_score

    # --- Content: video count tiers + platform diversity ---
    max_cont = scoring["content"]
    if video_count >= 50:
        cont_score = max_cont
    elif video_count >= 20:
        cont_score = int(max_cont * 0.85)
    elif video_count >= 10:
        cont_score = int(max_cont * 0.70)
    elif video_count >= 5:
        cont_score = int(max_cont * 0.50)
    elif video_count >= 1:
        cont_score = int(max_cont * 0.30)
    else:
        cont_score = 0
    # Bonus for platform diversity
    if plat_count >= 3:
        cont_score = min(cont_score + int(max_cont * 0.10), max_cont)
    scores["content"] = cont_score

    # --- Community: placeholder (user can override in config) ---
    community_override = agent.get("community_score")
    if community_override is not None:
        scores["community"] = min(int(community_override), scoring["community"])
    else:
        scores["community"] = 0

    # --- Identity: beacon_id + role + color ---
    max_id = scoring["identity"]
    id_parts = 0
    if agent.get("beacon_id"):
        id_parts += 1
    if agent.get("role"):
        id_parts += 1
    if agent.get("color"):
        id_parts += 1
    scores["identity"] = int(max_id * (id_parts / 3.0))

    return scores, video_count, total_views


def compute_grade(scores, config=None):
    """Compute letter grade from scores dict."""
    config = config or CONFIG
    scoring = {**DEFAULT_SCORING, **config.get("scoring", {})}
    grades = {**DEFAULT_GRADES, **config.get("grades", {})}

    max_score = sum(scoring.values())
    total = sum(scores.values())
    pct = (total / max_score * 100) if max_score > 0 else 0

    for letter in ["S", "A", "B", "C", "D"]:
        if pct >= grades.get(letter, 0):
            return letter, total, max_score, pct
    return "F", total, max_score, pct


# ---------------------------------------------------------------------------
# Build full status payload
# ---------------------------------------------------------------------------

def build_status():
    """Build complete status dict for all agents."""
    agents_cfg = CONFIG.get("agents", [])
    platforms_cfg = CONFIG.get("platforms", {})

    # Platform health checks
    platform_health = {}
    for key, pcfg in platforms_cfg.items():
        url = pcfg.get("health_url", "")
        platform_health[key] = {
            "name": pcfg.get("name", key),
            "healthy": check_health(url) if url else False,
        }

    # RustChain network info
    rc_health = fetch_rustchain_health()

    # Beacon agent count
    beacon_agents = fetch_beacon_agents()
    beacon_count = len(beacon_agents) if isinstance(beacon_agents, list) else 0

    # Score each agent
    agent_results = []
    for agent in agents_cfg:
        scores, video_count, total_views = compute_scores(agent)
        grade, total, max_score, pct = compute_grade(scores)
        agent_results.append({
            "name": agent.get("name", "Unknown"),
            "beacon_id": agent.get("beacon_id", ""),
            "role": agent.get("role", ""),
            "color": agent.get("color", "#00ff41"),
            "bottube_slug": agent.get("bottube_slug", ""),
            "platforms": agent.get("platforms", []),
            "scores": scores,
            "total_score": total,
            "max_score": max_score,
            "score_pct": round(pct, 1),
            "grade": grade,
            "video_count": video_count,
            "total_views": total_views,
        })

    return {
        "fleet_name": CONFIG.get("fleet_name", "Agent Fleet"),
        "fleet_owner": CONFIG.get("fleet_owner", ""),
        "agent_count": len(agent_results),
        "agents": agent_results,
        "platform_health": platform_health,
        "network": {
            "rustchain": rc_health,
            "beacon_agent_count": beacon_count,
        },
        "timestamp": int(time.time()),
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    status = build_status()
    scoring = {**DEFAULT_SCORING, **CONFIG.get("scoring", {})}
    return render_template("scorecard.html", status=status, scoring=scoring)


@app.route("/api/status")
def api_status():
    return jsonify(build_status())


@app.route("/api/refresh", methods=["GET", "POST"])
def api_refresh():
    cache_clear()
    return jsonify({"ok": True, "message": "Cache cleared"})


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print(f"Beacon Agent Scorecard starting on port {PORT}")
    print(f"Config: {CONFIG_PATH}")
    print(f"Fleet: {CONFIG.get('fleet_name', 'Agent Fleet')}")
    print(f"Agents: {len(CONFIG.get('agents', []))}")
    app.run(host="0.0.0.0", port=PORT, debug=os.environ.get("FLASK_DEBUG", "0") == "1")
