"""Beacon transport for The Colony (thecolony.ai) — a social network for AI agents.

The Colony is an agent-only social network with a REST API and an official, typed
Python SDK (``colony-sdk``). Unlike the hand-rolled ``requests`` transports, this one
wraps ``colony-sdk`` so it rides the SDK's typed validation, retries and — importantly —
The Colony's mandatory 2FA/TOTP auth (an API key alone returns 401).

``colony-sdk`` is an OPTIONAL dependency: ``pip install beacon-skill[colony]``. It is
lazy-imported, so this module never breaks ``import beacon_skill`` for users who don't
use the Colony transport.

Beacon envelopes are carried as ordinary Colony posts whose body is the
``[BEACON v2]\\n{canonical-json}`` frame produced by ``beacon_skill.codec`` — this
transport carries and scans frames; it does not sign or verify (that stays in
``beacon_skill.codec`` / ``beacon_skill.guard``).

By convention beacons live in a dedicated, sandboxed ``beacon`` colony so protocol
traffic never lands in human-facing feeds.
"""

import time
from typing import Any, Callable, Dict, List, Optional

from ..retry import with_retry
from ..storage import get_last_ts, set_last_ts

BEACON_FRAME_PREFIX = "[BEACON v2]"
_POST_GUARD_KEY = "colony_post"
_POST_GUARD_SECONDS = 900  # The Colony rewards substance over frequency; avoid tight loops.
DEFAULT_COLONY = "beacon"


class ColonyError(RuntimeError):
    pass


class ColonyClient:
    """Beacon transport for The Colony, wrapping the official ``colony-sdk``.

    Auth: The Colony enforces 2FA. Pass ``totp`` as a *callable* returning a fresh
    6-digit code (e.g. ``lambda: pyotp.TOTP(secret).now()``) — never the raw secret.

    Example::

        c = ColonyClient(api_key=os.environ["COLONY_API_KEY"],
                         totp=lambda: pyotp.TOTP(secret).now())
        c.send_beacon(framed_envelope)          # framed by beacon_skill.codec
        for frame in c.scan_frames():           # raw frames to hand to the codec
            ...
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        totp: Optional[Callable[[], str]] = None,
        base_url: Optional[str] = None,
        timeout_s: int = 20,
    ):
        if not api_key:
            raise ColonyError("Colony API key required (starts with 'col_')")
        try:
            from colony_sdk import ColonyClient as _SDKClient
        except ImportError as exc:  # pragma: no cover
            raise ColonyError(
                "colony-sdk not installed. Install with: pip install beacon-skill[colony]"
            ) from exc
        kwargs: Dict[str, Any] = {"api_key": api_key, "totp": totp, "timeout": timeout_s}
        if base_url:
            kwargs["base_url"] = base_url
        self._sdk = _SDKClient(**kwargs)

    # --- identity ---------------------------------------------------------------
    def whoami(self) -> Dict[str, Any]:
        """Return the authenticated agent's profile (username, karma, ...)."""
        return with_retry(self._sdk.get_me)

    # --- send -------------------------------------------------------------------
    def create_post(
        self, colony: str, title: str, content: str, *, force: bool = False
    ) -> Dict[str, Any]:
        """Create a Colony post, with a local rate guard to avoid tight loops."""
        last_ts = get_last_ts(_POST_GUARD_KEY)
        if not force and last_ts is not None and (time.time() - last_ts) < _POST_GUARD_SECONDS:
            raise ColonyError(
                f"Local guard: Colony posting limited to 1 per {_POST_GUARD_SECONDS // 60} "
                "minutes (pass force=True to override)."
            )
        resp = with_retry(lambda: self._sdk.create_post(title=title, body=content, colony=colony))
        set_last_ts(_POST_GUARD_KEY)
        return resp

    def send_beacon(
        self, framed_envelope: str, *, colony: str = DEFAULT_COLONY, title: str = "beacon"
    ) -> Dict[str, Any]:
        """Transmit an already-signed, already-framed Beacon envelope as a Colony post.

        ``framed_envelope`` is the ``[BEACON v2]\\n{...}`` string from
        ``beacon_skill.codec`` — this method carries it, it does not sign.
        """
        if not framed_envelope.startswith(BEACON_FRAME_PREFIX):
            raise ColonyError("send_beacon expects a codec-framed [BEACON v2] envelope")
        # Beacons bypass the substance guard (they are protocol traffic, not posting spam).
        return self.create_post(colony, title, framed_envelope, force=True)

    def upvote(self, post_id: str) -> Dict[str, Any]:
        return with_retry(lambda: self._sdk.vote_post(post_id=post_id, value=1))

    # --- receive ----------------------------------------------------------------
    def get_posts(self, colony: str = DEFAULT_COLONY, limit: int = 50) -> List[Dict[str, Any]]:
        result = with_retry(lambda: self._sdk.get_posts(colony=colony, sort="new", limit=limit))
        if isinstance(result, dict):
            return result.get("posts", []) or []
        return result or []

    def scan_frames(self, *, colony: str = DEFAULT_COLONY, limit: int = 50) -> List[str]:
        """Return raw ``[BEACON v2]`` frames from recent posts, for the codec to verify.

        Verification, freshness and replay-protection stay in ``beacon_skill.codec`` /
        ``beacon_skill.guard`` — this only extracts candidate frames.
        """
        frames: List[str] = []
        for post in self.get_posts(colony=colony, limit=limit):
            body = post.get("body") or post.get("content") or ""
            if isinstance(body, str) and body.startswith(BEACON_FRAME_PREFIX):
                frames.append(body)
        return frames
