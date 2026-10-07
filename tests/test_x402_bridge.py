import copy
import json
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


class TestRtcPaymentVerification(unittest.TestCase):
    def setUp(self):
        x402_bridge._VERIFIED_RTC_TX_HASHES.clear()

    def _header(self, **overrides):
        payload = {
            "tx_hash": "abc123",
            "amount_rtc": 0.1,
            "from_wallet": "RTCsender",
            "to_wallet": "RTCservice",
        }
        payload.update(overrides)
        return json.dumps(payload)

    def test_rejects_missing_tx_hash(self):
        with patch.dict("os.environ", {"RTC_PAY_TO": "RTCservice"}):
            result = x402_bridge._verify_rtc_payment(self._header(tx_hash=""), "inference_llm")

        self.assertFalse(result["verified"])
        self.assertEqual(result["error"], "missing_tx_hash")

    def test_rejects_client_pay_to_mismatch(self):
        with patch.dict("os.environ", {"RTC_PAY_TO": "RTCservice"}):
            result = x402_bridge._verify_rtc_payment(self._header(to_wallet="RTCattacker"), "inference_llm")

        self.assertFalse(result["verified"])
        self.assertEqual(result["error"], "rtc_pay_to_mismatch")

    @patch("requests.get")
    def test_rejects_pending_history_transaction(self, mock_get):
        mock_get.return_value = FakeResponse({
            "transactions": [{
                "tx_hash": "abc123",
                "amount": 0.1,
                "from": "RTCsender",
                "status": "pending",
                "timestamp": time.time(),
            }]
        })

        with patch.dict("os.environ", {"RTC_PAY_TO": "RTCservice"}):
            result = x402_bridge._verify_rtc_payment(self._header(), "inference_llm")

        self.assertFalse(result["verified"])
        self.assertEqual(result["error"], "unsettled_rtc_tx:pending")
        mock_get.assert_called_once()
        self.assertEqual(mock_get.call_args.kwargs["params"], {"miner_id": "RTCservice"})

    @patch("requests.get")
    def test_verifies_settled_recipient_history_transaction(self, mock_get):
        mock_get.return_value = FakeResponse({
            "transactions": [{
                "tx_hash": "abc123",
                "amount": 0.1,
                "from": "RTCsender",
                "status": "settled",
                "timestamp": time.time(),
            }]
        })

        with patch.dict("os.environ", {"RTC_PAY_TO": "RTCservice"}):
            result = x402_bridge._verify_rtc_payment(self._header(), "inference_llm")

        self.assertTrue(result["verified"])
        self.assertEqual(result["tx_hash"], "abc123")

    @patch("requests.get")
    def test_rejects_replayed_tx_hash(self, mock_get):
        mock_get.return_value = FakeResponse({
            "transactions": [{
                "tx_hash": "abc123",
                "amount": 0.1,
                "from": "RTCsender",
                "status": "confirmed",
                "timestamp": time.time(),
            }]
        })

        with patch.dict("os.environ", {"RTC_PAY_TO": "RTCservice"}):
            first = x402_bridge._verify_rtc_payment(self._header(), "inference_llm")
            second = x402_bridge._verify_rtc_payment(self._header(), "inference_llm")

        self.assertTrue(first["verified"])
        self.assertFalse(second["verified"])
        self.assertEqual(second["error"], "rtc_payment_replay")


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


class TestRtcPaymentLiveHistoryShape(unittest.TestCase):
    """Regression for #964 using the real /wallet/history payload shape."""

    def setUp(self):
        x402_bridge._VERIFIED_RTC_TX_HASHES.clear()

    @staticmethod
    def _history(*rows):
        return FakeResponse({
            "miner_id": "RTCservice",
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
    def _header(row, **overrides):
        payload = {
            "tx_hash": row["tx_hash"],
            "amount_rtc": float(row["amount"]),
            "from_wallet": row["from"],
            "to_wallet": "RTCservice",
        }
        payload.update(overrides)
        return json.dumps(payload)

    def _verify(self, mock_get, response, header, service="atlas_query"):
        mock_get.return_value = response
        with patch.dict("os.environ", {"RTC_PAY_TO": "RTCservice"}):
            return x402_bridge._verify_rtc_payment(header, service)

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

        self.assertFalse(result["verified"])
        self.assertEqual(result["error"], "unsettled_rtc_tx:pending")

    @patch("requests.get")
    def test_voided_row_is_rejected(self, mock_get):
        row = self._fresh(LIVE_PENDING_ROW)
        row["status"] = "voided"
        result = self._verify(mock_get, self._history(row), self._header(row))

        self.assertFalse(result["verified"])
        self.assertEqual(result["error"], "unsettled_rtc_tx:voided")

    @patch("requests.get")
    def test_row_without_status_or_ledger_reason_is_rejected(self, mock_get):
        # Missing status alone is not proof of settlement.
        row = self._fresh(LIVE_PENDING_ROW)
        del row["status"]
        result = self._verify(mock_get, self._history(row), self._header(row))

        self.assertFalse(result["verified"])
        self.assertEqual(result["error"], "unsettled_rtc_tx:unproven")

    @patch("requests.get")
    def test_row_whose_reason_names_another_tx_is_rejected(self, mock_get):
        row = self._fresh(LIVE_CONFIRMED_ROW)
        row["reason"] = "transfer_in:founder_team_bounty:0000000000000000000000000000000"
        result = self._verify(mock_get, self._history(row), self._header(row))

        self.assertFalse(result["verified"])
        self.assertEqual(result["error"], "unsettled_rtc_tx:unproven")

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

        self.assertFalse(result["verified"])
        self.assertEqual(result["error"], "rtc_from_wallet_mismatch")

    @patch("requests.get")
    def test_replayed_tx_hash_is_rejected(self, mock_get):
        row = self._fresh(LIVE_CONFIRMED_ROW)
        first = self._verify(mock_get, self._history(row), self._header(row))
        second = self._verify(mock_get, self._history(row), self._header(row))

        self.assertTrue(first["verified"], first)
        self.assertFalse(second["verified"])
        self.assertEqual(second["error"], "rtc_payment_replay")

    @patch("requests.get")
    def test_stale_confirmed_row_is_rejected(self, mock_get):
        row = copy.deepcopy(LIVE_CONFIRMED_ROW)  # original 2026 timestamp, far past 3600 s
        with patch.dict("os.environ", {"RTC_PAYMENT_MAX_AGE_SECONDS": "3600"}):
            result = self._verify(mock_get, self._history(row), self._header(row))

        self.assertFalse(result["verified"])
        self.assertEqual(result["error"], "stale_rtc_tx")


if __name__ == "__main__":
    unittest.main()
