import pytest
from beacon_skill.codec import decode_envelopes

def test_decode_envelopes_deep_nesting():
    # Test deep nesting to see if it causes issues or crashes
    payload = "[BEACON v2]\n" + "{" * 1000 + "}" * 1000
    # Should handle it gracefully or return None/empty list instead of crashing
    try:
        decode_envelopes(payload)
    except Exception as e:
        pytest.fail(f"decode_envelopes crashed with deep nesting: {e}")

def test_decode_envelopes_malformed_header():
    # Test header without 'v' or ']'
    payload = "[BEACON version 2]\n{}"
    # Should fallback to version 1
    res = decode_envelopes(payload)
    assert res[0]["_beacon_version"] == 1

def test_decode_envelopes_unbalanced_json():
    # Test JSON that starts but never ends
    payload = "[BEACON v2]\n{\"key\": \"value\""
    # Should not crash and return empty list
    assert decode_envelopes(payload) == []

def test_decode_envelopes_escaped_brackets():
    # Test JSON with escaped brackets inside strings
    payload = '[BEACON v2]\n{"text": "This is a bracket } in a string"}'
    res = decode_envelopes(payload)
    assert len(res) == 1
    assert res[0]["text"] == "This is a bracket } in a string"
