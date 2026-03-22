import pytest
from protection import ReplayProtection

class TestReplayProtection:
    def test_init(self):
        rp = ReplayProtection()
        assert isinstance(rp.seen_nonces, set)
    
    def test_is_valid(self):
        rp = ReplayProtection()
        assert rp.is_valid("nonce1") == True
        assert rp.is_valid("nonce1") == False  # Replay detected

if __name__ == '__main__':
    pytest.main([__file__, '-v'])
