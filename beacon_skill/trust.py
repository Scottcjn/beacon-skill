"""Trust & Reputation — track agent reliability and manage review/block status."""

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .storage import _dir


INTERACTIONS_FILE = "interactions.jsonl"
BLOCKED_FILE = "blocked.json"
RECENCY_THRESHOLD_DAYS = 30
RECENCY_WEIGHT = 0.5


class TrustManager:
    """Per-agent trust scoring based on interaction history."""

    def __init__(self, data_dir: Optional[Path] = None):
        self._dir = data_dir or _dir()
        self._blocked: Dict[str, str] = {}
        self._load_blocked()

    def _blocked_path(self) -> Path:
        return self._dir / BLOCKED_FILE

    def _interactions_path(self) -> Path:
        return self._dir / INTERACTIONS_FILE

    def _append_interaction(self, entry: Dict[str, Any]) -> None:
        """Append an interaction entry to our data_dir (not the global ~/.beacon)."""
        path = self._interactions_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, sort_keys=True) + "\n")

    def _load_blocked(self) -> None:
        path = self._blocked_path()
        if path.exists():
            try:
                self._blocked = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                self._blocked = {}

    def _save_blocked(self) -> None:
        self._blocked_path().parent.mkdir(parents=True, exist_ok=True)
        self._blocked_path().write_text(
            json.dumps(self._blocked, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def _read_interactions(self) -> List[Dict[str, Any]]:
        path = self._interactions_path()
        if not path.exists():
            return []
        results = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                results.append(json.loads(line))
            except Exception:
                continue
        return results

    # ── Recording ──

    def record(
        self,
        agent_id: str,
        direction: str,
        kind: str,
        outcome: str = "ok",
        rtc: float = 0.0,
    ) -> None:
        """Record an interaction with an agent.

        direction: 'in' or 'out'
        outcome: 'ok', 'delivered', 'paid', 'spam', 'scam', 'timeout', 'rejected'
        """
        entry = {
            "ts": int(time.time()),
            "agent_id": agent_id,
            "dir": direction,
            "kind": kind,
            "outcome": outcome,
        }
        if rtc:
            entry["rtc"] = rtc
        self._append_interaction(entry)

    # ── Scoring ──

    def score(self, agent_id: str) -> Dict[str, Any]:
        """Calculate trust score for a specific agent.

        Score formula: (positive - negative * 3) / max(total, 1), clamped [-1.0, 1.0]
        Interactions older than 30 days count at 50% weight.
        """
        interactions = self._read_interactions()
        now = time.time()
        recency_cutoff = now - (RECENCY_THRESHOLD_DAYS * 86400)

        positive = 0.0
        negative = 0.0
        total = 0
        rtc_volume = 0.0

        positive_outcomes = {"ok", "delivered", "paid"}
        negative_outcomes = {"spam", "scam", "timeout", "rejected"}

        for ix in interactions:
            if ix.get("agent_id") != agent_id:
                continue
            total += 1
            weight = 1.0 if ix.get("ts", 0) >= recency_cutoff else RECENCY_WEIGHT
            outcome = ix.get("outcome", "ok")
            if outcome in positive_outcomes:
                positive += weight
            elif outcome in negative_outcomes:
                negative += weight
            rtc_volume += abs(ix.get("rtc", 0))

        raw = (positive - negative * 3) / max(total, 1)
        clamped = max(-1.0, min(1.0, raw))

        return {
            "agent_id": agent_id,
            "score": round(clamped, 4),
            "total": total,
            "positive": positive,
            "negative": negative,
            "rtc_volume": round(rtc_volume, 6),
        }

    def scores(self, min_interactions: int = 0) -> List[Dict[str, Any]]:
        """Get trust scores for all known agents."""
        interactions = self._read_interactions()
        agent_ids = set(ix.get("agent_id", "") for ix in interactions)
        agent_ids.discard("")

        results = []
        for aid in sorted(agent_ids):
            s = self.score(aid)
            if s["total"] >= min_interactions:
                results.append(s)

        results.sort(key=lambda x: x["score"], reverse=True)
        return results

    # ── Review / block registry ──

    def _normalize_review_entry(self, value: Any) -> Optional[Dict[str, Any]]:
        if isinstance(value, dict):
            status = str(value.get("status", "blocked") or "blocked").strip() or "blocked"
            return {
                "status": status,
                "reason": str(value.get("reason", "") or status),
                "created_at": int(value.get("created_at") or time.time()),
                "reviewed_at": int(value.get("reviewed_at") or 0),
                "reviewer_note": str(value.get("reviewer_note", "") or ""),
            }
        if isinstance(value, str):
            return {
                "status": "blocked",
                "reason": value or "blocked",
                "created_at": int(time.time()),
                "reviewed_at": 0,
                "reviewer_note": "",
            }
        return None

    def _set_review_status(
        self,
        agent_id: str,
        *,
        status: str,
        reason: str = "",
        reviewer_note: str = "",
    ) -> None:
        self._blocked[agent_id] = {
            "status": status,
            "reason": reason or status,
            "created_at": int(time.time()),
            "reviewed_at": int(time.time()) if status in {"released", "dismissed"} else 0,
            "reviewer_note": reviewer_note or "",
        }
        if status in {"released", "dismissed"}:
            self._blocked.pop(agent_id, None)
        self._save_blocked()

    def hold(self, agent_id: str, reason: str = "", reviewer_note: str = "") -> None:
        """Put an agent into review hold instead of hard-blocking by default."""
        self._set_review_status(
            agent_id,
            status="needs_review",
            reason=reason or "needs review",
            reviewer_note=reviewer_note,
        )

    def release(self, agent_id: str, reviewer_note: str = "") -> None:
        """Release an agent from review/block state."""
        self._set_review_status(
            agent_id,
            status="released",
            reason="released",
            reviewer_note=reviewer_note,
        )

    def escalate(self, agent_id: str, reason: str = "", reviewer_note: str = "") -> None:
        """Escalate an agent from review into a hard block."""
        self._set_review_status(
            agent_id,
            status="blocked",
            reason=reason or "blocked",
            reviewer_note=reviewer_note,
        )

    def block(self, agent_id: str, reason: str = "") -> None:
        """Compatibility alias: explicit hard block / escalation."""
        self.escalate(agent_id, reason=reason)

    def unblock(self, agent_id: str) -> None:
        """Compatibility alias: release an agent from review/block."""
        self.release(agent_id)

    def review_status(self, agent_id: str) -> str:
        """Return the current trust action status for an agent."""
        entry = self._normalize_review_entry(self._blocked.get(agent_id))
        return entry["status"] if entry else "ok"

    def review_reason(self, agent_id: str) -> str:
        """Return the current trust action reason for an agent."""
        entry = self._normalize_review_entry(self._blocked.get(agent_id))
        return entry["reason"] if entry else ""

    def is_blocked(self, agent_id: str) -> bool:
        """Check if an agent is hard-blocked."""
        return self.review_status(agent_id) == "blocked"

    def can_interact(self, agent_id: str) -> tuple[bool, str]:
        """Return whether an agent can be contacted and why not if blocked/held."""
        status = self.review_status(agent_id)
        if status == "blocked":
            return False, "blocked"
        if status == "needs_review":
            return False, "needs_review"
        return True, ""

    def blocked_list(self) -> Dict[str, str]:
        """Return hard-blocked agents only (agent_id -> reason)."""
        blocked: Dict[str, str] = {}
        for agent_id, raw in self._blocked.items():
            entry = self._normalize_review_entry(raw)
            if entry and entry["status"] == "blocked":
                blocked[agent_id] = entry["reason"]
        return blocked

    def review_list(self) -> Dict[str, Dict[str, Any]]:
        """Return the full review/block registry with status metadata."""
        results: Dict[str, Dict[str, Any]] = {}
        for agent_id, raw in self._blocked.items():
            entry = self._normalize_review_entry(raw)
            if entry:
                results[agent_id] = entry
        return results

    def interaction_count(self, agent_id: str) -> int:
        """Count total interactions with an agent."""
        return sum(
            1 for ix in self._read_interactions()
            if ix.get("agent_id") == agent_id
        )
