import unittest
from unittest import mock

from beacon_skill.cli import main


class TestCliWebhookBackend(unittest.TestCase):
    def _run(self, argv):
        try:
            main(argv)
        except SystemExit as exc:
            return exc.code
        return 0

    @mock.patch("beacon_skill.transports.webhook.WebhookServer")
    def test_flask_backend_remains_default(self, mock_server_cls):
        server = mock_server_cls.return_value

        code = self._run(["webhook", "serve", "--host", "127.0.0.1", "--port", "18402"])

        self.assertEqual(code, 0)
        mock_server_cls.assert_called_once()
        server.start.assert_called_once_with(blocking=True)

    @mock.patch("beacon_skill.transports.webhook_fastapi.FastAPIWebhookServer")
    def test_fastapi_backend_selects_fastapi_server(self, mock_server_cls):
        server = mock_server_cls.return_value

        code = self._run([
            "webhook", "serve",
            "--backend", "fastapi",
            "--host", "127.0.0.1",
            "--port", "18402",
        ])

        self.assertEqual(code, 0)
        mock_server_cls.assert_called_once()
        server.run.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
