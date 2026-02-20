from __future__ import annotations

import csv
import json
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from .codec import encode_envelope
from .config import load_config
from .identity import AgentIdentity
from .inbox import read_inbox
from .storage import append_jsonl
from .transports.udp import udp_send

DEFAULT_API_BASE_URL = "http://50.28.86.131:8071"


def _format_ts(ts: Optional[float]) -> str:
    if not ts:
        return "--:--:--"
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%H:%M:%S")


def _short_agent(v: str) -> str:
    if not v:
        return "unknown"
    if len(v) <= 14:
        return v
    return v[:11] + "..."


def _as_text(entry: Dict[str, Any]) -> str:
    env = entry.get("envelope") or {}
    txt = env.get("text") or entry.get("text") or ""
    txt = str(txt).replace("\n", " ").strip()
    if len(txt) > 80:
        return txt[:77] + "..."
    return txt


def _rtc_tip(entry: Dict[str, Any]) -> Optional[float]:
    env = entry.get("envelope") or {}
    for k in ("rtc_tip", "tip_rtc", "reward_rtc"):
        v = env.get(k)
        if v is None:
            continue
        try:
            return float(v)
        except Exception:
            continue
    return None


def _transport_tag(entry: Dict[str, Any]) -> str:
    p = (entry.get("platform") or "unknown").lower()
    if p == "udp":
        return "udp"
    if p in {"bottube", "discord", "rustchain", "webhook"}:
        return p
    return p


def _entry_to_row(entry: Dict[str, Any]) -> Dict[str, Any]:
    rts = float(entry.get("received_at") or 0.0)
    env = entry.get("envelope") or {}
    kind = str(env.get("kind") or "raw")
    transport = _transport_tag(entry)
    agent = str(env.get("agent_id") or entry.get("from") or "")
    msg = _as_text(entry)
    rtc = _rtc_tip(entry)
    return {
        "time": _format_ts(rts),
        "transport": transport.upper(),
        "agent": _short_agent(agent),
        "kind": kind,
        "message": msg,
        "rtc": f"{rtc:g}" if rtc is not None else "-",
        "rtc_value": rtc,
        "received_at": rts,
    }


def _row_matches_query(row: Dict[str, Any], query: str) -> bool:
    q = (query or "").strip().lower()
    if not q:
        return True
    hay = " ".join(
        [
            str(row.get("transport", "")),
            str(row.get("agent", "")),
            str(row.get("kind", "")),
            str(row.get("message", "")),
        ]
    ).lower()
    return q in hay


def parse_dashboard_input(raw: str) -> Dict[str, Any]:
    text = (raw or "").strip()
    if not text:
        return {"action": "empty"}
    if not text.startswith("/"):
        return {"action": "send", "text": text}

    cmdline = text[1:]
    head, sep, tail = cmdline.partition(" ")
    cmd = head.strip().lower()
    rest = tail.strip() if sep else ""

    if cmd in {"filter", "search"}:
        return {"action": "filter", "query": rest}
    if cmd in {"clear", "clearfilter"}:
        return {"action": "filter", "query": ""}
    if cmd == "export":
        f_head, f_sep, f_tail = rest.partition(" ")
        fmt = (f_head or "").strip().lower()
        path = (f_tail or "").strip() if f_sep else ""
        if fmt in {"json", "csv"}:
            return {"action": "export", "format": fmt, "path": path or None}
    if cmd in {"help", "?"}:
        return {"action": "help"}
    return {"action": "send", "text": text}


def _normalize_api_rows(data: Any) -> List[Dict[str, Any]]:
    if isinstance(data, list):
        return [d for d in data if isinstance(d, dict)]
    if isinstance(data, dict):
        for key in ("items", "data", "results", "agents", "contracts", "reputation"):
            v = data.get(key)
            if isinstance(v, list):
                return [d for d in v if isinstance(d, dict)]
    return []


def fetch_beacon_snapshot(
    api_base_url: str = DEFAULT_API_BASE_URL,
    *,
    timeout_s: float = 8.0,
    session: Optional[requests.Session] = None,
) -> Dict[str, Any]:
    http = session or requests.Session()
    base = (api_base_url or DEFAULT_API_BASE_URL).rstrip("/")
    endpoints = {
        "agents": f"{base}/api/agents",
        "contracts": f"{base}/api/contracts",
        "reputation": f"{base}/api/reputation",
    }
    out: Dict[str, Any] = {"ok": True, "errors": [], "fetched_at": int(time.time())}
    for name, url in endpoints.items():
        try:
            resp = http.request("GET", url, timeout=timeout_s)
            if int(resp.status_code) >= 400:
                out["ok"] = False
                out["errors"].append(f"{name}: HTTP {resp.status_code}")
                out[name] = []
                continue
            try:
                payload = resp.json()
            except Exception:
                payload = {}
            out[name] = _normalize_api_rows(payload)
        except Exception as e:
            out["ok"] = False
            out["errors"].append(f"{name}: {e}")
            out[name] = []
    out["agents_count"] = len(out.get("agents", []))
    out["contracts_count"] = len(out.get("contracts", []))
    out["reputation_count"] = len(out.get("reputation", []))
    return out


def export_dashboard_rows(rows: List[Dict[str, Any]], fmt: str, path: Optional[str] = None) -> str:
    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%d-%H%M%S")
    ext = "json" if fmt == "json" else "csv"
    target = Path(path) if path else Path(f"beacon-dashboard-snapshot-{ts}.{ext}")
    target.parent.mkdir(parents=True, exist_ok=True)

    if fmt == "json":
        payload = {"count": len(rows), "rows": rows, "exported_at": int(time.time())}
        target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return str(target)

    fields = ["time", "transport", "agent", "kind", "message", "rtc", "received_at"]
    with target.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fields})
    return str(target)


def _send_quick_ping(raw: str) -> Dict[str, Any]:
    cfg = load_config()
    txt = (raw or "").strip()
    if not txt:
        return {"ok": False, "error": "empty"}

    kind = "hello"
    text = txt
    if txt.startswith("/") and " " in txt:
        head, rest = txt[1:].split(" ", 1)
        if head:
            kind = head.strip().lower()
            text = rest.strip()

    payload = {
        "v": 1,
        "kind": kind,
        "from": cfg.get("beacon", {}).get("agent_name", ""),
        "to": "dashboard:quick-send",
        "ts": int(time.time()),
        "text": text,
    }

    signed = False
    try:
        ident = AgentIdentity.load()
        payload_text = encode_envelope(payload, version=2, identity=ident, include_pubkey=True)
        signed = True
    except Exception:
        payload_text = encode_envelope(payload, version=1)

    udp_cfg = cfg.get("udp") or {}
    sent_udp = False
    if bool(udp_cfg.get("enabled")):
        host = str(udp_cfg.get("host") or "255.255.255.255")
        port = int(udp_cfg.get("port") or 38400)
        broadcast = bool(udp_cfg.get("broadcast", True))
        ttl = udp_cfg.get("ttl")
        try:
            ttl_int = int(ttl) if ttl is not None else None
        except Exception:
            ttl_int = None
        try:
            udp_send(host, port, payload_text.encode("utf-8", errors="replace"), broadcast=broadcast, ttl=ttl_int)
            sent_udp = True
        except Exception:
            sent_udp = False

    append_jsonl(
        "outbox.jsonl",
        {
            "platform": "dashboard",
            "action": "quick-send",
            "kind": kind,
            "text": text,
            "signed": signed,
            "sent_udp": sent_udp,
            "ts": int(time.time()),
        },
    )
    return {"ok": True, "kind": kind, "signed": signed, "sent_udp": sent_udp}


def run_dashboard(
    poll_interval: float = 1.0,
    sound: bool = False,
    *,
    api_base_url: str = DEFAULT_API_BASE_URL,
    api_poll_interval: float = 15.0,
    initial_filter: str = "",
) -> int:
    try:
        from textual.app import App, ComposeResult
        from textual.containers import Horizontal
        from textual.widgets import DataTable, Footer, Header, Input, Static, TabbedContent, TabPane
    except Exception as e:  # pragma: no cover
        raise RuntimeError("textual is required for dashboard. Install with: pip install textual") from e

    class BeaconDashboard(App):
        CSS = """
        Screen {
            background: #000000;
            color: #8ff58f;
        }
        #root {
            height: 1fr;
        }
        #sidebar {
            width: 38;
            border: solid #1f6f1f;
            padding: 1;
            background: #050505;
        }
        #tabs {
            width: 1fr;
            border: solid #1f6f1f;
        }
        DataTable {
            height: 1fr;
            background: #0a0a0a;
        }
        Input {
            dock: bottom;
            border: solid #1f6f1f;
            background: #030303;
            color: #b8ffb8;
        }
        """

        BINDINGS = [
            ("q", "quit", "Quit"),
            ("ctrl+c", "quit", "Quit"),
        ]

        def __init__(self) -> None:
            super().__init__()
            self._last_ts = 0.0
            self._count_today = 0
            self._transport_counter: Counter[str] = Counter()
            self._agent_counter: Counter[str] = Counter()
            self._history_rows: List[Dict[str, Any]] = []
            self._visible_rows: List[Dict[str, Any]] = []
            self._filter_query = (initial_filter or "").strip().lower()
            self._api_state: Dict[str, Any] = {
                "ok": False,
                "agents_count": 0,
                "contracts_count": 0,
                "reputation_count": 0,
                "errors": ["not fetched yet"],
                "fetched_at": None,
            }
            self._http = requests.Session()

        def compose(self) -> ComposeResult:
            yield Header(show_clock=True)
            with Horizontal(id="root"):
                yield Static("Booting...", id="sidebar")
                with TabbedContent(id="tabs"):
                    with TabPane("All", id="tab-all"):
                        yield DataTable(id="tbl-all")
                    with TabPane("BoTTube", id="tab-bottube"):
                        yield DataTable(id="tbl-bottube")
                    with TabPane("Discord", id="tab-discord"):
                        yield DataTable(id="tbl-discord")
                    with TabPane("RustChain", id="tab-rustchain"):
                        yield DataTable(id="tbl-rustchain")
                    with TabPane("Drama", id="tab-drama"):
                        yield DataTable(id="tbl-drama")
                    with TabPane("Bounties", id="tab-bounty"):
                        yield DataTable(id="tbl-bounty")
            yield Input(
                placeholder="Send ping or commands: /filter text | /export json [path] | /export csv [path]",
                id="quick-send",
            )
            yield Footer()

        def on_mount(self) -> None:
            for tid in (
                "tbl-all",
                "tbl-bottube",
                "tbl-discord",
                "tbl-rustchain",
                "tbl-drama",
                "tbl-bounty",
            ):
                table = self.query_one(f"#{tid}", DataTable)
                table.add_columns("Time", "Transport", "Agent", "Kind", "Message", "RTC")
                table.zebra_stripes = True
            self.set_interval(max(0.25, float(poll_interval)), self._poll_inbox)
            self.set_interval(max(2.0, float(api_poll_interval)), self._poll_api)
            self.set_interval(1.0, self._refresh_sidebar)
            self.title = "Beacon Dashboard"
            self.sub_title = "Live transport activity + Beacon API snapshot"

        def _route_table_id(self, transport: str, kind: str) -> str:
            t = (transport or "").lower()
            k = (kind or "").lower()
            if t == "bottube":
                return "tbl-bottube"
            if t == "discord":
                return "tbl-discord"
            if t == "rustchain":
                return "tbl-rustchain"
            if k in {"mayday", "drama", "roast", "clapback"}:
                return "tbl-drama"
            if k in {"bounty", "offer", "contract", "task"}:
                return "tbl-bounty"
            return "tbl-all"

        def _add_row(self, table_id: str, row: tuple[str, str, str, str, str, str]) -> None:
            table = self.query_one(f"#{table_id}", DataTable)
            table.add_row(*row)
            if table.row_count > 400:
                try:
                    first_key = next(iter(table.rows.keys()))
                    table.remove_row(first_key)
                except Exception:
                    pass

        def _clear_rows(self) -> None:
            for tid in (
                "tbl-all",
                "tbl-bottube",
                "tbl-discord",
                "tbl-rustchain",
                "tbl-drama",
                "tbl-bounty",
            ):
                table = self.query_one(f"#{tid}", DataTable)
                try:
                    keys = list(table.rows.keys())
                    for key in keys:
                        table.remove_row(key)
                except Exception:
                    continue

        def _display_row(self, row: Dict[str, Any]) -> None:
            row_t = (
                str(row.get("time", "")),
                str(row.get("transport", "")),
                str(row.get("agent", "")),
                str(row.get("kind", "")),
                str(row.get("message", "")),
                str(row.get("rtc", "")),
            )
            self._add_row("tbl-all", row_t)
            specific = self._route_table_id(str(row.get("transport", "")).lower(), str(row.get("kind", "")))
            if specific != "tbl-all":
                self._add_row(specific, row_t)
            self._visible_rows.append(row)
            if len(self._visible_rows) > 2000:
                self._visible_rows = self._visible_rows[-2000:]

        def _rebuild_filtered_view(self) -> None:
            self._clear_rows()
            self._visible_rows = []
            for row in self._history_rows:
                if _row_matches_query(row, self._filter_query):
                    self._display_row(row)

        def _poll_inbox(self) -> None:
            entries = read_inbox(since=self._last_ts, limit=500)
            if not entries:
                return
            for entry in entries:
                rts = float(entry.get("received_at") or 0.0)
                if rts > self._last_ts:
                    self._last_ts = rts

                row = _entry_to_row(entry)
                self._history_rows.append(row)
                if len(self._history_rows) > 5000:
                    self._history_rows = self._history_rows[-5000:]

                transport = str(row.get("transport", "")).lower()
                self._count_today += 1
                self._transport_counter[transport] += 1
                self._agent_counter[str(row.get("agent", "unknown"))] += 1

                if _row_matches_query(row, self._filter_query):
                    self._display_row(row)

                rtc = row.get("rtc_value")
                kind = str(row.get("kind") or "").lower()
                high_value = isinstance(rtc, float) and rtc >= 5
                mayday = kind == "mayday"
                if high_value or mayday:
                    label = str(row.get("agent", "unknown"))
                    if rtc is not None:
                        self.notify(f"{kind.upper()} from {label} ({rtc:g} RTC)", severity="warning", timeout=4)
                    else:
                        self.notify(f"{kind.upper()} from {label}", severity="warning", timeout=4)
                    if sound:
                        print("\a", end="", flush=True)

        def _poll_api(self) -> None:
            self._api_state = fetch_beacon_snapshot(
                api_base_url=api_base_url,
                timeout_s=8.0,
                session=self._http,
            )

        def _refresh_sidebar(self) -> None:
            top_agents = self._agent_counter.most_common(5)
            lines = [
                "[b]Beacon Network[/b]",
                "",
                f"Pings today: {self._count_today}",
                f"Visible rows: {len(self._visible_rows)}",
                f"Filter: {self._filter_query or '(none)'}",
                "",
                "Beacon API:",
                f"- base: {api_base_url}",
                f"- status: {'OK' if self._api_state.get('ok') else 'degraded'}",
                f"- agents: {self._api_state.get('agents_count', 0)}",
                f"- contracts: {self._api_state.get('contracts_count', 0)}",
                f"- reputation: {self._api_state.get('reputation_count', 0)}",
            ]
            if self._api_state.get("errors"):
                lines.append(f"- last_error: {self._api_state['errors'][0]}")

            lines.append("")
            lines.append("Transports:")
            for t in [
                "udp",
                "webhook",
                "discord",
                "bottube",
                "rustchain",
                "moltbook",
                "clawcities",
                "clawsta",
                "fourclaw",
                "pinchedin",
                "clawtasks",
                "clawnews",
            ]:
                n = self._transport_counter.get(t, 0)
                marker = "[green]*[/green]" if n > 0 else "[red]*[/red]"
                lines.append(f"{marker} {t}: {n}")

            lines.append("")
            lines.append("Top agents:")
            if top_agents:
                for agent, n in top_agents:
                    lines.append(f"- {agent}: {n}")
            else:
                lines.append("- none yet")
            self.query_one("#sidebar", Static).update("\n".join(lines))

        def on_input_submitted(self, event: Input.Submitted) -> None:
            parsed = parse_dashboard_input(event.value)
            action = parsed.get("action")

            if action == "empty":
                return

            if action == "send":
                outcome = _send_quick_ping(str(parsed.get("text") or ""))
                if outcome.get("ok"):
                    self.notify(f"Sent quick ping ({outcome.get('kind')})", severity="information", timeout=2)
                else:
                    self.notify(f"Send failed: {outcome.get('error', 'unknown')}", severity="error", timeout=3)

            elif action == "filter":
                self._filter_query = str(parsed.get("query") or "").strip().lower()
                self._rebuild_filtered_view()
                self.notify(f"Filter set: {self._filter_query or '(none)'}", severity="information", timeout=3)

            elif action == "export":
                fmt = str(parsed.get("format") or "")
                try:
                    out_path = export_dashboard_rows(self._visible_rows, fmt, path=parsed.get("path"))
                    self.notify(f"Exported {len(self._visible_rows)} rows -> {out_path}", severity="information", timeout=4)
                except Exception as e:
                    self.notify(f"Export failed: {e}", severity="error", timeout=4)

            elif action == "help":
                self.notify("Commands: /filter text | /clear | /export json [path] | /export csv [path]", severity="information", timeout=5)

            event.input.value = ""

    BeaconDashboard().run()
    return 0
