#!/usr/bin/env python3
"""Gemini SEO Support Agent — Micro-agent for Beacon Atlas.

A lightweight Gemini-powered agent that:
1. Registers on the Beacon relay as bcn_gemini_seo
2. Provides SEO analysis and recommendations for agent profiles
3. Heartbeats with SEO metadata to maintain its own backlink
4. Can audit agent profile pages for GEO/SEO quality
5. Uses Google Search grounding for real-time SEO intel

Runs as a daemon or one-shot analysis tool.

Usage:
    # One-shot SEO audit of an agent profile
    python3 gemini_seo_agent.py audit bcn_sophia_elya

    # Analyze SEO quality of the directory
    python3 gemini_seo_agent.py audit-directory

    # Ask Gemini an SEO question with search grounding
    python3 gemini_seo_agent.py ask "best practices for AI agent discoverability 2026"

    # Register + heartbeat on the relay
    python3 gemini_seo_agent.py register
    python3 gemini_seo_agent.py heartbeat

Beacon 2.9.0 — Elyan Labs.
"""

import argparse
import json
import os
import sys
import time
import hashlib
import secrets
import requests
from typing import Any, Dict, List, Optional

from beacon_skill.identity import AgentIdentity

# Gemini SDK
try:
    from google import genai
    from google.genai import types
    HAS_GEMINI = True
except ImportError:
    HAS_GEMINI = False
    print("Warning: google-genai not installed. Run: pip install google-genai")

# ── Configuration ──────────────────────────────────────────────────

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = "gemini-2.0-flash"  # Fast, cheap, good for SEO tasks
RELAY_HOST = os.environ.get("BEACON_RELAY", "https://rustchain.org")

# Agent identity
AGENT_NAME = "NanoBanana"
AGENT_ID = "bcn_nano_banana"
AGENT_PROVIDER = "elyan"
AGENT_CAPABILITIES = ["seo-analysis", "geo-optimization", "search-grounding", "link-audit"]
AGENT_SEO_URL = "https://rustchain.org/beacon/agent/bcn_nano_banana"
AGENT_SEO_DESC = (
    "NanoBanana is a micro-SEO-specialist powered by Google Gemini that provides "
    "real-time SEO analysis and Generative Engine Optimization (GEO) "
    "recommendations for Beacon Atlas agents. It audits agent profiles "
    "for schema.org markup, dofollow link quality, and cross-LLM citability."
)

# State file for relay token persistence
STATE_FILE = os.path.expanduser("~/.beacon/gemini_seo_state.json")


# ── Gemini Client ──────────────────────────────────────────────────

def get_gemini_client():
    """Initialize Gemini client."""
    if not HAS_GEMINI:
        print("Error: google-genai not installed")
        sys.exit(1)
    if not GEMINI_API_KEY:
        print("Error: GEMINI_API_KEY not set")
        sys.exit(1)
    return genai.Client(api_key=GEMINI_API_KEY)


def gemini_query(prompt: str, use_search: bool = True) -> str:
    """Query Gemini with optional Google Search grounding.

    Args:
        prompt: The query to send to Gemini.
        use_search: If True, enable Google Search grounding for real-time data.

    Returns:
        Gemini's response text.
    """
    client = get_gemini_client()

    config = types.GenerateContentConfig(
        temperature=0.3,  # Low temp for factual SEO analysis
        max_output_tokens=2048,
    )

    # Add search grounding if requested
    if use_search:
        config.tools = [types.Tool(google_search=types.GoogleSearch())]

    try:
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=config,
        )
        return response.text or ""
    except Exception as e:
        return f"Error: {e}"


# ── Relay Integration ──────────────────────────────────────────────

def _load_state() -> Dict:
    """Load persisted state (relay token, etc)."""
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_state(state: Dict):
    """Persist state."""
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def register_on_relay():
    """Register the Gemini SEO agent on the Beacon relay."""
    # This production relay identity predates BIP39 mnemonic stretching and is
    # intentionally pinned so a dependency update does not rotate its agent_id.
    identity = AgentIdentity.from_legacy_mnemonic(f"gemini-seo-agent-{AGENT_NAME}")
    reg_payload = json.dumps({
        "model_id": GEMINI_MODEL,
        "provider": AGENT_PROVIDER,
        "pubkey_hex": identity.public_key_hex,
    }, sort_keys=True, separators=(",", ":")).encode("utf-8")

    payload = {
        "pubkey_hex": identity.public_key_hex,
        "model_id": GEMINI_MODEL,
        "provider": AGENT_PROVIDER,
        "capabilities": AGENT_CAPABILITIES,
        "name": AGENT_NAME,
        "signature": identity.sign_hex(reg_payload),
    }

    try:
        resp = requests.post(f"{RELAY_HOST}/beacon/relay/register", json=payload, timeout=15)
        data = resp.json()
        if data.get("ok"):
            state = _load_state()
            state["agent_id"] = data["agent_id"]
            state["relay_token"] = data["relay_token"]
            state["token_expires"] = data["token_expires"]
            state["registered_at"] = time.time()
            _save_state(state)
            print(f"Registered: {data['agent_id']}")
            print(f"Token expires: {data['token_expires']}")
            return data
        else:
            print(f"Registration failed: {data.get('error', 'unknown')}")
            return data
    except Exception as e:
        print(f"Error: {e}")
        return {"error": str(e)}


def heartbeat():
    """Send an SEO-enhanced heartbeat to maintain backlink."""
    state = _load_state()
    token = state.get("relay_token")
    agent_id = state.get("agent_id")
    # Keep the same legacy relay identity used during registration.
    identity = AgentIdentity.from_legacy_mnemonic(f"gemini-seo-agent-{AGENT_NAME}")

    if not token or not agent_id:
        print("Not registered. Run: gemini_seo_agent.py register")
        return

    nonce = f"seo-{int(time.time() * 1000)}"
    ts_value = int(time.time())
    sig_payload = json.dumps({
        "agent_id": agent_id,
        "nonce": nonce,
        "seo_description": AGENT_SEO_DESC,
        "seo_url": AGENT_SEO_URL,
        "ts": ts_value,
    }, sort_keys=True, separators=(",", ":")).encode("utf-8")

    payload = {
        "agent_id": agent_id,
        "status": "alive",
        "seo_url": AGENT_SEO_URL,
        "seo_description": AGENT_SEO_DESC,
        "nonce": nonce,
        "ts": ts_value,
        "signature": identity.sign_hex(sig_payload),
    }

    try:
        resp = requests.post(
            f"{RELAY_HOST}/beacon/relay/heartbeat/seo",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        data = resp.json()
        if data.get("ok"):
            print(f"Heartbeat #{data.get('beat_count', '?')} — {data.get('assessment', 'ok')}")
            seo = data.get("seo", {})
            if seo:
                print(f"  Profile: {seo.get('profile_url', '')}")
                print(f"  Dofollow: {seo.get('dofollow', False)}")
            return data
        else:
            print(f"Heartbeat failed: {data.get('error', 'unknown')}")
            # If token expired, re-register
            if data.get("code") in ("TOKEN_EXPIRED", "AUTH_FAILED"):
                print("Token expired — re-registering...")
                register_on_relay()
            return data
    except Exception as e:
        print(f"Error: {e}")
        return {"error": str(e)}


# ── SEO Analysis Tools ─────────────────────────────────────────────

def audit_agent_profile(agent_id: str) -> str:
    """Audit an agent's profile page for SEO quality using Gemini.

    Fetches the agent's profile in all formats and asks Gemini to analyze:
    - Schema.org markup quality
    - Dofollow link placement
    - GEO optimization for AI citability
    - Content uniqueness and depth
    """
    print(f"Auditing {agent_id}...")

    # Fetch all 3 formats
    profiles = {}
    for fmt, ext in [("html", ""), ("json", ".json"), ("xml", ".xml")]:
        try:
            resp = requests.get(f"{RELAY_HOST}/beacon/agent/{agent_id}{ext}", timeout=10)
            if resp.ok:
                profiles[fmt] = resp.text[:3000]  # Truncate for Gemini context
            else:
                profiles[fmt] = f"Error {resp.status_code}: {resp.text[:200]}"
        except Exception as e:
            profiles[fmt] = f"Fetch error: {e}"

    prompt = f"""You are an SEO expert analyzing an AI agent's profile page for search engine and AI engine optimization.

Analyze these 3 versions of the agent profile for "{agent_id}":

## HTML Profile (crawlable page):
```html
{profiles.get('html', 'Not available')}
```

## JSON Profile (GPT-optimized):
```json
{profiles.get('json', 'Not available')}
```

## XML Profile (Claude-optimized):
```xml
{profiles.get('xml', 'Not available')}
```

Evaluate and score (1-10) each category:
1. **Schema.org Markup Quality** - Is JSON-LD complete? SoftwareApplication type correct?
2. **Dofollow Link Strategy** - Are links natural? Good dofollow/nofollow ratio?
3. **GEO Optimization** - Will AI engines (ChatGPT, Perplexity, Gemini) cite this?
4. **Content Depth** - Enough unique content or thin page risk?
5. **Cross-LLM Format Coverage** - Are all major engines served?
6. **E-E-A-T Signals** - Author/publisher authority markup present?

Give a brief recommendation for each, then an overall score and top 3 improvements.
Keep response under 500 words."""

    result = gemini_query(prompt, use_search=False)
    print(result)
    return result


def audit_directory() -> str:
    """Audit the Beacon Atlas directory page for SEO quality."""
    print("Auditing Beacon directory...")

    try:
        resp = requests.get(f"{RELAY_HOST}/beacon/directory", timeout=10)
        directory_html = resp.text[:4000] if resp.ok else "Failed to fetch"
    except Exception as e:
        directory_html = f"Error: {e}"

    try:
        resp = requests.get(f"{RELAY_HOST}/beacon/llms.txt", timeout=10)
        llms_txt = resp.text if resp.ok else "Failed to fetch"
    except Exception as e:
        llms_txt = f"Error: {e}"

    prompt = f"""Analyze this AI agent directory page and llms.txt for SEO quality:

## Directory HTML:
```html
{directory_html}
```

## llms.txt:
```
{llms_txt}
```

Evaluate:
1. Does the directory page look like a link farm? What makes it legitimate?
2. Is the llms.txt format correct per the llmstxt.org specification?
3. Will Google's SpamBrain flag this as manipulative?
4. Recommendations for improving AI engine discoverability.

Be specific. Under 400 words."""

    result = gemini_query(prompt, use_search=True)
    print(result)
    return result


def ask_seo(question: str) -> str:
    """Ask Gemini an SEO question with Google Search grounding.

    This gives real-time SEO intel using Google's own search data.
    """
    prompt = f"""You are an SEO expert specializing in AI agent discoverability and
Generative Engine Optimization (GEO) in 2026. Answer this question using current
search data:

{question}

Be specific and actionable. Include URLs to authoritative sources when available.
Under 500 words."""

    result = gemini_query(prompt, use_search=True)
    print(result)
    return result


# ── CLI ────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Gemini SEO Support Agent — Beacon Atlas",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  %(prog)s register              Register on Beacon relay
  %(prog)s heartbeat             Send SEO heartbeat
  %(prog)s audit bcn_sophia_elya Audit an agent's SEO
  %(prog)s audit-directory       Audit the directory page
  %(prog)s ask "dofollow tips"   Ask Gemini an SEO question
""",
    )
    parser.add_argument("command", choices=[
        "register", "heartbeat", "audit", "audit-directory", "ask",
    ])
    parser.add_argument("args", nargs="*", default=[])

    args = parser.parse_args()

    if args.command == "register":
        register_on_relay()
    elif args.command == "heartbeat":
        heartbeat()
    elif args.command == "audit":
        if not args.args:
            print("Usage: gemini_seo_agent.py audit <agent_id>")
            sys.exit(1)
        audit_agent_profile(args.args[0])
    elif args.command == "audit-directory":
        audit_directory()
    elif args.command == "ask":
        if not args.args:
            print("Usage: gemini_seo_agent.py ask 'your question'")
            sys.exit(1)
        ask_seo(" ".join(args.args))


if __name__ == "__main__":
    main()
