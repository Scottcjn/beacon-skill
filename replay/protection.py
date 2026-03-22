"""
Beacon Skill - Replay Protection (#163)
"""
class ReplayProtection:
    def __init__(self):
        self.seen_nonces = set()
    
    def is_valid(self, nonce: str) -> bool:
        if nonce in self.seen_nonces:
            return False
        self.seen_nonces.add(nonce)
        return True
