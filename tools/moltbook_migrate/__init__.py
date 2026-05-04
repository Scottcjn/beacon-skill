# tools/moltbook_migrate/__init__.py
"""
Moltbook Migration Tool — Beacon Protocol Migration Path.

This package provides tools for migrating agent identities from Moltbook
to the Beacon Protocol with AgentFolio SATP integration.

Usage:
    from tools.moltbook_migrate import MoltbookMigrator
    migrator = MoltbookMigrator()
    result = await migrator.migrate("@agent_name")
"""

__version__ = "1.0.0"

from .migrate import MoltbookMigrator, MigrationError, MigrationResult
from .hardware import HardwareFingerprint, HardwareFingerprintError
from .moltbook_api import MoltbookProfile, MoltbookAPIError, MoltbookClient
from .cli import main as cli_main

__all__ = [
    "__version__",
    "MoltbookMigrator",
    "MigrationError",
    "MigrationResult",
    "HardwareFingerprint",
    "HardwareFingerprintError",
    "MoltbookProfile",
    "MoltbookAPIError",
    "MoltbookClient",
    "cli_main",
]