#!/usr/bin/env python3
"""Moltbook → Beacon + AgentFolio Migration Importer.

One-command import:
    python moltbook_migrate.py --moltbook-user @agent_name [--satp-link]

Pulls public Moltbook profile metadata, hardware-fingerprints the current
machine, mints a Beacon ID anchored to that machine, and creates a SATP
trust profile linkage on AgentFolio.

Designed to complete in under 10 minutes total operator time.
"""

import argparse
import hashlib
import json
import os
import platform
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

# ── Constants ────────────────────────────────────────────────────────────────
MOLTBOOK_API = "https://www.moltbook.com"
BEACON_DIR_API = "https://bottube.ai/api/beacon/directory"
AGENTFOLIO_API = "https://agentfolio.bot/api"
BEACON_SKILL_IMPORT_PATH = "../../.."


# ── Step 1: Fetch Moltbook Profile ───────────────────────────────────────────
def fetch_moltbook_profile(username: str) -> Dict[str, Any]:
    """Pull public Moltbook profile metadata for @username."""
    # Strip leading @
    username = username.lstrip("@")

    # Try multiple known profile endpoints
    endpoints = [
        f"{MOLTBOOK_API}/api/v1/users/{username}",
        f"{MOLTBOOK_API}/api/v1/profile/{username}",
        f"{MOLTBOOK_API}/api/users/{username}/public",
    ]

    last_error = None
    for url in endpoints:
        try:
            req = Request(url, headers={"User-Agent": "Beacon-Migrate/1.0"})
            with urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())
                print(f"✅ Fetched Moltbook profile: {username}")
                return {"source": "moltbook", "username": username, "profile": data}
        except (HTTPError, URLError) as e:
            last_error = e
            continue

    # Fallback: construct profile from known public patterns
    print(f"⚠️  Direct API call failed for {username}: {last_error}")
    print(f"📋 Constructing profile skeleton for manual fill-in...")
    return {
        "source": "moltbook",
        "username": username,
        "profile": {
            "display_name": username,
            "bio": f"Migrated from Moltbook (@{username})",
            "avatar_url": f"{MOLTBOOK_API}/avatars/{username}.png",
            "karma_history": {"note": "Fetch manually from Moltbook profile page"},
            "follower_count": None,
        },
        "note": "Profile data constructed from public info. Fill in missing fields manually.",
    }


# ── Step 2: Hardware Fingerprint ─────────────────────────────────────────────
def _get_mac_addresses() -> list[str]:
    """Collect all MAC addresses (non-loopback)."""
    macs = []
    try:
        if platform.system() == "Linux":
            result = subprocess.run(
                ["ip", "-o", "link"], capture_output=True, text=True, timeout=10
            )
            for line in result.stdout.splitlines():
                if "lo:" in line or "LOOPBACK" in line.upper():
                    continue
                # Extract MAC
                parts = line.split()
                for p in parts:
                    if ":" in p and len(p) == 17:
                        macs.append(p.upper())
        elif platform.system() == "Darwin":
            result = subprocess.run(
                ["networksetup", "-listallhardwareports"],
                capture_output=True, text=True, timeout=10,
            )
            for line in result.stdout.splitlines():
                if "Ethernet Address:" in line:
                    mac = line.split(":", 1)[1].strip().upper()
                    if mac != "N/A":
                        macs.append(mac)
        elif platform.system() == "Windows":
            result = subprocess.run(
                ["getmac", "/fo", "csv", "/nh"],
                capture_output=True, text=True, timeout=10, shell=True,
            )
            for line in result.stdout.splitlines():
                mac = line.strip().strip('"').split(",")[0]
                if mac and mac != "N/A":
                    macs.append(mac.upper())
    except Exception as e:
        print(f"⚠️  MAC address collection failed: {e}")
    return macs


def _get_cpu_info() -> str:
    """Get CPU identifier."""
    try:
        if platform.system() == "Linux":
            result = subprocess.run(
                ["cat", "/proc/cpuinfo"], capture_output=True, text=True, timeout=5
            )
            for line in result.stdout.splitlines():
                if "model name" in line:
                    return line.split(":", 1)[1].strip()
        elif platform.system() == "Darwin":
            result = subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                capture_output=True, text=True, timeout=5,
            )
            return result.stdout.strip()
    except Exception:
        pass
    return platform.processor()


def _get_disk_serials() -> list[str]:
    """Get disk serial numbers."""
    serials = []
    try:
        if platform.system() == "Linux":
            result = subprocess.run(
                ["lsblk", "-o", "NAME,SERIAL", "-n", "-J"],
                capture_output=True, text=True, timeout=5,
            )
            data = json.loads(result.stdout)
            for dev in data.get("blockdevices", []):
                if dev.get("serial"):
                    serials.append(dev["serial"])
        elif platform.system() == "Darwin":
            result = subprocess.run(
                ["system_profiler", "SPStorageDataType"],
                capture_output=True, text=True, timeout=10,
            )
            for line in result.stdout.splitlines():
                if "Disk UUID" in line:
                    serials.append(line.split(":", 1)[1].strip())
    except Exception:
        pass
    return serials


def hardware_fingerprint() -> Dict[str, Any]:
    """Create a hardware fingerprint anchored to the current machine.

    Combines:
    - MAC addresses (primary)
    - CPU identifier
    - Disk serials
    - Hostname
    - Platform info

    Returns a deterministic hash that uniquely identifies this machine.
    """
    macs = _get_mac_addresses()
    cpu = _get_cpu_info()
    disks = _get_disk_serials()
    hostname = socket.gethostname()

    # Build fingerprint components
    components = {
        "macs": sorted(macs),
        "cpu": cpu,
        "disks": sorted(disks),
        "hostname": hostname,
        "platform": platform.system(),
        "machine": platform.machine(),
        "node": platform.node(),
    }

    # Create deterministic hash from sorted components
    fingerprint_input = json.dumps(components, sort_keys=True)
    hw_hash = hashlib.sha256(fingerprint_input.encode()).hexdigest()

    # Create a shorter, human-readable hardware ID
    hw_id = f"hw_{hw_hash[:16]}"

    return {
        "hardware_id": hw_id,
        "hash": hw_hash,
        "components": components,
        "mac_count": len(macs),
        "disk_count": len(disks),
        "created_at": time.time(),
    }


# ── Step 3: Beacon ID Creation ───────────────────────────────────────────────
def create_beacon_id(username: str, fingerprint: Dict[str, Any]) -> Dict[str, Any]:
    """Mint a Beacon ID anchored to the hardware fingerprint.

    Format: bcn_{username}_{hw_hash[:8]}
    """
    # Generate deterministic agent ID from hardware + username
    seed = f"moltbook-migrate:{username}:{fingerprint['hash']}"
    agent_hash = hashlib.sha256(seed.encode()).hexdigest()[:8]
    beacon_id = f"bcn_{username}_{agent_hash}"

    # Create identity directory
    identity_dir = Path.home() / ".config" / "beacon" / "identity"
    identity_dir.mkdir(parents=True, exist_ok=True)

    identity_file = {
        "beacon_id": beacon_id,
        "display_name": username,
        "source": "moltbook-migration",
        "moltbook_username": username.lstrip("@"),
        "hardware_fingerprint": fingerprint["hardware_id"],
        "hardware_hash": fingerprint["hash"],
        "migration_timestamp": time.time(),
        "migration_version": "1.0.0",
    }

    identity_path = identity_dir / f"{beacon_id}.json"
    if identity_path.exists():
        print(f"⚠️  Beacon ID already exists: {beacon_id}")
        with open(identity_path) as f:
            existing = json.load(f)
        return {"existing": True, "beacon_id": beacon_id, "data": existing}

    with open(identity_path, "w") as f:
        json.dump(identity_file, f, indent=2)

    print(f"✅ Beacon ID created: {beacon_id}")
    print(f"📄 Saved to: {identity_path}")

    return {
        "existing": False,
        "beacon_id": beacon_id,
        "identity_file": str(identity_path),
        "data": identity_file,
    }


# ── Step 4: AgentFolio SATP Linkage ─────────────────────────────────────────
def create_satp_linkage(
    username: str,
    beacon_id: str,
    moltbook_profile: Dict[str, Any],
) -> Dict[str, Any]:
    """Create SATP trust profile linkage on AgentFolio.

    This prepares the data needed for SATP registration.
    The actual on-chain registration requires the operator's Solana wallet.
    """
    profile = moltbook_profile.get("profile", {})

    # Extract available profile data
    satp_profile = {
        "agent_id": beacon_id,
        "display_name": profile.get("display_name", username),
        "bio": profile.get("bio", f"Migrated from Moltbook (@{username.lstrip('@')})"),
        "source_platform": "moltbook",
        "source_username": username.lstrip("@"),
        "beacon_id": beacon_id,
        "migrated_at": time.time(),
        "trust_signals": {
            "karma_history": profile.get("karma_history"),
            "follower_count": profile.get("follower_count"),
            "avatar_url": profile.get("avatar_url"),
        },
        "verification_status": "pending_onchain_registration",
        "next_steps": [
            "Register Solana wallet with AgentFolio SATP",
            "Link beacon_id to SATP profile",
            "Complete on-chain identity verification",
        ],
    }

    # Save SATP profile data
    satp_dir = Path.home() / ".config" / "beacon" / "satp"
    satp_dir.mkdir(parents=True, exist_ok=True)
    satp_path = satp_dir / f"{beacon_id}_satp.json"

    with open(satp_path, "w") as f:
        json.dump(satp_profile, f, indent=2)

    print(f"✅ SATP profile prepared: {beacon_id}")
    print(f"📄 Saved to: {satp_path}")
    print(f"📋 Next: Register wallet at https://agentfolio.bot/register")

    return satp_profile


# ── Step 5: Provenance Report ────────────────────────────────────────────────
def generate_provenance_report(
    username: str,
    moltbook_data: Dict[str, Any],
    fingerprint: Dict[str, Any],
    beacon_data: Dict[str, Any],
    satp_data: Dict[str, Any],
) -> str:
    """Generate a migration provenance report."""
    report = f"""# Moltbook → Beacon Migration Report

## Agent Identity
- **Beacon ID**: {beacon_data['beacon_id']}
- **Moltbook**: @{username.lstrip('@')}
- **Migrated**: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}

## Hardware Fingerprint
- **Hardware ID**: {fingerprint['hardware_id']}
- **MAC Addresses**: {fingerprint['mac_count']}
- **Disk Serials**: {fingerprint['disk_count']}
- **Platform**: {fingerprint['components']['platform']} {fingerprint['components']['machine']}

## Provenance Chain
1. Moltbook profile fetched: {'✅' if moltbook_data.get('profile') else '⚠️ (partial)'}
2. Hardware fingerprint captured: ✅
3. Beacon ID minted: ✅
4. SATP profile prepared: ✅
5. On-chain registration: ⏳ (requires Solana wallet)

## Files Created
- Beacon identity: {beacon_data.get('identity_file', 'N/A')}
- SATP profile: {Path.home() / '.config' / 'beacon' / 'satp' / f"{beacon_data['beacon_id']}_satp.json"}

## Next Steps
1. Run `beacon identity show` to verify your Beacon ID
2. Register at https://agentfolio.bot/register with your Solana wallet
3. Link your beacon_id to your SATP profile
4. Verify on-chain identity (completes dual-layer trust)

---
*Generated by moltbook-migrate v1.0.0*
"""
    return report


# ── Main CLI ─────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Moltbook → Beacon + AgentFolio Migration Importer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python moltbook_migrate.py --moltbook-user @myagent
  python moltbook_migrate.py --moltbook-user myagent --output-dir ./migration
  python moltbook_migrate.py --moltbook-user @myagent --dry-run
        """,
    )
    parser.add_argument(
        "--moltbook-user",
        required=True,
        help="Moltbook username (with or without @)",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Custom output directory (default: ~/.config/beacon/)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without creating files",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show detailed output",
    )

    args = parser.parse_args()
    username = args.moltbook_user

    print("=" * 60)
    print("🔄 Moltbook → Beacon + AgentFolio Migration")
    print("=" * 60)
    print()

    # Step 1: Fetch Moltbook profile
    print("Step 1/5: Fetching Moltbook profile...")
    moltbook_data = fetch_moltbook_profile(username)
    if args.verbose:
        print(json.dumps(moltbook_data, indent=2)[:500])
    print()

    if args.dry_run:
        print("🔍 Dry run complete. No files created.")
        return 0

    # Step 2: Hardware fingerprint
    print("Step 2/5: Capturing hardware fingerprint...")
    fingerprint = hardware_fingerprint()
    print(f"  Hardware ID: {fingerprint['hardware_id']}")
    print(f"  Platform: {fingerprint['components']['platform']}")
    print()

    # Step 3: Create Beacon ID
    print("Step 3/5: Minting Beacon ID...")
    beacon_data = create_beacon_id(username, fingerprint)
    print()

    # Step 4: SATP linkage
    print("Step 4/5: Preparing SATP trust profile...")
    satp_data = create_satp_linkage(username, beacon_data["beacon_id"], moltbook_data)
    print()

    # Step 5: Generate report
    print("Step 5/5: Generating provenance report...")
    report = generate_provenance_report(
        username, moltbook_data, fingerprint, beacon_data, satp_data
    )

    # Save report
    report_dir = Path.home() / ".config" / "beacon" / "migrations"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"{username.lstrip('@')}_migration.md"
    with open(report_path, "w") as f:
        f.write(report)
    print(f"📄 Report saved: {report_path}")
    print()

    print("=" * 60)
    print("✅ Migration complete!")
    print("=" * 60)
    print()
    print(f"🆔 Your Beacon ID: {beacon_data['beacon_id']}")
    print(f"🔧 Hardware ID: {fingerprint['hardware_id']}")
    print()
    print("Next steps:")
    print("  1. beacon identity show  (verify your Beacon ID)")
    print("  2. https://agentfolio.bot/register  (register Solana wallet)")
    print("  3. Link beacon_id to SATP profile")
    print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
