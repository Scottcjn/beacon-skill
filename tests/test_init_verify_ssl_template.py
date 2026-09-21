# SPDX-License-Identifier: MIT
"""Regression tests: `beacon init` must not seed an insecure config, and an
existing insecure config must be called out.

Background: PR #925 (@xxzzzzy) flips the *code* default for
``rustchain.verify_ssl`` to True, but the interactive ``beacon init``
questionnaire kept writing ``"verify_ssl": false`` into every new config, so
anyone who ran init stayed insecure regardless of the code default. These
tests pin both the template and the one-time migration warning.
"""
import argparse
import builtins
import io
import json
import sys

import pytest

import beacon_skill.config as config_mod
from beacon_skill import cli


@pytest.fixture(autouse=True)
def _reset_warning_state(monkeypatch):
    monkeypatch.setattr(config_mod, "_INSECURE_TLS_WARNED", False)
    monkeypatch.delenv("BEACON_INSECURE_SKIP_TLS_VERIFY", raising=False)
    yield


# ── beacon init template ──────────────────────────────────────────────────


def _run_interactive_init(tmp_path, monkeypatch):
    """Drive the questionnaire accepting every default (Enter on each prompt)."""
    # cmd_init does `from .config import ensure_config_dir` locally, so patch
    # the source module, not the cli namespace.
    monkeypatch.setattr(config_mod, "ensure_config_dir", lambda: tmp_path)
    monkeypatch.setattr(builtins, "input", lambda *_a, **_k: "")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    args = argparse.Namespace(quick=False, overwrite=True)
    rc = cli.cmd_init(args)
    assert rc == 0
    return json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))


def test_interactive_init_writes_verify_ssl_true(tmp_path, monkeypatch):
    cfg = _run_interactive_init(tmp_path, monkeypatch)
    assert cfg["rustchain"]["verify_ssl"] is True
    assert cfg["rustchain"]["base_url"] == "https://rustchain.org"


def test_quick_init_writes_verify_ssl_true(tmp_path, monkeypatch):
    monkeypatch.setattr(config_mod, "ensure_config_dir", lambda: tmp_path)
    path = config_mod.write_default_config(overwrite=True)
    cfg = json.loads(path.read_text(encoding="utf-8"))
    assert cfg["rustchain"]["verify_ssl"] is True


# ── detection helper ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "cfg, expected",
    [
        ({}, False),
        ({"rustchain": {}}, False),
        ({"rustchain": {"verify_ssl": True}}, False),
        ({"rustchain": {"verify_ssl": None}}, False),
        ({"rustchain": {"verify_ssl": ""}}, False),
        ({"rustchain": "not-a-dict"}, False),
        ({"rustchain": {"verify_ssl": False}}, True),
        ({"rustchain": {"verify_ssl": 0}}, True),
        ({"rustchain": {"verify_ssl": "false"}}, True),
        ({"rustchain": {"verify_ssl": "0"}}, True),
        ({"rustchain": {"verify_ssl": "No"}}, True),
    ],
)
def test_rustchain_verify_ssl_is_off(cfg, expected):
    assert config_mod.rustchain_verify_ssl_is_off(cfg) is expected


# ── migration warning on load ─────────────────────────────────────────────


def _write_cfg(tmp_path, monkeypatch, cfg):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    monkeypatch.setenv("BEACON_CONFIG_PATH", str(path))
    return path


def test_load_config_warns_once_for_insecure_config(tmp_path, monkeypatch, capsys):
    _write_cfg(tmp_path, monkeypatch, {"rustchain": {"verify_ssl": False}})

    cfg = config_mod.load_config()
    assert cfg["rustchain"]["verify_ssl"] is False  # never rewritten silently
    err = capsys.readouterr().err
    assert "verify_ssl is false" in err
    assert "beacon init" in err

    # Second load in the same process stays quiet.
    config_mod.load_config()
    assert capsys.readouterr().err == ""


def test_load_config_silent_for_secure_config(tmp_path, monkeypatch, capsys):
    _write_cfg(tmp_path, monkeypatch, {"rustchain": {"verify_ssl": True}})
    config_mod.load_config()
    assert capsys.readouterr().err == ""


def test_load_config_silent_when_key_absent(tmp_path, monkeypatch, capsys):
    _write_cfg(tmp_path, monkeypatch, {"rustchain": {"base_url": "https://rustchain.org"}})
    config_mod.load_config()
    assert capsys.readouterr().err == ""


def test_warning_suppressed_by_explicit_env_optout(tmp_path, monkeypatch, capsys):
    _write_cfg(tmp_path, monkeypatch, {"rustchain": {"verify_ssl": False}})
    monkeypatch.setenv("BEACON_INSECURE_SKIP_TLS_VERIFY", "1")
    config_mod.load_config()
    assert capsys.readouterr().err == ""


def test_warn_helper_writes_to_given_stream():
    buf = io.StringIO()
    warned = config_mod.warn_if_insecure_rustchain_tls({"rustchain": {"verify_ssl": "false"}}, stream=buf)
    assert warned is True
    assert "TLS certificate verification is OFF" in buf.getvalue()
    # Once per process.
    assert config_mod.warn_if_insecure_rustchain_tls({"rustchain": {"verify_ssl": False}}, stream=buf) is False


def test_load_config_non_dict_json_returns_empty(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    monkeypatch.setenv("BEACON_CONFIG_PATH", str(path))
    assert config_mod.load_config() == {}
