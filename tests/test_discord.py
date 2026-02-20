import argparse
import json
import unittest
from unittest import mock

import requests

from beacon_skill.cli import cmd_discord_listen, cmd_discord_ping, cmd_discord_send
from beacon_skill.transports.discord import DiscordClient, DiscordError


class _Resp:
    def __init__(self, status_code=204, text="", data=None):
        self.status_code = status_code
        self.text = text
        self._data = data

    def json(self):
        if self._data is None:
            raise ValueError("no json")
        return self._data


class TestDiscordTransport(unittest.TestCase):
    def test_send_message_success_204(self) -> None:
        client = DiscordClient(webhook_url="https://discord.invalid/webhook")
        with mock.patch.object(client.session, "request", return_value=_Resp(status_code=204, text="")) as req:
            result = client.send_message("hello")
            self.assertTrue(result["ok"])
            self.assertEqual(result["status"], 204)
            self.assertEqual(req.call_args.args[0], "POST")
            self.assertEqual(req.call_args.args[1], "https://discord.invalid/webhook")
            self.assertEqual(req.call_args.kwargs["json"]["content"], "hello")

    def test_send_beacon_includes_embed_fields(self) -> None:
        client = DiscordClient(webhook_url="https://discord.invalid/webhook")
        with mock.patch.object(
            client.session,
            "request",
            return_value=_Resp(status_code=200, text='{"ok":true}', data={"id": "1"}),
        ) as req:
            result = client.send_beacon(
                content="hello world",
                kind="bounty",
                agent_id="bcn_abcdef123456",
                rtc_tip=7.5,
                signature_preview="abc123",
            )
            self.assertTrue(result["ok"])
            payload = req.call_args.kwargs["json"]
            self.assertIn("embeds", payload)
            embed = payload["embeds"][0]
            self.assertTrue(embed["title"].endswith("BOUNTY"))
            field_names = [f["name"] for f in embed["fields"]]
            self.assertIn("RTC Tip", field_names)
            self.assertIn("Signature", field_names)

    @mock.patch("beacon_skill.transports.discord.time.sleep")
    def test_retry_429_then_success(self, sleep_mock) -> None:
        client = DiscordClient(webhook_url="https://discord.invalid/webhook", max_attempts=3, base_delay_s=0.1)
        rate_limited = _Resp(
            status_code=429,
            text='{"message":"Too Many Requests","retry_after":2}',
            data={"message": "Too Many Requests", "retry_after": 2},
        )
        ok = _Resp(status_code=204, text="")
        with mock.patch.object(client.session, "request", side_effect=[rate_limited, ok]) as req:
            result = client.send_message("hello")
            self.assertTrue(result["ok"])
            self.assertEqual(req.call_count, 2)
            sleep_mock.assert_called_once_with(2.0)

    @mock.patch("beacon_skill.transports.discord.time.sleep")
    def test_retry_5xx_then_success(self, sleep_mock) -> None:
        client = DiscordClient(webhook_url="https://discord.invalid/webhook", max_attempts=3, base_delay_s=0.5)
        e500 = _Resp(status_code=500, text='{"message":"Internal"}', data={"message": "Internal"})
        ok = _Resp(status_code=200, text='{"id":"1"}', data={"id": "1"})
        with mock.patch.object(client.session, "request", side_effect=[e500, ok]) as req:
            result = client.send_message("hello")
            self.assertTrue(result["ok"])
            self.assertEqual(req.call_count, 2)
            sleep_mock.assert_called_once_with(0.5)

    def test_4xx_returns_parsed_error(self) -> None:
        client = DiscordClient(webhook_url="https://discord.invalid/webhook")
        bad = _Resp(status_code=400, text='{"message":"Invalid webhook"}', data={"message": "Invalid webhook"})
        with mock.patch.object(client.session, "request", return_value=bad):
            with self.assertRaises(DiscordError) as ctx:
                client.send_message("hi")
        err = ctx.exception
        self.assertEqual(err.status_code, 400)
        self.assertEqual(err.error_type, "client")
        self.assertIn("Invalid webhook", str(err))

    @mock.patch("beacon_skill.transports.discord.time.sleep")
    def test_retry_request_exception_then_success(self, sleep_mock) -> None:
        client = DiscordClient(webhook_url="https://discord.invalid/webhook", max_attempts=2, base_delay_s=0.25)
        ok = _Resp(status_code=204, text="")
        with mock.patch.object(
            client.session,
            "request",
            side_effect=[requests.RequestException("timeout"), ok],
        ) as req:
            result = client.send_message("hello")
            self.assertTrue(result["ok"])
            self.assertEqual(req.call_count, 2)
            sleep_mock.assert_called_once_with(0.25)

    def test_send_without_webhook_errors(self) -> None:
        client = DiscordClient(webhook_url="")
        with self.assertRaises(DiscordError):
            client.send_message("hi")

    def test_listener_requires_bot_token_and_channel(self) -> None:
        with self.assertRaises(DiscordError):
            DiscordClient(bot_token="", channel_id="123").listen_messages()
        with self.assertRaises(DiscordError):
            DiscordClient(bot_token="token", channel_id="").listen_messages()

    def test_listener_fetches_messages(self) -> None:
        client = DiscordClient(bot_token="abc123", channel_id="111222")
        resp = _Resp(
            status_code=200,
            text='[{"id":"m1"}]',
            data=[
                {
                    "id": "m1",
                    "content": "hello",
                    "timestamp": "2026-01-01T00:00:00.000Z",
                    "author": {"id": "u1", "username": "alice"},
                }
            ],
        )
        with mock.patch.object(client.session, "request", return_value=resp) as req:
            result = client.listen_messages(limit=10, after_id="m0")
            self.assertTrue(result["ok"])
            self.assertEqual(result["count"], 1)
            self.assertEqual(result["messages"][0]["author_username"], "alice")
            self.assertEqual(req.call_args.args[0], "GET")
            self.assertIn("/channels/111222/messages", req.call_args.args[1])
            self.assertEqual(req.call_args.kwargs["params"]["after"], "m0")
            self.assertEqual(req.call_args.kwargs["headers"]["Authorization"], "Bot abc123")


class TestDiscordCli(unittest.TestCase):
    def test_ping_dry_run_has_payload_shape(self) -> None:
        args = argparse.Namespace(
            text="hello",
            kind="hello",
            rtc=1.5,
            link=["https://example.com"],
            webhook_url="https://discord.invalid/webhook",
            username=None,
            avatar_url=None,
            dry_run=True,
            password=None,
        )
        with mock.patch("beacon_skill.cli.load_config", return_value={"beacon": {"agent_name": "test"}}), mock.patch(
            "beacon_skill.cli._load_identity", return_value=None
        ), mock.patch("beacon_skill.cli._build_envelope", return_value="[BEACON v1]\n{}\n[/BEACON]"), mock.patch(
            "beacon_skill.cli.decode_envelopes",
            return_value=[{"agent_id": "bcn_abc", "sig": "sig123"}],
        ), mock.patch(
            "builtins.print"
        ) as print_mock:
            rc = cmd_discord_ping(args)
            self.assertEqual(rc, 0)
            out = json.loads(print_mock.call_args.args[0])
            self.assertIn("payload", out)
            self.assertIn("embeds", out["payload"])
            self.assertEqual(out["payload"]["embeds"][0]["fields"][0]["name"], "Kind")

    def test_send_dry_run_has_payload_shape(self) -> None:
        args = argparse.Namespace(
            kind="bounty",
            text="new bounty",
            link=[],
            bounty_url="https://example.com/bounty/1",
            reward_rtc=120.0,
            rtc=2.0,
            webhook_url="https://discord.invalid/webhook",
            username=None,
            avatar_url=None,
            dry_run=True,
            password=None,
        )
        with mock.patch("beacon_skill.cli.load_config", return_value={"beacon": {"agent_name": "test"}}), mock.patch(
            "beacon_skill.cli._load_identity", return_value=None
        ), mock.patch("beacon_skill.cli._build_envelope", return_value="[BEACON v1]\n{}\n[/BEACON]"), mock.patch(
            "beacon_skill.cli.decode_envelopes",
            return_value=[{"agent_id": "bcn_abc", "sig": "sig123"}],
        ), mock.patch(
            "builtins.print"
        ) as print_mock:
            rc = cmd_discord_send(args)
            self.assertEqual(rc, 0)
            out = json.loads(print_mock.call_args.args[0])
            self.assertIn("payload", out)
            self.assertIn("embeds", out["payload"])
            self.assertEqual(out["payload"]["embeds"][0]["fields"][0]["name"], "Kind")

    def test_listen_dry_run(self) -> None:
        args = argparse.Namespace(
            channel_id="123",
            bot_token="",
            limit=20,
            after_id=None,
            dry_run=True,
        )
        with mock.patch("beacon_skill.cli.load_config", return_value={}), mock.patch("builtins.print") as print_mock:
            rc = cmd_discord_listen(args)
            self.assertEqual(rc, 0)
            out = json.loads(print_mock.call_args.args[0])
            self.assertEqual(out["channel_id"], "123")
            self.assertFalse(out["bot_token_set"])


if __name__ == "__main__":
    unittest.main()
