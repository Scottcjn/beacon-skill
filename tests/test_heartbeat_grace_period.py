"""
Unit tests for Heartbeat grace period and network partition tolerance.
"""

import json
import os
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from beacon_skill.heartbeat import HeartbeatManager, DEFAULT_SILENCE_THRESHOLD_S, DEFAULT_DEAD_THRESHOLD_S


class TestHeartbeatGracePeriod(unittest.TestCase):
    def setUp(self):
        self.tmpdir = TemporaryDirectory()
        self.data_dir = Path(self.tmpdir.name)
        self.mgr = HeartbeatManager(data_dir=self.data_dir)

    def tearDown(self):
        self.tmpdir.cleanup()

    def _seed_peer(self, agent_id: str, age_s: int, status: str = "alive"):
        state_file = self.data_dir / "heartbeats.json"
        now = int(time.time())
        state = {
            "own": {},
            "peers": {
                agent_id: {
                    "last_beat": now - age_s,
                    "status": status,
                    "beat_count": 5,
                }
            }
        }
        state_file.write_text(json.dumps(state), encoding="utf-8")

    def test_default_threshold_assessments(self):
        """Test default healthy / concerning / presumed_dead boundaries."""
        self._seed_peer("peer_fresh", age_s=300)
        self.assertEqual(self.mgr._assess_peer("peer_fresh"), "healthy")

        self._seed_peer("peer_concerning", age_s=1200)
        self.assertEqual(self.mgr._assess_peer("peer_concerning"), "concerning")

        self._seed_peer("peer_dead", age_s=4000)
        self.assertEqual(self.mgr._assess_peer("peer_dead"), "presumed_dead")

    @patch.dict(os.environ, {"BEACON_GRACE_PERIOD_MS": "600000"})  # +600s (10 min)
    def test_grace_period_via_env_ms(self):
        """Test that BEACON_GRACE_PERIOD_MS extends the silence window during network partitions."""
        # Age 1200s (20 min) is normally 'concerning' (since 1200 > 900)
        # With 600s grace period, effective threshold is 900 + 600 = 1500s -> 'healthy'
        self._seed_peer("peer_lagging", age_s=1200)
        self.assertEqual(self.mgr._assess_peer("peer_lagging"), "healthy")

        # Age 1600s exceeds 1500s -> 'concerning'
        self._seed_peer("peer_very_lagging", age_s=1600)
        self.assertEqual(self.mgr._assess_peer("peer_very_lagging"), "concerning")

    @patch.dict(os.environ, {"BEACON_GRACE_PERIOD_S": "300"})
    def test_grace_period_via_env_s(self):
        """Test that BEACON_GRACE_PERIOD_S extends silence threshold by integer seconds."""
        self._seed_peer("peer_lagging", age_s=1000)
        self.assertEqual(self.mgr._assess_peer("peer_lagging"), "healthy")

    def test_grace_period_via_config(self):
        """Test that heartbeat.grace_period_s in config dictionary is respected."""
        mgr = HeartbeatManager(data_dir=self.data_dir, config={"heartbeat": {"grace_period_s": 500}})
        self._seed_peer("peer_lagging", age_s=1300)
        self.assertEqual(mgr._assess_peer("peer_lagging"), "healthy")


if __name__ == "__main__":
    unittest.main()
