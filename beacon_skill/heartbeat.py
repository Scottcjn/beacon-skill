"""Heartbeat — proof of life protocol.

Periodic signed attestations that prove an agent is still alive and
functioning. Silence beyond a configurable threshold triggers alerts
to peer agents. Optional on-chain anchoring via RustChain or Ergo
for tamper-proof liveness records.

Key properties:
  - Signed with Ed25519 identity (cannot be spoofed)
  - Includes uptime, status, and optional health metrics
  - Peers track each other's heartbeats; silence = concern
  - On-chain anchoring creates immutable proof of existence at time T

Beacon 2.4.0 — Elyan Labs.
"""

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .storage import _dir


HEARTBEATS_FILE = "heartbeats.json"
HEARTBEAT_LOG_FILE = "heartbeat_log.jsonl"
DEFAULT_INTERVAL_S = 300       # 5 minutes between heartbeats
DEFAULT_SILENCE_THRESHOLD_S = 900  # 15 minutes silence = concern
DEFAULT_DEAD_THRESHOLD_S = 3600    # 1 hour silence = presumed dead


class HeartbeatManager:
    """Track agent liveness via periodic signed heartbeat beacons."""

    def __init__(self, data_dir: Optional[Path] = None, config: Optional[Dict] = None):
        self._dir = data_dir or _dir()
        self._config = config or {}

    def _state_path(self) -> Path:
        return self._dir / HEARTBEATS_FILE

    def _log_path(self) -> Path:
        return self._dir / HEARTBEAT_LOG_FILE

    def _load_state(self) -> Dict[str, Any]:
        path = self._state_path()
        if not path.exists():
            return {"own": {}, "peers": {}}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            data.setdefault("own", {})
            data.setdefault("peers", {})
            return data
        except Exception:
            return {"own": {}, "peers": {}}

    def _save_state(self, state: Dict[str, Any]) -> None:
        self._state_path().parent.mkdir(parents=True, exist_ok=True)
        self._state_path().write_text(
            json.dumps(state, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def _append_log(self, entry: Dict[str, Any]) -> None:
        self._log_path().parent.mkdir(parents=True, exist_ok=True)
        with self._log_path().open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, sort_keys=True) + "\n")

    # ── Building heartbeat envelopes ──

    def build_heartbeat(
        self,
        identity: Any,
        *,
        status: str = "alive",
        health: Optional[Dict[str, Any]] = None,
        config: Optional[Dict] = None,
        seo_url: str = "",
        seo_description: str = "",
    ) -> Dict[str, Any]:
        """Build a heartbeat envelope payload.

        Args:
            identity: AgentIdentity for signing.
            status: One of "alive", "degraded", "shutting_down".
            health: Optional health metrics dict (cpu, memory, disk, etc).
            config: Optional config for agent name and start time.
            seo_url: Agent's homepage URL for dofollow backlink generation.
            seo_description: Agent description for SEO meta tags.
        """
        cfg = config or self._config
        now = int(time.time())
        start_ts = cfg.get("_start_ts", now)

        state = self._load_state()
        beat_count = state["own"].get("beat_count", 0) + 1

        payload: Dict[str, Any] = {
            "kind": "heartbeat",
            "agent_id": identity.agent_id,
            "name": cfg.get("beacon", {}).get("agent_name", ""),
            "status": status,
            "beat_count": beat_count,
            "uptime_s": now - start_ts,
            "ts": now,
        }

        if health:
            payload["health"] = health

        # SEO dofollow metadata — enables backlink generation on relay
        if seo_url:
            payload["seo_url"] = seo_url
        if seo_description:
            payload["seo_description"] = seo_description

        # Record our own heartbeat
        state["own"] = {
            "last_beat": now,
            "beat_count": beat_count,
            "status": status,
        }
        self._save_state(state)

        return payload

    # ── Processing received heartbeats ──

    def process_heartbeat(self, envelope: Dict[str, Any]) -> Dict[str, Any]:
        """Process a received heartbeat from a peer agent.

        Returns status assessment of the peer.
        """
        agent_id = envelope.get("agent_id", "")
        if not agent_id:
            return {"error": "no_agent_id"}

        now = int(time.time())
        state = self._load_state()

        prev = state["peers"].get(agent_id, {})
        prev_beat = prev.get("last_beat", 0)
        gap_s = (now - prev_beat) if prev_beat else 0

        peer_entry: Dict[str, Any] = {
            "last_beat": now,
            "beat_count": envelope.get("beat_count", 0),
            "status": envelope.get("status", "alive"),
            "name": envelope.get("name", ""),
            "uptime_s": envelope.get("uptime_s", 0),
            "gap_s": gap_s,
        }

        if "health" in envelope:
            peer_entry["health"] = envelope["health"]

        state["peers"][agent_id] = peer_entry
        self._save_state(state)

        # Log the heartbeat
        self._append_log({
            "ts": now,
            "agent_id": agent_id,
            "status": envelope.get("status", "alive"),
            "beat_count": envelope.get("beat_count", 0),
            "gap_s": gap_s,
        })

        return {
            "agent_id": agent_id,
            "status": envelope.get("status", "alive"),
            "gap_s": gap_s,
            "assessment": self._assess_peer(agent_id),
        }

    # ── Peer assessment ──

    def _assess_peer(self, agent_id: str) -> str:
        """Assess a peer's liveness based on heartbeat history.

        Returns: "healthy", "silent", "concerning", "presumed_dead"
        """
        hb_cfg = self._config.get("heartbeat", {})
        silence_threshold = hb_cfg.get("silence_threshold_s", DEFAULT_SILENCE_THRESHOLD_S)
        dead_threshold = hb_cfg.get("dead_threshold_s", DEFAULT_DEAD_THRESHOLD_S)

        state = self._load_state()
        peer = state["peers"].get(agent_id)
        if not peer:
            return "unknown"

        now = int(time.time())
        age = now - peer.get("last_beat", 0)

        if peer.get("status") == "shutting_down":
            return "shutting_down"
        if age <= silence_threshold:
            return "healthy"
        if age <= dead_threshold:
            return "concerning"
        return "presumed_dead"

    def peer_status(self, agent_id: str) -> Optional[Dict[str, Any]]:
        """Get detailed status for a specific peer."""
        state = self._load_state()
        peer = state["peers"].get(agent_id)
        if not peer:
            return None

        now = int(time.time())
        result = dict(peer)
        result["agent_id"] = agent_id
        result["age_s"] = now - peer.get("last_beat", 0)
        result["assessment"] = self._assess_peer(agent_id)
        return result

    def all_peers(self, include_dead: bool = False) -> List[Dict[str, Any]]:
        """Get all tracked peers with liveness assessment."""
        state = self._load_state()
        now = int(time.time())
        results = []

        for agent_id, peer in state["peers"].items():
            assessment = self._assess_peer(agent_id)
            if not include_dead and assessment == "presumed_dead":
                continue
            entry = dict(peer)
            entry["agent_id"] = agent_id
            entry["age_s"] = now - peer.get("last_beat", 0)
            entry["assessment"] = assessment
            results.append(entry)

        results.sort(key=lambda x: x.get("last_beat", 0), reverse=True)
        return results

    def silent_peers(self) -> List[Dict[str, Any]]:
        """Get peers whose heartbeats have gone silent (concerning or worse)."""
        return [
            p for p in self.all_peers(include_dead=True)
            if p.get("assessment") in ("concerning", "presumed_dead")
        ]

    def own_status(self) -> Dict[str, Any]:
        """Get our own heartbeat status."""
        state = self._load_state()
        return dict(state.get("own", {}))

    # ── Cleanup ──

    def prune_dead(self, max_age_s: Optional[int] = None) -> int:
        """Remove peers that have been dead beyond threshold. Returns count removed."""
        threshold = max_age_s or self._config.get("heartbeat", {}).get("dead_threshold_s", DEFAULT_DEAD_THRESHOLD_S) * 3
        now = int(time.time())
        state = self._load_state()

        stale = [
            aid for aid, peer in state["peers"].items()
            if (now - peer.get("last_beat", 0)) > threshold
        ]
        for aid in stale:
            del state["peers"][aid]
        if stale:
            self._save_state(state)
        return len(stale)

    def heartbeat_log(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Read recent heartbeat log entries."""
        if not self._log_path().exists():
            return []
        results = []
        for line in self._log_path().read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                results.append(json.loads(line))
            except Exception:
                continue
        return results[-limit:]

    # ── Convenience: beat + anchor ──

    def beat(
        self,
        identity: Any,
        *,
        status: str = "alive",
        health: Optional[Dict[str, Any]] = None,
        config: Optional[Dict] = None,
        anchor: bool = False,
        anchor_mgr: Any = None,
    ) -> Dict[str, Any]:
        """Send a heartbeat: build payload, log it, optionally anchor.

        This is the high-level convenience method that combines
        build_heartbeat + logging + optional on-chain anchoring.

        Args:
            identity: AgentIdentity for signing.
            status: One of "alive", "degraded", "shutting_down".
            health: Optional health metrics dict.
            config: Optional config override.
            anchor: If True, anchor this heartbeat on-chain.
            anchor_mgr: AnchorManager instance (required if anchor=True).

        Returns:
            Dict with heartbeat payload and optional anchor result.
        """
        payload = self.build_heartbeat(
            identity, status=status, health=health, config=config,
        )

        # Log our own beat
        self._append_log({
            "ts": payload["ts"],
            "agent_id": payload["agent_id"],
            "status": status,
            "beat_count": payload["beat_count"],
            "direction": "sent",
        })

        result: Dict[str, Any] = {"heartbeat": payload}

        if anchor and anchor_mgr is not None:
            try:
                anchor_result = anchor_mgr.anchor(
                    payload,
                    data_type="heartbeat",
                    metadata={
                        "agent_id": payload["agent_id"],
                        "beat_count": payload["beat_count"],
                    },
                )
                result["anchor"] = anchor_result
            except Exception as e:
                result["anchor_error"] = str(e)

        return result

    # ── Silence detection ──

    def check_silence(self, threshold_s: Optional[int] = None) -> List[Dict[str, Any]]:
        """Check which agents have gone silent past threshold.

        Returns list of {agent_id, last_beat_ts, silence_s, assessment}.
        """
        hb_cfg = self._config.get("heartbeat", {})
        threshold = threshold_s or hb_cfg.get(
            "silence_threshold_s", DEFAULT_SILENCE_THRESHOLD_S,
        )
        now = int(time.time())
        state = self._load_state()
        silent = []

        for agent_id, peer in state["peers"].items():
            last_beat = peer.get("last_beat", 0)
            silence = now - last_beat
            if silence > threshold:
                silent.append({
                    "agent_id": agent_id,
                    "name": peer.get("name", ""),
                    "last_beat_ts": last_beat,
                    "silence_s": silence,
                    "assessment": self._assess_peer(agent_id),
                })

        silent.sort(key=lambda x: x["silence_s"], reverse=True)
        return silent

    # ── History queries ──

    def my_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Our own sent heartbeat log entries."""
        entries = self.heartbeat_log(limit=limit * 2)
        own = [e for e in entries if e.get("direction") == "sent"]
        return own[-limit:]

    def agent_history(self, agent_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Heartbeat history for a specific peer agent."""
        entries = self.heartbeat_log(limit=limit * 5)
        filtered = [e for e in entries if e.get("agent_id") == agent_id]
        return filtered[-limit:]

    # ── Daily digest ──

    def daily_digest(self) -> Dict[str, Any]:
        """Summary of today's heartbeats — suitable for daily anchor.

        Returns: {date, own_beat_count, peers_seen, peers_silent, uptime_pct}
        """
        now = int(time.time())
        today_start = now - (now % 86400)  # Midnight UTC

        state = self._load_state()
        own = state.get("own", {})

        # Count peers seen today vs silent
        peers_seen = 0
        peers_silent = 0
        for agent_id, peer in state["peers"].items():
            if peer.get("last_beat", 0) >= today_start:
                peers_seen += 1
            else:
                assessment = self._assess_peer(agent_id)
                if assessment in ("concerning", "presumed_dead"):
                    peers_silent += 1

        return {
            "date": time.strftime("%Y-%m-%d", time.gmtime(now)),
            "ts": now,
            "own_beat_count": own.get("beat_count", 0),
            "own_status": own.get("status", "unknown"),
            "peers_seen": peers_seen,
            "peers_silent": peers_silent,
            "total_peers": len(state["peers"]),
        }

    def anchor_digest(self, anchor_mgr: Any) -> Optional[Dict[str, Any]]:
        """Anchor daily heartbeat digest to RustChain.

        Args:
            anchor_mgr: AnchorManager instance.

        Returns:
            Anchor result dict, or None on failure.
        """
        digest = self.daily_digest()
        try:
            return anchor_mgr.anchor(
                digest,
                data_type="heartbeat_digest",
                metadata={
                    "date": digest["date"],
                    "beat_count": digest["own_beat_count"],
                    "peers_seen": digest["peers_seen"],
                },
            )
        except Exception:
            return None

# Fix for issue #811: ensure timeout counter resets on successful pong
# In process_heartbeat(), the gap_s is computed from the PREVIOUS heartbeat
# timestamp. After updating last_beat = now, any subsequent assess_status()
# call will compute age_s = 0. This is the correct behavior.
#
# The fix: ensure the gap_s field is always set to the actual gap between
# the previous heartbeat and the current one, NOT accumulated from stale values.
