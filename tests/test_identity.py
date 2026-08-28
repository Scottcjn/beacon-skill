import hashlib
import unittest

from beacon_skill.identity import AgentIdentity, agent_id_from_pubkey

BIP39_TEST_PHRASE = "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about"


def _expected_bip39_seed_hex(phrase: str, passphrase: str = "") -> str:
    seed = hashlib.pbkdf2_hmac(
        "sha512",
        phrase.encode("utf-8"),
        f"mnemonic{passphrase}".encode("utf-8"),
        2048,
        dklen=64,
    )
    return seed[:32].hex()


class TestAgentIdentity(unittest.TestCase):
    def test_create_identity(self) -> None:
        ident = AgentIdentity.generate()
        self.assertTrue(ident.agent_id.startswith("bcn_"))
        self.assertEqual(len(ident.agent_id), 16)
        self.assertEqual(len(ident.private_key_hex), 64)
        self.assertEqual(len(ident.public_key_hex), 64)

    def test_load_from_private_key(self) -> None:
        a = AgentIdentity.generate()
        b = AgentIdentity.from_private_key_hex(a.private_key_hex)
        self.assertEqual(a.agent_id, b.agent_id)
        self.assertEqual(a.public_key_hex, b.public_key_hex)

    def test_sign_and_verify(self) -> None:
        ident = AgentIdentity.generate()
        msg = b"hello beacon"
        sig_hex = ident.sign_hex(msg)
        self.assertTrue(AgentIdentity.verify(ident.public_key_hex, sig_hex, msg))
        # Tampered message should fail.
        self.assertFalse(AgentIdentity.verify(ident.public_key_hex, sig_hex, b"tampered"))

    def test_agent_id_determinism(self) -> None:
        a = AgentIdentity.generate()
        expected = agent_id_from_pubkey(bytes.fromhex(a.public_key_hex))
        self.assertEqual(a.agent_id, expected)
        # Recreate from same private key — same ID.
        b = AgentIdentity.from_private_key_hex(a.private_key_hex)
        self.assertEqual(a.agent_id, b.agent_id)

    def test_mnemonic_roundtrip(self) -> None:
        try:
            a = AgentIdentity.generate(use_mnemonic=True)
        except RuntimeError:
            self.skipTest("mnemonic package not installed")
        self.assertIsNotNone(a.mnemonic)
        words = a.mnemonic.split()
        self.assertEqual(len(words), 24)
        # Restore from same mnemonic.
        b = AgentIdentity.from_mnemonic(a.mnemonic)
        self.assertEqual(a.agent_id, b.agent_id)
        self.assertEqual(a.public_key_hex, b.public_key_hex)

    def test_mnemonic_derivation_uses_bip39_pbkdf2(self) -> None:
        ident = AgentIdentity.from_mnemonic(BIP39_TEST_PHRASE)
        raw_sha256_seed = hashlib.sha256(BIP39_TEST_PHRASE.encode("utf-8")).hexdigest()

        self.assertEqual(
            ident.private_key_hex,
            _expected_bip39_seed_hex(BIP39_TEST_PHRASE),
        )
        self.assertNotEqual(ident.private_key_hex, raw_sha256_seed)

    def test_mnemonic_passphrase_changes_identity(self) -> None:
        plain = AgentIdentity.from_mnemonic(BIP39_TEST_PHRASE)
        protected = AgentIdentity.from_mnemonic(
            BIP39_TEST_PHRASE,
            passphrase="correct horse battery staple",
        )

        self.assertEqual(
            protected.private_key_hex,
            _expected_bip39_seed_hex(
                BIP39_TEST_PHRASE,
                "correct horse battery staple",
            ),
        )
        self.assertNotEqual(plain.agent_id, protected.agent_id)

    def test_legacy_mnemonic_requires_explicit_opt_in(self) -> None:
        legacy = AgentIdentity.from_legacy_mnemonic(BIP39_TEST_PHRASE)
        legacy_flag = AgentIdentity.from_mnemonic(
            BIP39_TEST_PHRASE,
            legacy_sha256=True,
        )
        modern = AgentIdentity.from_mnemonic(BIP39_TEST_PHRASE)

        self.assertEqual(
            legacy.private_key_hex,
            hashlib.sha256(BIP39_TEST_PHRASE.encode("utf-8")).hexdigest(),
        )
        self.assertEqual(legacy.agent_id, legacy_flag.agent_id)
        self.assertNotEqual(legacy.agent_id, modern.agent_id)

    def test_encrypted_keystore_roundtrip(self) -> None:
        ident = AgentIdentity.generate()
        pw = "test-password-42"
        keystore = ident.export_encrypted(pw)
        self.assertTrue(keystore["encrypted"])
        restored = AgentIdentity.from_encrypted(keystore, pw)
        self.assertEqual(ident.agent_id, restored.agent_id)
        self.assertEqual(ident.private_key_hex, restored.private_key_hex)
        # Wrong password should fail.
        with self.assertRaises(ValueError):
            AgentIdentity.from_encrypted(keystore, "wrong-password")


if __name__ == "__main__":
    unittest.main()
