import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from beacon_skill.relay import RELAY_STATE_FILE, RelayManager


class TestRelayTokenStatus(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self._tmp.name)
        self.manager = RelayManager(data_dir=self.data_dir)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _register(self, now: int = 1000):
        with patch("beacon_skill.relay.time.time", return_value=now):
            return self.manager.register(
                pubkey_hex="12" * 32,
                model_id="demo-model",
                provider="other",
                name="North Star",
            )

    def _set_expiry(self, agent_id: str, expires_at: int) -> None:
        state_path = self.data_dir / RELAY_STATE_FILE
        agents = json.loads(state_path.read_text(encoding="utf-8"))
        agents[agent_id]["token_expires"] = expires_at
        state_path.write_text(json.dumps(agents), encoding="utf-8")

    def test_active_status_reports_expiry_without_exposing_token(self) -> None:
        registered = self._register()
        with patch("beacon_skill.relay.time.time", return_value=1500):
            result = self.manager.token_status(
                registered["agent_id"],
                refresh_window_s=60,
            )

        self.assertTrue(result["active"])
        self.assertEqual("active", result["atlas_status"])
        self.assertEqual("none", result["next_action"])
        self.assertEqual("1970-01-02T00:16:40Z", result["expires_at"])
        self.assertNotIn("relay_token", result)
        self.assertNotIn(registered["relay_token"], json.dumps(result))

    def test_near_expiry_recommends_heartbeat_with_placeholder(self) -> None:
        registered = self._register()
        self._set_expiry(registered["agent_id"], 2500)

        with patch("beacon_skill.relay.time.time", return_value=2000):
            result = self.manager.token_status(
                registered["agent_id"],
                refresh_window_s=600,
            )

        self.assertTrue(result["active"])
        self.assertEqual(500, result["seconds_remaining"])
        self.assertIn("beacon relay heartbeat", result["next_action"])
        self.assertIn("<relay-token>", result["next_action"])
        self.assertNotIn(registered["relay_token"], json.dumps(result))

    def test_expired_status_recommends_registration(self) -> None:
        registered = self._register()
        self._set_expiry(registered["agent_id"], 1500)

        with patch("beacon_skill.relay.time.time", return_value=2000):
            result = self.manager.token_status(registered["agent_id"])

        self.assertFalse(result["active"])
        self.assertEqual(0, result["seconds_remaining"])
        self.assertIn("beacon relay register", result["next_action"])
        self.assertIn("--name \"North Star\"", result["next_action"])
        self.assertNotIn(registered["relay_token"], json.dumps(result))

    def test_sole_registration_is_selected_without_agent_id(self) -> None:
        registered = self._register()

        with patch("beacon_skill.relay.time.time", return_value=2000):
            result = self.manager.token_status()

        self.assertEqual(registered["agent_id"], result["agent_id"])

    def test_empty_state_returns_registration_guidance(self) -> None:
        result = self.manager.token_status()

        self.assertEqual("NOT_REGISTERED", result["code"])
        self.assertFalse(result["active"])
        self.assertEqual("unregistered", result["atlas_status"])
        self.assertIn("beacon relay register", result["next_action"])


if __name__ == "__main__":
    unittest.main()
