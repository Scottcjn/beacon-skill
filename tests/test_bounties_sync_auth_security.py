import os
import tempfile
import unittest

from atlas import beacon_chat


class TestBountiesSyncAuthSecurity(unittest.TestCase):
    """/api/bounties/sync mutates bounty_contracts, so it must be admin-gated
    like its sibling writers /api/bounties/<id>/claim and /complete."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_db_path = beacon_chat.DB_PATH
        beacon_chat.DB_PATH = f"{self._tmp.name}/beacon_atlas_test.db"
        beacon_chat.ATLAS_RATE_LIMITER._entries.clear()
        beacon_chat.ATLAS_RATE_LIMITER._last_cleanup = 0.0
        beacon_chat.init_db()
        beacon_chat.app.config["TESTING"] = True
        self.client = beacon_chat.app.test_client()

        self._orig_admin_key = os.environ.get("RC_ADMIN_KEY")
        os.environ["RC_ADMIN_KEY"] = "test-admin-key"

    def tearDown(self) -> None:
        beacon_chat.DB_PATH = self._orig_db_path
        if self._orig_admin_key is None:
            os.environ.pop("RC_ADMIN_KEY", None)
        else:
            os.environ["RC_ADMIN_KEY"] = self._orig_admin_key
        self._tmp.cleanup()

    def test_sync_rejects_anonymous_caller(self) -> None:
        resp = self.client.post("/api/bounties/sync", json={})
        self.assertEqual(resp.status_code, 401)
        self.assertIn("admin key", resp.get_json()["error"].lower())

    def test_sync_rejects_wrong_admin_key(self) -> None:
        resp = self.client.post(
            "/api/bounties/sync",
            json={},
            headers={"X-Admin-Key": "wrong-key"},
        )
        self.assertEqual(resp.status_code, 401)

    def test_sync_rejects_when_no_server_key_configured(self) -> None:
        os.environ.pop("RC_ADMIN_KEY", None)
        resp = self.client.post(
            "/api/bounties/sync",
            json={},
            headers={"X-Admin-Key": ""},
        )
        self.assertEqual(resp.status_code, 401)


if __name__ == "__main__":
    unittest.main()
