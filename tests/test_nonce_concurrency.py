import unittest
import threading
import time
from beacon_skill.guard import check_envelope_window, clear_nonce_cache

class TestNonceConcurrency(unittest.TestCase):
    def setUp(self):
        clear_nonce_cache()

    def test_concurrent_nonces(self):
        results = []
        envelope = {"nonce": "shared-nonce", "ts": int(time.time())}
        
        def attempt_check():
            ok, reason = check_envelope_window(envelope)
            results.append(ok)

        threads = [threading.Thread(target=attempt_check) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Only exactly one thread should have succeeded
        success_count = results.count(True)
        self.assertEqual(success_count, 1, f"Expected 1 success, got {success_count}. Results: {results}")

if __name__ == "__main__":
    unittest.main()
