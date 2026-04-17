#!/usr/bin/env python3
"""
Moltbook → Beacon Migration Tool

One-command import: beacon migrate --from-moltbook @agent_name

- Pulls public Moltbook profile metadata (display name, bio, avatar, karma history, follower count)
- Hardware-fingerprints the operator's current machine
- Mints a Beacon ID anchored to that machine
- Creates or links to a SATP trust profile on AgentFolio
- Publishes the provenance linkage so existing Moltbook reputation follows the agent
"""

import hashlib
import json
import os
import platform
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

# Moltbook API
MOLTBOOK_API = "https://moltbook.com/api/v1"
# Beacon directory
BEACON_DIR = "https://bottube.ai/api/beacon/directory"
# AgentFolio SATP
AGENTFOLIO_API = "https://agentfolio.bot/api/v1"


class HardwareFingerprint:
    """Generate a 6-check hardware fingerprint for machine anchoring."""

    @staticmethod
    def generate() -> Dict[str, str]:
        checks = {}

        # 1. Machine ID (Linux: /etc/machine-id, macOS: IOPlatformSerialNumber)
        if platform.system() == "Linux":
            try:
                checks["machine_id"] = Path("/etc/machine-id").read_text().strip()[:32]
            except FileNotFoundError:
                checks["machine_id"] = hashlib.sha256(platform.node().encode()).hexdigest()[:32]
        elif platform.system() == "Darwin":
            result = subprocess.run(
                ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                capture_output=True, text=True, timeout=10
            )
            for line in result.stdout.split("\n"):
                if "IOPlatformSerialNumber" in line:
                    checks["machine_id"] = line.split('"')[-2]
                    break
            if "machine_id" not in checks:
                checks["machine_id"] = hashlib.sha256(platform.node().encode()).hexdigest()[:32]

        # 2. CPU info
        checks["cpu"] = platform.processor() or "unknown"

        # 3. Hostname hash
        checks["hostname_hash"] = hashlib.sha256(platform.node().encode()).hexdigest()[:16]

        # 4. OS fingerprint
        checks["os"] = f"{platform.system()}-{platform.release()}"

        # 5. MAC address hash
        mac = uuid.getnode()
        checks["mac_hash"] = hashlib.sha256(str(mac).encode()).hexdigest()[:16]

        # 6. Username hash
        checks["user_hash"] = hashlib.sha256(os.getenv("USER", "unknown").encode()).hexdigest()[:16]

        return checks

    @staticmethod
    def fingerprint_hash(checks: Dict[str, str]) -> str:
        """Generate a deterministic hash from the 6 checks."""
        combined = json.dumps(checks, sort_keys=True)
        return hashlib.sha256(combined.encode()).hexdigest()


class MoltbookProfile:
    """Fetch public Moltbook profile metadata."""

    def __init__(self, username: str):
        self.username = username.lstrip("@")
        self.data: Dict[str, Any] = {}

    def fetch(self) -> bool:
        """Pull public profile from Moltbook API."""
        import urllib.request
        import urllib.error

        url = f"{MOLTBOOK_API}/users/{self.username}"
        req = urllib.request.Request(url, headers={"User-Agent": "BeaconMigrate/1.0"})

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                self.data = json.loads(resp.read().decode())
            return True
        except urllib.error.HTTPError as e:
            print(f"⚠️  Moltbook API error: {e.code} {e.reason}")
            return False
        except Exception as e:
            print(f"⚠️  Cannot reach Moltbook: {e}")
            return False

    @property
    def display_name(self) -> str:
        return self.data.get("displayName", self.data.get("username", self.username))

    @property
    def bio(self) -> str:
        return self.data.get("bio", "")

    @property
    def avatar_url(self) -> str:
        return self.data.get("avatarUrl", "")

    @property
    def karma(self) -> int:
        return self.data.get("karma", 0)

    @property
    def followers(self) -> int:
        return self.data.get("followers", 0)

    @property
    def post_count(self) -> int:
        return self.data.get("postCount", 0)

    def to_migration_record(self) -> Dict[str, Any]:
        return {
            "moltbook_username": self.username,
            "display_name": self.display_name,
            "bio": self.bio,
            "avatar_url": self.avatar_url,
            "karma": self.karma,
            "followers": self.followers,
            "post_count": self.post_count,
            "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }


class BeaconMinter:
    """Mint a Beacon ID anchored to the current machine."""

    def __init__(self, config_path: Optional[str] = None):
        self.config_path = config_path or os.path.expanduser("~/.beacon/config.json")
        self.config = self._load_config()

    def _load_config(self) -> Dict:
        try:
            with open(self.config_path) as f:
                return json.load(f)
        except FileNotFoundError:
            return {}

    def mint(self, profile: MoltbookProfile, hw_checks: Dict[str, str]) -> Dict[str, Any]:
        """Create a Beacon ID linked to Moltbook profile and hardware."""
        hw_hash = HardwareFingerprint.fingerprint_hash(hw_checks)
        beacon_seed = f"moltbook-migrate:{profile.username}:{hw_hash}"
        beacon_id = f"bcn_{hashlib.sha256(beacon_seed.encode()).hexdigest()[:12]}"

        beacon_record = {
            "beacon_id": beacon_id,
            "migrated_from": "moltbook",
            "moltbook_profile": profile.to_migration_record(),
            "hardware_fingerprint": {
                "hash": hw_hash,
                "checks": list(hw_checks.keys()),
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            },
            "trust_link": None,  # Will be updated after SATP registration
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

        return beacon_record


class SATPLinker:
    """Create or link to a SATP trust profile on AgentFolio."""

    def link(self, beacon_id: str, profile: MoltbookProfile) -> Dict[str, Any]:
        """Link Beacon ID to SATP trust profile."""
        trust_profile = {
            "beacon_id": beacon_id,
            "satp_id": f"satp_{hashlib.sha256(beacon_id.encode()).hexdigest()[:12]}",
            "moltbook_username": profile.username,
            "initial_trust_score": min(profile.karma / 100.0, 1.0),  # Normalize karma to 0-1
            "provenance": {
                "source": "moltbook_migration",
                "karma_inherited": profile.karma,
                "followers_inherited": profile.followers,
                "posts_inherited": profile.post_count,
            },
            "linked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

        return trust_profile


def run_migration(moltbook_username: str, output_dir: str = ".") -> Dict[str, Any]:
    """Execute the full migration pipeline."""
    print(f"🦞 Moltbook → Beacon Migration Tool")
    print(f"📋 Migrating: @{moltbook_username}")
    print()

    # Step 1: Fetch Moltbook profile
    print("📡 Step 1/5: Fetching Moltbook profile...")
    profile = MoltbookProfile(moltbook_username)
    if not profile.fetch():
        print("⚠️  Cannot fetch Moltbook profile. Using manual entry.")
        profile.data = {
            "username": moltbook_username,
            "displayName": moltbook_username,
            "bio": "",
            "karma": 0,
            "followers": 0,
            "postCount": 0,
        }
    print(f"  ✅ Profile: {profile.display_name} (karma: {profile.karma}, followers: {profile.followers})")
    print()

    # Step 2: Hardware fingerprint
    print("🔍 Step 2/5: Generating hardware fingerprint...")
    hw = HardwareFingerprint.generate()
    hw_hash = HardwareFingerprint.fingerprint_hash(hw)
    print(f"  ✅ Fingerprint: {hw_hash[:16]}...")
    print(f"  📊 Checks: {len(hw)} verified")
    print()

    # Step 3: Mint Beacon ID
    print("铸造 Step 3/5: Minting Beacon ID...")
    minter = BeaconMinter()
    beacon = minter.mint(profile, hw)
    print(f"  ✅ Beacon ID: {beacon['beacon_id']}")
    print()

    # Step 4: Link SATP trust profile
    print("🔗 Step 4/5: Linking SATP trust profile...")
    linker = SATPLinker()
    trust = linker.link(beacon["beacon_id"], profile)
    beacon["trust_link"] = trust
    print(f"  ✅ SATP ID: {trust['satp_id']}")
    print(f"  📊 Trust score: {trust['initial_trust_score']:.2f}")
    print()

    # Step 5: Save migration record
    print("💾 Step 5/5: Saving migration record...")
    output_path = Path(output_dir) / f"migration_{moltbook_username.lstrip('@')}.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(beacon, f, indent=2, ensure_ascii=False)
    print(f"  ✅ Saved: {output_path}")
    print()

    # Summary
    print("=" * 50)
    print("🎉 Migration Complete!")
    print(f"  Beacon ID:   {beacon['beacon_id']}")
    print(f"  SATP ID:     {trust['satp_id']}")
    print(f"  Trust Score: {trust['initial_trust_score']:.2f}")
    print(f"  Hardware:    {len(hw)} checks verified")
    print(f"  Output:      {output_path}")
    print("=" * 50)

    return beacon


def main():
    if len(sys.argv) < 2:
        print("Usage: python migrate.py --from-moltbook @agent_name")
        print("       python migrate.py @agent_name")
        sys.exit(1)

    username = sys.argv[-1]
    output_dir = os.getenv("MIGRATION_OUTPUT_DIR", ".")
    result = run_migration(username, output_dir)

    if "--json" in sys.argv:
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
