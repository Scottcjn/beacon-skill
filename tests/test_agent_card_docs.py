import json
import re
from pathlib import Path


def test_agent_card_schema_doc_has_minimal_example_and_reject_checklist() -> None:
    text = Path("docs/AGENT_CARD.md").read_text()
    assert "`/.well-known/beacon.json`" in text
    assert "## Field reference" in text
    assert "## Discovery-client reject checklist" in text

    # The first JSON block is the minimal card shape shown to new implementers.
    match = re.search(r"```json\n(.*?)\n```", text, re.S)
    assert match is not None
    card = json.loads(match.group(1))
    assert set(card) == {
        "beacon_version",
        "agent_id",
        "public_key_hex",
        "signature",
    }
    assert card["beacon_version"] == "1.0.0"

    for field in ("beacon_version", "agent_id", "public_key_hex", "signature"):
        assert f"| `{field}` | yes |" in text

    for rejection in ("required fields are missing", "agent_id does not match", "signature does not verify"):
        assert rejection in text
