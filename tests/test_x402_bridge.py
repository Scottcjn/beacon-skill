import copy
import json
import os
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from beacon_skill import x402_bridge


class FakeResponse:
    def __init__(self, payload, ok=True):
        self._payload = payload
        self.ok = ok

    def json(self):
        return self._payload


PAY_TO = "RTCservice"
TX_HASH = "0123456789abcdef0123456789abcdef"

# Rows captured verbatim from https://rustchain.org/wallet/history on 2026-10-07
# (read-only GET). A confirmed transfer comes from the node's ``ledger`` table:
# it has a ``reason`` and NO ``status`` field (issue #964).
LIVE_CONFIRMED_ROW = {
    "amount": 12,
    "epoch": 32983,
    "from": "founder_team_bounty",
    "reason": "transfer_in:founder_team_bounty:dde21dc9c56167093b1adcc5b3d6b2b8",
    "timestamp": 1784585686,
    "tx_hash": "dde21dc9c56167093b1adcc5b3d6b2b8",
    "type": "transfer_in",
}

# An in-flight transfer comes from ``pending_ledger``: explicit ``status``,
# ``epoch`` null, and no ``reason``. Captured from the recipient's history.
LIVE_PENDING_ROW = {
    "amount": 50.0,
    "epoch": None,
    "from": "founder_community",
    "status": "pending",
    "timestamp": 1791329978,
    "tx_hash": "c5743a7b7302ae11a4455289881bef43",
    "type": "transfer_in",
}


class RtcTestBase(unittest.TestCase):
    """Payments enabled, isolated durable replay DB per test."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self._tmp.name, "rtc.db")
        self._env = patch.dict("os.environ", {
            "RTC_PAY_TO": PAY_TO,
            "RTC_PAYMENT_DB": self.db_path,
            "RTC_X402_PAYMENTS_ENABLED": "1",
        })
        self._env.start()

    def tearDown(self):
        self._env.stop()
        self._tmp.cleanup()

    @staticmethod
    def _history(*rows, miner_id=PAY_TO):
        return FakeResponse({
            "miner_id": miner_id,
            "ok": True,
            "total": len(rows),
            "transactions": list(rows),
        })

    @staticmethod
    def _fresh(row):
        row = copy.deepcopy(row)
        row["timestamp"] = int(time.time())
        return row

    @staticmethod
    def _confirmed(tx_hash=TX_HASH, sender="RTCsender", amount=0.1):
        return {
            "amount": amount,
            "epoch": 43033,
            "from": sender,
            "reason": f"transfer_in:{sender}:{tx_hash}",
            "timestamp": int(time.time()),
            "tx_hash": tx_hash,
            "type": "transfer_in",
        }

    @staticmethod
    def _header(row=None, **overrides):
        row = row or {"tx_hash": TX_HASH, "amount": 0.1, "from": "RTCsender"}
        payload = {
            "tx_hash": row["tx_hash"],
            "amount_rtc": float(row["amount"]),
            "from_wallet": row["from"],
            "to_wallet": PAY_TO,
        }
        payload.update(overrides)
        return json.dumps(payload)

    def _verify(self, mock_get, response, header, service="atlas_query"):
        mock_get.return_value = response
        return x402_bridge._verify_rtc_payment(header, service)


class TestRtcPaymentVerification(RtcTestBase):
    def test_rejects_missing_tx_hash(self):
        result = x402_bridge._verify_rtc_payment(self._header(tx_hash=""), "inference_llm")
        self.assertEqual(result, {"verified": False, "error": "missing_tx_hash"})

    def test_rejects_client_pay_to_mismatch(self):
        result = x402_bridge._verify_rtc_payment(self._header(to_wallet="RTCattacker"), "inference_llm")
        self.assertEqual(result, {"verified": False, "error": "rtc_pay_to_mismatch"})

    @patch("requests.get")
    def test_requests_recipient_history_with_limit(self, mock_get):
        result = self._verify(mock_get, self._history(self._confirmed()), self._header(), "inference_llm")

        self.assertTrue(result["verified"], result)
        mock_get.assert_called_once()
        self.assertEqual(mock_get.call_args.kwargs["params"], {"miner_id": PAY_TO, "limit": 200})


class TestRtcPaymentsDisabledByDefault(unittest.TestCase):
    @patch("requests.get")
    def test_disabled_without_flag(self, mock_get):
        env = {k: v for k, v in os.environ.items() if k != "RTC_X402_PAYMENTS_ENABLED"}
        env["RTC_PAY_TO"] = PAY_TO
        with patch.dict("os.environ", env, clear=True):
            result = x402_bridge._verify_rtc_payment(RtcTestBase._header(), "atlas_query")

        self.assertEqual(result, {"verified": False, "error": "rtc_payments_disabled"})
        mock_get.assert_not_called()

    @patch("requests.get")
    def test_disabled_when_flag_zero(self, mock_get):
        with patch.dict("os.environ", {"RTC_PAY_TO": PAY_TO, "RTC_X402_PAYMENTS_ENABLED": "0"}):
            result = x402_bridge._verify_rtc_payment(RtcTestBase._header(), "atlas_query")

        self.assertEqual(result["error"], "rtc_payments_disabled")
        mock_get.assert_not_called()


class TestRtcPaymentLiveHistoryShape(RtcTestBase):
    """Regression for #964 using the real /wallet/history payload shape."""

    @patch("requests.get")
    def test_confirmed_ledger_row_without_status_verifies(self, mock_get):
        row = self._fresh(LIVE_CONFIRMED_ROW)
        result = self._verify(mock_get, self._history(row), self._header(row))

        self.assertTrue(result["verified"], result)
        self.assertEqual(result["tx_hash"], row["tx_hash"])
        self.assertEqual(result["amount_rtc"], 12.0)
        self.assertNotIn("status", row)

    @patch("requests.get")
    def test_pending_row_is_rejected(self, mock_get):
        row = self._fresh(LIVE_PENDING_ROW)
        result = self._verify(mock_get, self._history(row), self._header(row))
        self.assertEqual(result, {"verified": False, "error": "unsettled_rtc_tx:pending"})

    @patch("requests.get")
    def test_voided_row_is_rejected(self, mock_get):
        row = self._fresh(LIVE_PENDING_ROW)
        row["status"] = "voided"
        result = self._verify(mock_get, self._history(row), self._header(row))
        self.assertEqual(result, {"verified": False, "error": "unsettled_rtc_tx:voided"})

    @patch("requests.get")
    def test_row_without_status_or_ledger_reason_is_rejected(self, mock_get):
        # Missing status alone is not proof of settlement.
        row = self._fresh(LIVE_PENDING_ROW)
        del row["status"]
        result = self._verify(mock_get, self._history(row), self._header(row))
        self.assertEqual(result, {"verified": False, "error": "unsettled_rtc_tx:unproven"})

    @patch("requests.get")
    def test_row_whose_reason_names_another_tx_is_rejected(self, mock_get):
        row = self._fresh(LIVE_CONFIRMED_ROW)
        row["reason"] = "transfer_in:founder_team_bounty:00000000000000000000000000000000"
        result = self._verify(mock_get, self._history(row), self._header(row))
        self.assertEqual(result, {"verified": False, "error": "unsettled_rtc_tx:unproven"})

    @patch("requests.get")
    def test_wrong_amount_is_rejected(self, mock_get):
        row = self._fresh(LIVE_CONFIRMED_ROW)
        row["amount"] = 0.001
        # Header claims enough; the settled transfer itself is below the price.
        result = self._verify(mock_get, self._history(row), self._header(row, amount_rtc=1.0))

        self.assertFalse(result["verified"])
        self.assertTrue(result["error"].startswith("Insufficient RTC transfer"), result)

    @patch("requests.get")
    def test_wrong_sender_is_rejected(self, mock_get):
        row = self._fresh(LIVE_CONFIRMED_ROW)
        result = self._verify(mock_get, self._history(row),
                              self._header(row, from_wallet="founder_community"))
        self.assertEqual(result, {"verified": False, "error": "rtc_from_wallet_mismatch"})

    @patch("requests.get")
    def test_replayed_tx_hash_is_rejected(self, mock_get):
        row = self._fresh(LIVE_CONFIRMED_ROW)
        first = self._verify(mock_get, self._history(row), self._header(row))
        second = self._verify(mock_get, self._history(row), self._header(row))

        self.assertTrue(first["verified"], first)
        self.assertEqual(second, {"verified": False, "error": "rtc_payment_replay"})

    @patch("requests.get")
    def test_stale_confirmed_row_is_rejected(self, mock_get):
        row = copy.deepcopy(LIVE_CONFIRMED_ROW)  # original timestamp, far past 3600 s
        with patch.dict("os.environ", {"RTC_PAYMENT_MAX_AGE_SECONDS": "3600"}):
            result = self._verify(mock_get, self._history(row), self._header(row))
        self.assertEqual(result, {"verified": False, "error": "stale_rtc_tx"})


class TestRtcDurableReplay(RtcTestBase):
    """Blocker 1: replay protection is shared, durable and atomic."""

    @patch("requests.get")
    def test_replay_survives_process_restart(self, mock_get):
        row = self._confirmed()
        first = self._verify(mock_get, self._history(row), self._header(row))
        self.assertTrue(first["verified"], first)

        # Nothing lives in module memory any more: a fresh process / other
        # worker sees the same consumed row through the shared DB file.
        self.assertFalse(hasattr(x402_bridge, "_VERIFIED_RTC_TX_HASHES"))
        with sqlite3.connect(self.db_path) as conn:
            stored = conn.execute(
                "SELECT tx_hash, from_wallet, pay_to FROM consumed_rtc_payments"
            ).fetchall()
        self.assertEqual(stored, [(TX_HASH, "RTCsender", PAY_TO)])

        second = self._verify(mock_get, self._history(row), self._header(row))
        self.assertEqual(second, {"verified": False, "error": "rtc_payment_replay"})

    def test_consume_is_atomic_under_concurrency(self):
        results = []
        barrier = threading.Barrier(8)

        def claim():
            barrier.wait()
            results.append(x402_bridge._consume_rtc_tx_hash(TX_HASH, "RTCsender", PAY_TO, 0.1, "atlas_query"))

        threads = [threading.Thread(target=claim) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(sorted(results), [False] * 7 + [True])

    @patch("requests.get")
    def test_concurrent_requests_verify_exactly_once(self, mock_get):
        mock_get.return_value = self._history(self._confirmed())
        results = []
        barrier = threading.Barrier(6)

        def attempt():
            barrier.wait()
            results.append(x402_bridge._verify_rtc_payment(self._header(), "atlas_query"))

        threads = [threading.Thread(target=attempt) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(sum(1 for r in results if r["verified"]), 1, results)
        self.assertTrue(all(r["error"] == "rtc_payment_replay" for r in results if not r["verified"]))

    @patch("requests.get")
    def test_failed_verification_does_not_consume(self, mock_get):
        pending = dict(self._confirmed(), status="pending")
        del pending["reason"]
        result = self._verify(mock_get, self._history(pending), self._header())
        self.assertEqual(result["error"], "unsettled_rtc_tx:pending")

        result = self._verify(mock_get, self._history(self._confirmed()), self._header())
        self.assertTrue(result["verified"], result)


class TestRtcNonFiniteAmounts(RtcTestBase):
    """Blocker 3: NaN / inf / non-positive amounts are rejected."""

    def test_header_non_finite_or_non_positive_amount(self):
        for bad in ("NaN", "Infinity", "-Infinity", 0, -1, "1.0", None, True, [1]):
            with self.subTest(amount_rtc=bad):
                header = json.dumps({"tx_hash": TX_HASH, "amount_rtc": bad,
                                     "from_wallet": "RTCsender"})
                if isinstance(bad, str) and bad.lower().lstrip("-") in ("nan", "infinity"):
                    # Python's json emits bare NaN/Infinity tokens; build that form.
                    header = header.replace(f'"{bad}"', bad)
                result = x402_bridge._verify_rtc_payment(header, "atlas_query")
                self.assertEqual(result, {"verified": False, "error": "malformed_amount_rtc"})

    @patch("requests.get")
    def test_row_non_finite_or_non_positive_amount(self, mock_get):
        for bad in (float("nan"), float("inf"), "NaN", 0, -5, None, True):
            with self.subTest(amount=bad):
                row = self._confirmed()
                row["amount"] = bad
                result = self._verify(mock_get, self._history(row), self._header())
                self.assertEqual(result, {"verified": False, "error": "malformed_rtc_tx_amount"})


class TestRtcHardening(RtcTestBase):
    @patch("requests.get")
    def test_explicit_confirmed_status_requires_transfer_in(self, mock_get):
        # Item 4: a status-bearing row of another type must not verify.
        row = dict(self._confirmed(), status="confirmed", type="reward", reason=None)
        result = self._verify(mock_get, self._history(row), self._header())
        self.assertEqual(result, {"verified": False, "error": "unsettled_rtc_tx:confirmed"})

    @patch("requests.get")
    def test_explicit_confirmed_transfer_in_verifies(self, mock_get):
        row = dict(self._confirmed(), status="confirmed")
        del row["reason"]
        result = self._verify(mock_get, self._history(row), self._header())
        self.assertTrue(result["verified"], result)

    @patch("requests.get")
    def test_other_final_looking_statuses_are_rejected(self, mock_get):
        for status in ("settled", "completed", "finalized", "success", "paid"):
            with self.subTest(status=status):
                row = dict(self._confirmed(), status=status)
                result = self._verify(mock_get, self._history(row), self._header())
                self.assertEqual(result, {"verified": False, "error": f"unsettled_rtc_tx:{status}"})

    @patch("requests.get")
    def test_missing_zero_or_non_finite_timestamp_fails_closed(self, mock_get):
        # Item 5.
        for bad in (None, 0, float("nan"), "1791380000", "missing"):
            with self.subTest(timestamp=bad):
                row = self._confirmed()
                if bad == "missing":
                    del row["timestamp"]
                else:
                    row["timestamp"] = bad
                result = self._verify(mock_get, self._history(row), self._header())
                self.assertEqual(result, {"verified": False, "error": "missing_rtc_tx_timestamp"})

    @patch("requests.get")
    def test_future_timestamp_is_rejected(self, mock_get):
        row = dict(self._confirmed(), timestamp=int(time.time()) + 3600)
        result = self._verify(mock_get, self._history(row), self._header())
        self.assertEqual(result, {"verified": False, "error": "future_rtc_tx"})

    @patch("requests.get")
    def test_response_miner_id_must_match_pay_to(self, mock_get):
        # Item 6.
        for miner_id in ("attacker", None, ""):
            with self.subTest(miner_id=miner_id):
                result = self._verify(mock_get, self._history(self._confirmed(), miner_id=miner_id),
                                      self._header())
                self.assertEqual(result, {"verified": False, "error": "rtc_history_wallet_mismatch"})

    def test_header_ids_must_be_canonical_strings(self):
        # Item 7: no str() coercion of None, numbers or containers.
        cases = [
            ({"tx_hash": None}, "missing_tx_hash"),
            ({"tx_hash": 123}, "malformed_tx_hash"),
            ({"tx_hash": ["a"]}, "malformed_tx_hash"),
            ({"tx_hash": "None"}, "malformed_tx_hash"),
            ({"tx_hash": TX_HASH.upper()}, "malformed_tx_hash"),
            ({"tx_hash": f" {TX_HASH}"}, "malformed_tx_hash"),
            ({"from_wallet": None}, "missing_from_wallet"),
            ({"from_wallet": 42}, "malformed_from_wallet"),
            ({"from_wallet": {"a": 1}}, "malformed_from_wallet"),
            ({"from_wallet": "a:b"}, "malformed_from_wallet"),
            ({"to_wallet": 7}, "rtc_pay_to_mismatch"),
        ]
        for overrides, error in cases:
            with self.subTest(overrides=overrides):
                result = x402_bridge._verify_rtc_payment(self._header(**overrides), "atlas_query")
                self.assertEqual(result, {"verified": False, "error": error})

    @patch("requests.get")
    def test_row_ids_are_not_coerced(self, mock_get):
        row = self._confirmed()
        row["from"] = None
        result = self._verify(mock_get, self._history(row), self._header())
        self.assertEqual(result, {"verified": False, "error": "rtc_from_wallet_mismatch"})

        row = self._confirmed()
        row["tx_hash"] = int("123")
        result = self._verify(mock_get, self._history(row), self._header())
        self.assertEqual(result, {"verified": False, "error": "rtc_tx_not_found"})

    @patch("requests.get")
    def test_duplicate_tx_hash_rows_are_rejected(self, mock_get):
        # Item 8.
        result = self._verify(mock_get, self._history(self._confirmed(), self._confirmed()), self._header())
        self.assertEqual(result, {"verified": False, "error": "rtc_tx_ambiguous"})

    @patch("requests.get")
    def test_malformed_history_body_is_rejected(self, mock_get):
        mock_get.return_value = FakeResponse({"miner_id": PAY_TO, "transactions": "nope"})
        result = x402_bridge._verify_rtc_payment(self._header(), "atlas_query")
        self.assertEqual(result, {"verified": False, "error": "malformed_rtc_history"})


class TestRtcRound2Hardening(RtcTestBase):
    @patch("requests.get")
    def test_in_memory_or_empty_db_path_fails_closed(self, mock_get):
        # Item 1: each connection to ":memory:" is a fresh DB, so replay would pass.
        mock_get.return_value = self._history(self._confirmed())
        for bad in (":memory:", "", "file::memory:", "file:x?mode=memory&cache=shared",
                    "FILE:rtc.db", " /tmp/rtc.db", self._tmp.name):
            with self.subTest(db=bad), patch.dict("os.environ", {"RTC_PAYMENT_DB": bad}):
                first = x402_bridge._verify_rtc_payment(self._header(), "atlas_query")
                second = x402_bridge._verify_rtc_payment(self._header(), "atlas_query")
                self.assertFalse(first["verified"], first)
                self.assertFalse(second["verified"], second)
                self.assertTrue(first["error"].startswith("invalid_rtc_payment_db"), first)
        mock_get.assert_not_called()

    def test_consume_refuses_in_memory_db(self):
        with patch.dict("os.environ", {"RTC_PAYMENT_DB": ":memory:"}):
            with self.assertRaises(x402_bridge.RtcPaymentDbConfigError):
                x402_bridge._consume_rtc_tx_hash(TX_HASH, "RTCsender", PAY_TO, 0.1, "atlas_query")

    @patch("requests.get")
    def test_trailing_newline_ids_are_rejected(self, mock_get):
        # Item 2: re.match + "$" would accept a final "\n".
        result = x402_bridge._verify_rtc_payment(self._header(tx_hash=TX_HASH + "\n"), "atlas_query")
        self.assertEqual(result, {"verified": False, "error": "malformed_tx_hash"})
        result = x402_bridge._verify_rtc_payment(self._header(from_wallet="RTCsender\n"), "atlas_query")
        self.assertEqual(result, {"verified": False, "error": "malformed_from_wallet"})

        mock_get.assert_not_called()

        # Row-side sender check, even if a row's sender and reason agree.
        row = self._confirmed(sender="RTCsender\n")
        self.assertFalse(x402_bridge._rtc_history_row_is_settled(row, TX_HASH))
        self.assertTrue(x402_bridge._rtc_history_row_is_settled(self._confirmed(), TX_HASH))

    @patch("requests.get")
    def test_non_positive_or_invalid_max_age_fails_closed(self, mock_get):
        # Item 3: max age must never disable freshness.
        old_row = dict(self._confirmed(), timestamp=1_000_000_000)
        for bad in ("0", "-1", "abc"):
            with self.subTest(max_age=bad), patch.dict("os.environ", {"RTC_PAYMENT_MAX_AGE_SECONDS": bad}):
                result = self._verify(mock_get, self._history(old_row), self._header())
                self.assertEqual(result, {"verified": False, "error": "invalid_rtc_payment_max_age"})
                result = self._verify(mock_get, self._history(self._confirmed()), self._header())
                self.assertEqual(result, {"verified": False, "error": "invalid_rtc_payment_max_age"})

    def test_flag_accepts_exactly_one(self):
        # Item 5.
        for value, expected in (("1", True), (" 1", True), ("1 ", True), ("true", False),
                                ("yes", False), ("on", False), ("TRUE", False), ("2", False),
                                ("0", False), ("", False), ("01", False)):
            with self.subTest(value=value), patch.dict("os.environ", {"RTC_X402_PAYMENTS_ENABLED": value}):
                self.assertIs(x402_bridge._rtc_payments_enabled(), expected)


class TestRtcAdvertising(unittest.TestCase):
    """Item 4: discovery must not advertise RTC while the RTC path is off."""

    def setUp(self):
        from flask import Flask

        app = Flask(__name__)
        app.register_blueprint(x402_bridge.x402_bp)
        self.client = app.test_client()

    def _get(self, path, flag):
        with patch.dict("os.environ", {"RTC_X402_PAYMENTS_ENABLED": flag}):
            resp = self.client.get(path)
        self.assertEqual(resp.status_code, 200)
        return resp.get_json()

    def test_pricing_hides_rtc_when_disabled(self):
        for flag in ("0", "true"):
            with self.subTest(flag=flag):
                body = self._get("/api/x402/pricing", flag)
                self.assertFalse(body["dual_economy"])
                self.assertEqual(body["accepts"], ["x402_usdc"])
                self.assertNotIn("rustchain_rtc", json.dumps(body))
                self.assertTrue(all("rtc_equivalent" not in v for v in body["pricing"].values()))

    def test_pricing_advertises_rtc_when_enabled(self):
        body = self._get("/api/x402/pricing", "1")
        self.assertTrue(body["dual_economy"])
        self.assertEqual(body["accepts"], ["x402_usdc", "rustchain_rtc"])
        self.assertEqual(body["pricing"]["inference_llm"]["rtc_equivalent"], 0.1)

    def test_agent_card_mentions_rtc_only_when_enabled(self):
        off = self._get("/.well-known/agent-card.json", "0")
        self.assertNotIn("RTC", off["description"])
        on = self._get("/.well-known/agent-card.json", "1")
        self.assertIn("RTC on RustChain", on["description"])


if __name__ == "__main__":
    unittest.main()
