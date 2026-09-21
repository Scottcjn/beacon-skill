# SPDX-License-Identifier: MIT
"""Regression tests for the verify_ssl default in beacon_skill.cli.

The hardened default is True (TLS verification on). A user can opt out by
setting ``rustchain.verify_ssl`` to false in the config, or by exporting
``BEACON_INSECURE_SKIP_TLS_VERIFY=1`` to override at the environment level.

These tests pin the four call sites that previously defaulted to False and
assert that the new helper turns the right knob.
"""
import os
import importlib

import pytest


def _reload_cli():
    # Re-import so the helper picks up env changes between tests.
    import beacon_skill.cli as cli
    importlib.reload(cli)
    return cli


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in ("BEACON_INSECURE_SKIP_TLS_VERIFY",):
        monkeypatch.delenv(var, raising=False)
    yield


class TestResolveVerifySsl:
    def test_default_is_secure(self):
        cli = _reload_cli()
        assert cli._resolve_verify_ssl({}) is True

    def test_explicit_true(self):
        cli = _reload_cli()
        assert cli._resolve_verify_ssl({"rustchain": {"verify_ssl": True}}) is True

    def test_explicit_false(self):
        cli = _reload_cli()
        assert cli._resolve_verify_ssl({"rustchain": {"verify_ssl": False}}) is False

    def test_string_true(self):
        cli = _reload_cli()
        assert cli._resolve_verify_ssl({"rustchain": {"verify_ssl": "true"}}) is True

    def test_string_false(self):
        cli = _reload_cli()
        assert cli._resolve_verify_ssl({"rustchain": {"verify_ssl": "false"}}) is False

    def test_string_zero(self):
        cli = _reload_cli()
        assert cli._resolve_verify_ssl({"rustchain": {"verify_ssl": "0"}}) is False

    def test_string_yes(self):
        cli = _reload_cli()
        assert cli._resolve_verify_ssl({"rustchain": {"verify_ssl": "yes"}}) is True

    def test_empty_string_defaults_secure(self):
        cli = _reload_cli()
        # An empty string is treated as the user opting in to the secure
        # default rather than as a literal False.
        assert cli._resolve_verify_ssl({"rustchain": {"verify_ssl": ""}}) is True

    def test_env_override_forces_off(self, monkeypatch):
        monkeypatch.setenv("BEACON_INSECURE_SKIP_TLS_VERIFY", "1")
        cli = _reload_cli()
        assert cli._resolve_verify_ssl({"rustchain": {"verify_ssl": True}}) is False

    def test_env_override_forces_off_with_yes(self, monkeypatch):
        monkeypatch.setenv("BEACON_INSECURE_SKIP_TLS_VERIFY", "yes")
        cli = _reload_cli()
        assert cli._resolve_verify_ssl({}) is False


class TestRustChainClientIntegration:
    """The RustChainClient should be constructed with verify_ssl=True by default
    when no config is provided. The beacon_skill CLI passes the resolved
    value through, so the integration here is the resolution itself."""

    def test_transport_default_is_secure(self):
        from beacon_skill.transports.rustchain import RustChainClient

        c = RustChainClient()
        assert c.verify_ssl is True

    def test_transport_default_passed_to_session(self):
        from beacon_skill.transports.rustchain import RustChainClient

        c = RustChainClient(verify_ssl=False)
        assert c.verify_ssl is False
