import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from beacon_skill.heartbeat import (
    DEFAULT_DEAD_THRESHOLD_S,
    DEFAULT_SILENCE_THRESHOLD_S,
    HEARTBEATS_FILE,
    HeartbeatManager,
)
from beacon_skill.identity import AgentIdentity


class TestHeartbeatAssessments(unittest.TestCase):
    def test_build_heartbeat_increments_beat_count(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            manager = HeartbeatManager(data_dir=data_dir, config={"_start_ts": 100})
            identity = AgentIdentity.generate()

            with patch("beacon_skill.heartbeat.time.time", return_value=200):
                first = manager.build_heartbeat(identity, status="alive")
            with patch("beacon_skill.heartbeat.time.time", return_value=260):
                second = manager.build_heartbeat(identity, status="degraded")

            self.assertEqual(first["beat_count"], 1)
            self.assertEqual(second["beat_count"], 2)
            self.assertEqual(second["status"], "degraded")
            self.assertEqual(second["uptime_s"], 160)

    def test_process_heartbeat_assesses_peer_health_by_age(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            manager = HeartbeatManager(data_dir=data_dir)
            envelope = {
                "agent_id": "bcn_peer123456",
                "beat_count": 1,
                "status": "alive",
                "name": "Peer Agent",
                "uptime_s": 42,
            }

            with patch("beacon_skill.heartbeat.time.time", return_value=1000):
                processed = manager.process_heartbeat(envelope)
            self.assertEqual(processed["assessment"], "healthy")

            with patch(
                "beacon_skill.heartbeat.time.time",
                return_value=1000 + DEFAULT_SILENCE_THRESHOLD_S + 5,
            ):
                peer = manager.peer_status("bcn_peer123456")
            self.assertIsNotNone(peer)
            self.assertEqual(peer["assessment"], "concerning")

            with patch(
                "beacon_skill.heartbeat.time.time",
                return_value=1000 + DEFAULT_DEAD_THRESHOLD_S + 5,
            ):
                peer = manager.peer_status("bcn_peer123456")
                all_peers = manager.all_peers(include_dead=True)
                silent_peers = manager.silent_peers()
            self.assertEqual(peer["assessment"], "presumed_dead")
            self.assertEqual(all_peers[0]["assessment"], "presumed_dead")
            self.assertEqual(silent_peers[0]["agent_id"], "bcn_peer123456")

    def test_process_heartbeat_records_gap_and_health(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            manager = HeartbeatManager(data_dir=data_dir)
            first = {
                "agent_id": "bcn_gappeer001",
                "beat_count": 1,
                "status": "alive",
                "health": {"cpu": 0.1},
            }
            second = {
                "agent_id": "bcn_gappeer001",
                "beat_count": 2,
                "status": "alive",
                "health": {"cpu": 0.2},
            }

            with patch("beacon_skill.heartbeat.time.time", return_value=1000):
                manager.process_heartbeat(first)
            with patch("beacon_skill.heartbeat.time.time", return_value=1120):
                manager.process_heartbeat(second)

            state = json.loads((data_dir / HEARTBEATS_FILE).read_text(encoding="utf-8"))
            peer = state["peers"]["bcn_gappeer001"]
            self.assertEqual(peer["gap_s"], 120)
            self.assertEqual(peer["health"], {"cpu": 0.2})


if __name__ == "__main__":
    unittest.main()
