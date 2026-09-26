# SPDX-License-Identifier: MIT
"""Regression tests: every version source must agree, and `beacon --help`
must show the real version (it used to hard-code "Beacon 2.4.0")."""
import json
import re
from pathlib import Path

import pytest

from beacon_skill import __version__, cli

ROOT = Path(__file__).resolve().parent.parent


def test_pyproject_version_matches_package():
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    assert m, "no version in pyproject.toml"
    assert m.group(1) == __version__


def test_package_json_version_matches_package():
    data = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    assert data["version"] == __version__


def test_help_banner_shows_real_version(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    out = capsys.readouterr().out
    assert f"Beacon {__version__}" in out
    assert "Beacon 2.4.0" not in out
