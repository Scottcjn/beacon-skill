"""Outbox — persistent outbound message queue with status tracking.

Queues actions from rules, goals, matchmaker, and manual sends.
The executor drains pending items via transports.
"""

import json
import random
import secrets
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .storage import _dir


OUTBOX_LOG = "outbox.jsonl"
OUTBOX_PENDING = "outbox_pending.json"
MAX_RETRY_ATTEMPTS = 3
BASE_RETRY_DELAY_SECONDS = 1.0
MAX_RETRY_DELAY_SECONDS = 60.0
RETRY_JITTER_SECONDS = 0.5


def _gen_action_id() -> str:
    return secrets.token_hex(6)


def _retry_delay_seconds(
    attempts: int,
    *,
    base_delay: float = BASE_RETRY_DELAY_SECONDS,
    max_delay: float = MAX_RETRY_DELAY_SECONDS,
    jitter: float = RETRY_JITTER_SECONDS,
) -> float:
    delay = base_delay * (2 ** max(attempts - 1, 0))
    if jitter > 0:
        delay += random.uniform(0, jitter)
    return min(delay, max_delay)


class OutboxManager:
    """Persistent outbound message queue."""

    def __init__(self, data_dir: Optional[Path] = None):
        self._dir = data_dir or _dir()

    def _log_path(self) -> Path:
        return self._dir / OUTBOX_LOG

    def _pending_path(self) -> Path:
        return self._dir / OUTBOX_PENDING

    def _read_pending(self) -> Dict[str, Dict[str, Any]]:
        path = self._pending_path()
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _write_pending(self, data: Dict[str, Dict[str, Any]]) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        self._pending_path().write_text(
            json.dumps(data, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def _append_log(self, item: Dict[str, Any]) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        with self._log_path().open("a", encoding="utf-8") as f:
            f.write(json.dumps(item, sort_keys=True) + "\n")

    def queue(
        self,
        action_type: str,
        target_agent_id: str,
        envelope: Dict[str, Any],
        transport_hint: str = "",
        source: str = "",
        conversation_id: str = "",
    ) -> str:
        """Add an action to the outbox. Returns action_id."""
        now = int(time.time())
        action_id = _gen_action_id()
        item = {
            "action_id": action_id,
            "action_type": action_type,
            "target_agent_id": target_agent_id,
            "envelope": envelope,
            "transport_hint": transport_hint,
            "status": "pending",
            "source": source,
            "created_at": now,
            "updated_at": now,
            "attempts": 0,
            "next_attempt_at": now,
            "error": "",
            "conversation_id": conversation_id,
        }
        self._append_log(item)
        pending = self._read_pending()
        pending[action_id] = item
        self._write_pending(pending)
        return action_id

    def pending(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Return items ready to send (pending status, attempts < MAX_RETRY)."""
        data = self._read_pending()
        results = []
        now = time.time()
        for item in data.values():
            if item.get("status") != "pending":
                continue
            if item.get("attempts", 0) >= MAX_RETRY_ATTEMPTS:
                continue
            if item.get("next_attempt_at", 0) > now:
                continue
            results.append(item)
        results.sort(key=lambda x: x.get("created_at", 0))
        return results[:limit]

    def mark_sent(self, action_id: str) -> None:
        """Mark an action as sent."""
        self._update_status(action_id, "sent")

    def mark_delivered(self, action_id: str) -> None:
        """Mark an action as delivered (got positive response)."""
        self._update_status(action_id, "delivered")

    def mark_failed(self, action_id: str, error: str = "") -> None:
        """Mark an action as permanently failed."""
        pending = self._read_pending()
        if action_id in pending:
            pending[action_id]["status"] = "failed"
            pending[action_id]["updated_at"] = int(time.time())
            if error:
                pending[action_id]["error"] = error
            self._write_pending(pending)
            self._append_log({"action_id": action_id, "status": "failed", "error": error, "ts": int(time.time())})

    def mark_retry(self, action_id: str) -> None:
        """Increment attempts counter and schedule exponential backoff."""
        pending = self._read_pending()
        if action_id in pending:
            now = time.time()
            pending[action_id]["attempts"] = pending[action_id].get("attempts", 0) + 1
            pending[action_id]["updated_at"] = int(now)
            if pending[action_id]["attempts"] >= MAX_RETRY_ATTEMPTS:
                pending[action_id]["status"] = "failed"
                pending[action_id]["error"] = "max_retries_exceeded"
            else:
                delay = _retry_delay_seconds(pending[action_id]["attempts"])
                pending[action_id]["next_attempt_at"] = now + delay
            self._write_pending(pending)

    def get(self, action_id: str) -> Optional[Dict[str, Any]]:
        """Get a specific action by ID."""
        pending = self._read_pending()
        return pending.get(action_id)

    def recent(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Read recent outbox log entries."""
        path = self._log_path()
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
        results.reverse()
        return results[:limit]

    def count_pending(self) -> int:
        """Count items with pending status and attempts < max."""
        return len(self.pending(limit=9999))

    def cleanup(self, max_age_days: int = 7) -> int:
        """Remove completed/failed items older than max_age_days from pending index."""
        cutoff = int(time.time()) - (max_age_days * 86400)
        pending = self._read_pending()
        to_remove = []
        for aid, item in pending.items():
            if item.get("status") in ("sent", "delivered", "failed"):
                if item.get("updated_at", 0) < cutoff:
                    to_remove.append(aid)
        for aid in to_remove:
            del pending[aid]
        if to_remove:
            self._write_pending(pending)
        return len(to_remove)

    def _update_status(self, action_id: str, status: str) -> None:
        pending = self._read_pending()
        if action_id in pending:
            pending[action_id]["status"] = status
            pending[action_id]["updated_at"] = int(time.time())
            self._write_pending(pending)
            self._append_log({"action_id": action_id, "status": status, "ts": int(time.time())})
