"""Agent identity: Ed25519 keypair, deterministic agent IDs, and keystore management."""

import hashlib
import json
import os
import secrets
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes


AGENT_ID_PREFIX = "bcn_"
IDENTITY_DIR_NAME = "identity"
KEY_FILE_NAME = "agent.key"
PBKDF2_ITERATIONS = 600_000


def _derive_aes_key(password: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=PBKDF2_ITERATIONS,
    )
    return kdf.derive(password.encode("utf-8"))


def agent_id_from_pubkey(pubkey_bytes: bytes) -> str:
    """Derive a deterministic agent ID from an Ed25519 public key.

    Format: bcn_ + first 12 hex chars of SHA256(pubkey) = 16 chars total.
    """
    h = hashlib.sha256(pubkey_bytes).hexdigest()[:12]
    return f"{AGENT_ID_PREFIX}{h}"


class AgentIdentity:
    """Ed25519 keypair with deterministic agent ID and signing/verification."""

    def __init__(self, private_key: Ed25519PrivateKey, mnemonic: Optional[str] = None):
        self._sk = private_key
        self._pk = private_key.public_key()
        self._sk_bytes = private_key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
        self._pk_bytes = self._pk.public_bytes(Encoding.Raw, PublicFormat.Raw)
        self._mnemonic = mnemonic

    @property
    def private_key_hex(self) -> str:
        return self._sk_bytes.hex()

    @property
    def public_key_hex(self) -> str:
        return self._pk_bytes.hex()

    @property
    def agent_id(self) -> str:
        return agent_id_from_pubkey(self._pk_bytes)

    @property
    def mnemonic(self) -> Optional[str]:
        return self._mnemonic

    # ── Creation ──

    @classmethod
    def generate(cls, use_mnemonic: bool = False) -> "AgentIdentity":
        """Create a new random identity.  Optionally derive from a BIP39 mnemonic."""
        mnemonic_phrase: Optional[str] = None
        if use_mnemonic:
            try:
                from mnemonic import Mnemonic  # optional dep
                m = Mnemonic("english")
                mnemonic_phrase = m.generate(strength=256)  # 24 words
                seed = hashlib.sha256(mnemonic_phrase.encode("utf-8")).digest()
                sk = Ed25519PrivateKey.from_private_bytes(seed)
                return cls(sk, mnemonic=mnemonic_phrase)
            except ImportError:
                raise RuntimeError(
                    "mnemonic package required for --mnemonic (pip install mnemonic)"
                )

        sk = Ed25519PrivateKey.generate()
        return cls(sk)

    @classmethod
    def from_private_key_hex(cls, hex_key: str) -> "AgentIdentity":
        sk = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(hex_key))
        return cls(sk)

    @classmethod
    def from_mnemonic(cls, phrase: str) -> "AgentIdentity":
        seed = hashlib.sha256(phrase.strip().encode("utf-8")).digest()
        sk = Ed25519PrivateKey.from_private_bytes(seed)
        return cls(sk, mnemonic=phrase.strip())

    # ── Signing & Verification ──

    def sign(self, data: bytes) -> bytes:
        return self._sk.sign(data)

    def sign_hex(self, data: bytes) -> str:
        return self.sign(data).hex()

    @staticmethod
    def verify(pubkey_hex: str, signature_hex: str, data: bytes) -> bool:
        try:
            pk = Ed25519PublicKey.from_public_bytes(bytes.fromhex(pubkey_hex))
            pk.verify(bytes.fromhex(signature_hex), data)
            return True
        except Exception:
            return False

    # ── Persistence ──

    def _identity_dir(self) -> Path:
        d = Path.home() / ".beacon" / IDENTITY_DIR_NAME
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _identity_path(self) -> Path:
        """Return the full identity key path, respecting BEACON_IDENTITY_PATH env var."""
        custom = os.environ.get("BEACON_IDENTITY_PATH")
        if custom:
            p = Path(custom)
            p.parent.mkdir(parents=True, exist_ok=True)
            return p
        return self._identity_dir() / KEY_FILE_NAME

    def save(self, password: Optional[str] = None) -> Path:
        """Save identity to ~/.beacon/identity/agent.key (JSON, chmod 600)."""
        path = self._identity_path()

        data: Dict[str, Any] = {
            "version": 1,
            "agent_id": self.agent_id,
            "public_key_hex": self.public_key_hex,
        }

        if password:
            salt = secrets.token_bytes(16)
            aes_key = _derive_aes_key(password, salt)
            nonce = secrets.token_bytes(12)
            plaintext = self._sk_bytes
            ciphertext = AESGCM(aes_key).encrypt(nonce, plaintext, None)
            data["encrypted"] = True
            data["salt"] = salt.hex()
            data["nonce"] = nonce.hex()
            data["ciphertext"] = ciphertext.hex()
        else:
            data["encrypted"] = False
            data["private_key_hex"] = self.private_key_hex

        if self._mnemonic:
            data["has_mnemonic"] = True
            # Never store mnemonic to disk by default — the user should write it down.

        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        try:
            os.chmod(path, 0o600)
        except Exception:
            pass
        return path

    @classmethod
    def load(cls, password: Optional[str] = None) -> "AgentIdentity":
        """Load identity from ~/.beacon/identity/agent.key."""
        custom = os.environ.get("BEACON_IDENTITY_PATH")
        if custom:
            path = Path(custom)
        else:
            path = Path.home() / ".beacon" / IDENTITY_DIR_NAME / KEY_FILE_NAME
        if not path.exists():
            raise FileNotFoundError(f"No identity found at {path}")

        data = json.loads(path.read_text(encoding="utf-8"))

        if data.get("encrypted"):
            if not password:
                raise ValueError("Identity is password-encrypted; supply --password")
            salt = bytes.fromhex(data["salt"])
            nonce = bytes.fromhex(data["nonce"])
            ciphertext = bytes.fromhex(data["ciphertext"])
            aes_key = _derive_aes_key(password, salt)
            try:
                sk_bytes = AESGCM(aes_key).decrypt(nonce, ciphertext, None)
            except Exception:
                raise ValueError("Wrong password or corrupted keystore")
            sk = Ed25519PrivateKey.from_private_bytes(sk_bytes)
        else:
            sk = Ed25519PrivateKey.from_private_bytes(
                bytes.fromhex(data["private_key_hex"])
            )

        return cls(sk)

    # ── Export helpers ──

    def export_encrypted(self, password: str) -> Dict[str, Any]:
        """Return an encrypted keystore dict (for portable backup)."""
        salt = secrets.token_bytes(16)
        nonce = secrets.token_bytes(12)
        aes_key = _derive_aes_key(password, salt)
        ciphertext = AESGCM(aes_key).encrypt(nonce, self._sk_bytes, None)
        return {
            "version": 1,
            "agent_id": self.agent_id,
            "public_key_hex": self.public_key_hex,
            "encrypted": True,
            "salt": salt.hex(),
            "nonce": nonce.hex(),
            "ciphertext": ciphertext.hex(),
        }

    @classmethod
    def from_encrypted(cls, data: Dict[str, Any], password: str) -> "AgentIdentity":
        """Restore identity from an encrypted keystore dict."""
        salt = bytes.fromhex(data["salt"])
        nonce = bytes.fromhex(data["nonce"])
        ciphertext = bytes.fromhex(data["ciphertext"])
        aes_key = _derive_aes_key(password, salt)
        try:
            sk_bytes = AESGCM(aes_key).decrypt(nonce, ciphertext, None)
        except Exception:
            raise ValueError("Wrong password or corrupted keystore")
        return cls(Ed25519PrivateKey.from_private_bytes(sk_bytes))

    def to_dict(self) -> Dict[str, str]:
        """Public-safe summary (no secrets)."""
        return {
            "agent_id": self.agent_id,
            "public_key_hex": self.public_key_hex,
        }
