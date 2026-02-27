import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from beacon_skill.relay import RELAY_DEAD_THRESHOLD_S, RELAY_STATE_FILE, RelayManager


class TestRelayStatusAssessment(unittest.TestCase):
    def _make_manager_with_stale_agent(self):
        tmp = tempfile.TemporaryDirectory()
        data_dir = Path(tmp.name)
        manager = RelayManager(data_dir=data_dir)

        with patch("beacon_skill.relay.time.time", return_value=1000):
            registered = manager.register(
                pubkey_hex="11" * 32,
                model_id="demo-model",
                name="Nebula Runner",
            )

        agent_id = registered["agent_id"]
        state_path = data_dir / RELAY_STATE_FILE
        agents = json.loads(state_path.read_text(encoding="utf-8"))
        agents[agent_id]["last_heartbeat"] = 1000
        agents[agent_id]["status"] = "active"
        state_path.write_text(json.dumps(agents), encoding="utf-8")

        return tmp, manager, agent_id

    def test_discover_returns_assessed_status(self) -> None:
        tmp, manager, _ = self._make_manager_with_stale_agent()
        try:
            with patch(
                "beacon_skill.relay.time.time",
                return_value=1000 + RELAY_DEAD_THRESHOLD_S + 5,
            ):
                discovered = manager.discover()
            self.assertEqual("presumed_dead", discovered[0]["status"])
        finally:
            tmp.cleanup()

    def test_get_agent_returns_assessed_status(self) -> None:
        tmp, manager, agent_id = self._make_manager_with_stale_agent()
        try:
            with patch(
                "beacon_skill.relay.time.time",
                return_value=1000 + RELAY_DEAD_THRESHOLD_S + 5,
            ):
                agent = manager.get_agent(agent_id)
            self.assertIsNotNone(agent)
            self.assertEqual("presumed_dead", agent["status"])
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
