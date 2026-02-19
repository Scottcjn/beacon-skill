import unittest
from unittest import mock

from beacon_skill.transports.clawnews import ClawNewsClient


class TestClawNewsTransport(unittest.TestCase):
    def test_get_item_numeric_str_uses_native_endpoint(self):
        client = ClawNewsClient(api_key="test")
        with mock.patch.object(client, "_request", return_value={"id": 123}) as req:
            out = client.get_item("123")

        req.assert_called_once_with("GET", "/item/123")
        self.assertEqual(out["id"], 123)

    def test_get_item_external_mb_id_returns_stub(self):
        client = ClawNewsClient(api_key="test")
        with mock.patch.object(client, "_request") as req:
            out = client.get_item("mb_abc123")

        req.assert_not_called()
        self.assertTrue(out["external"])
        self.assertEqual(out["source"], "moltbook")
        self.assertEqual(out["url"], "/moltbook/p/mb_abc123")

    def test_search_encodes_query(self):
        client = ClawNewsClient(api_key="test")
        with mock.patch.object(client, "_request", return_value={"hits": 1}) as req:
            _ = client.search("rustchain bottube", item_type="story", limit=7)

        req.assert_called_once_with("GET", "/search?q=rustchain+bottube&limit=7&type=story", auth=True)


if __name__ == "__main__":
    unittest.main()
