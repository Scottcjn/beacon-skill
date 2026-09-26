"""Regression tests: verify_envelope must not crash on hostile input and must
accept the key-metadata shape returned by key_management.load_known_keys().

Before the fix:
  * a non-hex ``pubkey``/``sig`` raised ValueError out of verify_envelope,
    which killed ``beacon udp listen`` and broke inbox reads;
  * passing ``load_known_keys()`` (agent_id -> metadata dict) raised
    ``TypeError: fromhex() argument must be str, not dict`` — which is exactly
    what the CLI UDP listener and the stdlib webhook server pass.
"""

import time
import unittest

from beacon_skill.codec import decode_envelopes, encode_envelope, verify_envelope
from beacon_skill.identity import AgentIdentity


def _signed(ident, include_pubkey):
    txt = encode_envelope(
        {"kind": "hello", "ts": int(time.time())},
        identity=ident,
        include_pubkey=include_pubkey,
    )
    return decode_envelopes(txt)[0]


def _meta(pubkey_hex, revoked=False):
    return {
        "pubkey_hex": pubkey_hex,
        "first_seen": time.time(),
        "last_seen": time.time(),
        "rotation_count": 0,
        "previous_key": None,
        "revoked": revoked,
        "revoked_at": None,
        "revoked_reason": None,
    }


class TestVerifyEnvelopeMalformedInput(unittest.TestCase):
    def test_non_hex_pubkey_returns_false(self):
        env = {"kind": "x", "agent_id": "bcn_x", "pubkey": "zz", "sig": "00" * 64}
        self.assertIs(verify_envelope(env), False)

    def test_non_hex_sig_returns_false(self):
        ident = AgentIdentity.generate()
        env = _signed(ident, include_pubkey=True)
        env["sig"] = "not-hex"
        self.assertIs(verify_envelope(env), False)

    def test_wrong_length_pubkey_returns_false(self):
        env = {"kind": "x", "pubkey": "ab" * 5, "sig": "00" * 64}
        self.assertIs(verify_envelope(env), False)

    def test_non_string_fields_return_false(self):
        self.assertIs(verify_envelope({"pubkey": 123, "sig": "00"}), False)
        self.assertIs(verify_envelope({"pubkey": "ab" * 32, "sig": ["x"]}), False)
        self.assertIs(verify_envelope({"agent_id": 7, "pubkey": "ab" * 32, "sig": "00"}), False)

    def test_malformed_known_key_returns_false(self):
        env = {"agent_id": "bcn_abc", "sig": "00" * 64}
        self.assertIs(verify_envelope(env, known_keys={"bcn_abc": "zz"}), False)


class TestVerifyEnvelopeKnownKeyShapes(unittest.TestCase):
    def test_metadata_dict_known_keys_verifies(self):
        ident = AgentIdentity.generate()
        env = _signed(ident, include_pubkey=False)
        keys = {ident.agent_id: _meta(ident.public_key_hex)}
        self.assertIs(verify_envelope(env, known_keys=keys), True)

    def test_metadata_dict_known_keys_detects_tamper(self):
        ident = AgentIdentity.generate()
        env = _signed(ident, include_pubkey=False)
        env["kind"] = "tampered"
        keys = {ident.agent_id: _meta(ident.public_key_hex)}
        self.assertIs(verify_envelope(env, known_keys=keys), False)

    def test_legacy_string_known_keys_still_verifies(self):
        ident = AgentIdentity.generate()
        env = _signed(ident, include_pubkey=False)
        self.assertIs(verify_envelope(env, known_keys={ident.agent_id: ident.public_key_hex}), True)

    def test_unknown_agent_still_unverifiable(self):
        ident = AgentIdentity.generate()
        env = _signed(ident, include_pubkey=False)
        self.assertIsNone(verify_envelope(env, known_keys={"bcn_other": _meta("ab" * 32)}))


if __name__ == "__main__":
    unittest.main()
