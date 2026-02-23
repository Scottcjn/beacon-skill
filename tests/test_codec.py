import unittest

from beacon_skill.codec import decode_envelopes, encode_envelope, verify_envelope
from beacon_skill.identity import AgentIdentity


class TestCodec(unittest.TestCase):
    def test_decode_envelopes_rejects_non_string(self) -> None:
        with self.assertRaises(TypeError):
            decode_envelopes([{"kind": "invalid"}])  # type: ignore[arg-type]

    def test_verify_envelope_rejects_non_dict(self) -> None:
        with self.assertRaises(TypeError):
            verify_envelope("not a dict")  # type: ignore[arg-type]

    def test_encode_decode_roundtrip(self) -> None:
        payload = {"v": 1, "kind": "hello", "from": "a", "to": "b", "ts": 123}
        txt = f"hi\n\n{encode_envelope(payload, version=1)}\nbye"
        envs = decode_envelopes(txt)
        self.assertEqual(len(envs), 1)
        self.assertEqual(envs[0]["kind"], "hello")

    def test_v2_signed_envelope(self) -> None:
        ident = AgentIdentity.generate()
        payload = {"kind": "hello", "from": "test", "to": "peer", "ts": 999}
        text = encode_envelope(payload, version=2, identity=ident, include_pubkey=True)
        self.assertIn("[BEACON v2]", text)
        envs = decode_envelopes(text)
        self.assertEqual(len(envs), 1)
        env = envs[0]
        self.assertEqual(env["agent_id"], ident.agent_id)
        self.assertIn("sig", env)
        self.assertIn("nonce", env)
        self.assertIn("pubkey", env)
        # Verify signature.
        result = verify_envelope(env)
        self.assertTrue(result)

    def test_mixed_v1_v2_decode(self) -> None:
        ident = AgentIdentity.generate()
        v1 = encode_envelope({"v": 1, "kind": "like", "from": "a", "to": "b", "ts": 1}, version=1)
        v2 = encode_envelope({"kind": "want", "from": "c", "to": "d", "ts": 2}, version=2, identity=ident)
        text = f"prefix\n{v1}\nmiddle\n{v2}\nsuffix"
        envs = decode_envelopes(text)
        self.assertEqual(len(envs), 2)
        self.assertEqual(envs[0]["kind"], "like")
        self.assertEqual(envs[0]["_beacon_version"], 1)
        self.assertEqual(envs[1]["kind"], "want")
        self.assertEqual(envs[1]["_beacon_version"], 2)

    def test_malformed_skip(self) -> None:
        text = "[BEACON v1]\n{not valid json}\n[BEACON v1]\n{\"kind\":\"ok\"}"
        envs = decode_envelopes(text)
        self.assertEqual(len(envs), 1)
        self.assertEqual(envs[0]["kind"], "ok")

    def test_v2_field_roundtrip(self) -> None:
        ident = AgentIdentity.generate()
        payload = {
            "kind": "bounty",
            "from": "alice",
            "to": "bob",
            "ts": 12345,
            "reward_rtc": 50.0,
            "links": ["https://example.com"],
        }
        text = encode_envelope(payload, version=2, identity=ident)
        envs = decode_envelopes(text)
        self.assertEqual(len(envs), 1)
        env = envs[0]
        self.assertEqual(env["kind"], "bounty")
        self.assertEqual(env["reward_rtc"], 50.0)
        self.assertEqual(env["links"], ["https://example.com"])
        self.assertEqual(env["agent_id"], ident.agent_id)

    def test_v2_tampered_sig_fails(self) -> None:
        ident = AgentIdentity.generate()
        payload = {"kind": "hello", "from": "a", "to": "b", "ts": 1}
        text = encode_envelope(payload, version=2, identity=ident, include_pubkey=True)
        envs = decode_envelopes(text)
        env = envs[0]
        # Tamper with the kind field.
        env["kind"] = "HACKED"
        result = verify_envelope(env)
        self.assertFalse(result)

    def test_v1_verify_returns_none(self) -> None:
        payload = {"v": 1, "kind": "hello", "from": "a", "to": "b", "ts": 1}
        text = encode_envelope(payload, version=1)
        envs = decode_envelopes(text)
        result = verify_envelope(envs[0])
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
