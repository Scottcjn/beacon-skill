import hashlib
import json
import unittest

from beacon_skill.transports.rustchain import RustChainClient, RustChainKeypair

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


class TestRustChainSigning(unittest.TestCase):
    def test_sign_transfer_shape(self) -> None:
        kp = RustChainKeypair.generate()
        c = RustChainClient(base_url="https://example.invalid", verify_ssl=True)
        payload = c.sign_transfer(
            private_key_hex=kp.private_key_hex,
            to_address="RTC" + ("0" * 40),
            amount_rtc=1.5,
            memo="test",
            nonce=123,
        )
        self.assertEqual(payload["from_address"], kp.address)
        self.assertEqual(payload["nonce"], 123)
        self.assertEqual(payload["amount_rtc"], 1.5)
        # Basic sanity: JSON serialization should work.
        json.dumps(payload)

    def test_mnemonic_roundtrip(self) -> None:
        try:
            kp1 = RustChainKeypair.generate_with_mnemonic()
        except RuntimeError:
            self.skipTest("mnemonic package not installed")
        self.assertIsNotNone(kp1.mnemonic)
        words = kp1.mnemonic.split()
        self.assertEqual(len(words), 24)
        # Restore from same mnemonic.
        kp2 = RustChainKeypair.from_mnemonic(kp1.mnemonic)
        self.assertEqual(kp1.address, kp2.address)
        self.assertEqual(kp1.private_key_hex, kp2.private_key_hex)

    def test_mnemonic_derivation_uses_bip39_pbkdf2(self) -> None:
        kp = RustChainKeypair.from_mnemonic(BIP39_TEST_PHRASE)
        raw_sha256_seed = hashlib.sha256(BIP39_TEST_PHRASE.encode("utf-8")).hexdigest()

        self.assertEqual(
            kp.private_key_hex,
            _expected_bip39_seed_hex(BIP39_TEST_PHRASE),
        )
        self.assertNotEqual(kp.private_key_hex, raw_sha256_seed)

    def test_mnemonic_passphrase_changes_keypair(self) -> None:
        plain = RustChainKeypair.from_mnemonic(BIP39_TEST_PHRASE)
        protected = RustChainKeypair.from_mnemonic(
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
        self.assertNotEqual(plain.address, protected.address)

    def test_legacy_mnemonic_requires_explicit_opt_in(self) -> None:
        legacy = RustChainKeypair.from_legacy_mnemonic(BIP39_TEST_PHRASE)
        legacy_flag = RustChainKeypair.from_mnemonic(
            BIP39_TEST_PHRASE,
            legacy_sha256=True,
        )
        modern = RustChainKeypair.from_mnemonic(BIP39_TEST_PHRASE)

        self.assertEqual(
            legacy.private_key_hex,
            hashlib.sha256(BIP39_TEST_PHRASE.encode("utf-8")).hexdigest(),
        )
        self.assertEqual(legacy.address, legacy_flag.address)
        self.assertNotEqual(legacy.address, modern.address)

    def test_encrypted_keystore(self) -> None:
        kp = RustChainKeypair.generate()
        pw = "test-wallet-pass"
        keystore = kp.export_encrypted(pw)
        self.assertTrue(keystore["encrypted"])
        self.assertEqual(keystore["address"], kp.address)
        # Restore.
        restored = RustChainKeypair.from_encrypted(keystore, pw)
        self.assertEqual(kp.address, restored.address)
        self.assertEqual(kp.private_key_hex, restored.private_key_hex)
        # Wrong password should fail.
        with self.assertRaises(ValueError):
            RustChainKeypair.from_encrypted(keystore, "wrong")


if __name__ == "__main__":
    unittest.main()
