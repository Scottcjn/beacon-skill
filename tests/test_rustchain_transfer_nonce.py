# SPDX-License-Identifier: MIT
"""Regression tests for RustChainClient.sign_transfer node-canonical nonce signing."""

import json

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from beacon_skill.transports.rustchain import RustChainClient, RustChainKeypair


def test_sign_transfer_signs_nonce_as_string_matching_rustchain_node():
    """RustChain POST /wallet/transfer/signed verifies nonce as str(nonce)."""
    kp = RustChainKeypair.generate()
    client = RustChainClient(base_url="https://example.invalid", verify_ssl=True)
    to_addr = "RTC" + ("a" * 40)
    payload = client.sign_transfer(
        private_key_hex=kp.private_key_hex,
        to_address=to_addr,
        amount_rtc=2.5,
        memo="bounty-payout",
        nonce=1700000000123,
    )

    assert payload["nonce"] == 1700000000123
    assert isinstance(payload["nonce"], int)

    node_canonical = {
        "from": payload["from_address"],
        "to": payload["to_address"],
        "amount": float(payload["amount_rtc"]),
        "memo": payload["memo"],
        "nonce": str(int(str(payload["nonce"]))),
    }
    msg = json.dumps(node_canonical, sort_keys=True, separators=(",", ":")).encode()
    pk = Ed25519PublicKey.from_public_bytes(bytes.fromhex(payload["public_key"]))
    pk.verify(bytes.fromhex(payload["signature"]), msg)


@pytest.mark.parametrize("bad_amount", [0, -1.0, True, float("nan"), float("inf")])
def test_sign_transfer_rejects_invalid_amount(bad_amount):
    kp = RustChainKeypair.generate()
    client = RustChainClient(base_url="https://example.invalid", verify_ssl=True)
    with pytest.raises(ValueError, match="amount_rtc"):
        client.sign_transfer(
            private_key_hex=kp.private_key_hex,
            to_address="RTC" + ("0" * 40),
            amount_rtc=bad_amount,
        )


@pytest.mark.parametrize("bad_to", ["", "   "])
def test_sign_transfer_rejects_blank_to_address(bad_to):
    kp = RustChainKeypair.generate()
    client = RustChainClient(base_url="https://example.invalid", verify_ssl=True)
    with pytest.raises(ValueError, match="to_address"):
        client.sign_transfer(
            private_key_hex=kp.private_key_hex,
            to_address=bad_to,
            amount_rtc=1.0,
        )
