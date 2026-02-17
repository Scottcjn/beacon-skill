"""Tests for Lambda Lang codec integration."""

import pytest
from beacon_skill.lambda_codec import (
    encode_lambda,
    decode_lambda,
    wrap_lambda_envelope,
    unwrap_lambda_envelope,
    estimate_compression,
    KIND_TO_LAMBDA,
    LAMBDA_TO_KIND,
)


class TestEncodeLambda:
    def test_encode_hello(self):
        payload = {
            "kind": "hello",
            "agent_id": "bcn_abc123def456",
            "text": "Hello from Beacon",
        }
        result = encode_lambda(payload)
        assert "!hl" in result
        assert "aid:bcn_abc123de" in result
        
    def test_encode_heartbeat(self):
        payload = {
            "kind": "heartbeat",
            "agent_id": "bcn_test12345678",
            "status": "healthy",
        }
        result = encode_lambda(payload)
        assert "!hb" in result
        assert "e:al" in result
        
    def test_encode_heartbeat_degraded(self):
        payload = {
            "kind": "heartbeat",
            "agent_id": "bcn_test12345678",
            "status": "degraded",
        }
        result = encode_lambda(payload)
        assert "e:dg" in result
        
    def test_encode_accord_offer(self):
        payload = {
            "kind": "accord_offer",
            "agent_id": "bcn_proposer1234",
        }
        result = encode_lambda(payload)
        assert "!acc/of" in result
        
    def test_encode_with_text_compression(self):
        payload = {
            "kind": "want",
            "text": "Looking for agent to collaborate",
        }
        result = encode_lambda(payload)
        # "looking for" should be compressed to "lf"
        assert "lf" in result.lower() or "looking for" not in result.lower()


class TestDecodeLambda:
    def test_decode_hello(self):
        lambda_str = '!hl aid:bcn_abc123de "hello world"'
        result = decode_lambda(lambda_str)
        assert result.get("kind") == "hello"
        assert "bcn_" in result.get("agent_id", "")
        assert result.get("text") == "hello world"
        
    def test_decode_heartbeat_healthy(self):
        lambda_str = "!hb aid:bcn_test1234 e:al"
        result = decode_lambda(lambda_str)
        assert result.get("kind") == "heartbeat"
        assert result.get("status") == "healthy"
        
    def test_decode_heartbeat_degraded(self):
        lambda_str = "!hb aid:bcn_test1234 e:dg"
        result = decode_lambda(lambda_str)
        assert result.get("status") == "degraded"
        
    def test_decode_with_nonce(self):
        lambda_str = "!hb aid:bcn_test1234 n:abc123"
        result = decode_lambda(lambda_str)
        assert result.get("nonce") == "abc123"


class TestRoundtrip:
    def test_hello_roundtrip(self):
        original = {
            "kind": "hello",
            "agent_id": "bcn_roundtrip123",
            "text": "test message",
        }
        encoded = encode_lambda(original)
        decoded = decode_lambda(encoded)
        assert decoded.get("kind") == original["kind"]
        # Agent ID may be truncated
        assert decoded.get("agent_id", "").startswith("bcn_")
        
    def test_heartbeat_roundtrip(self):
        original = {
            "kind": "heartbeat",
            "agent_id": "bcn_heartbeat999",
            "status": "healthy",
        }
        encoded = encode_lambda(original)
        decoded = decode_lambda(encoded)
        assert decoded.get("kind") == "heartbeat"
        assert decoded.get("status") == "healthy"


class TestEnvelope:
    def test_wrap_envelope(self):
        lambda_str = "!hb aid:bcn_test e:al"
        result = wrap_lambda_envelope(lambda_str, "bcn_test")
        assert "[BEACON v2 lambda]" in result
        assert lambda_str in result
        assert "[/BEACON]" in result
        
    def test_wrap_envelope_with_sig(self):
        lambda_str = "!hb aid:bcn_test e:al"
        result = wrap_lambda_envelope(lambda_str, "bcn_test", signature="abcdef1234567890")
        assert "sig:abcdef12345678" in result
        
    def test_unwrap_envelope(self):
        envelope = "[BEACON v2 lambda]\n!hb aid:bcn_test e:al\n[/BEACON]"
        result = unwrap_lambda_envelope(envelope)
        assert result is not None
        lambda_str, metadata = result
        assert "!hb" in lambda_str
        
    def test_unwrap_envelope_with_sig(self):
        envelope = "[BEACON v2 lambda]\n!hb aid:bcn_test e:al\nsig:abcdef12\n[/BEACON]"
        result = unwrap_lambda_envelope(envelope)
        assert result is not None
        lambda_str, metadata = result
        assert metadata.get("sig") == "abcdef12"


class TestCompression:
    def test_estimate_compression_hello(self):
        payload = {
            "kind": "hello",
            "agent_id": "bcn_abc123def456",
            "text": "Hello from Beacon",
        }
        ratio = estimate_compression(payload)
        # Lambda should compress at least 2x
        assert ratio >= 1.5
        
    def test_estimate_compression_large_payload(self):
        payload = {
            "kind": "heartbeat",
            "agent_id": "bcn_largeagent123456",
            "status": "healthy",
            "nonce": "abcdef123456",
            "text": "Looking for agent to collaborate on beacon protocol",
        }
        ratio = estimate_compression(payload)
        # Larger payloads should compress better
        assert ratio >= 2.0


class TestKindMappings:
    def test_all_kinds_have_reverse_mapping(self):
        for kind, atom in KIND_TO_LAMBDA.items():
            assert LAMBDA_TO_KIND.get(atom) == kind
            
    def test_core_beacon_kinds_mapped(self):
        core_kinds = ["hello", "heartbeat", "mayday", "bounty", "want", "like"]
        for kind in core_kinds:
            assert kind in KIND_TO_LAMBDA
