# SPDX-License-Identifier: MIT
"""Regression tests: every RustChain client the CLI builds must verify TLS
unless the config explicitly opts out.

Before this fix, cli.py built RustChainClient with ``verify_ssl`` defaulting
to False when the config had no ``rustchain.verify_ssl`` key (and treated the
string ``"false"`` as truthy), contradicting the secure default documented in
config.py and the CHANGELOG.
"""
import argparse

import pytest

from beacon_skill import cli


class _FakeClient:
    instances = []

    def __init__(self, base_url="", verify_ssl=True, **kwargs):
        self.base_url = base_url
        self.verify_ssl = verify_ssl
        _FakeClient.instances.append(self)

    def balance(self, address):
        return {"address": address}

    def sign_transfer(self, **kwargs):
        return {"nonce": 1}


@pytest.fixture
def fake_client(monkeypatch):
    _FakeClient.instances = []
    monkeypatch.setattr(cli, "RustChainClient", _FakeClient)
    return _FakeClient


def _use_config(monkeypatch, cfg):
    monkeypatch.setattr(cli, "load_config", lambda *a, **k: cfg)


# (config, expected verify_ssl)
CASES = [
    ({}, True),
    ({"rustchain": {}}, True),
    ({"rustchain": {"verify_ssl": None}}, True),
    ({"rustchain": {"verify_ssl": True}}, True),
    ({"rustchain": {"verify_ssl": False}}, False),
    ({"rustchain": {"verify_ssl": "false"}}, False),
    ({"rustchain": {"verify_ssl": 0}}, False),
]


@pytest.mark.parametrize("cfg,expected", CASES)
def test_rustchain_balance_verify_ssl(monkeypatch, fake_client, cfg, expected, capsys):
    _use_config(monkeypatch, cfg)
    assert cli.cmd_rustchain_balance(argparse.Namespace(address="RTCabc")) == 0
    assert fake_client.instances[-1].verify_ssl is expected


@pytest.mark.parametrize("cfg,expected", CASES)
def test_rustchain_pay_verify_ssl(monkeypatch, fake_client, cfg, expected, capsys):
    _use_config(monkeypatch, cfg)
    args = argparse.Namespace(
        private_key_hex="11" * 32, to_address="RTCdef", amount_rtc="1",
        memo="", nonce=None, dry_run=True,
    )
    assert cli.cmd_rustchain_pay(args) == 0
    assert fake_client.instances[-1].verify_ssl is expected


@pytest.mark.parametrize("cfg,expected", CASES)
def test_anchor_manager_verify_ssl(monkeypatch, fake_client, cfg, expected):
    _use_config(monkeypatch, cfg)
    monkeypatch.setattr(cli, "_load_identity", lambda args: object())
    mgr, _ = cli._build_anchor_mgr(argparse.Namespace())
    assert mgr is not None
    assert fake_client.instances[-1].verify_ssl is expected


def test_no_insecure_verify_ssl_default_left_in_cli():
    """Guard the construction site inside the agent loop, which is hard to
    drive in a unit test: no RustChain client may default verify_ssl to False."""
    import inspect

    src = inspect.getsource(cli)
    assert '"verify_ssl", False' not in src
    assert '"verify_ssl", default=False' not in src
