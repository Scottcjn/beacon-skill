import os
import tempfile
import time
import unittest

from atlas import beacon_chat


class TestProfileDirectoryHrefXss(unittest.TestCase):
    """Public profile and directory pages must not emit a dangerous href scheme.

    html.escape stops quote-breakout but does not stop a javascript: or data:
    URL from running when it lands in an href. A row can carry such a seo_url
    from a write path that skipped scheme validation or from before that check
    existed, so the crawlable pages gate the scheme again at output.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_db_path = beacon_chat.DB_PATH
        self._orig_admin_key = os.environ.get("RC_ADMIN_KEY")
        os.environ["RC_ADMIN_KEY"] = "test-admin-key"
        beacon_chat.DB_PATH = f"{self._tmp.name}/beacon_atlas_test.db"
        beacon_chat.ATLAS_RATE_LIMITER._entries.clear()
        beacon_chat.ATLAS_RATE_LIMITER._last_cleanup = 0.0
        beacon_chat.init_db()
        beacon_chat.app.config["TESTING"] = True
        self.client = beacon_chat.app.test_client()

    def tearDown(self):
        beacon_chat.DB_PATH = self._orig_db_path
        if self._orig_admin_key is None:
            os.environ.pop("RC_ADMIN_KEY", None)
        else:
            os.environ["RC_ADMIN_KEY"] = self._orig_admin_key
        self._tmp.cleanup()

    def _insert_agent(self, agent_id, seo_url):
        now = time.time()
        with beacon_chat.app.app_context():
            db = beacon_chat.get_db()
            db.execute(
                """
                INSERT INTO relay_agents (
                    agent_id, pubkey_hex, model_id, provider, capabilities, webhook_url,
                    relay_token, token_expires, name, status, beat_count, registered_at,
                    last_heartbeat, metadata, origin_ip, seo_url, seo_description
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    agent_id,
                    "a" * 64,
                    "test-model",
                    "beacon",
                    "[]",
                    "",
                    "relay_valid_token",
                    now + 3600,
                    "Href Agent",
                    "active",
                    1,
                    now,
                    now,
                    "{}",
                    "127.0.0.1",
                    seo_url,
                    "",
                ),
            )
            db.commit()

    def test_javascript_scheme_dropped_on_profile(self):
        agent_id = "relay_href_profile"
        self._insert_agent(agent_id, "javascript:alert(document.cookie)")
        body = self.client.get(f"/beacon/agent/{agent_id}").get_data(as_text=True)
        self.assertNotIn("javascript:", body)

    def test_javascript_scheme_dropped_on_directory(self):
        self._insert_agent("relay_href_dir", "javascript:alert(1)")
        resp = self.client.get("/beacon/directory")
        # The directory must render (a shadowed html module name previously
        # made this route 500 whenever a relay agent existed).
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("javascript:", resp.get_data(as_text=True))

    def test_http_scheme_preserved(self):
        agent_id = "relay_href_ok"
        self._insert_agent(agent_id, "https://example.com/home")
        body = self.client.get(f"/beacon/agent/{agent_id}").get_data(as_text=True)
        self.assertIn('href="https://example.com/home"', body)

    def test_safe_href_helper(self):
        self.assertEqual(beacon_chat._safe_href("https://a.test"), "https://a.test")
        self.assertEqual(beacon_chat._safe_href("http://a.test"), "http://a.test")
        self.assertEqual(beacon_chat._safe_href("/beacon/directory"), "/beacon/directory")
        self.assertEqual(beacon_chat._safe_href("javascript:alert(1)"), "")
        self.assertEqual(beacon_chat._safe_href("data:text/html,<script>"), "")
        self.assertEqual(beacon_chat._safe_href("//evil.test"), "")
        self.assertEqual(beacon_chat._safe_href(""), "")
        self.assertEqual(beacon_chat._safe_href(None), "")


if __name__ == "__main__":
    unittest.main()
