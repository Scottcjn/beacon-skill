"""Smoke tests for beacon-skill installation."""

import subprocess
import sys


def test_beacon_version():
    """Test that beacon --version works."""
    result = subprocess.run(
        ["beacon", "--version"],
        capture_output=True,
        text=True
    )
    assert result.returncode == 0, f"beacon --version failed: {result.stderr}"
    print(f"✓ beacon --version: {result.stdout.strip()}")


def test_beacon_help():
    """Test that beacon --help works."""
    result = subprocess.run(
        ["beacon", "--help"],
        capture_output=True,
        text=True
    )
    assert result.returncode == 0, f"beacon --help failed: {result.stderr}"
    assert "beacon" in result.stdout.lower()
    print("✓ beacon --help works")


def test_beacon_identity_help():
    """Test that beacon identity subcommand is available."""
    result = subprocess.run(
        ["beacon", "identity", "--help"],
        capture_output=True,
        text=True
    )
    assert result.returncode == 0, f"beacon identity --help failed: {result.stderr}"
    print("✓ beacon identity --help works")


def test_beacon_webhook_help():
    """Test that beacon webhook subcommand is available."""
    result = subprocess.run(
        ["beacon", "webhook", "--help"],
        capture_output=True,
        text=True
    )
    assert result.returncode == 0, f"beacon webhook --help failed: {result.stderr}"
    print("✓ beacon webhook --help works")


def test_beacon_webhook_send_help():
    """Test that beacon webhook send subcommand is available."""
    result = subprocess.run(
        ["beacon", "webhook", "send", "--help"],
        capture_output=True,
        text=True
    )
    assert result.returncode == 0, f"beacon webhook send --help failed: {result.stderr}"
    print("✓ beacon webhook send --help works")


if __name__ == "__main__":
    tests = [
        test_beacon_version,
        test_beacon_help,
        test_beacon_identity_help,
        test_beacon_webhook_help,
        test_beacon_webhook_send_help,
    ]
    
    failed = 0
    for test in tests:
        try:
            test()
        except AssertionError as e:
            print(f"✗ {test.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"✗ {test.__name__}: {e}")
            failed += 1
    
    if failed:
        print(f"\n{failed} test(s) failed")
        sys.exit(1)
    else:
        print("\n✓ All smoke tests passed!")
        sys.exit(0)
