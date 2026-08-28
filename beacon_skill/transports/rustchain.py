"""RustChain transport: Ed25519 signing, wallet management, and signed transfers."""

import hashlib
import json
import os
import secrets
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

import requests
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, PublicFormat, NoEncryption

from ..identity import _derive_mnemonic_private_key_seed
from ..retry import with_retry


PBKDF2_ITERATIONS = 600_000


class RustChainError(RuntimeError):
    pass


def _rtc_address_from_public_key_bytes(pubkey: bytes) -> str:
    h = hashlib.sha256(pubkey).hexdigest()[:40]
    return f"RTC{h}"


def _derive_aes_key(password: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=PBKDF2_ITERATIONS,
    )
    return kdf.derive(password.encode("utf-8"))


@dataclass(frozen=True)
class RustChainKeypair:
    private_key_hex: str
    public_key_hex: str
    address: str
    mnemonic: Optional[str] = None

    @staticmethod
    def generate() -> "RustChainKeypair":
        sk = Ed25519PrivateKey.generate()
        sk_bytes = sk.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
        pk_bytes = sk.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        return RustChainKeypair(
            private_key_hex=sk_bytes.hex(),
            public_key_hex=pk_bytes.hex(),
            address=_rtc_address_from_public_key_bytes(pk_bytes),
        )

    @staticmethod
    def generate_with_mnemonic(mnemonic_passphrase: str = "") -> "RustChainKeypair":
        """Generate a keypair backed by a 24-word BIP39 mnemonic."""
        try:
            from mnemonic import Mnemonic
        except ImportError:
            raise RuntimeError("mnemonic package required (pip install mnemonic)")
        m = Mnemonic("english")
        phrase = m.generate(strength=256)
        seed = _derive_mnemonic_private_key_seed(
            phrase,
            passphrase=mnemonic_passphrase,
        )
        sk = Ed25519PrivateKey.from_private_bytes(seed)
        sk_bytes = sk.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
        pk_bytes = sk.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        return RustChainKeypair(
            private_key_hex=sk_bytes.hex(),
            public_key_hex=pk_bytes.hex(),
            address=_rtc_address_from_public_key_bytes(pk_bytes),
            mnemonic=phrase,
        )

    @staticmethod
    def from_mnemonic(
        phrase: str,
        *,
        passphrase: str = "",
        legacy_sha256: bool = False,
    ) -> "RustChainKeypair":
        """Restore keypair from a 24-word mnemonic."""
        seed = _derive_mnemonic_private_key_seed(
            phrase,
            passphrase=passphrase,
            legacy_sha256=legacy_sha256,
        )
        sk = Ed25519PrivateKey.from_private_bytes(seed)
        sk_bytes = sk.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
        pk_bytes = sk.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        return RustChainKeypair(
            private_key_hex=sk_bytes.hex(),
            public_key_hex=pk_bytes.hex(),
            address=_rtc_address_from_public_key_bytes(pk_bytes),
            mnemonic=phrase.strip(),
        )

    @staticmethod
    def from_legacy_mnemonic(phrase: str) -> "RustChainKeypair":
        """Restore a keypair created by pre-BIP39 Beacon releases."""
        return RustChainKeypair.from_mnemonic(phrase, legacy_sha256=True)

    @staticmethod
    def from_private_key_hex(private_key_hex: str) -> "RustChainKeypair":
        sk_bytes = bytes.fromhex(private_key_hex)
        if len(sk_bytes) != 32:
            raise ValueError("private_key_hex must be 32 bytes (64 hex chars)")
        sk = Ed25519PrivateKey.from_private_bytes(sk_bytes)
        pk_bytes = sk.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        return RustChainKeypair(
            private_key_hex=private_key_hex,
            public_key_hex=pk_bytes.hex(),
            address=_rtc_address_from_public_key_bytes(pk_bytes),
        )

    def export_encrypted(self, password: str) -> Dict[str, Any]:
        """Export keypair as an encrypted keystore dict."""
        salt = secrets.token_bytes(16)
        nonce = secrets.token_bytes(12)
        aes_key = _derive_aes_key(password, salt)
        ciphertext = AESGCM(aes_key).encrypt(nonce, bytes.fromhex(self.private_key_hex), None)
        return {
            "version": 1,
            "address": self.address,
            "public_key_hex": self.public_key_hex,
            "encrypted": True,
            "salt": salt.hex(),
            "nonce": nonce.hex(),
            "ciphertext": ciphertext.hex(),
        }

    @staticmethod
    def from_encrypted(data: Dict[str, Any], password: str) -> "RustChainKeypair":
        """Restore keypair from an encrypted keystore dict."""
        salt = bytes.fromhex(data["salt"])
        nonce = bytes.fromhex(data["nonce"])
        ciphertext = bytes.fromhex(data["ciphertext"])
        aes_key = _derive_aes_key(password, salt)
        try:
            sk_bytes = AESGCM(aes_key).decrypt(nonce, ciphertext, None)
        except Exception:
            raise ValueError("Wrong password or corrupted keystore")
        return RustChainKeypair.from_private_key_hex(sk_bytes.hex())


class RustChainClient:
    def __init__(
        self,
        base_url: str = "https://rustchain.org",
        timeout_s: int = 20,
        verify_ssl: bool = True,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self.verify_ssl = verify_ssl
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "Beacon/1.0.0 (Elyan Labs)"})

    def _request(self, method: str, path: str, **kwargs) -> Dict[str, Any]:
        url = f"{self.base_url}{path}"

        def _do():
            resp = self.session.request(method, url, timeout=self.timeout_s, verify=self.verify_ssl, **kwargs)
            try:
                data = resp.json()
            except Exception:
                data = {"raw": resp.text}
            if resp.status_code >= 400:
                raise RustChainError(data.get("error") or f"HTTP {resp.status_code}")
            return data

        return with_retry(_do)

    def balance(self, miner_id: str) -> Dict[str, Any]:
        return self._request("GET", "/wallet/balance", params={"miner_id": miner_id})

    def sign_transfer(
        self,
        *,
        private_key_hex: str,
        to_address: str,
        amount_rtc: float,
        memo: str = "",
        nonce: Optional[int] = None,
    ) -> Dict[str, Any]:
        kp = RustChainKeypair.from_private_key_hex(private_key_hex)
        sk = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(kp.private_key_hex))
        if nonce is None:
            nonce = int(time.time() * 1000)

        tx_data = {
            "from": kp.address,
            "to": to_address,
            "amount": float(amount_rtc),
            "memo": memo,
            "nonce": nonce,
        }
        msg = json.dumps(tx_data, sort_keys=True, separators=(",", ":")).encode()
        sig = sk.sign(msg).hex()

        return {
            "from_address": kp.address,
            "to_address": to_address,
            "amount_rtc": float(amount_rtc),
            "nonce": nonce,
            "signature": sig,
            "public_key": kp.public_key_hex,
            "memo": memo,
        }

    def transfer_signed(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._request("POST", "/wallet/transfer/signed", json=payload, headers={"Content-Type": "application/json"})

    # ── Anchoring ──

    def anchor_submit(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Submit a hash anchor. Raises RustChainError on failure (except 409 duplicate)."""
        url = f"{self.base_url}/anchor/submit"
        resp = self.session.post(url, json=payload, timeout=self.timeout_s, verify=self.verify_ssl)
        try:
            data = resp.json()
        except Exception:
            data = {"raw": resp.text}
        if resp.status_code == 409:
            raise RustChainError(data.get("error") or "commitment_exists")
        if resp.status_code >= 400:
            raise RustChainError(data.get("error") or f"HTTP {resp.status_code}")
        return data

    def anchor_verify(self, commitment: str) -> Dict[str, Any]:
        """Check if a commitment exists on-chain."""
        return self._request("GET", "/anchor/verify", params={"commitment": commitment})

    def anchor_list(self, submitter: str = "", data_type: str = "", limit: int = 50) -> Dict[str, Any]:
        """List anchors with optional filters."""
        params: Dict[str, Any] = {"limit": limit}
        if submitter:
            params["submitter"] = submitter
        if data_type:
            params["data_type"] = data_type
        return self._request("GET", "/anchor/list", params=params)
