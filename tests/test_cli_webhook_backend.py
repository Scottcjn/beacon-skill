# SPDX-License-Identifier: MIT
import argparse
import contextlib
import io
import json
import sys
from unittest import mock

import pytest

from beacon_skill import cli


def test_webhook_serve_parser_supports_backend_option(monkeypatch):
    captured = {}

    def _run_cli(argv):
        monkeypatch.setattr(sys, "argv", ["beacon", *argv])
        with mock.patch("beacon_skill.cli.cmd_webhook_serve", side_effect=lambda a: captured.setdefault("args", a) and 0 or 0):
            with pytest.raises(SystemExit) as exc:
                cli.main()
            return exc.value.code

    assert _run_cli(["webhook", "serve", "--backend", "fastapi", "--port", "8402"]) == 0
    assert captured["args"].backend == "fastapi"
    assert captured["args"].port == 8402

    captured.clear()
    assert _run_cli(["webhook", "serve"]) == 0
    assert captured["args"].backend == "flask"

    monkeypatch.setattr(sys, "argv", ["beacon", "webhook", "serve", "--backend", "invalid"])
    with pytest.raises(SystemExit) as exc_info:
        cli.main()
    assert exc_info.value.code == 2


def test_cmd_webhook_serve_selects_flask_default():
    stdout = io.StringIO()
    with mock.patch("beacon_skill.identity.AgentIdentity.load", side_effect=FileNotFoundError), \
            mock.patch("beacon_skill.transports.webhook.WebhookServer") as mock_server, \
            contextlib.redirect_stdout(stdout):
        rc = cli.cmd_webhook_serve(argparse.Namespace(port=8402, host="127.0.0.1", password=None, backend="flask"))

    assert rc == 0
    mock_server.assert_called_once_with(port=8402, host="127.0.0.1", identity=None, agent_card=None)
    mock_server.return_value.start.assert_called_once_with(blocking=True)
    assert json.loads(stdout.getvalue())["backend"] == "flask"


def test_cmd_webhook_serve_selects_fastapi_backend():
    stdout = io.StringIO()
    fake_cls = mock.MagicMock()
    fake_mod = mock.MagicMock(FastAPIWebhookServer=fake_cls)
    with mock.patch.dict(sys.modules, {"beacon_skill.transports.webhook_fastapi": fake_mod}), \
            mock.patch("beacon_skill.identity.AgentIdentity.load", side_effect=FileNotFoundError), \
            contextlib.redirect_stdout(stdout):
        rc = cli.cmd_webhook_serve(argparse.Namespace(port=8402, host="127.0.0.1", password=None, backend="fastapi"))

    assert rc == 0
    fake_cls.assert_called_once_with(port=8402, host="127.0.0.1", identity=None, agent_card=None)
    fake_cls.return_value.run.assert_called_once_with()
    assert json.loads(stdout.getvalue())["backend"] == "fastapi"


def test_cmd_webhook_serve_fastapi_missing_extra_prints_install_hint():
    stderr = io.StringIO()
    with mock.patch.dict(sys.modules, {"beacon_skill.transports.webhook_fastapi": None, "fastapi": None}), \
            contextlib.redirect_stderr(stderr):
        rc = cli.cmd_webhook_serve(argparse.Namespace(port=8402, host="127.0.0.1", password=None, backend="fastapi"))

    assert rc == 1
    assert 'pip install "beacon-skill[webhook]"' in stderr.getvalue()
    assert "Traceback" not in stderr.getvalue()
