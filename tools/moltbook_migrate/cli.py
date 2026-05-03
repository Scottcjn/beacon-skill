#!/usr/bin/env python3
"""CLI for migrating agents from Moltbook to Beacon Protocol."""

import argparse
import sys
from typing import Optional

from .migrate import MoltbookMigrator
from .hardware import HardwareFingerprint


def main() -> None:
    """Main entry point for the beacon migration CLI."""
    parser = argparse.ArgumentParser(
        prog="beacon",
        description="Beacon Protocol migration tool for Moltbook agents",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    migrate_parser = subparsers.add_parser(
        "migrate",
        help="Migrate an agent from Moltbook to Beacon Protocol",
    )
    migrate_parser.add_argument(
        "--from-moltbook",
        required=True,
        metavar="@agent_name",
        help="Name of the Moltbook agent to migrate",
    )
    migrate_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate migration without making changes",
    )
    migrate_parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose output",
    )

    args = parser.parse_args()

    if args.command != "migrate":
        parser.print_help()
        sys.exit(1)

    agent_name = args.from_moltbook.lstrip("@")
    migrator = MoltbookMigrator()

    if args.verbose:
        print(f"[1/4] Initializing migration for agent: {agent_name}")

    # Step 1: Fetch Moltbook profile
    if args.verbose:
        print("[2/4] Fetching Moltbook profile...")
    try:
        profile = migrator.client.fetch_profile(agent_name)
        if args.verbose:
            print(f"      Profile loaded: {profile.name}")
    except Exception as e:
        print(f"Error: Failed to fetch Moltbook profile: {e}", file=sys.stderr)
        sys.exit(1)

    # Step 2: Collect hardware fingerprint
    if args.verbose:
        print("[3/4] Collecting hardware fingerprint...")
    try:
        fingerprint = HardwareFingerprint()
        hw_data = fingerprint.collect()
        if args.verbose:
            print(f"      Fingerprint collected: {hw_data.get('fingerprint_id', 'N/A')[:16]}...")
    except Exception as e:
        print(f"Error: Failed to collect hardware fingerprint: {e}", file=sys.stderr)
        sys.exit(1)

    # Step 3: Perform migration
    if args.verbose:
        print("[4/4] Running migration...")
    try:
        result = migrator.migrate(
            agent_name=agent_name,
            hardware_fingerprint=hw_data,
            dry_run=args.dry_run,
        )
    except Exception as e:
        print(f"Error: Migration failed: {e}", file=sys.stderr)
        sys.exit(1)

    # Print result summary
    print()
    if result.success:
        print("✅ Migration completed successfully!")
        print(f"   Beacon ID: {result.beacon_id}")
        print(f"   Profile: {result.profile_url}")
        if result.dry_run:
            print("   (Dry run - no changes were made)")
        sys.exit(0)
    else:
        print("❌ Migration failed!")
        if result.error_message:
            print(f"   Error: {result.error_message}")
        if result.beacon_id:
            print(f"   Beacon ID: {result.beacon_id}")
        sys.exit(1)


if __name__ == "__main__":
    main()