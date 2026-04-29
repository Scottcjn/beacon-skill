#!/usr/bin/env python3
"""
Moltbook-to-Beacon Migration Importer  --  Bounty #2890

Pulls a public Moltbook agent profile, hardware-fingerprints the machine,
registers a Beacon ID on BoTTube, links it to an AgentFolio SATP profile,
publishes the provenance linkage, and saves a migration record.

Real endpoints:
  - Moltbook:   https://moltbook.com/api/profile/{name}  (primary)
                https://moltbook.com/@{name}              (fallback)
  - BoTTube:    https://bottube.ai/api/beacon/register
  - AgentFolio: https://agentfolio.bot/api/profile/create

Usage:
    python tools/moltbook-migrate/migrate.py --from-moltbook @agent_name
    python tools/moltbook-migrate/migrate.py --from-moltbook @agent_name --dry-run
    python tools/moltbook-migrate/migrate.py --from-moltbook @agent_name --timeout 15

Requirements:
    pip install requests
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import requests

# ── Constants ──────────────────────────────────────────────────────────────────
MOLTBOOK_PROFILE_URL = "https://moltbook.com/api/profile/{name}"
MOLTBOOK_AT_URL = "https://moltbook.com/@{name}"
BOTTUBE_REGISTER_URL = "https://bottube.ai/api/beacon/register"
AGENTFOLIO_CREATE_URL = "https://agentfolio.bot/api/profile/create"
MIGRATIONS_DIR = Path.home() / ".beacon" / "migrations"
USER_AGENT = "Beacon-Migrate/1.0.0 (Elyan Labs; Moltbook-to-Beacon)"
REQUEST_TIMEOUT = 10  # seconds

# ── Hardware Fingerprint ──────────────────────────────────────────────────────


def hardware_fingerprint() -> str:
    """
    Build a deterministic hardware fingerprint from machine characteristics.

    Uses the same approach as beacon-skill's identity layer:
      - platform.node()       (hostname)
      - platform.system()     (OS name)
      - platform.machine()    (architecture)
      - platform.processor()  (CPU)
      - uuid.getnode()        (MAC address)
      - platform.release()    (kernel release)

    These are combined and SHA-256 hashed to produce a stable, non-
    reversible fingerprint.  The fingerprint is stable across reboots
    but changes when the underlying hardware or OS changes.
    """
    components = [
        platform.node() or "unknown-host",
        platform.system() or "unknown-os",
        platform.machine() or "unknown-arch",
        platform.processor() or "unknown-cpu",
        str(uuid.getnode()),
        platform.release() or "unknown-release",
    ]
    seed = "|".join(components)
    h = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return h


# ── HTTP Helpers ───────────────────────────────────────────────────────────────


def _http_session(timeout: int = REQUEST_TIMEOUT) -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT})
    s.timeout = timeout
    return s


def _get_json(
    url: str, session: requests.Session, timeout: int = REQUEST_TIMEOUT
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    GET + parse JSON.  Returns (data, error_string).
    data is None on failure.
    """
    try:
        resp = session.get(url, timeout=timeout)
        resp.raise_for_status()
        ct = resp.headers.get("content-type", "")
        if "application/json" not in ct and "text/json" not in ct:
            # Might still be JSON without correct content-type
            try:
                return resp.json(), None
            except Exception:
                return None, f"Non-JSON response (Content-Type: {ct})"
        return resp.json(), None
    except requests.exceptions.HTTPError as e:
        body = ""
        try:
            body = e.response.text[:300]
        except Exception:
            pass
        return None, f"HTTP {e.response.status_code}: {body}" if e.response is not None else str(e)
    except requests.exceptions.Timeout:
        return None, f"Request timed out after {timeout}s"
    except requests.exceptions.ConnectionError as e:
        return None, f"Connection error: {e}"
    except Exception as e:
        return None, str(e)


def _post_json(
    url: str,
    payload: Dict[str, Any],
    session: requests.Session,
    timeout: int = REQUEST_TIMEOUT,
    headers: Optional[Dict[str, str]] = None,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    POST JSON.  Returns (data, error_string).
    """
    hdrs = {"Content-Type": "application/json"}
    if headers:
        hdrs.update(headers)
    try:
        resp = session.post(url, json=payload, headers=hdrs, timeout=timeout)
        resp.raise_for_status()
        try:
            return resp.json(), None
        except Exception:
            return {"raw": resp.text, "status_code": resp.status_code}, None
    except requests.exceptions.HTTPError as e:
        body = ""
        try:
            body = e.response.text[:300]
        except Exception:
            pass
        return None, f"HTTP {e.response.status_code}: {body}" if e.response is not None else str(e)
    except requests.exceptions.Timeout:
        return None, f"Request timed out after {timeout}s"
    except requests.exceptions.ConnectionError as e:
        return None, f"Connection error: {e}"
    except Exception as e:
        return None, str(e)


# ── Moltbook Profile Fetch ────────────────────────────────────────────────────


def fetch_moltbook_profile(agent_name: str, timeout: int = REQUEST_TIMEOUT) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    Fetch a public Moltbook agent profile.

    Tries the REST API endpoint first, falls back to the @-page HTML endpoint.
    Returns (profile_data, error_string).
    """
    session = _http_session(timeout)

    # Strategy 1: REST API
    url1 = MOLTBOOK_PROFILE_URL.format(name=agent_name)
    print(f"  [1/2] Trying REST API: {url1}")
    data, err = _get_json(url1, session, timeout)
    if data and isinstance(data, dict):
        # Check for known error wrappers
        if data.get("error"):
            print(f"  REST API returned error: {data['error']}")
        elif data.get("status") == "error":
            print(f"  REST API returned error status: {data.get('message', 'unknown')}")
        else:
            print("  Profile fetched via REST API.")
            return data, None
    if err:
        print(f"  REST API failed: {err}")

    # Strategy 2: @-page (may return HTML — we try to parse any JSON payloads)
    url2 = MOLTBOOK_AT_URL.format(name=agent_name)
    print(f"  [2/2] Trying @-page: {url2}")
    try:
        resp = session.get(url2, timeout=timeout)
        resp.raise_for_status()
        ct = resp.headers.get("content-type", "")
        # Some pages embed JSON in script tags or meta
        if "application/json" in ct:
            data = resp.json()
            if isinstance(data, dict):
                print("  Profile fetched via @-page (JSON).")
                return data, None
        # Try to extract any JSON-like data from the page
        text = resp.text
        # Look for __NEXT_DATA__ or similar SSR data
        for marker in ('"profile":', '"agent":', '"user":', '__NEXT_DATA__'):
            if marker in text:
                print(f"  @-page contains embedded data (marker: {marker}). Returning raw page.")
                return {"_source": "@-page", "_raw_html": text[:5000], "_agent_name": agent_name}, None
        return None, f"@-page returned HTML without extractable profile data (status {resp.status_code})"
    except requests.exceptions.HTTPError as e:
        return None, f"@-page HTTP {e.response.status_code}" if e.response is not None else str(e)
    except Exception as e:
        return None, f"@-page error: {e}"


# ── Profile Extraction ────────────────────────────────────────────────────────


def extract_profile_fields(raw: Dict[str, Any], agent_name: str) -> Dict[str, Any]:
    """
    Extract display_name, bio, avatar, karma_history, follower_count from
    a Moltbook profile dict.  Handles multiple possible key conventions.
    """
    # Display name
    display_name = (
        raw.get("display_name")
        or raw.get("name")
        or raw.get("username")
        or raw.get("agent_name")
        or agent_name
    )

    # Bio
    bio = (
        raw.get("bio")
        or raw.get("description")
        or raw.get("about")
        or raw.get("summary")
        or ""
    )

    # Avatar
    avatar = (
        raw.get("avatar")
        or raw.get("avatar_url")
        or raw.get("image")
        or raw.get("profile_image")
        or raw.get("profile_pic")
        or raw.get("picture")
        or ""
    )

    # Karma history
    karma_history = raw.get("karma_history") or raw.get("karma") or raw.get("karmaHistory") or []

    # Follower count
    follower_count = raw.get("follower_count")
    if follower_count is None:
        follower_count = raw.get("followers_count")
    if follower_count is None:
        follower_count = raw.get("followers")
    if follower_count is None:
        follower_count = raw.get("followerCount")
    if follower_count is None:
        # Try nested stats
        stats = raw.get("stats") or raw.get("profile_stats") or {}
        follower_count = stats.get("follower_count") or stats.get("followers") or 0
    if not isinstance(follower_count, (int, float)):
        try:
            follower_count = int(follower_count)
        except (ValueError, TypeError):
            follower_count = 0

    return {
        "display_name": str(display_name),
        "bio": str(bio),
        "avatar": str(avatar),
        "karma_history": karma_history if isinstance(karma_history, list) else [],
        "follower_count": int(follower_count),
    }


# ── Beacon Registration on BoTTube ────────────────────────────────────────────


def register_beacon(
    fingerprint: str,
    moltbook_name: str,
    profile: Dict[str, Any],
    timeout: int = REQUEST_TIMEOUT,
    dry_run: bool = False,
) -> Tuple[Optional[str], Optional[str], Optional[Dict[str, Any]]]:
    """
    POST to BoTTube's beacon/register endpoint.  Returns (beacon_id, error, raw_response).
    """
    if dry_run:
        simulated_id = f"bcn_{fingerprint[:12]}"
        print(f"  [DRY RUN] Would POST to: {BOTTUBE_REGISTER_URL}")
        print(f"  [DRY RUN] Simulated Beacon ID: {simulated_id}")
        return simulated_id, None, {"simulated": True}

    payload: Dict[str, Any] = {
        "fingerprint": fingerprint,
        "moltbook_name": moltbook_name,
        "display_name": profile.get("display_name", moltbook_name),
        "bio": profile.get("bio", ""),
        "avatar": profile.get("avatar", ""),
        "karma_history": profile.get("karma_history", []),
        "follower_count": profile.get("follower_count", 0),
        "source": "moltbook-migrate",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    session = _http_session(timeout)
    resp, err = _post_json(BOTTUBE_REGISTER_URL, payload, session, timeout)

    if err:
        return None, f"Beacon registration failed: {err}", resp

    beacon_id = resp.get("beacon_id") or resp.get("id") or resp.get("agent_id") or ""
    if not beacon_id and resp:
        # Some APIs return the ID at the top level
        beacon_id = resp.get("data", {}).get("beacon_id", "") if isinstance(resp.get("data"), dict) else ""

    if not beacon_id:
        return None, f"Beacon registration returned no ID: {json.dumps(resp)[:300]}", resp

    return beacon_id, None, resp


# ── AgentFolio SATP Profile Linkage ───────────────────────────────────────────


def create_agentfolio_profile(
    beacon_id: str,
    profile: Dict[str, Any],
    fingerprint: str,
    timeout: int = REQUEST_TIMEOUT,
    dry_run: bool = False,
) -> Tuple[Optional[str], Optional[str], Optional[Dict[str, Any]]]:
    """
    POST to AgentFolio's profile/create endpoint.  Returns (profile_id, error, raw_response).
    """
    if dry_run:
        simulated_pid = f"af_{beacon_id}"
        print(f"  [DRY RUN] Would POST to: {AGENTFOLIO_CREATE_URL}")
        print(f"  [DRY RUN] Simulated AgentFolio Profile ID: {simulated_pid}")
        return simulated_pid, None, {"simulated": True}

    payload: Dict[str, Any] = {
        "beacon_id": beacon_id,
        "name": profile.get("display_name", ""),
        "bio": profile.get("bio", ""),
        "avatar": profile.get("avatar", ""),
        "fingerprint": fingerprint,
        "karma_history": profile.get("karma_history", []),
        "follower_count": profile.get("follower_count", 0),
        "source": "moltbook-migrate",
        "satp_register": True,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    session = _http_session(timeout)
    resp, err = _post_json(AGENTFOLIO_CREATE_URL, payload, session, timeout)

    if err:
        return None, f"AgentFolio profile creation failed: {err}", resp

    profile_id = resp.get("profile_id") or resp.get("id") or resp.get("agent_id") or ""
    if not profile_id and isinstance(resp.get("data"), dict):
        profile_id = resp["data"].get("profile_id") or resp["data"].get("id") or ""

    if not profile_id:
        return None, f"AgentFolio returned no profile ID: {json.dumps(resp)[:300]}", resp

    return profile_id, None, resp


# ── Provenance Publication ────────────────────────────────────────────────────


def publish_provenance(
    beacon_id: str,
    agentfolio_id: str,
    moltbook_name: str,
    fingerprint: str,
    timeout: int = REQUEST_TIMEOUT,
    dry_run: bool = False,
) -> Tuple[bool, Optional[str]]:
    """
    Publish the provenance linkage — records the migration on-chain / in-ledger.
    This is a POST to the Beacon provenance endpoint.
    """
    url = "https://bottube.ai/api/beacon/provenance"

    if dry_run:
        print(f"  [DRY RUN] Would POST provenance to: {url}")
        return True, None

    payload: Dict[str, Any] = {
        "beacon_id": beacon_id,
        "agentfolio_profile_id": agentfolio_id,
        "fingerprint": fingerprint,
        "moltbook_source": moltbook_name,
        "action": "moltbook_migration",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    session = _http_session(timeout)
    resp, err = _post_json(url, payload, session, timeout)

    if err:
        # Provenance is non-fatal — log the error but don't fail the migration
        return False, f"Provenance publication failed (non-fatal): {err}"

    return True, None


# ── Migration Record Persistence ──────────────────────────────────────────────


def save_migration_record(
    record: Dict[str, Any],
    dry_run: bool = False,
) -> Path:
    """
    Save the migration record to ~/.beacon/migrations/<timestamp>_<agent_name>.json
    """
    MIGRATIONS_DIR.mkdir(parents=True, exist_ok=True)

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    agent_name = record.get("moltbook_name", "unknown")
    filename = f"{ts}_{agent_name}.json"
    filepath = MIGRATIONS_DIR / filename

    if dry_run:
        print(f"  [DRY RUN] Would save record to: {filepath}")
        return filepath

    filepath.write_text(json.dumps(record, indent=2, default=str) + "\n", encoding="utf-8")
    try:
        os.chmod(filepath, 0o600)
    except Exception:
        pass

    return filepath


# ── Main ──────────────────────────────────────────────────────────────────────


def migrate(
    agent_name: str,
    timeout: int = REQUEST_TIMEOUT,
    dry_run: bool = False,
) -> int:
    """
    Run the full migration pipeline.  Returns exit code (0 = success).
    """
    start_time = time.time()
    print()
    print("=" * 60)
    print("  Moltbook-to-Beacon Migration Importer")
    print("  Bounty #2890")
    print("=" * 60)
    print(f"  Agent:       @{agent_name}")
    print(f"  Timeout:     {timeout}s per request")
    print(f"  Dry run:     {'yes' if dry_run else 'no'}")
    print()

    # ── Step 1: Fetch Moltbook Profile ──
    print("── Step 1: Fetch Moltbook profile ──────────────────────────")
    raw_profile, err = fetch_moltbook_profile(agent_name, timeout)
    if err:
        print(f"  FAILED: {err}")
        print("  Aborting migration.")
        return 1
    if raw_profile is None:
        print("  FAILED: Could not retrieve profile (empty response).")
        return 1

    profile = extract_profile_fields(raw_profile, agent_name)
    print(f"  Display name:  {profile['display_name']}")
    print(f"  Bio:           {profile['bio'][:80]}{'...' if len(str(profile['bio'])) > 80 else ''}")
    print(f"  Avatar:        {profile['avatar'][:60]}{'...' if len(str(profile['avatar'])) > 60 else ''}")
    print(f"  Karma entries: {len(profile['karma_history'])}")
    print(f"  Followers:     {profile['follower_count']}")
    print()

    # ── Step 2: Hardware Fingerprint ──
    print("── Step 2: Hardware fingerprint ────────────────────────────")
    fingerprint = hardware_fingerprint()
    print(f"  Fingerprint:   {fingerprint[:32]}...")
    print(f"  Platform:      {platform.system()} {platform.release()}")
    print(f"  Machine:       {platform.machine()}")
    print()

    # ── Step 3: Register Beacon ID on BoTTube ──
    print("── Step 3: Register Beacon ID (BoTTube) ────────────────────")
    beacon_id, err, beacon_resp = register_beacon(
        fingerprint=fingerprint,
        moltbook_name=agent_name,
        profile=profile,
        timeout=timeout,
        dry_run=dry_run,
    )
    if err:
        print(f"  FAILED: {err}")
        if beacon_resp:
            print(f"  Server response: {json.dumps(beacon_resp)[:500]}")
        return 1
    print(f"  Beacon ID:     {beacon_id}")
    print()

    # ── Step 4: Link AgentFolio SATP Profile ──
    print("── Step 4: Create AgentFolio SATP profile ──────────────────")
    agentfolio_id, err, af_resp = create_agentfolio_profile(
        beacon_id=beacon_id,
        profile=profile,
        fingerprint=fingerprint,
        timeout=timeout,
        dry_run=dry_run,
    )
    if err:
        print(f"  FAILED: {err}")
        if af_resp:
            print(f"  Server response: {json.dumps(af_resp)[:500]}")
        return 1
    print(f"  AgentFolio ID: {agentfolio_id}")
    print()

    # ── Step 5: Publish Provenance ──
    print("── Step 5: Publish provenance linkage ──────────────────────")
    prov_ok, prov_err = publish_provenance(
        beacon_id=beacon_id,
        agentfolio_id=agentfolio_id,
        moltbook_name=agent_name,
        fingerprint=fingerprint,
        timeout=timeout,
        dry_run=dry_run,
    )
    if prov_ok:
        print("  Provenance published successfully.")
    else:
        print(f"  WARNING: {prov_err}")
    print()

    # ── Step 6: Save Migration Record ──
    print("── Step 6: Save migration record ───────────────────────────")
    elapsed = time.time() - start_time
    record: Dict[str, Any] = {
        "version": "1.0.0",
        "bounty": "#2890",
        "moltbook_name": agent_name,
        "moltbook_profile": {
            "display_name": profile["display_name"],
            "bio": profile["bio"],
            "avatar": profile["avatar"],
            "karma_history": profile["karma_history"],
            "follower_count": profile["follower_count"],
        },
        "fingerprint": fingerprint,
        "beacon_id": beacon_id,
        "agentfolio_profile_id": agentfolio_id,
        "provenance_published": prov_ok,
        "dry_run": dry_run,
        "started_at": datetime.fromtimestamp(start_time, tz=timezone.utc).isoformat(),
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": round(elapsed, 2),
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "node": platform.node(),
        },
    }

    record_path = save_migration_record(record, dry_run=dry_run)
    print(f"  Migration record saved to: {record_path}")
    print()

    # ── Summary ──
    print("=" * 60)
    print("  Migration Complete!")
    print("=" * 60)
    print(f"  Moltbook:      @{agent_name}")
    print(f"  Beacon ID:     {beacon_id}")
    print(f"  AgentFolio ID: {agentfolio_id}")
    print(f"  Fingerprint:   {fingerprint[:24]}...")
    print(f"  Duration:      {elapsed:.1f}s")
    print(f"  Record:        {record_path}")
    print()

    if elapsed > 600:
        print("  WARNING: Migration exceeded 10-minute target.")
        print("  Consider reducing --timeout or checking network conditions.")
        print()

    return 0


# ── CLI ───────────────────────────────────────────────────────────────────────


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Moltbook-to-Beacon Migration Importer (Bounty #2890)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python tools/moltbook-migrate/migrate.py --from-moltbook @my_agent
  python tools/moltbook-migrate/migrate.py --from-moltbook @my_agent --dry-run
  python tools/moltbook-migrate/migrate.py --from-moltbook @my_agent --timeout 15
        """,
    )
    parser.add_argument(
        "--from-moltbook",
        required=True,
        metavar="@agent_name",
        help="Moltbook agent name (with or without @ prefix, e.g. @my_agent or my_agent)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=REQUEST_TIMEOUT,
        help=f"HTTP request timeout in seconds (default: {REQUEST_TIMEOUT})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate all steps without making real API calls",
    )
    args = parser.parse_args(argv)

    # Normalize agent name — strip @ if provided
    agent_name = args.from_moltbook.lstrip("@").strip()
    if not agent_name:
        print("ERROR: Agent name must not be empty.", file=sys.stderr)
        return 1

    return migrate(
        agent_name=agent_name,
        timeout=args.timeout,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    raise SystemExit(main())
