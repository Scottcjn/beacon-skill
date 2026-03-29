"""
Batch heartbeat processing for large agent networks.

Beacon 2.4.0+ feature: Efficient batch heartbeat for networks
with hundreds or thousands of agents.
"""
import json
import time
from typing import Any, Dict, List, Optional

from .heartbeat import HeartbeatManager
from .storage import _dir

DEFAULT_INTERVAL_S = 300       # 5 minutes between heartbeats
DEFAULT_SILENCE_THRESHOLD_S = 900  # 15 minutes silence = concern
DEFAULT_DEAD_THRESHOLD_S = 3600   # 1 hour silence = presumed dead


class BatchHeartbeatManager:
    """Batch-optimized heartbeat processor for large agent networks.

    Reduces N API calls to 1 by grouping multiple heartbeats into
    a single batch operation. Essential for networks with 100+ agents.

    Example:
        # Before: N API calls for N agents
        for agent_id in agent_ids:
            beacon.heartbeat(agent_id=agent_id)

        # After: 1 API call for N agents
        batch = BatchHeartbeatManager()
        result = batch.process_batch([envelope1, envelope2, ...])
    """

    def __init__(self, data_dir=None, config=None):
        self._dir = data_dir or _dir()
        self._config = config or {}
        self._hb = HeartbeatManager(data_dir=data_dir, config=config)

    # ── Batch building ──────────────────────────────────────────────

    def build_batch_heartbeat(
        self,
        identity: Any,
        *,
        agents: List[Dict[str, Any]],
        status: str = "alive",
        config: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        """Build a batch heartbeat for multiple agents in one call.

        Reduces N API calls to 1 for N agents.

        Args:
            identity: AgentIdentity for signing.
            agents: List of agent dicts, each with 'agent_id', 'name',
                    optional 'status', 'health', 'seo_url', 'seo_description'.
            status: Default status if agent doesn't specify one.
            config: Optional config override.

        Returns:
            Dict with 'kind': 'batch_heartbeat' and 'agents': list of heartbeats.
        """
        cfg = config or self._config
        now = int(time.time())
        start_ts = cfg.get("_start_ts", now)

        heartbeats = []
        for agent in agents:
            agent_id = agent.get("agent_id")
            if not agent_id:
                continue

            state = self._hb._load_state()
            beat_count = state["own"].get("beat_count", 0) + 1

            payload: Dict[str, Any] = {
                "kind": "heartbeat",
                "agent_id": agent_id,
                "name": agent.get("name", cfg.get("beacon", {}).get("agent_name", "")),
                "status": agent.get("status", status),
                "beat_count": beat_count,
                "uptime_s": now - start_ts,
                "ts": now,
                "batch": True,
            }

            if agent.get("health"):
                payload["health"] = agent["health"]
            if agent.get("seo_url"):
                payload["seo_url"] = agent["seo_url"]
            if agent.get("seo_description"):
                payload["seo_description"] = agent["seo_description"]

            heartbeats.append(payload)

            # Update own state
            state["own"] = {
                "last_beat": now,
                "beat_count": beat_count,
                "status": agent.get("status", status),
            }
            self._hb._save_state(state)

        return {
            "kind": "batch_heartbeat",
            "count": len(heartbeats),
            "ts": now,
            "agents": heartbeats,
        }

    def build_batch_from_ids(
        self,
        identity: Any,
        agent_ids: List[str],
        *,
        names: Optional[List[str]] = None,
        statuses: Optional[List[str]] = None,
        config: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        """Build batch heartbeat from agent IDs list (minimal data).

        Args:
            identity: AgentIdentity for signing.
            agent_ids: List of agent IDs to include.
            names: Optional list of names (same length as agent_ids).
            statuses: Optional list of statuses (same length as agent_ids).
            config: Optional config override.

        Returns:
            Batch heartbeat dict ready to broadcast.
        """
        agents = []
        for i, agent_id in enumerate(agent_ids):
            agent = {"agent_id": agent_id}
            if names and i < len(names):
                agent["name"] = names[i]
            if statuses and i < len(statuses):
                agent["status"] = statuses[i]
            agents.append(agent)
        return self.build_batch_heartbeat(identity, agents=agents, config=config)

    # ── Batch processing ─────────────────────────────────────────────

    def process_batch(
        self, envelopes: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Process multiple heartbeat envelopes in one call.

        Reduces N API calls to 1 for N heartbeats.

        Args:
            envelopes: List of heartbeat envelope dicts.

        Returns:
            Dict with 'processed': count and 'results': list of assessments.
        """
        now = int(time.time())
        state = self._hb._load_state()
        results = []
        silent_count = 0
        healthy_count = 0

        silence_threshold = self._config.get("heartbeat", {}).get(
            "silence_threshold_s", DEFAULT_SILENCE_THRESHOLD_S
        )
        dead_threshold = self._config.get("heartbeat", {}).get(
            "dead_threshold_s", DEFAULT_DEAD_THRESHOLD_S
        )

        for envelope in envelopes:
            agent_id = envelope.get("agent_id")
            if not agent_id:
                results.append({"error": "no_agent_id"})
                continue

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

            # Quick assessment
            age = now - prev_beat if prev_beat else 0
            if prev_beat and envelope.get("status") == "shutting_down":
                assessment = "shutting_down"
            elif not prev_beat:
                assessment = "unknown"
            elif age <= silence_threshold:
                assessment = "healthy"
                healthy_count += 1
            elif age <= dead_threshold:
                assessment = "concerning"
                silent_count += 1
            else:
                assessment = "presumed_dead"
                silent_count += 1

            results.append({
                "agent_id": agent_id,
                "status": envelope.get("status", "alive"),
                "gap_s": gap_s,
                "assessment": assessment,
            })

        self._hb._save_state(state)

        return {
            "kind": "batch_heartbeat_response",
            "processed": len(envelopes),
            "healthy": healthy_count,
            "silent": silent_count,
            "ts": now,
            "results": results,
        }

    def get_network_summary(self) -> Dict[str, Any]:
        """Get a quick summary of the entire agent network health.

        Returns:
            Dict with network-wide statistics.
        """
        state = self._hb._load_state()
        now = int(time.time())
        peers = state.get("peers", {})

        silence_threshold = self._config.get("heartbeat", {}).get(
            "silence_threshold_s", DEFAULT_SILENCE_THRESHOLD_S
        )
        dead_threshold = self._config.get("heartbeat", {}).get(
            "dead_threshold_s", DEFAULT_DEAD_THRESHOLD_S
        )

        healthy = 0
        concerning = 0
        presumed_dead = 0
        unknown = 0

        for agent_id, peer in peers.items():
            age = now - peer.get("last_beat", 0)
            last_beat = peer.get("last_beat")
            if not last_beat:
                unknown += 1
            elif age <= silence_threshold:
                healthy += 1
            elif age <= dead_threshold:
                concerning += 1
            else:
                presumed_dead += 1

        return {
            "total_agents": len(peers),
            "healthy": healthy,
            "concerning": concerning,
            "presumed_dead": presumed_dead,
            "unknown": unknown,
            "ts": now,
        }
