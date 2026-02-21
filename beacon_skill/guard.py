"""Envelope guard helpers: replay nonce cache + timestamp freshness checks."""

from __future__ import annotations

import time
from typing import Any, Dict, Tuple

from .storage import read_state, write_state, state_lock


DEFAULT_MAX_AGE_S = 900          # 15 minutes
DEFAULT_MAX_FUTURE_SKEW_S = 120  # 2 minutes
DEFAULT_MAX_NONCES = 50000


def _to_int(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(float(value))
    except Exception:
        return None


def _prune_nonce_cache(cache: Dict[str, int], *, now: int, max_age_s: int, max_entries: int) -> Dict[str, int]:
    floor = now - int(max_age_s)
    pruned = {k: int(v) for k, v in cache.items() if _to_int(v) is not None and int(v) >= floor}
    if len(pruned) <= max_entries:
        return pruned
    # Keep newest max_entries by timestamp.
    newest = sorted(pruned.items(), key=lambda kv: kv[1], reverse=True)[:max_entries]
    return dict(newest)


def clear_nonce_cache() -> None:
    """Reset nonce replay cache (mainly for testing)."""
    with state_lock(write=True):
        state = read_state()
        state.pop("seen_nonces", None)
        write_state(state)


def check_envelope_window(
    envelope: Dict[str, Any],
    *,
    max_age_s: int = DEFAULT_MAX_AGE_S,
    max_future_skew_s: int = DEFAULT_MAX_FUTURE_SKEW_S,
    max_nonces: int = DEFAULT_MAX_NONCES,
    now: int | None = None,
) -> Tuple[bool, str]:
    """Validate timestamp freshness + single-use nonce.

    Returns `(ok, reason)` where reason is one of:
    - `ok`
    - `missing_nonce`
    - `missing_ts`
    - `stale_ts`
    - `future_ts`
    - `replay_nonce`
    """
    nonce = str(envelope.get("nonce") or "").strip()
    if not nonce:
        return False, "missing_nonce"

    ts = _to_int(envelope.get("ts"))
    if ts is None:
        return False, "missing_ts"

    now_ts = int(now if now is not None else time.time())
    if ts < (now_ts - int(max_age_s)):
        return False, "stale_ts"
    if ts > (now_ts + int(max_future_skew_s)):
        return False, "future_ts"

    with state_lock(write=True):
        state = read_state()
        raw_cache = state.get("seen_nonces")
        cache: Dict[str, int] = raw_cache if isinstance(raw_cache, dict) else {}
        cache = _prune_nonce_cache(cache, now=now_ts, max_age_s=max_age_s, max_entries=max_nonces)

        if nonce in cache:
            return False, "replay_nonce"

        cache[nonce] = ts
        state["seen_nonces"] = cache
        write_state(state)
        
    return True, "ok"

