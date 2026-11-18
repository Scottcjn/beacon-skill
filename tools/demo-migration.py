#!/usr/bin/env python3
"""
Demo Script for Moltbook → Beacon + AgentFolio Migration

This script demonstrates the migration flow for the bounty #2890 demo video.
It simulates migrating a Moltbook agent to Beacon Protocol with AgentFolio SATP integration.

Usage:
    python demo_migration.py --from-moltbook @test_user
"""

import argparse
import json
import sys
import time
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from tools.moltbook_migrate.migrate import migrate


def main():
    parser = argparse.ArgumentParser(
        description="Demo: Moltbook → Beacon + AgentFolio Migration",
    )
    parser.add_argument(
        "--from-moltbook",
        required=True,
        help="Moltbook username (with or without @)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="migration_demo.json",
        help="Output file for migration result (default: migration_demo.json)",
    )
    
    args = parser.parse_args()
    
    print("=" * 70)
    print("🎬 DEMO: Moltbook → Beacon + AgentFolio Migration")
    print("=" * 70)
    print(f"\nMigrating agent: @{args.from_moltbook}\n")
    
    # Execute migration
    result = migrate(
        username=args.from_moltbook,
        is_human=False,
        skip_agentfolio=False,
    )
    
    # Save result
    output_data = {
        "status": result.status,
        "beacon_id": result.beacon_registration.beacon_id if result.beacon_registration else None,
        "moltbook_username": result.moltbook_profile.username if result.moltbook_profile else None,
        "agentfolio_linked": result.agentfolio_link.linked if result.agentfolio_link else False,
        "provenance_published": result.provenance_published,
        "errors": result.errors,
        "elapsed_seconds": (
            result.completed_at - result.started_at
            if result.completed_at
            else None
        ),
    }
    
    output_path = Path(args.output)
    output_path.write_text(json.dumps(output_data, indent=2))
    
    print(f"\n✅ Demo complete!")
    print(f"   Result saved to: {output_path}")
    print(f"\n{json.dumps(output_data, indent=2)}")


if __name__ == "__main__":
    main()
