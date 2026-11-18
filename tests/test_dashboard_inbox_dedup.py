"""Regression test for #958: idle polls must not recount the boundary entry.

`read_inbox(since=...)` includes entries whose `received_at` equals `since`, so
the dashboard's `_last_ts` boundary entry is returned again on the next idle
poll and was appended/counted a second time. `_select_new_entries` deduplicates
already-consumed boundary identities while keeping distinct same-timestamp
arrivals visible.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from beacon_skill.dashboard import _entry_identity, _select_new_entries  # noqa: E402


def _entry(nonce, received_at, text="hello"):
    return {
        "received_at": received_at,
        "platform": "udp",
        "text": text,
        "envelope": {"kind": "hello", "agent_id": "bcn_x", "nonce": nonce, "text": text},
    }


def test_boundary_entry_not_recounted_on_idle_poll():
    entry = _entry("n1", 100.0)
    seen = set()

    first = _select_new_entries([entry], seen)
    assert len(first) == 1

    # the next idle poll returns the same boundary entry again
    second = _select_new_entries([entry], seen)
    assert second == []


def test_distinct_same_timestamp_arrivals_still_surface():
    first = _entry("n1", 100.0)
    later = _entry("n2", 100.0, text="different")
    seen = set()

    assert len(_select_new_entries([first], seen)) == 1
    fresh = _select_new_entries([first, later], seen)
    assert len(fresh) == 1
    assert fresh[0]["message"] != "" or fresh[0]["kind"] == "hello"


def test_identity_is_stable_for_same_entry():
    entry = _entry("n1", 100.0)
    assert _entry_identity(entry) == _entry_identity(entry)
    assert _entry_identity(_entry("n1", 100.0)) == _entry_identity(entry)


def test_different_nonce_same_timestamp_is_distinct():
    assert _entry_identity(_entry("n1", 100.0)) != _entry_identity(_entry("n2", 100.0))
