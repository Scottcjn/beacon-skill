"""x402 Bridge — Gate Beacon services behind x402 USDC micropayments.

This module provides Flask middleware that wraps Beacon Atlas, Memory Market,
and other endpoints with Coinbase's x402 payment protocol. Conway Automaton
agents (and any x402-compatible client) can discover and pay for services
using USDC on Base chain.

How it works:
  1. Agent requests a protected endpoint (e.g., /api/compute/inference)
  2. Server returns HTTP 402 with PAYMENT-REQUIRED header specifying price
  3. Agent signs a USDC TransferWithAuthorization (EIP-712)
  4. Agent retries with PAYMENT-SIGNATURE header
  5. Server sends to Coinbase facilitator for verification + on-chain settlement
  6. Server fulfills the request

Beacon 2.9.0 — Elyan Labs.
"""

import base64
import json
import os
import time
from functools import wraps
from typing import Any, Callable, Dict, Optional

from flask import Blueprint, Response, jsonify, request

# Base chain USDC config
BASE_CHAIN_ID = 8453
USDC_BASE = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
X402_FACILITATOR = os.environ.get("X402_FACILITATOR", "https://x402.org/facilitator")
RUSTCHAIN_API_BASE = os.environ.get("RUSTCHAIN_API_BASE", "https://rustchain.org").rstrip("/")

# Wallet to receive payments — set via env or override
PAY_TO_ADDRESS = os.environ.get("X402_PAY_TO", "")
X402_VERSION = 2

x402_bp = Blueprint("x402_bridge", __name__)
_VERIFIED_RTC_TX_HASHES: Dict[str, float] = {}
_FINAL_RTC_STATUSES = {"settled", "confirmed", "completed", "finalized", "success", "paid"}


# ── Pricing table (USDC, 6 decimals) ──

PRICING = {
    # Inference endpoints
    "inference_llm":     10_000,   # $0.01 per LLM query
    "inference_vision":  50_000,   # $0.05 per vision query
    "inference_tts":     20_000,   # $0.02 per TTS synthesis

    # Video generation
    "video_generate":   500_000,   # $0.50 per video generation
    "video_turntable":  200_000,   # $0.20 per 3D turntable render

    # Beacon services
    "atlas_query":        1_000,   # $0.001 per atlas lookup
    "memory_shard":      10_000,   # $0.01 per memory shard access
    "reputation_lookup":  1_000,   # $0.001 per reputation check
    "proof_of_thought":  50_000,   # $0.05 per PoT verification
}


def make_402_response(service_key: str, description: str = "") -> Response:
    """Build an HTTP 402 response with x402 payment requirements."""
    amount = PRICING.get(service_key, 10_000)

    payment_required = {
        "x402Version": X402_VERSION,
        "description": description or f"Payment required for {service_key}",
        "accepts": [{
            "scheme": "exact",
            "network": f"eip155:{BASE_CHAIN_ID}",
            "amount": str(amount),
            "asset": USDC_BASE,
            "payTo": PAY_TO_ADDRESS,
            "maxTimeoutSeconds": 60,
            "extra": {
                "name": "Elyan Labs Compute",
                "description": f"{service_key} — Baton Rouge lab GPUs (V100/POWER8)",
            },
        }],
    }

    resp = Response(
        json.dumps({"error": "Payment Required", "x402": payment_required}),
        status=402,
        mimetype="application/json",
    )
    resp.headers["X-Payment-Required"] = json.dumps(payment_required)
    return resp


def verify_payment(payment_header: str, service_key: str) -> Dict[str, Any]:
    """Verify x402 payment via Coinbase facilitator."""
    import requests as _req

    amount = PRICING.get(service_key, 10_000)

    try:
        payment_data = json.loads(base64.b64decode(payment_header))
    except Exception:
        return {"verified": False, "error": "malformed_payment_header"}

    try:
        resp = _req.post(
            f"{X402_FACILITATOR}/verify",
            json={
                "payment": payment_data,
                "paymentRequirements": {
                    "scheme": "exact",
                    "network": f"eip155:{BASE_CHAIN_ID}",
                    "amount": str(amount),
                    "asset": USDC_BASE,
                    "payTo": PAY_TO_ADDRESS,
                },
            },
            timeout=30,
        )
        resp.raise_for_status()
        result = resp.json()
        return {
            "verified": result.get("isValid", False),
            "tx_hash": result.get("txHash", ""),
        }
    except Exception as e:
        return {"verified": False, "error": str(e)}


def x402_required(service_key: str, description: str = ""):
    """Decorator: require x402 USDC payment before serving the request.

    Usage:
        @app.route("/api/compute/inference", methods=["POST"])
        @x402_required("inference_llm", "LLM inference on POWER8 512GB")
        def inference():
            return jsonify({"result": "..."})
    """
    def decorator(f: Callable) -> Callable:
        @wraps(f)
        def wrapper(*args, **kwargs):
            # Check for payment signature header
            payment_sig = request.headers.get("X-Payment") or \
                          request.headers.get("Payment-Signature") or \
                          request.headers.get("X-Payment-Signature")

            if not payment_sig:
                # Also accept RTC payment via X-RTC-Payment header (dual economy)
                rtc_payment = request.headers.get("X-RTC-Payment")
                if rtc_payment:
                    # RTC payment path — verify against RustChain node
                    rtc_ok = _verify_rtc_payment(rtc_payment, service_key)
                    if rtc_ok.get("verified"):
                        return f(*args, **kwargs)
                    return jsonify({"error": "Invalid RTC payment"}), 400

                return make_402_response(service_key, description)

            # Verify x402 payment
            result = verify_payment(payment_sig, service_key)
            if not result.get("verified"):
                return jsonify({
                    "error": "Payment verification failed",
                    "details": result.get("error", ""),
                }), 402

            # Payment verified — serve the request
            return f(*args, **kwargs)

        return wrapper
    return decorator


def _verify_rtc_payment(rtc_header: str, service_key: str) -> Dict[str, Any]:
    """Verify RTC payment for dual-economy support.

    RTC header format:
    {"tx_hash": "...", "amount_rtc": 1.0, "from_wallet": "...", "to_wallet": "..."}

    The recipient wallet is configured server-side with RTC_PAY_TO,
    X402_RTC_PAY_TO, RUSTCHAIN_PAY_TO, or X402_PAY_TO. A client-supplied
    to_wallet is only accepted when it matches that configured recipient.
    Conversion: 1 RTC = $0.10 USD
    """
    import requests as _req

    try:
        data = json.loads(rtc_header)
        if not isinstance(data, dict):
            return {"verified": False, "error": "malformed_rtc_payment"}

        tx_hash = str(data.get("tx_hash", "")).strip()
        from_wallet = str(data.get("from_wallet", "")).strip()
        amount_rtc = float(data.get("amount_rtc", 0))
        configured_pay_to = (
            os.environ.get("RTC_PAY_TO")
            or os.environ.get("X402_RTC_PAY_TO")
            or os.environ.get("RUSTCHAIN_PAY_TO")
            or PAY_TO_ADDRESS
        ).strip()
        header_pay_to = str(data.get("to_wallet", "")).strip()

        if not tx_hash:
            return {"verified": False, "error": "missing_tx_hash"}
        if not from_wallet:
            return {"verified": False, "error": "missing_from_wallet"}
        if not configured_pay_to:
            return {"verified": False, "error": "missing_rtc_pay_to"}
        if header_pay_to and header_pay_to != configured_pay_to:
            return {"verified": False, "error": "rtc_pay_to_mismatch"}
        if tx_hash in _VERIFIED_RTC_TX_HASHES:
            return {"verified": False, "error": "rtc_payment_replay"}

        # Convert USDC price to RTC (1 RTC = $0.10)
        usdc_price = PRICING.get(service_key, 10_000)
        rtc_required = (usdc_price / 1_000_000) / 0.10  # e.g., $0.01 = 0.1 RTC

        if amount_rtc < rtc_required:
            return {"verified": False, "error": f"Insufficient RTC: need {rtc_required:.4f}"}

        # Verify the actual transfer in the recipient wallet history. Checking
        # only the sender balance would let clients invent tx_hash values.
        resp = _req.get(
            f"{RUSTCHAIN_API_BASE}/wallet/history",
            params={"miner_id": configured_pay_to},
            verify=True,
            timeout=10,
        )
        if not resp.ok:
            return {"verified": False, "error": "RustChain verification failed"}

        txs = resp.json().get("transactions", [])
        matching_tx = next((tx for tx in txs if str(tx.get("tx_hash", "")) == tx_hash), None)
        if not matching_tx:
            return {"verified": False, "error": "rtc_tx_not_found"}

        tx_amount = float(matching_tx.get("amount") or matching_tx.get("amount_rtc") or 0)
        if tx_amount < rtc_required:
            return {"verified": False, "error": f"Insufficient RTC transfer: need {rtc_required:.4f}"}

        tx_from = str(matching_tx.get("from", "")).strip()
        if tx_from != from_wallet:
            return {"verified": False, "error": "rtc_from_wallet_mismatch"}

        status = str(matching_tx.get("status", "")).strip().lower()
        if status not in _FINAL_RTC_STATUSES:
            return {"verified": False, "error": f"unsettled_rtc_tx:{status or 'missing'}"}

        max_age_s = int(os.environ.get("RTC_PAYMENT_MAX_AGE_SECONDS", "3600"))
        tx_timestamp = matching_tx.get("timestamp")
        if tx_timestamp and max_age_s > 0:
            if float(tx_timestamp) < time.time() - max_age_s:
                return {"verified": False, "error": "stale_rtc_tx"}

        _VERIFIED_RTC_TX_HASHES[tx_hash] = time.time()
        return {"verified": True, "tx_hash": tx_hash, "amount_rtc": tx_amount}
    except Exception as e:
        return {"verified": False, "error": str(e)}


# ── Public info endpoints ──

@x402_bp.route("/api/x402/pricing", methods=["GET"])
def x402_pricing():
    """Public pricing table for all x402-gated services."""
    table = {}
    for key, amount in PRICING.items():
        usd = amount / 1_000_000
        rtc = usd / 0.10
        table[key] = {
            "usdc_amount": amount,
            "usd": f"${usd:.4f}",
            "rtc_equivalent": round(rtc, 4),
            "network": f"eip155:{BASE_CHAIN_ID}",
        }
    return jsonify({
        "pricing": table,
        "pay_to": PAY_TO_ADDRESS,
        "facilitator": X402_FACILITATOR,
        "dual_economy": True,
        "accepts": ["x402_usdc", "rustchain_rtc"],
    })


@x402_bp.route("/.well-known/agent-card.json", methods=["GET"])
def conway_agent_card():
    """Serve Conway-compatible agent card for ERC-8004 discovery."""
    card = {
        "type": "https://eips.ethereum.org/EIPS/eip-8004#registration-v1",
        "name": "Elyan Labs Compute",
        "description": (
            "Lab GPU inference and video generation. "
            "V100 32GB, POWER8 512GB RAM, RTX 3060/4070/5070. "
            "Accepts x402 USDC on Base and RTC on RustChain."
        ),
        "active": True,
        "x402Support": True,
        "services": [],
        "x402_endpoints": [
            {
                "path": "/api/compute/inference",
                "method": "POST",
                "price_usdc": PRICING["inference_llm"] / 1_000_000,
                "description": "LLM inference (Ollama/vLLM on POWER8 512GB)",
            },
            {
                "path": "/api/compute/vision",
                "method": "POST",
                "price_usdc": PRICING["inference_vision"] / 1_000_000,
                "description": "Vision model inference (BAGEL-7B on V100)",
            },
            {
                "path": "/api/compute/tts",
                "method": "POST",
                "price_usdc": PRICING["inference_tts"] / 1_000_000,
                "description": "Text-to-speech (XTTS on RTX 4070)",
            },
            {
                "path": "/api/compute/video",
                "method": "POST",
                "price_usdc": PRICING["video_generate"] / 1_000_000,
                "description": "Video generation (LTX-2/ComfyUI on V100 32GB)",
            },
        ],
        "beacon": {
            "protocol_version": "2.9.0",
            "atlas": "https://rustchain.org/beacon/",
            "transports": ["webhook", "udp", "bottube", "moltbook", "conway"],
        },
        "hardware": {
            "gpus": ["V100-32GB x2", "RTX-5070 x2", "RTX-3060 x2", "RTX-4070", "M40 x2"],
            "total_vram_gb": 192,
            "cpu_ram_gb": 512,
            "special": "IBM POWER8 S824 128-thread, vec_perm PSE inference",
        },
    }

    if PAY_TO_ADDRESS:
        card["services"].append({
            "name": "agentWallet",
            "endpoint": f"eip155:{BASE_CHAIN_ID}:{PAY_TO_ADDRESS}",
        })

    return jsonify(card)
