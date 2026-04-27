import tempfile
import unittest
from pathlib import Path

from beacon_skill.identity import AgentIdentity
from beacon_skill.relay import RelayManager


class TestRelayHeartbeatAndRegistration(unittest.TestCase):
    def test_register_heartbeat_and_authenticate_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            manager = RelayManager(data_dir=data_dir)
            identity = AgentIdentity.generate()

            registered = manager.register(
                pubkey_hex=identity.public_key_hex,
                model_id="claude-opus-4-6",
                provider="anthropic",
                capabilities=["coding", "review"],
                webhook_url="https://example.com/hook",
                name="Nebula Runner",
            )

            self.assertTrue(registered["ok"])
            self.assertEqual(registered["ttl_s"], 86400)
            token = registered["relay_token"]
            agent_id = registered["agent_id"]

            agent = manager.authenticate(token)
            self.assertIsNotNone(agent)
            self.assertEqual(agent.agent_id, agent_id)
            self.assertEqual(agent.provider, "anthropic")

            heartbeat = manager.heartbeat(
                agent_id,
                token,
                status="degraded",
                health={"cpu": 0.73},
            )
            self.assertTrue(heartbeat["ok"])
            self.assertEqual(heartbeat["beat_count"], 1)
            self.assertEqual(heartbeat["status"], "degraded")
            self.assertEqual(heartbeat["assessment"], "active")

            public = manager.get_agent(agent_id)
            self.assertIsNotNone(public)
            self.assertEqual(public["status"], "active")
            self.assertNotIn("relay_token", public)

            discovered = manager.discover(provider="anthropic", capability="coding")
            self.assertEqual(len(discovered), 1)
            self.assertEqual(discovered[0]["agent_id"], agent_id)
            self.assertEqual(discovered[0]["status"], "active")

    def test_register_rejects_generic_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = RelayManager(data_dir=Path(tmpdir))
            result = manager.register(
                pubkey_hex=("11" * 32),
                model_id="grok-3",
                provider="xai",
                name="Grok Assistant",
            )
            self.assertIn("error", result)
            self.assertIn("Generic AI model names", result["error"])


if __name__ == "__main__":
    unittest.main()
