import unittest
from unittest import mock

from beacon_skill.retry import with_retry


class TestRetry(unittest.TestCase):
    def test_success_on_first_attempt(self) -> None:
        result = with_retry(lambda: 42)
        self.assertEqual(result, 42)

    @mock.patch("beacon_skill.retry.time.sleep")
    def test_retry_on_429(self, mock_sleep) -> None:
        call_count = {"n": 0}

        def flaky():
            call_count["n"] += 1
            if call_count["n"] < 3:
                raise RuntimeError("HTTP 429")
            return "ok"

        result = with_retry(flaky, max_attempts=3, base_delay=0.01)
        self.assertEqual(result, "ok")
        self.assertEqual(call_count["n"], 3)

    @mock.patch("beacon_skill.retry.time.sleep")
    def test_exhausted_raises(self, mock_sleep) -> None:
        def always_fail():
            raise RuntimeError("HTTP 500")

        with self.assertRaises(RuntimeError):
            with_retry(always_fail, max_attempts=2, base_delay=0.01)

    def test_invalid_max_attempts_raises_value_error(self) -> None:
        with self.assertRaises(ValueError):
            with_retry(lambda: "ok", max_attempts=0)

    def test_invalid_base_delay_raises_value_error(self) -> None:
        with self.assertRaises(ValueError):
            with_retry(lambda: "ok", base_delay=-0.1)


if __name__ == "__main__":
    unittest.main()
