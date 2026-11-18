import re
from pathlib import Path


def test_readme_quickstart_is_deduplicated_and_has_no_ellipsis():
    readme = Path("README.md").read_text(encoding="utf-8")

    # Exactly one Quick Start heading
    assert readme.count("## Quick Start (2 minutes)") == 1
    assert readme.count("### 🤖 AI Agent Quick Start") == 1
    assert readme.count("### Human Quick Start") == 1

    # Extract Quick Start section
    qs_match = re.search(
        r"## Quick Start \(2 minutes\)(.*?)## Getting Started",
        readme,
        re.DOTALL
    )
    assert qs_match is not None, "Quick Start section not found"
    qs_text = qs_match.group(1)

    # Check that literal ... is not present as a command line
    for line in qs_text.splitlines():
        stripped = line.strip()
        assert stripped != "...", f"Literal ... found in Quick Start: {line}"

    # Loopback commands are present and valid
    assert "beacon webhook serve --port 8402" in qs_text
    assert "beacon webhook send http://127.0.0.1:8402/beacon/inbox" in qs_text
    assert "beacon identity new" in qs_text
