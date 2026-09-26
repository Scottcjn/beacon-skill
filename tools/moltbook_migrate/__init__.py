# tools/moltbook_migrate/__init__.py
"""Backward-compatible alias for :mod:`beacon_skill.moltbook_migrate`.

The migration tool now lives inside the installed package so that
``beacon migrate`` works from a wheel. This shim keeps repo-level imports
such as ``from tools.moltbook_migrate import MoltbookMigrator`` working from
a source checkout; run the CLI as ``beacon migrate`` or
``python -m beacon_skill.moltbook_migrate.cli``.
The submodules are aliased (not copied), so patching
``tools.moltbook_migrate.migrate.X`` patches the real module.
"""

import sys

from beacon_skill import moltbook_migrate as _pkg
from beacon_skill.moltbook_migrate import cli, hardware, migrate, moltbook_api

for _name, _mod in (("cli", cli), ("hardware", hardware), ("migrate", migrate), ("moltbook_api", moltbook_api)):
    sys.modules[f"{__name__}.{_name}"] = _mod
sys.modules[__name__] = _pkg
