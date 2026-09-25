#!/usr/bin/env python3
"""CLI for migrating agents from Moltbook to Beacon Protocol."""

import argparse
import sys

from .migrate import MoltbookMigrator


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
        print(f"Migrating agent: {agent_name}")
        if args.dry_run:
            print("  (Dry run mode — no changes will be made)")

    try:
        result = migrator.migrate(agent_name=agent_name, dry_run=args.dry_run)
    except Exception as e:
        print(f"Error: Migration failed: {e}", file=sys.stderr)
        sys.exit(1)

    if result.success:
        print("Migration completed successfully!")
        if result.beacon_id:
            print(f"   Beacon ID: {result.beacon_id.agent_id}")
        if result.agentfolio_link:
            print(f"   SATP Profile: {result.agentfolio_link.satp_profile_id}")
        if result.dry_run:
            print("   (Dry run — no changes were made)")
        sys.exit(0)
    else:
        print("Migration failed!")
        if result.error_message:
            print(f"   Error: {result.error_message}")
        sys.exit(1)


if __name__ == "__main__":
    main()
