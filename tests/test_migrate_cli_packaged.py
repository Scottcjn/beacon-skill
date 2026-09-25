# SPDX-License-Identifier: MIT
"""Regression test: `beacon migrate` must not depend on the repo-level
``tools`` directory, which is not shipped in the wheel (it used to raise
ModuleNotFoundError: No module named 'tools' from an installed package)."""
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from beacon_skill import cli


def test_beacon_migrate_runs_without_repo_tools_dir(monkeypatch, capsys):
    # Make any `import tools...` fail, as it does from an installed wheel.
    for name in [m for m in sys.modules if m == "tools" or m.startswith("tools.")]:
        monkeypatch.delitem(sys.modules, name)
    monkeypatch.setitem(sys.modules, "tools", None)
    monkeypatch.setattr(sys, "argv", list(sys.argv))

    fake_result = SimpleNamespace(
        success=True, beacon_id=None, agentfolio_link=None, dry_run=True, error_message=None,
    )
    with patch("beacon_skill.moltbook_migrate.cli.MoltbookMigrator") as migrator_cls:
        migrator_cls.return_value.migrate.return_value = fake_result
        with pytest.raises(SystemExit) as exc:
            cli.main(["migrate", "--from-moltbook", "@someagent", "--dry-run"])

    assert exc.value.code == 0
    migrator_cls.return_value.migrate.assert_called_once_with(agent_name="someagent", dry_run=True)
    assert "Migration completed successfully!" in capsys.readouterr().out


def test_repo_level_tools_alias_still_importable():
    pytest.importorskip("tools.moltbook_migrate")
    import tools.moltbook_migrate.migrate as legacy
    import beacon_skill.moltbook_migrate.migrate as packaged

    assert legacy is packaged
