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
import math
import os
import re
import sqlite3
import time
from functools import wraps
from pathlib import Path
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


_RTC_TX_HASH_RE = re.compile(r"[0-9a-f]{32}")  # always used with fullmatch()
# RustChain wallet ids: miner ids and RTC addresses. ":" is excluded so the
# confirmer reason "transfer_in:<from>:<tx_hash>" stays unambiguous.
_RTC_WALLET_RE = re.compile(r"[A-Za-z0-9._-]{1,128}")  # always used with fullmatch()
_RTC_HISTORY_LIMIT = 200          # node caps /wallet/history at 200 rows
_RTC_FUTURE_SKEW_SECONDS = 300
_RTC_DEFAULT_MAX_AGE_SECONDS = 3600


def _rtc_payments_enabled() -> bool:
    """RTC payments are OFF unless RTC_X402_PAYMENTS_ENABLED is exactly "1".

    Any other value, including " 1" or "1 " with stray whitespace, "true", "yes",
    "on" or "2", leaves the path disabled, so the kill switch is unambiguous.

    The header proves knowledge of a public transfer, not ownership of it, so
    an observer can claim someone else's confirmed payment first. Keep the path
    disabled until payments are bound to a payer-held secret (see #964 / PR).
    """
    return os.environ.get("RTC_X402_PAYMENTS_ENABLED", "0") == "1"


class RtcPaymentDbConfigError(RuntimeError):
    """RTC_PAYMENT_DB does not name a persistent on-disk SQLite file."""


def _rtc_payment_db_path() -> str:
    """Return the replay DB path, refusing anything that is not a real file.

    Replay protection only holds if every worker and every restart sees the
    same database. ":memory:", "" and "file:" URIs (including in-memory URIs
    such as "file::memory:" or "file:x?mode=memory") would give each
    connection a private, empty database, so they are rejected.
    """
    raw = os.environ.get("RTC_PAYMENT_DB")
    if raw is None:
        return str(Path.home() / ".beacon" / "x402_rtc_payments.db")
    path = raw.strip()
    if (
        not path
        or path != raw
        or path == ":memory:"
        or path.lower().startswith("file:")
        or "\0" in path
    ):
        raise RtcPaymentDbConfigError("invalid_rtc_payment_db: must be a persistent file path")
    # A relative or "~" path resolves against the process's working directory
    # or user, so two workers (or a restart from another directory) could
    # each get their own replay DB. Require one unambiguous absolute path.
    if not os.path.isabs(path):
        raise RtcPaymentDbConfigError("invalid_rtc_payment_db: path must be absolute")
    if os.path.isdir(path):
        raise RtcPaymentDbConfigError("invalid_rtc_payment_db: path is a directory")
    return path


def _rtc_payment_db() -> sqlite3.Connection:
    path = _rtc_payment_db_path()
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS consumed_rtc_payments ("
        " tx_hash TEXT PRIMARY KEY,"
        " from_wallet TEXT NOT NULL,"
        " pay_to TEXT NOT NULL,"
        " amount_rtc REAL NOT NULL,"
        " service_key TEXT NOT NULL,"
        " consumed_at REAL NOT NULL)"
    )
    return conn


def _rtc_tx_hash_consumed(tx_hash: str) -> bool:
    """Cheap early check; the atomic INSERT in _consume_rtc_tx_hash is authoritative."""
    conn = _rtc_payment_db()
    try:
        return conn.execute(
            "SELECT 1 FROM consumed_rtc_payments WHERE tx_hash = ?", (tx_hash,)
        ).fetchone() is not None
    finally:
        conn.close()


def _consume_rtc_tx_hash(tx_hash: str, from_wallet: str, pay_to: str,
                         amount_rtc: float, service_key: str) -> bool:
    """Atomically claim tx_hash. Returns False if it was already consumed.

    The PRIMARY KEY makes the claim atomic across threads, gunicorn workers
    and restarts: exactly one INSERT for a given tx_hash can succeed.
    """
    conn = _rtc_payment_db()
    try:
        with conn:
            conn.execute(
                "INSERT INTO consumed_rtc_payments"
                " (tx_hash, from_wallet, pay_to, amount_rtc, service_key, consumed_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (tx_hash, from_wallet, pay_to, amount_rtc, service_key, time.time()),
            )
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def _finite_positive_number(value: Any) -> Optional[float]:
    """Return value as a float only if it is a real, finite, positive number."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        return None
    return number


def _rtc_history_row_is_settled(tx: Dict[str, Any], tx_hash: str) -> bool:
    """Return True only on positive evidence that a /wallet/history row is settled.

    RustChain transfers are two-phase. /wallet/history merges two sources:

    * ``ledger`` rows (balance already moved). The pending-transfer confirmer
      writes the recipient's row with reason ``transfer_in:<from>:<tx_hash>``;
      these rows carry a ``reason`` and no ``status`` field.
    * ``pending_ledger`` rows that are not yet confirmed. These carry an
      explicit ``status`` (``pending``, ``voided``, ...) and no ``reason``.

    Every accepted row must be a ``transfer_in`` from a well-formed sender.
    A row with an explicit status is accepted only if that status is
    ``confirmed``. A row with no status is accepted only if it carries the
    confirmer's exact ledger reason for this sender and tx_hash. Anything else
    is treated as unsettled.
    """
    if tx.get("type") != "transfer_in":
        return False
    tx_from = tx.get("from")
    if not isinstance(tx_from, str) or not _RTC_WALLET_RE.fullmatch(tx_from):
        return False

    status = tx.get("status")
    if status is not None:
        return isinstance(status, str) and status.strip().lower() == "confirmed"
    return tx.get("reason") == f"transfer_in:{tx_from}:{tx_hash}"


def _verify_rtc_payment(rtc_header: str, service_key: str) -> Dict[str, Any]:
    """Verify RTC payment for dual-economy support.

    Disabled unless RTC_X402_PAYMENTS_ENABLED is set (see _rtc_payments_enabled).

    RTC header format:
    {"tx_hash": "<32 hex>", "amount_rtc": 1.0, "from_wallet": "...", "to_wallet": "..."}

    The recipient wallet is configured server-side with RTC_PAY_TO,
    X402_RTC_PAY_TO, RUSTCHAIN_PAY_TO, or X402_PAY_TO. A client-supplied
    to_wallet is only accepted when it matches that configured recipient.
    Each tx_hash is consumed at most once, recorded durably in the SQLite DB
    at RTC_PAYMENT_DB (default ~/.beacon/x402_rtc_payments.db).
    Conversion: 1 RTC = $0.10 USD
    """
    import requests as _req

    if not _rtc_payments_enabled():
        return {"verified": False, "error": "rtc_payments_disabled"}

    try:
        data = json.loads(rtc_header)
        if not isinstance(data, dict):
            return {"verified": False, "error": "malformed_rtc_payment"}

        tx_hash = data.get("tx_hash")
        from_wallet = data.get("from_wallet")
        header_pay_to = data.get("to_wallet")
        configured_pay_to = (
            os.environ.get("RTC_PAY_TO")
            or os.environ.get("X402_RTC_PAY_TO")
            or os.environ.get("RUSTCHAIN_PAY_TO")
            or PAY_TO_ADDRESS
        ).strip()

        # JSON null / absent / "" are "missing"; anything else must be a
        # canonical string. No str() coercion of None, numbers or containers.
        if tx_hash is None or tx_hash == "":
            return {"verified": False, "error": "missing_tx_hash"}
        if not isinstance(tx_hash, str) or not _RTC_TX_HASH_RE.fullmatch(tx_hash):
            return {"verified": False, "error": "malformed_tx_hash"}
        if from_wallet is None or from_wallet == "":
            return {"verified": False, "error": "missing_from_wallet"}
        if not isinstance(from_wallet, str) or not _RTC_WALLET_RE.fullmatch(from_wallet):
            return {"verified": False, "error": "malformed_from_wallet"}
        if not configured_pay_to:
            return {"verified": False, "error": "missing_rtc_pay_to"}
        if header_pay_to is not None and header_pay_to != "":
            if not isinstance(header_pay_to, str) or header_pay_to != configured_pay_to:
                return {"verified": False, "error": "rtc_pay_to_mismatch"}

        amount_rtc = _finite_positive_number(data.get("amount_rtc"))
        if amount_rtc is None:
            return {"verified": False, "error": "malformed_amount_rtc"}

        # Convert USDC price to RTC (1 RTC = $0.10)
        usdc_price = PRICING.get(service_key, 10_000)
        rtc_required = (usdc_price / 1_000_000) / 0.10  # e.g., $0.01 = 0.1 RTC

        if amount_rtc < rtc_required:
            return {"verified": False, "error": f"Insufficient RTC: need {rtc_required:.4f}"}

        try:
            _rtc_payment_db_path()
        except RtcPaymentDbConfigError as e:
            return {"verified": False, "error": str(e)}
        if _rtc_tx_hash_consumed(tx_hash):
            return {"verified": False, "error": "rtc_payment_replay"}

        # Verify the actual transfer in the recipient wallet history. Checking
        # only the sender balance would let clients invent tx_hash values.
        resp = _req.get(
            f"{RUSTCHAIN_API_BASE}/wallet/history",
            params={"miner_id": configured_pay_to, "limit": _RTC_HISTORY_LIMIT},
            verify=True,
            timeout=10,
        )
        if not resp.ok:
            return {"verified": False, "error": "RustChain verification failed"}

        body = resp.json()
        if not isinstance(body, dict) or body.get("miner_id") != configured_pay_to:
            return {"verified": False, "error": "rtc_history_wallet_mismatch"}
        txs = body.get("transactions")
        if not isinstance(txs, list):
            return {"verified": False, "error": "malformed_rtc_history"}

        matches = [tx for tx in txs if isinstance(tx, dict) and tx.get("tx_hash") == tx_hash]
        if not matches:
            return {"verified": False, "error": "rtc_tx_not_found"}
        if len(matches) > 1:
            return {"verified": False, "error": "rtc_tx_ambiguous"}
        matching_tx = matches[0]

        tx_amount = _finite_positive_number(matching_tx.get("amount"))
        if tx_amount is None:
            return {"verified": False, "error": "malformed_rtc_tx_amount"}
        if tx_amount < rtc_required:
            return {"verified": False, "error": f"Insufficient RTC transfer: need {rtc_required:.4f}"}

        if matching_tx.get("from") != from_wallet:
            return {"verified": False, "error": "rtc_from_wallet_mismatch"}

        if not _rtc_history_row_is_settled(matching_tx, tx_hash):
            status = matching_tx.get("status")
            label = status.strip().lower() if isinstance(status, str) and status.strip() else "unproven"
            return {"verified": False, "error": f"unsettled_rtc_tx:{label}"}

        # A confirmed row's timestamp is when the node's confirmer settled it,
        # so the window starts at settlement. A missing or invalid timestamp
        # fails closed. Replay is enforced separately by the durable DB claim.
        now = time.time()
        tx_timestamp = _finite_positive_number(matching_tx.get("timestamp"))
        if tx_timestamp is None:
            return {"verified": False, "error": "missing_rtc_tx_timestamp"}
        if tx_timestamp > now + _RTC_FUTURE_SKEW_SECONDS:
            return {"verified": False, "error": "future_rtc_tx"}
        try:
            max_age_s = int(os.environ.get("RTC_PAYMENT_MAX_AGE_SECONDS", str(_RTC_DEFAULT_MAX_AGE_SECONDS)))
        except ValueError:
            return {"verified": False, "error": "invalid_rtc_payment_max_age"}
        if max_age_s <= 0:
            # Never let configuration disable freshness; fail closed instead.
            return {"verified": False, "error": "invalid_rtc_payment_max_age"}
        if tx_timestamp < now - max_age_s:
            return {"verified": False, "error": "stale_rtc_tx"}

        # Consume last, only after every check passed.
        if not _consume_rtc_tx_hash(tx_hash, from_wallet, configured_pay_to, tx_amount, service_key):
            return {"verified": False, "error": "rtc_payment_replay"}
        return {"verified": True, "tx_hash": tx_hash, "amount_rtc": tx_amount}
    except Exception as e:
        return {"verified": False, "error": str(e)}


# ── Public info endpoints ──

@x402_bp.route("/api/x402/pricing", methods=["GET"])
def x402_pricing():
    """Public pricing table for all x402-gated services."""
    rtc_enabled = _rtc_payments_enabled()
    table = {}
    for key, amount in PRICING.items():
        usd = amount / 1_000_000
        table[key] = {
            "usdc_amount": amount,
            "usd": f"${usd:.4f}",
            "network": f"eip155:{BASE_CHAIN_ID}",
        }
        if rtc_enabled:
            table[key]["rtc_equivalent"] = round(usd / 0.10, 4)
    # Only advertise RTC while the RTC path is enabled; otherwise an agent
    # could send an irreversible transfer that the server will then reject.
    return jsonify({
        "pricing": table,
        "pay_to": PAY_TO_ADDRESS,
        "facilitator": X402_FACILITATOR,
        "dual_economy": rtc_enabled,
        "accepts": ["x402_usdc", "rustchain_rtc"] if rtc_enabled else ["x402_usdc"],
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
            + (
                "Accepts x402 USDC on Base and RTC on RustChain."
                if _rtc_payments_enabled()
                else "Accepts x402 USDC on Base."
            )
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
