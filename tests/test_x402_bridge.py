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


if __name__ == "__main__":
    unittest.main()
