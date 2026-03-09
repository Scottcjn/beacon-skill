"""Executor — bridge intelligence → transport.

Queues actions from rules, goals, matchmaker into the outbox,
then drains the outbox via webhook/UDP transports.
"""

import json
import time
from typing import Any, Dict, List, Optional, Tuple

from .outbox import OutboxManager


class ActionExecutor:
    """Queue and execute outbound actions."""

    def __init__(
        self,
        outbox: OutboxManager,
        identity: Any = None,
        cfg: Optional[Dict] = None,
        trust_mgr: Any = None,
        presence_mgr: Any = None,
        match_mgr: Any = None,
        conversations: Any = None,
    ):
        self._outbox = outbox
        self._identity = identity
        self._cfg = cfg or {}
        self._trust_mgr = trust_mgr
        self._presence_mgr = presence_mgr
        self._match_mgr = match_mgr
        self._conversations = conversations

    # ── Queuing ──

    def queue_rule_action(self, action: Dict[str, Any], event: Dict[str, Any]) -> Optional[str]:
        """Queue a reply/emit action from the rules engine."""
        action_type = action.get("action", "")
        if action_type not in ("reply", "emit"):
            return None

        env = action.get("envelope") or action.get("data") or {}
        target = env.get("to", "")
        if not target and event:
            ev_env = event.get("envelope") or event
            target = ev_env.get("agent_id", "")

        # Pre-flight: don't queue for blocked or review-held agents
        ok, _reason = self._trust_gate(target)
        if target and not ok:
            return None

        transport_hint = self._guess_transport(target)
        conv_id = ""
        if self._conversations and target:
            topic = env.get("task_id", "") or "general"
            conv = self._conversations.get_or_create(target, topic)
            conv_id = conv["conversation_id"]

        return self._outbox.queue(
            action_type=action_type,
            target_agent_id=target,
            envelope=env,
            transport_hint=transport_hint,
            source=f"rule:{action.get('rule', 'unknown')}",
            conversation_id=conv_id,
        )

    def queue_contact(self, match: Dict[str, Any], my_offers: List[str] = None, my_needs: List[str] = None) -> Optional[str]:
        """Queue a contact action from the matchmaker."""
        target = match.get("agent_id", "")
        if not target:
            return None

        # Guards
        ok, _reason = self._trust_gate(target)
        if not ok:
            return None
        if self._match_mgr and not self._match_mgr.can_contact(target):
            return None
        if self._conversations and self._conversations.is_waiting_for_reply(target, "match"):
            return None

        reasons = match.get("reasons", [])
        text = f"Hello! I noticed we might be a good match: {', '.join(reasons)}"

        envelope = {
            "kind": "hello",
            "to": target,
            "text": text,
            "ts": int(time.time()),
        }
        if my_offers:
            envelope["offers"] = my_offers
        if my_needs:
            envelope["needs"] = my_needs

        transport_hint = self._guess_transport(target)
        conv_id = ""
        if self._conversations:
            conv = self._conversations.get_or_create(target, "match")
            conv_id = conv["conversation_id"]

        return self._outbox.queue(
            action_type="contact",
            target_agent_id=target,
            envelope=envelope,
            transport_hint=transport_hint,
            source="match",
            conversation_id=conv_id,
        )

    def queue_offer(self, suggestion: Dict[str, Any], identity: Any = None) -> Optional[str]:
        """Queue an offer action from a goal suggestion."""
        target = suggestion.get("agent_id", "")
        if not target:
            return None

        ok, _reason = self._trust_gate(target)
        if not ok:
            return None
        if self._conversations and self._conversations.is_waiting_for_reply(target):
            return None

        envelope = {
            "kind": "offer",
            "to": target,
            "text": suggestion.get("description", suggestion.get("action", "")),
            "goal": suggestion.get("goal_id", ""),
            "ts": int(time.time()),
        }

        transport_hint = self._guess_transport(target)
        conv_id = ""
        if self._conversations:
            topic = suggestion.get("goal_id", "") or "general"
            conv = self._conversations.get_or_create(target, topic)
            conv_id = conv["conversation_id"]

        return self._outbox.queue(
            action_type="offer",
            target_agent_id=target,
            envelope=envelope,
            transport_hint=transport_hint,
            source=f"goal:{suggestion.get('goal_id', '')}",
            conversation_id=conv_id,
        )

    def queue_emit(self, envelope: Dict[str, Any], source: str = "manual") -> str:
        """Queue a raw envelope for sending."""
        target = envelope.get("to", envelope.get("agent_id", ""))
        transport_hint = self._guess_transport(target)
        return self._outbox.queue(
            action_type="emit",
            target_agent_id=target,
            envelope=envelope,
            transport_hint=transport_hint,
            source=source,
        )

    # ── Drain cycle ──

    def drain(self, max_actions: int = 3) -> List[Dict[str, Any]]:
        """Execute pending outbox actions. Returns results for each attempted action."""
        items = self._outbox.pending(limit=max_actions)
        results = []

        for item in items:
            action_id = item["action_id"]
            ok, reason = self._can_execute(item)
            if not ok:
                self._outbox.mark_failed(action_id, error=reason)
                results.append({"action_id": action_id, "status": "skipped", "reason": reason})
                continue

            method, address = self._resolve_transport(item)
            if not method:
                self._outbox.mark_retry(action_id)
                results.append({"action_id": action_id, "status": "no_transport", "reason": "no transport available"})
                continue

            # Build signed envelope if we have identity
            envelope = item.get("envelope", {})
            if self._identity and method == "webhook":
                try:
                    from .codec import encode_envelope
                    signed_text = encode_envelope(envelope, identity=self._identity)
                    # For webhook, send the envelope dict directly (it gets JSON-encoded)
                    if self._identity:
                        envelope = dict(envelope)
                        envelope["agent_id"] = self._identity.agent_id
                except Exception:
                    pass

            # Execute transport
            send_result = self._execute_transport(method, address, envelope)

            if send_result.get("ok"):
                self._outbox.mark_sent(action_id)
                self._on_success(item)
                results.append({"action_id": action_id, "status": "sent", "method": method})
            else:
                self._outbox.mark_retry(action_id)
                results.append({"action_id": action_id, "status": "failed", "error": send_result.get("error", "unknown")})

        return results

    # ── Internal helpers ──

    def _can_execute(self, item: Dict[str, Any]) -> Tuple[bool, str]:
        """Pre-flight checks before sending."""
        target = item.get("target_agent_id", "")
        return self._trust_gate(target)

    def _trust_gate(self, target: str) -> Tuple[bool, str]:
        """Interpret trust manager review/blocked state consistently."""
        if not target or not self._trust_mgr:
            return True, ""
        if hasattr(self._trust_mgr, "can_interact"):
            ok, reason = self._trust_mgr.can_interact(target)
            return bool(ok), str(reason or "")
        if self._trust_mgr.is_blocked(target):
            return False, "blocked"
        return True, ""

    def _resolve_transport(self, item: Dict[str, Any]) -> Tuple[str, str]:
        """Determine transport method and address for an action.

        Resolution order:
        1. transport_hint (explicit)
        2. Roster card_url → webhook
        3. UDP broadcast fallback
        """
        hint = item.get("transport_hint", "")
        if hint.startswith("webhook:"):
            return "webhook", hint[len("webhook:"):]
        if hint.startswith("udp:"):
            return "udp", hint[len("udp:"):]

        # Try roster lookup
        target = item.get("target_agent_id", "")
        if target and self._presence_mgr:
            agent = self._presence_mgr.get_agent(target)
            if agent and agent.get("card_url"):
                card_url = agent["card_url"]
                # Construct inbox URL from card URL
                if card_url.endswith("/beacon.json") or card_url.endswith("/.well-known/beacon.json"):
                    base = card_url.rsplit("/", 1)[0]
                    if base.endswith("/.well-known"):
                        base = base[:-len("/.well-known")]
                    return "webhook", base + "/beacon/inbox"
                return "webhook", card_url

        # UDP broadcast fallback
        udp_cfg = self._cfg.get("udp", {})
        if udp_cfg.get("enabled"):
            host = str(udp_cfg.get("host", "255.255.255.255"))
            port = str(udp_cfg.get("port", 38400))
            return "udp", f"{host}:{port}"

        return "", ""

    def _execute_transport(self, method: str, address: str, envelope: Dict[str, Any]) -> Dict[str, Any]:
        """Send via the resolved transport. Returns {"ok": bool, ...}."""
        try:
            if method == "webhook":
                from .transports.webhook import webhook_send
                result = webhook_send(address, envelope, identity=self._identity)
                return {"ok": result.get("ok", result.get("status", 0) == 200), **result}
            elif method == "udp":
                from .transports.udp import udp_send
                parts = address.split(":")
                host = parts[0]
                port = int(parts[1]) if len(parts) > 1 else 38400
                broadcast = self._cfg.get("udp", {}).get("broadcast", False)
                text = json.dumps(envelope, sort_keys=True)
                udp_send(host, port, text.encode("utf-8"), broadcast=broadcast)
                return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}
        return {"ok": False, "error": f"unknown_method:{method}"}

    def _on_success(self, item: Dict[str, Any]) -> None:
        """Side effects after successful send."""
        target = item.get("target_agent_id", "")
        kind = item.get("envelope", {}).get("kind", item.get("action_type", ""))

        if target and self._trust_mgr:
            self._trust_mgr.record(target, "out", kind)

        if target and self._match_mgr:
            self._match_mgr.record_contact(target)

        conv_id = item.get("conversation_id", "")
        if conv_id and self._conversations:
            self._conversations.record_message(conv_id, "out", kind)

    def _guess_transport(self, target_agent_id: str) -> str:
        """Best-effort transport hint from roster."""
        if not target_agent_id or not self._presence_mgr:
            return ""
        agent = self._presence_mgr.get_agent(target_agent_id)
        if agent and agent.get("card_url"):
            return f"webhook:{agent['card_url']}"
        return ""
