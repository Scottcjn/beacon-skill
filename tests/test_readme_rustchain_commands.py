from pathlib import Path


def test_readme_uses_existing_rustchain_payment_command():
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "beacon rustchain pay" in readme
    assert "beacon rustchain send" not in readme
