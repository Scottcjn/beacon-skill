import json
import re
from pathlib import Path


def test_minimum_envelope_doc_has_minimal_example_and_receiver_rules() -> None:
    text = Path("docs/MINIMUM_ENVELOPE.md").read_text()

    assert "## Minimal valid v2 envelope" in text
    assert "## Required fields and types" in text
    assert "## What exactly gets signed" in text
    assert "## Verification rules" in text
    assert "## Receiver result codes" in text
    assert "## Cross-language checklist" in text
    assert "docs/SECURITY.md" in text
    assert "docs/BEACON_MECHANISM_TEST.md" in text

    match = re.search(r"```json\n(.*?)\n```", text, re.S)
    assert match is not None
    envelope = json.loads(match.group(1))
    assert set(envelope) == {"v", "kind", "agent_id", "ts", "nonce", "pubkey", "sig"}
    assert envelope["v"] == 2
    assert envelope["kind"] == "hello"

    for field in ("v", "kind", "agent_id", "ts", "nonce", "sig"):
        assert f"| `{field}` | yes |" in text
    assert "| `pubkey` | recommended |" in text

    for reason in (
        "ok",
        "signature_invalid",
        "signature_unverifiable",
        "missing_nonce",
        "missing_ts",
        "stale_ts",
        "future_ts",
        "replay_nonce",
    ):
        assert f"| `{reason}` |" in text

    assert "sort_keys=True" in text
    assert 'separators=(",", ":")' in text
    assert "maximum age: `900` seconds" in text
    assert "maximum future skew: `120` seconds" in text
