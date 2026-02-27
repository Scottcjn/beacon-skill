import importlib.util
import unittest
from pathlib import Path


EXAMPLE_PATH = (
    Path(__file__).resolve().parents[1] / "examples" / "atlas_relay_healthcheck.py"
)


def _load_example_module():
    spec = importlib.util.spec_from_file_location("atlas_relay_healthcheck", EXAMPLE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class TestAtlasRelayHealthcheckExample(unittest.TestCase):
    def test_parse_capabilities_fallback(self) -> None:
        module = _load_example_module()
        self.assertEqual(module.parse_capabilities(""), ["general"])
        self.assertEqual(module.parse_capabilities("coding, research"), ["coding", "research"])

    def test_run_session_pings_twice(self) -> None:
        module = _load_example_module()
        calls = []

        def fake_ping(*, agent_id, name, capabilities, identity=None, **_kwargs):
            calls.append(
                {
                    "agent_id": agent_id,
                    "name": name,
                    "capabilities": list(capabilities),
                    "identity": identity,
                }
            )
            return {"ok": True, "beat_count": len(calls)}

        result = module.run_session(
            name="test-agent",
            capabilities=["coding", "monitoring"],
            sleep_seconds=0,
            ping_fn=fake_ping,
        )

        self.assertEqual(len(calls), 2)
        self.assertIsNotNone(calls[0]["identity"])
        self.assertIsNone(calls[1]["identity"])
        self.assertTrue(result["first_ping"]["ok"])
        self.assertTrue(result["second_ping"]["ok"])
        self.assertEqual(result["local_heartbeat"]["status"], "alive")
        self.assertTrue(result["agent_id"].startswith("bcn_"))


if __name__ == "__main__":
    unittest.main()
