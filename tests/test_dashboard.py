import json
import shutil
import unittest
from pathlib import Path

from beacon_skill.dashboard import (
    _entry_to_row,
    _row_matches_query,
    export_dashboard_rows,
    fetch_beacon_snapshot,
    parse_dashboard_input,
)


class _Resp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload


class _Session:
    def __init__(self, handler):
        self._handler = handler

    def request(self, method, url, timeout=0):  # noqa: ARG002
        return self._handler(method, url)


class TestDashboardHelpers(unittest.TestCase):
    def test_parse_dashboard_input(self):
        self.assertEqual(parse_dashboard_input("hello")["action"], "send")
        self.assertEqual(parse_dashboard_input("/filter rust")["action"], "filter")
        self.assertEqual(parse_dashboard_input("/clear")["query"], "")
        self.assertEqual(parse_dashboard_input("/export json out.json")["format"], "json")
        self.assertEqual(parse_dashboard_input("/export csv")["format"], "csv")

    def test_row_matches_query(self):
        row = {
            "transport": "DISCORD",
            "agent": "bcn_test",
            "kind": "bounty",
            "message": "new mining bounty",
        }
        self.assertTrue(_row_matches_query(row, "mining"))
        self.assertTrue(_row_matches_query(row, "discord"))
        self.assertFalse(_row_matches_query(row, "rustchain"))

    def test_entry_to_row(self):
        entry = {
            "platform": "discord",
            "received_at": 1700000000,
            "envelope": {"kind": "hello", "agent_id": "bcn_abcdef", "text": "hi", "rtc_tip": 2},
        }
        row = _entry_to_row(entry)
        self.assertEqual(row["transport"], "DISCORD")
        self.assertEqual(row["kind"], "hello")
        self.assertEqual(row["rtc"], "2")

    def test_fetch_beacon_snapshot_success(self):
        def handler(method, url):  # noqa: ARG001
            if url.endswith("/api/agents"):
                return _Resp(200, [{"id": "a1"}, {"id": "a2"}])
            if url.endswith("/api/contracts"):
                return _Resp(200, {"items": [{"id": "c1"}]})
            if url.endswith("/api/reputation"):
                return _Resp(200, {"data": [{"agent": "a1"}, {"agent": "a2"}, {"agent": "a3"}]})
            return _Resp(404, {})

        snap = fetch_beacon_snapshot(session=_Session(handler))
        self.assertTrue(snap["ok"])
        self.assertEqual(snap["agents_count"], 2)
        self.assertEqual(snap["contracts_count"], 1)
        self.assertEqual(snap["reputation_count"], 3)

    def test_fetch_beacon_snapshot_partial_failure(self):
        def handler(method, url):  # noqa: ARG001
            if url.endswith("/api/agents"):
                return _Resp(500, {"error": "boom"})
            if url.endswith("/api/contracts"):
                raise RuntimeError("network down")
            return _Resp(200, [])

        snap = fetch_beacon_snapshot(session=_Session(handler))
        self.assertFalse(snap["ok"])
        self.assertGreaterEqual(len(snap["errors"]), 2)
        self.assertEqual(snap["reputation_count"], 0)

    def test_export_dashboard_rows_json_and_csv(self):
        rows = [
            {
                "time": "10:00:00",
                "transport": "DISCORD",
                "agent": "bcn_abc",
                "kind": "bounty",
                "message": "hello",
                "rtc": "5",
                "received_at": 1.0,
            }
        ]
        td = Path("tests") / "_tmp_dashboard_export"
        td.mkdir(parents=True, exist_ok=True)
        try:
            json_path = export_dashboard_rows(rows, "json", str(td / "out.json"))
            csv_path = export_dashboard_rows(rows, "csv", str(td / "out.csv"))

            self.assertTrue(Path(json_path).exists())
            self.assertTrue(Path(csv_path).exists())

            loaded = json.loads(Path(json_path).read_text(encoding="utf-8"))
            self.assertEqual(loaded["count"], 1)
            self.assertEqual(loaded["rows"][0]["transport"], "DISCORD")
        finally:
            shutil.rmtree(td, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
