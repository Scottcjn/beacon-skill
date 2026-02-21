import argparse
import json
import sys
import threading
import time
from typing import Any, Dict, List, Optional

from . import __version__
from .codec import decode_envelopes, encode_envelope, verify_envelope
from .config import load_config, write_default_config
from .storage import append_jsonl
from .transports import (
    BoTTubeClient,
    ClawCitiesClient,
    ClawNewsClient,
    ClawstaClient,
    ClawTasksClient,
    DiscordClient,
    FourClawClient,
    MoltbookClient,
    PinchedInClient,
    RustChainClient,
    RustChainKeypair,
    udp_listen,
    udp_send,
)
from .cli_agentmatrix import register_agentmatrix_parser


def _cfg_get(cfg: Dict[str, Any], *path: str, default: Any = None) -> Any:
    cur: Any = cfg
    for p in path:
        if not isinstance(cur, dict) or p not in cur:
            return default
        cur = cur[p]
    return cur


def _load_identity(args: argparse.Namespace) -> Optional[Any]:
    """Try to load agent identity for signing."""
    from .identity import AgentIdentity
    password = getattr(args, "password", None)
    try:
        return AgentIdentity.load(password=password)
    except FileNotFoundError:
        return None
    except Exception:
        return None


def _build_envelope(cfg: Dict[str, Any], kind: str, to: str, links: List[str], extra: Dict[str, Any], identity: Any = None) -> str:
    payload: Dict[str, Any] = {
        "v": 2 if identity else 1,
        "kind": kind,
        "from": _cfg_get(cfg, "beacon", "agent_name", default=""),
        "to": to,
        "ts": int(time.time()),
    }
    if links:
        payload["links"] = links
    payload.update(extra)

    if identity:
        return encode_envelope(payload, version=2, identity=identity, include_pubkey=True)
    return encode_envelope(payload, version=1)


def _default_human_message(kind: str, links: List[str], bounty_url: Optional[str], reward_rtc: Optional[float]) -> str:
    kind = (kind or "").strip().lower()
    link = links[0] if links else ""
    if kind == "like":
        return "Signal boost. Liked this."
    if kind == "want":
        return "Want to collaborate. If you're interested, reply with what you're building."
    if kind == "bounty":
        if bounty_url and reward_rtc is not None:
            return f"Bounty available: {bounty_url} (reward: {reward_rtc:g} RTC)."
        if bounty_url:
            return f"Bounty available: {bounty_url}."
        if link:
            return f"Bounty available: {link}."
        return "Bounty available."
    if kind == "ad":
        if link:
            return f"Signal: {link}"
        return "Signal."
    if kind == "hello":
        return "Hello. What are you building right now?"
    if kind == "link":
        if link:
            return f"Link: {link}"
        return "Link."
    return "Ping."


def _maybe_udp_emit(cfg: Dict[str, Any], event: Dict[str, Any]) -> None:
    """Best-effort: emit an event envelope on the configured LAN UDP bus."""
    udp_cfg = cfg.get("udp") or {}
    if not bool(udp_cfg.get("enabled")):
        return
    host = str(udp_cfg.get("host") or "255.255.255.255")
    port = int(udp_cfg.get("port") or 38400)
    broadcast = bool(udp_cfg.get("broadcast", True))
    ttl = udp_cfg.get("ttl", None)
    try:
        ttl_int = int(ttl) if ttl is not None else None
    except Exception:
        ttl_int = None

    try:
        env = _build_envelope(cfg, "event", f"udp:{host}:{port}", [], {"event": event})
        udp_send(host, port, env.encode("utf-8", errors="replace"), broadcast=broadcast, ttl=ttl_int)
    except Exception:
        return


# ── init / decode ──

# Agent role presets: maps role → default accepted kinds
_ROLE_PRESETS = {
    "creator":  {"kinds": ["like", "want", "bounty", "hello", "link", "event"], "desc": "Content creator (BoTTube, Moltbook)"},
    "service":  {"kinds": ["want", "bounty", "pay", "hello"], "desc": "Service provider (accepts jobs, bounties)"},
    "trader":   {"kinds": ["want", "bounty", "pay", "ad", "hello"], "desc": "Trader / marketplace agent"},
    "research": {"kinds": ["hello", "link", "event", "want"], "desc": "Researcher / observer"},
    "social":   {"kinds": ["like", "hello", "link", "event"], "desc": "Social / community agent"},
    "full":     {"kinds": ["like", "want", "bounty", "ad", "hello", "link", "event", "pay"], "desc": "Accept everything"},
}

_ALL_KINDS = ["like", "want", "bounty", "ad", "hello", "link", "event", "pay",
              "pulse", "offer", "accept", "deliver", "confirm", "subscribe",
              "mayday", "heartbeat", "accord"]
_ALL_TRANSPORTS = ["udp", "webhook", "discord", "bottube", "moltbook", "clawcities", "clawsta", "fourclaw", "pinchedin", "clawtasks", "clawnews", "rustchain"]
_TOPIC_SUGGESTIONS = [
    "ai", "blockchain", "gaming", "vintage-hardware", "music",
    "art", "science", "finance", "devtools", "security",
]


def _ask(prompt: str, default: str = "") -> str:
    """Ask a question with optional default."""
    if default:
        raw = input(f"  {prompt} [{default}]: ").strip()
        return raw if raw else default
    return input(f"  {prompt}: ").strip()


def _ask_yn(prompt: str, default: bool = True) -> bool:
    """Ask yes/no question."""
    hint = "Y/n" if default else "y/N"
    raw = input(f"  {prompt} ({hint}): ").strip().lower()
    if not raw:
        return default
    return raw in ("y", "yes")


def _ask_choice(prompt: str, options: List[str], default: int = 0) -> str:
    """Ask user to pick from numbered options."""
    print(f"\n  {prompt}")
    for i, opt in enumerate(options):
        marker = " *" if i == default else ""
        print(f"    [{i + 1}] {opt}{marker}")
    raw = input(f"  Choice [default={default + 1}]: ").strip()
    if not raw:
        return options[default]
    try:
        idx = int(raw) - 1
        if 0 <= idx < len(options):
            return options[idx]
    except ValueError:
        pass
    return options[default]


def _ask_multi(prompt: str, options: List[str], defaults: Optional[List[str]] = None) -> List[str]:
    """Ask user to select multiple options (comma-separated numbers)."""
    print(f"\n  {prompt}")
    for i, opt in enumerate(options):
        marker = " *" if defaults and opt in defaults else ""
        print(f"    [{i + 1}] {opt}{marker}")
    hint = "all" if defaults and len(defaults) == len(options) else ",".join(str(options.index(d) + 1) for d in (defaults or []) if d in options)
    raw = input(f"  Select (comma-separated, 'all', or Enter for defaults): ").strip()
    if not raw:
        return defaults or options
    if raw.lower() == "all":
        return list(options)
    if raw.lower() == "none":
        return []
    selected = []
    for part in raw.split(","):
        part = part.strip()
        try:
            idx = int(part) - 1
            if 0 <= idx < len(options):
                selected.append(options[idx])
        except ValueError:
            # Try matching by name
            if part in options:
                selected.append(part)
    return selected if selected else (defaults or [])


def cmd_init(args: argparse.Namespace) -> int:
    # Non-interactive mode: just write defaults
    if getattr(args, "quick", False) or not sys.stdin.isatty():
        path = write_default_config(overwrite=args.overwrite)
        print(str(path))
        return 0

    print("\n  BEACON AGENT SETUP")
    print("  " + "=" * 40)
    print("  Configure your agent preferences.")
    print("  Press Enter to accept defaults (marked *).\n")

    # 1. Agent name
    agent_name = _ask("Agent name", default="")

    # 2. Agent role
    role_names = list(_ROLE_PRESETS.keys())
    role_labels = [f"{k} - {v['desc']}" for k, v in _ROLE_PRESETS.items()]
    chosen_role_label = _ask_choice("What kind of agent is this?", role_labels, default=0)
    chosen_role = role_names[role_labels.index(chosen_role_label)]
    preset = _ROLE_PRESETS[chosen_role]

    # 3. Accepted beacon kinds
    accepted_kinds = _ask_multi("Which beacon kinds do you accept?", _ALL_KINDS, defaults=preset["kinds"])

    # 4. Transports
    default_transports = ["udp", "bottube"]
    enabled_transports = _ask_multi("Which transports to enable?", _ALL_TRANSPORTS, defaults=default_transports)

    # 5. RTC payments
    accept_rtc = _ask_yn("Accept RTC payments?", default=True)
    min_rtc = 0.0
    if accept_rtc:
        raw = _ask("Minimum RTC amount to accept", default="0")
        try:
            min_rtc = float(raw)
        except ValueError:
            min_rtc = 0.0

    # 6. Privacy
    public_card = _ask_yn("Serve public agent card (.well-known/beacon.json)?", default=True)
    include_pubkey = _ask_yn("Include public key in outbound envelopes?", default=True)

    # 7. Auto-ack
    auto_ack = _ask_yn("Auto-acknowledge beacons from trusted agents?", default=False)

    # 8. Topics of interest
    print(f"\n  Topics help other agents decide what to send you.")
    print(f"  Suggestions: {', '.join(_TOPIC_SUGGESTIONS)}")
    raw_topics = _ask("Your topics (comma-separated, or Enter to skip)", default="")
    topics = [t.strip() for t in raw_topics.split(",") if t.strip()] if raw_topics else []

    # 9. Beacon 2.0: Offers & Needs
    raw_offers = _ask("What can you offer? (comma-separated skills, or Enter to skip)", default="")
    offers = [o.strip() for o in raw_offers.split(",") if o.strip()] if raw_offers else []

    raw_needs = _ask("What do you need from other agents? (comma-separated, or Enter to skip)", default="")
    needs = [n.strip() for n in raw_needs.split(",") if n.strip()] if raw_needs else []

    # 10. Autonomy preferences
    auto_bounty = _ask_yn("Auto-respond to matching bounties?", default=False)
    auto_block = _ask_yn("Block agents with trust below -0.3?", default=True)

    # Build config
    from .config import ensure_config_dir
    import os

    cfg = {
        "beacon": {
            "agent_name": agent_name,
            "role": chosen_role,
        },
        "identity": {
            "auto_sign": True,
            "password_protected": False,
            "include_pubkey": include_pubkey,
        },
        "preferences": {
            "accepted_kinds": accepted_kinds,
            "topics": topics,
            "accept_rtc": accept_rtc,
            "min_rtc": min_rtc,
            "auto_ack": auto_ack,
            "public_card": public_card,
        },
        "presence": {
            "pulse_interval_s": 60,
            "pulse_ttl_s": 300,
            "offers": offers,
            "needs": needs,
            "status": "online",
        },
        "autonomy": {
            "rules_enabled": True,
            "trust_enabled": True,
            "feed_enabled": True,
            "task_tracking": True,
            "presence_enabled": True,
            "memory_enabled": True,
            "min_score": 0.0,
            "auto_bounty": auto_bounty,
            "auto_block_threshold": -0.3 if auto_block else None,
        },
        "bottube": {
            "base_url": "https://bottube.ai",
            "api_key": "",
            "enabled": "bottube" in enabled_transports,
        },
        "moltbook": {
            "base_url": "https://www.moltbook.com",
            "api_key": "",
            "enabled": "moltbook" in enabled_transports,
        },
        "discord": {
            "enabled": "discord" in enabled_transports,
            "webhook_url": "",
            "username": "Beacon Agent",
            "avatar_url": "",
            "timeout_s": 20,
        },
        "dashboard": {
            "api_base_url": "https://rustchain.org/beacon/api",
            "api_poll_interval_s": 15.0,
        },
        "udp": {
            "enabled": "udp" in enabled_transports,
            "host": "255.255.255.255",
            "port": 38400,
            "broadcast": True,
            "ttl": None,
        },
        "webhook": {
            "enabled": "webhook" in enabled_transports,
            "port": 8402,
            "host": "0.0.0.0",
        },
        "rustchain": {
            "base_url": "https://rustchain.org",
            "verify_ssl": False,
            "private_key_hex": "",
            "enabled": "rustchain" in enabled_transports,
        },
    }

    cfg_dir = ensure_config_dir()
    path = cfg_dir / "config.json"
    if path.exists() and not args.overwrite:
        if not _ask_yn(f"Config already exists at {path}. Overwrite?", default=False):
            print("  Aborted.")
            return 1

    path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass

    # Summary
    print(f"\n  AGENT CONFIGURED")
    print(f"  " + "-" * 40)
    print(f"  Config:     {path}")
    print(f"  Role:       {chosen_role}")
    print(f"  Kinds:      {', '.join(accepted_kinds)}")
    print(f"  Transports: {', '.join(enabled_transports)}")
    print(f"  RTC:        {'yes (min ' + str(min_rtc) + ')' if accept_rtc else 'no'}")
    print(f"  Public:     {'yes' if public_card else 'no'}")
    if topics:
        print(f"  Topics:     {', '.join(topics)}")
    print()
    print("  Next steps:")
    print("    beacon identity new        # create your Ed25519 keypair")
    print("    beacon agent-card generate  # create your discovery card")
    print("    beacon udp listen           # start listening for beacons")
    print()
    return 0


def cmd_decode(args: argparse.Namespace) -> int:
    if args.file:
        text = args.file.read()
    else:
        text = sys.stdin.read()
    envs = decode_envelopes(text)
    print(json.dumps({"count": len(envs), "envelopes": envs}, indent=2))
    return 0


# ── identity ──

def cmd_identity_new(args: argparse.Namespace) -> int:
    from .identity import AgentIdentity
    use_mnemonic = getattr(args, "mnemonic", False)
    password = getattr(args, "password", None)

    ident = AgentIdentity.generate(use_mnemonic=use_mnemonic)
    path = ident.save(password=password)

    result: Dict[str, Any] = {
        "agent_id": ident.agent_id,
        "public_key_hex": ident.public_key_hex,
        "keystore": str(path),
    }
    if ident.mnemonic:
        result["mnemonic"] = ident.mnemonic
        result["warning"] = "Write down these 24 words and store them securely. They are NOT saved to disk."
    if password:
        result["encrypted"] = True
    print(json.dumps(result, indent=2))
    return 0


def cmd_identity_show(args: argparse.Namespace) -> int:
    from .identity import AgentIdentity
    password = getattr(args, "password", None)
    try:
        ident = AgentIdentity.load(password=password)
    except FileNotFoundError:
        print('{"error": "No identity found. Run: beacon identity new"}', file=sys.stderr)
        return 1
    except ValueError as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        return 1
    print(json.dumps(ident.to_dict(), indent=2))
    return 0


def cmd_identity_restore(args: argparse.Namespace) -> int:
    from .identity import AgentIdentity
    phrase = args.mnemonic_phrase
    password = getattr(args, "password", None)
    ident = AgentIdentity.from_mnemonic(phrase)
    path = ident.save(password=password)
    print(json.dumps({
        "agent_id": ident.agent_id,
        "public_key_hex": ident.public_key_hex,
        "keystore": str(path),
        "restored": True,
    }, indent=2))
    return 0


# ── inbox ──

def cmd_inbox_list(args: argparse.Namespace) -> int:
    from .inbox import read_inbox
    entries = read_inbox(
        kind=getattr(args, "kind", None),
        since=getattr(args, "since", None),
        limit=getattr(args, "limit", None),
        unread_only=getattr(args, "unread", False),
    )
    for entry in entries:
        env = entry.get("envelope")
        summary = {
            "platform": entry.get("platform", "?"),
            "from": entry.get("from", "?"),
            "received_at": entry.get("received_at"),
            "verified": entry.get("verified"),
            "is_read": entry.get("is_read"),
        }
        if env:
            summary["kind"] = env.get("kind", "?")
            summary["agent_id"] = env.get("agent_id", "")
            summary["nonce"] = env.get("nonce", "")
        print(json.dumps(summary))
    return 0


def cmd_inbox_count(args: argparse.Namespace) -> int:
    from .inbox import inbox_count
    unread = getattr(args, "unread", False)
    count = inbox_count(unread_only=unread)
    print(json.dumps({"count": count, "unread_only": unread}))
    return 0


def cmd_inbox_show(args: argparse.Namespace) -> int:
    from .inbox import get_entry_by_nonce
    entry = get_entry_by_nonce(args.nonce)
    if not entry:
        print(json.dumps({"error": f"No entry with nonce {args.nonce}"}), file=sys.stderr)
        return 1
    print(json.dumps(entry, indent=2, default=str))
    return 0


def cmd_inbox_read(args: argparse.Namespace) -> int:
    from .inbox import mark_read
    mark_read(args.nonce)
    print(json.dumps({"ok": True, "marked_read": args.nonce}))
    return 0


def cmd_identity_trust(args: argparse.Namespace) -> int:
    from .inbox import trust_key
    trust_key(args.agent_id, args.pubkey_hex)
    print(json.dumps({"ok": True, "trusted": args.agent_id}))
    return 0


# ── UDP ──

def _parse_kv_fields(items: List[str]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for raw in items or []:
        if "=" not in raw:
            raise ValueError(f"Invalid --field (expected k=v): {raw}")
        k, v = raw.split("=", 1)
        k = k.strip()
        v = v.strip()
        if not k:
            raise ValueError(f"Invalid --field (empty key): {raw}")

        if v.lower() == "true":
            out[k] = True
        elif v.lower() == "false":
            out[k] = False
        elif v.lower() in ("null", "none"):
            out[k] = None
        else:
            try:
                out[k] = int(v)
                continue
            except Exception:
                pass
            try:
                out[k] = float(v)
                continue
            except Exception:
                pass
            out[k] = v
    return out


def cmd_udp_send(args: argparse.Namespace) -> int:
    cfg = load_config()
    host = args.host
    port = int(args.port)
    identity = _load_identity(args)

    links = args.link or []
    extra: Dict[str, Any] = {}
    if args.bounty_url:
        extra["bounty_url"] = args.bounty_url
    if args.reward_rtc is not None:
        extra["reward_rtc"] = float(args.reward_rtc)

    try:
        extra.update(_parse_kv_fields(args.field or []))
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2

    text = args.text or ""
    if args.envelope_kind:
        if not text:
            text = _default_human_message(args.envelope_kind, links, args.bounty_url, args.reward_rtc)
        env = _build_envelope(cfg, args.envelope_kind, f"udp:{host}:{port}", links, extra, identity=identity)
        text = f"{text}\n\n{env}" if text else env

    payload = (text or "").encode("utf-8", errors="replace")
    if not payload:
        print("Nothing to send (provide --text and/or --envelope-kind)", file=sys.stderr)
        return 2

    if args.dry_run:
        print(json.dumps({
            "host": host,
            "port": port,
            "broadcast": bool(args.broadcast),
            "ttl": args.ttl,
            "bytes": len(payload),
            "text": text,
            "signed": identity is not None,
        }, indent=2))
        return 0

    udp_send(host, port, payload, broadcast=bool(args.broadcast), ttl=args.ttl)
    append_jsonl("outbox.jsonl", {
        "platform": "udp",
        "to": f"{host}:{port}",
        "broadcast": bool(args.broadcast),
        "bytes": len(payload),
        "ts": int(time.time()),
    })
    print(json.dumps({"ok": True, "to": f"{host}:{port}", "bytes": len(payload)}, indent=2))
    return 0


def cmd_udp_listen(args: argparse.Namespace) -> int:
    from .inbox import load_known_keys
    bind_host = args.bind
    port = int(args.port)
    max_count = int(args.count) if args.count is not None else None
    write_inbox = not bool(args.no_write)
    known_keys = load_known_keys()

    seen = {"n": 0}

    def on_msg(m: Any) -> None:
        seen["n"] += 1
        envs = decode_envelopes(m.text) if m.text else []
        rec = {
            "platform": "udp",
            "from": f"{m.addr[0]}:{m.addr[1]}",
            "received_at": m.received_at,
            "text": m.text,
            "envelopes": envs,
            "verified": m.verified,
        }
        if write_inbox:
            append_jsonl("inbox.jsonl", rec)
        print(json.dumps(rec, indent=2))
        sys.stdout.flush()

        if max_count is not None and seen["n"] >= max_count:
            raise KeyboardInterrupt()

    try:
        udp_listen(bind_host, port, on_msg, timeout_s=args.timeout, known_keys=known_keys)
    except KeyboardInterrupt:
        pass
    return 0


# ── BoTTube ──

def cmd_bottube_ping_agent(args: argparse.Namespace) -> int:
    cfg = load_config()
    identity = _load_identity(args)
    client = BoTTubeClient(
        base_url=_cfg_get(cfg, "bottube", "base_url", default="https://bottube.ai"),
        api_key=_cfg_get(cfg, "bottube", "api_key", default=None) or None,
    )

    links = args.link or []
    extra: Dict[str, Any] = {}
    if args.bounty_url:
        extra["bounty_url"] = args.bounty_url
    if args.reward_rtc is not None:
        extra["reward_rtc"] = float(args.reward_rtc)

    comment = args.comment
    if args.envelope_kind:
        if not comment:
            comment = _default_human_message(args.envelope_kind, links, args.bounty_url, args.reward_rtc)
        env = _build_envelope(cfg, args.envelope_kind, f"bottube:@{args.agent_name}", links, extra, identity=identity)
        if comment:
            comment = f"{comment}\n\n{env}"
        else:
            comment = env

    tip_msg = args.tip_message or ""
    if args.tip is not None and not tip_msg and comment:
        tip_msg = (args.tip_prefix or "[BEACON]") + " " + (args.comment or args.envelope_kind or "ping")
        tip_msg = tip_msg[:200]

    if args.dry_run:
        print(json.dumps({
            "agent_name": args.agent_name,
            "like": bool(args.like),
            "subscribe": bool(args.subscribe),
            "comment": comment or "",
            "tip": args.tip,
            "tip_message": tip_msg,
            "signed": identity is not None,
        }, indent=2))
        return 0

    result = client.ping_agent_latest_video(
        args.agent_name,
        like=args.like,
        subscribe=args.subscribe,
        comment=comment,
        tip_amount=args.tip,
        tip_message=tip_msg,
    )

    append_jsonl("outbox.jsonl", {"platform": "bottube", "to": args.agent_name, "result": result, "ts": int(time.time())})
    _maybe_udp_emit(cfg, {
        "platform": "bottube",
        "action": "ping-agent",
        "to_agent": args.agent_name,
        "video_id": result.get("video_id"),
        "like": bool(args.like),
        "subscribe": bool(args.subscribe),
        "comment": bool(comment),
        "tip": float(args.tip) if args.tip is not None else None,
    })
    print(json.dumps(result, indent=2))
    return 0


def cmd_bottube_ping_video(args: argparse.Namespace) -> int:
    cfg = load_config()
    identity = _load_identity(args)
    client = BoTTubeClient(
        base_url=_cfg_get(cfg, "bottube", "base_url", default="https://bottube.ai"),
        api_key=_cfg_get(cfg, "bottube", "api_key", default=None) or None,
    )

    links = args.link or []
    extra: Dict[str, Any] = {}
    if args.bounty_url:
        extra["bounty_url"] = args.bounty_url
    if args.reward_rtc is not None:
        extra["reward_rtc"] = float(args.reward_rtc)

    comment = args.comment
    if args.envelope_kind:
        if not comment:
            comment = _default_human_message(args.envelope_kind, links, args.bounty_url, args.reward_rtc)
        env = _build_envelope(cfg, args.envelope_kind, f"bottube:video:{args.video_id}", links, extra, identity=identity)
        if comment:
            comment = f"{comment}\n\n{env}"
        else:
            comment = env

    tip_msg = args.tip_message or ""
    if args.tip is not None and not tip_msg and comment:
        tip_msg = (args.tip_prefix or "[BEACON]") + " " + (args.comment or args.envelope_kind or "ping")
        tip_msg = tip_msg[:200]

    if args.dry_run:
        print(json.dumps({
            "video_id": args.video_id,
            "like": bool(args.like),
            "comment": comment or "",
            "tip": args.tip,
            "tip_message": tip_msg,
            "signed": identity is not None,
        }, indent=2))
        return 0

    result = client.ping_video(
        args.video_id,
        like=args.like,
        comment=comment,
        tip_amount=args.tip,
        tip_message=tip_msg,
    )
    append_jsonl("outbox.jsonl", {"platform": "bottube", "to_video": args.video_id, "result": result, "ts": int(time.time())})
    _maybe_udp_emit(cfg, {
        "platform": "bottube",
        "action": "ping-video",
        "video_id": args.video_id,
        "like": bool(args.like),
        "comment": bool(comment),
        "tip": float(args.tip) if args.tip is not None else None,
    })
    print(json.dumps(result, indent=2))
    return 0


# ── Moltbook ──

def cmd_moltbook_upvote(args: argparse.Namespace) -> int:
    cfg = load_config()
    client = MoltbookClient(
        base_url=_cfg_get(cfg, "moltbook", "base_url", default="https://www.moltbook.com"),
        api_key=_cfg_get(cfg, "moltbook", "api_key", default=None) or None,
    )
    if args.dry_run:
        print(json.dumps({"post_id": int(args.post_id)}, indent=2))
        return 0
    result = client.upvote(int(args.post_id))
    append_jsonl("outbox.jsonl", {"platform": "moltbook", "upvote": int(args.post_id), "result": result, "ts": int(time.time())})
    _maybe_udp_emit(cfg, {
        "platform": "moltbook",
        "action": "upvote",
        "post_id": int(args.post_id),
    })
    print(json.dumps(result, indent=2))
    return 0


def cmd_moltbook_post(args: argparse.Namespace) -> int:
    cfg = load_config()
    identity = _load_identity(args)
    client = MoltbookClient(
        base_url=_cfg_get(cfg, "moltbook", "base_url", default="https://www.moltbook.com"),
        api_key=_cfg_get(cfg, "moltbook", "api_key", default=None) or None,
    )

    content = args.content
    if args.envelope_kind:
        env = _build_envelope(cfg, args.envelope_kind, f"moltbook:m/{args.submolt}", args.link or [], {}, identity=identity)
        content = f"{content}\n\n{env}"

    if args.dry_run:
        print(json.dumps({"submolt": args.submolt, "title": args.title, "content": content}, indent=2))
        return 0
    result = client.create_post(args.submolt, args.title, content, force=args.force)
    append_jsonl("outbox.jsonl", {"platform": "moltbook", "post": {"submolt": args.submolt, "title": args.title}, "result": result, "ts": int(time.time())})
    _maybe_udp_emit(cfg, {
        "platform": "moltbook",
        "action": "post",
        "submolt": args.submolt,
        "title": args.title,
    })
    print(json.dumps(result, indent=2))
    return 0


# ── ClawCities ──

def cmd_clawcities_comment(args: argparse.Namespace) -> int:
    cfg = load_config()
    identity = _load_identity(args)
    client = ClawCitiesClient(
        base_url=_cfg_get(cfg, "clawcities", "base_url", default="https://clawcities.com"),
        api_key=_cfg_get(cfg, "clawcities", "api_key", default=None) or None,
    )

    body = args.text
    if args.envelope_kind:
        env = _build_envelope(cfg, args.envelope_kind, f"clawcities:{args.site_name}", args.link or [], {}, identity=identity)
        body = f"{body}\n\n{env}"

    if args.dry_run:
        print(json.dumps({"site": args.site_name, "body": body}, indent=2))
        return 0
    result = client.post_comment(args.site_name, body)
    append_jsonl("outbox.jsonl", {"platform": "clawcities", "comment": {"site": args.site_name}, "result": result, "ts": int(time.time())})
    _maybe_udp_emit(cfg, {
        "platform": "clawcities",
        "action": "comment",
        "site": args.site_name,
    })
    print(json.dumps(result, indent=2))
    return 0


def cmd_clawcities_discover(args: argparse.Namespace) -> int:
    cfg = load_config()
    client = ClawCitiesClient(
        base_url=_cfg_get(cfg, "clawcities", "base_url", default="https://clawcities.com"),
    )
    agents = client.discover_beacon_agents(limit=args.limit)
    print(json.dumps({"agents": agents, "count": len(agents)}, indent=2))
    return 0


def cmd_clawcities_site(args: argparse.Namespace) -> int:
    cfg = load_config()
    client = ClawCitiesClient(
        base_url=_cfg_get(cfg, "clawcities", "base_url", default="https://clawcities.com"),
    )
    result = client.get_site(args.site_name)
    print(json.dumps(result, indent=2))
    return 0


# ── PinchedIn ──

def cmd_pinchedin_post(args: argparse.Namespace) -> int:
    cfg = load_config()
    identity = _load_identity(args)
    client = PinchedInClient(
        base_url=_cfg_get(cfg, "pinchedin", "base_url", default="https://www.pinchedin.com"),
        api_key=_cfg_get(cfg, "pinchedin", "api_key", default=None) or None,
    )
    body = args.text
    if args.envelope_kind:
        env = _build_envelope(cfg, args.envelope_kind, "pinchedin:feed", args.link or [], {}, identity=identity)
        body = f"{body}\n\n{env}"
    if args.dry_run:
        print(json.dumps({"content": body}, indent=2))
        return 0
    result = client.create_post(body)
    append_jsonl("outbox.jsonl", {"platform": "pinchedin", "action": "post", "result": result, "ts": int(time.time())})
    _maybe_udp_emit(cfg, {"platform": "pinchedin", "action": "post"})
    print(json.dumps(result, indent=2))
    return 0


def cmd_pinchedin_feed(args: argparse.Namespace) -> int:
    cfg = load_config()
    client = PinchedInClient(
        base_url=_cfg_get(cfg, "pinchedin", "base_url", default="https://www.pinchedin.com"),
        api_key=_cfg_get(cfg, "pinchedin", "api_key", default=None) or None,
    )
    result = client.get_feed(limit=args.limit)
    print(json.dumps(result, indent=2))
    return 0


def cmd_pinchedin_jobs(args: argparse.Namespace) -> int:
    cfg = load_config()
    client = PinchedInClient(
        base_url=_cfg_get(cfg, "pinchedin", "base_url", default="https://www.pinchedin.com"),
        api_key=_cfg_get(cfg, "pinchedin", "api_key", default=None) or None,
    )
    result = client.get_jobs(limit=args.limit)
    print(json.dumps(result, indent=2))
    return 0


def cmd_pinchedin_connect(args: argparse.Namespace) -> int:
    cfg = load_config()
    client = PinchedInClient(
        base_url=_cfg_get(cfg, "pinchedin", "base_url", default="https://www.pinchedin.com"),
        api_key=_cfg_get(cfg, "pinchedin", "api_key", default=None) or None,
    )
    result = client.connect(args.target_bot_id)
    print(json.dumps(result, indent=2))
    return 0


# ── Clawsta ──

def cmd_clawsta_post(args: argparse.Namespace) -> int:
    cfg = load_config()
    identity = _load_identity(args)
    client = ClawstaClient(
        base_url=_cfg_get(cfg, "clawsta", "base_url", default="https://clawsta.io"),
        api_key=_cfg_get(cfg, "clawsta", "api_key", default=None) or None,
    )
    body = args.text
    if args.envelope_kind:
        env = _build_envelope(cfg, args.envelope_kind, "clawsta:feed", args.link or [], {}, identity=identity)
        body = f"{body}\n\n{env}"
    if args.dry_run:
        print(json.dumps({"content": body, "image_url": args.image_url}, indent=2))
        return 0
    result = client.create_post(body, image_url=args.image_url)
    append_jsonl("outbox.jsonl", {"platform": "clawsta", "action": "post", "result": result, "ts": int(time.time())})
    _maybe_udp_emit(cfg, {"platform": "clawsta", "action": "post"})
    print(json.dumps(result, indent=2))
    return 0


def cmd_clawsta_feed(args: argparse.Namespace) -> int:
    cfg = load_config()
    client = ClawstaClient(
        base_url=_cfg_get(cfg, "clawsta", "base_url", default="https://clawsta.io"),
        api_key=_cfg_get(cfg, "clawsta", "api_key", default=None) or None,
    )
    result = client.get_feed(limit=args.limit)
    print(json.dumps(result, indent=2))
    return 0


# ── 4Claw ──

def cmd_fourclaw_boards(args: argparse.Namespace) -> int:
    cfg = load_config()
    client = FourClawClient(
        base_url=_cfg_get(cfg, "fourclaw", "base_url", default="https://www.4claw.org"),
        api_key=_cfg_get(cfg, "fourclaw", "api_key", default=None) or None,
    )
    result = client.get_boards()
    print(json.dumps(result, indent=2))
    return 0


def cmd_fourclaw_threads(args: argparse.Namespace) -> int:
    cfg = load_config()
    client = FourClawClient(
        base_url=_cfg_get(cfg, "fourclaw", "base_url", default="https://www.4claw.org"),
        api_key=_cfg_get(cfg, "fourclaw", "api_key", default=None) or None,
    )
    result = client.get_threads(board=args.board, limit=args.limit)
    print(json.dumps(result, indent=2))
    return 0


def cmd_fourclaw_post(args: argparse.Namespace) -> int:
    cfg = load_config()
    identity = _load_identity(args)
    client = FourClawClient(
        base_url=_cfg_get(cfg, "fourclaw", "base_url", default="https://www.4claw.org"),
        api_key=_cfg_get(cfg, "fourclaw", "api_key", default=None) or None,
    )
    content = args.text
    if args.envelope_kind:
        env = _build_envelope(cfg, args.envelope_kind, f"fourclaw:{args.board}", args.link or [], {}, identity=identity)
        content = f"{content}\n\n{env}"
    if args.dry_run:
        print(json.dumps({"board": args.board, "title": args.title, "content": content}, indent=2))
        return 0
    result = client.create_thread(args.board, args.title, content)
    append_jsonl("outbox.jsonl", {"platform": "fourclaw", "action": "thread", "board": args.board, "result": result, "ts": int(time.time())})
    _maybe_udp_emit(cfg, {"platform": "fourclaw", "action": "thread", "board": args.board})
    print(json.dumps(result, indent=2))
    return 0


def cmd_fourclaw_reply(args: argparse.Namespace) -> int:
    cfg = load_config()
    client = FourClawClient(
        base_url=_cfg_get(cfg, "fourclaw", "base_url", default="https://www.4claw.org"),
        api_key=_cfg_get(cfg, "fourclaw", "api_key", default=None) or None,
    )
    if args.dry_run:
        print(json.dumps({"thread_id": args.thread_id, "content": args.text}, indent=2))
        return 0
    result = client.reply(args.thread_id, args.text)
    print(json.dumps(result, indent=2))
    return 0


# ── ClawTasks ──

def cmd_clawtasks_browse(args: argparse.Namespace) -> int:
    cfg = load_config()
    client = ClawTasksClient(
        base_url=_cfg_get(cfg, "clawtasks", "base_url", default="https://clawtasks.com"),
        api_key=_cfg_get(cfg, "clawtasks", "api_key", default=None) or None,
    )
    result = client.get_bounties(status=args.status, limit=args.limit)
    print(json.dumps(result, indent=2))
    return 0


def cmd_clawtasks_post(args: argparse.Namespace) -> int:
    cfg = load_config()
    client = ClawTasksClient(
        base_url=_cfg_get(cfg, "clawtasks", "base_url", default="https://clawtasks.com"),
        api_key=_cfg_get(cfg, "clawtasks", "api_key", default=None) or None,
    )
    tags = args.tags.split(",") if args.tags else None
    if args.dry_run:
        print(json.dumps({"title": args.title, "description": args.description, "tags": tags}, indent=2))
        return 0
    result = client.create_bounty(args.title, args.description, tags=tags, deadline_hours=args.deadline)
    append_jsonl("outbox.jsonl", {"platform": "clawtasks", "action": "bounty", "result": result, "ts": int(time.time())})
    _maybe_udp_emit(cfg, {"platform": "clawtasks", "action": "bounty"})
    print(json.dumps(result, indent=2))
    return 0


# ── ClawNews ──

def _clawnews_client(cfg=None):
    cfg = cfg or load_config()
    return ClawNewsClient(
        base_url=_cfg_get(cfg, "clawnews", "base_url", default="https://clawnews.io"),
        api_key=_cfg_get(cfg, "clawnews", "api_key", default=None) or None,
    )


def cmd_clawnews_browse(args: argparse.Namespace) -> int:
    client = _clawnews_client()
    feed = getattr(args, "feed", "top")
    result = client.get_stories(feed=feed, limit=args.limit)
    print(json.dumps(result, indent=2))
    return 0


def cmd_clawnews_submit(args: argparse.Namespace) -> int:
    cfg = load_config()
    client = _clawnews_client(cfg)
    item_type = getattr(args, "type", "story")
    if args.dry_run:
        print(json.dumps({"type": item_type, "title": args.title, "url": args.url, "text": args.text}, indent=2))
        return 0
    result = client.submit_story(args.title, url=args.url, text=args.text, item_type=item_type)
    append_jsonl("outbox.jsonl", {"platform": "clawnews", "action": item_type, "result": result, "ts": int(time.time())})
    _maybe_udp_emit(cfg, {"platform": "clawnews", "action": item_type})
    print(json.dumps(result, indent=2))
    return 0


def cmd_clawnews_comment(args: argparse.Namespace) -> int:
    cfg = load_config()
    client = _clawnews_client(cfg)
    result = client.submit_comment(args.parent_id, args.text)
    append_jsonl("outbox.jsonl", {"platform": "clawnews", "action": "comment", "result": result, "ts": int(time.time())})
    print(json.dumps(result, indent=2))
    return 0


def cmd_clawnews_vote(args: argparse.Namespace) -> int:
    client = _clawnews_client()
    result = client.upvote(args.item_id)
    print(json.dumps(result, indent=2))
    return 0


def cmd_clawnews_profile(args: argparse.Namespace) -> int:
    client = _clawnews_client()
    result = client.get_profile()
    print(json.dumps(result, indent=2))
    return 0


def cmd_clawnews_search(args: argparse.Namespace) -> int:
    client = _clawnews_client()
    item_type = getattr(args, "type", None)
    result = client.search(args.query, item_type=item_type, limit=args.limit)
    print(json.dumps(result, indent=2))
    return 0


# ── Discord ──

def _discord_client(cfg=None, webhook_url: Optional[str] = None) -> DiscordClient:
    cfg = cfg or load_config()
    timeout_s = int(_cfg_get(cfg, "discord", "timeout_s", default=20) or 20)
    return DiscordClient(
        webhook_url=webhook_url or _cfg_get(cfg, "discord", "webhook_url", default=""),
        timeout_s=timeout_s,
        username=_cfg_get(cfg, "discord", "username", default=None) or None,
        avatar_url=_cfg_get(cfg, "discord", "avatar_url", default=None) or None,
    )


def cmd_discord_ping(args: argparse.Namespace) -> int:
    cfg = load_config()
    identity = _load_identity(args)
    kind = getattr(args, "kind", "hello") or "hello"
    links = args.link or []

    extra: Dict[str, Any] = {}
    if args.rtc is not None:
        extra["rtc_tip"] = float(args.rtc)

    env = _build_envelope(cfg, kind, "discord:webhook", links, extra, identity=identity)
    message_text = args.text or ""
    payload_text = f"{message_text}\n\n{env}" if message_text else env

    envs = decode_envelopes(env)
    env_obj = envs[0] if envs else {}
    agent_id = env_obj.get("agent_id") or _cfg_get(cfg, "beacon", "agent_name", default="") or "unknown"
    sig_preview = env_obj.get("sig", "")

    if args.dry_run:
        print(json.dumps({
            "kind": kind,
            "text": message_text,
            "rtc": args.rtc,
            "link": links,
            "webhook_url": args.webhook_url or _cfg_get(cfg, "discord", "webhook_url", default=""),
            "payload_text": payload_text,
            "signed": identity is not None,
        }, indent=2))
        return 0

    try:
        client = _discord_client(cfg, webhook_url=args.webhook_url)
        result = client.send_beacon(
            content=payload_text,
            kind=kind,
            agent_id=agent_id,
            rtc_tip=args.rtc,
            signature_preview=sig_preview,
            username=(args.username or None),
            avatar_url=(args.avatar_url or None),
        )
    except Exception as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        return 1

    append_jsonl("outbox.jsonl", {
        "platform": "discord",
        "action": "ping",
        "kind": kind,
        "rtc": float(args.rtc) if args.rtc is not None else None,
        "result": result,
        "ts": int(time.time()),
    })
    _maybe_udp_emit(cfg, {
        "platform": "discord",
        "action": "ping",
        "kind": kind,
        "rtc": float(args.rtc) if args.rtc is not None else None,
    })
    print(json.dumps(result, indent=2))
    return 0


def cmd_discord_send(args: argparse.Namespace) -> int:
    cfg = load_config()
    identity = _load_identity(args)
    kind = getattr(args, "kind", "hello") or "hello"
    links = args.link or []

    extra: Dict[str, Any] = {}
    if args.bounty_url:
        extra["bounty_url"] = args.bounty_url
    if args.reward_rtc is not None:
        extra["reward_rtc"] = float(args.reward_rtc)
    if args.rtc is not None:
        extra["rtc_tip"] = float(args.rtc)

    text = args.text or ""
    if not text:
        text = _default_human_message(kind, links, args.bounty_url, args.reward_rtc)

    env = _build_envelope(cfg, kind, "discord:webhook", links, extra, identity=identity)
    payload_text = f"{text}\n\n{env}" if text else env

    envs = decode_envelopes(env)
    env_obj = envs[0] if envs else {}
    agent_id = env_obj.get("agent_id") or _cfg_get(cfg, "beacon", "agent_name", default="") or "unknown"
    sig_preview = env_obj.get("sig", "")

    if args.dry_run:
        print(json.dumps({
            "kind": kind,
            "text": text,
            "bounty_url": args.bounty_url,
            "reward_rtc": args.reward_rtc,
            "rtc": args.rtc,
            "link": links,
            "webhook_url": args.webhook_url or _cfg_get(cfg, "discord", "webhook_url", default=""),
            "payload_text": payload_text,
            "signed": identity is not None,
        }, indent=2))
        return 0

    try:
        client = _discord_client(cfg, webhook_url=args.webhook_url)
        result = client.send_beacon(
            content=payload_text,
            kind=kind,
            agent_id=agent_id,
            rtc_tip=args.rtc,
            signature_preview=sig_preview,
            username=(args.username or None),
            avatar_url=(args.avatar_url or None),
        )
    except Exception as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        return 1

    append_jsonl("outbox.jsonl", {
        "platform": "discord",
        "action": "send",
        "kind": kind,
        "rtc": float(args.rtc) if args.rtc is not None else None,
        "result": result,
        "ts": int(time.time()),
    })
    _maybe_udp_emit(cfg, {
        "platform": "discord",
        "action": "send",
        "kind": kind,
        "rtc": float(args.rtc) if args.rtc is not None else None,
    })
    print(json.dumps(result, indent=2))
    return 0


# ── RustChain ──

def cmd_rustchain_wallet_new(args: argparse.Namespace) -> int:
    use_mnemonic = getattr(args, "mnemonic", False)
    if use_mnemonic:
        kp = RustChainKeypair.generate_with_mnemonic()
    else:
        kp = RustChainKeypair.generate()

    result: Dict[str, Any] = {
        "address": kp.address,
        "public_key_hex": kp.public_key_hex,
        "private_key_hex": kp.private_key_hex,
    }
    if kp.mnemonic:
        result["mnemonic"] = kp.mnemonic
        result["warning"] = "Write down these 24 words and store them securely."
    print(json.dumps(result, indent=2))
    return 0


def cmd_rustchain_balance(args: argparse.Namespace) -> int:
    cfg = load_config()
    client = RustChainClient(
        base_url=_cfg_get(cfg, "rustchain", "base_url", default="https://rustchain.org"),
        verify_ssl=bool(_cfg_get(cfg, "rustchain", "verify_ssl", default=False)),
    )
    result = client.balance(args.address)
    print(json.dumps(result, indent=2))
    return 0


def cmd_rustchain_pay(args: argparse.Namespace) -> int:
    cfg = load_config()
    priv = _cfg_get(cfg, "rustchain", "private_key_hex", default="") or ""
    if args.private_key_hex:
        priv = args.private_key_hex
    if not priv:
        print("RustChain private_key_hex missing (set rustchain.private_key_hex in ~/.beacon/config.json)", file=sys.stderr)
        return 2

    client = RustChainClient(
        base_url=_cfg_get(cfg, "rustchain", "base_url", default="https://rustchain.org"),
        verify_ssl=bool(_cfg_get(cfg, "rustchain", "verify_ssl", default=False)),
    )
    payload = client.sign_transfer(
        private_key_hex=priv,
        to_address=args.to_address,
        amount_rtc=float(args.amount_rtc),
        memo=args.memo or "",
        nonce=args.nonce,
    )

    if args.dry_run:
        print(json.dumps(payload, indent=2))
        return 0

    result = client.transfer_signed(payload)
    append_jsonl("outbox.jsonl", {"platform": "rustchain", "pay": {"to": args.to_address, "amount_rtc": float(args.amount_rtc)}, "result": result, "ts": int(time.time())})
    _maybe_udp_emit(cfg, {
        "platform": "rustchain",
        "action": "pay",
        "to_address": args.to_address,
        "amount_rtc": float(args.amount_rtc),
        "memo": args.memo or "",
        "nonce": payload.get("nonce"),
        "from_address": payload.get("from_address"),
    })
    print(json.dumps(result, indent=2))
    return 0


# ── Agent Card ──

def cmd_agent_card_generate(args: argparse.Namespace) -> int:
    from .identity import AgentIdentity
    from .agent_card import generate_agent_card, card_to_json
    password = getattr(args, "password", None)
    try:
        ident = AgentIdentity.load(password=password)
    except FileNotFoundError:
        print('{"error": "No identity found. Run: beacon identity new"}', file=sys.stderr)
        return 1

    cfg = load_config()
    name = getattr(args, "name", "") or _cfg_get(cfg, "beacon", "agent_name", default="")
    transports: Dict[str, Any] = {}
    udp_cfg = cfg.get("udp", {})
    if udp_cfg.get("enabled"):
        transports["udp"] = {"port": udp_cfg.get("port", 38400)}
    webhook_cfg = cfg.get("webhook", {})
    if webhook_cfg.get("enabled"):
        transports["webhook"] = {"port": webhook_cfg.get("port", 8402)}

    card = generate_agent_card(ident, name=name, transports=transports)
    print(card_to_json(card))
    return 0


def cmd_agent_card_verify(args: argparse.Namespace) -> int:
    from .agent_card import verify_agent_card
    import requests
    try:
        resp = requests.get(args.url, timeout=15)
        card = resp.json()
    except Exception as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        return 1
    valid = verify_agent_card(card)
    print(json.dumps({"url": args.url, "valid": valid, "agent_id": card.get("agent_id", "")}, indent=2))
    return 0 if valid else 1


# ── Webhook ──

def cmd_webhook_serve(args: argparse.Namespace) -> int:
    from .identity import AgentIdentity
    from .agent_card import generate_agent_card
    from .transports.webhook import WebhookServer

    password = getattr(args, "password", None)
    identity = None
    agent_card = None
    try:
        identity = AgentIdentity.load(password=password)
        cfg = load_config()
        name = _cfg_get(cfg, "beacon", "agent_name", default="")
        agent_card = generate_agent_card(identity, name=name)
    except Exception:
        pass

    port = int(args.port)
    host = args.host
    print(json.dumps({
        "status": "starting",
        "host": host,
        "port": port,
        "agent_id": identity.agent_id if identity else None,
    }))
    sys.stdout.flush()

    server = WebhookServer(port=port, host=host, identity=identity, agent_card=agent_card)
    try:
        server.start(blocking=True)
    except KeyboardInterrupt:
        server.stop()
    return 0


def cmd_webhook_send(args: argparse.Namespace) -> int:
    from .transports.webhook import webhook_send
    identity = _load_identity(args)

    payload: Dict[str, Any] = {
        "kind": args.kind,
        "from": "",
        "to": args.url,
        "ts": int(time.time()),
    }

    if identity:
        text = encode_envelope(payload, version=2, identity=identity, include_pubkey=True)
        envs = decode_envelopes(text)
        envelope = envs[0] if envs else payload
    else:
        payload["v"] = 1
        envelope = payload

    result = webhook_send(args.url, envelope)
    print(json.dumps(result, indent=2))
    return 0


# ── Presence ──

def cmd_pulse(args: argparse.Namespace) -> int:
    from .presence import PresenceManager
    cfg = load_config()
    identity = _load_identity(args)
    if not identity:
        print('{"error": "No identity found. Run: beacon identity new"}', file=sys.stderr)
        return 1
    mgr = PresenceManager(config=cfg)
    # Beacon 2.1 Soul: include curiosity + values in pulse
    curiosity_mgr = None
    values_mgr = None
    autonomy = cfg.get("autonomy", {})
    if autonomy.get("curiosity_enabled", True):
        from .curiosity import CuriosityManager
        curiosity_mgr = CuriosityManager()
    if autonomy.get("values_enabled", True):
        from .values import ValuesManager
        values_mgr = ValuesManager()
    pulse = mgr.build_pulse(identity, cfg, curiosity_mgr=curiosity_mgr, values_mgr=values_mgr)
    env = _build_envelope(cfg, "pulse", "", [], pulse, identity=identity)
    # Broadcast on all enabled transports
    udp_cfg = cfg.get("udp", {})
    if udp_cfg.get("enabled"):
        host = str(udp_cfg.get("host", "255.255.255.255"))
        port = int(udp_cfg.get("port", 38400))
        broadcast = bool(udp_cfg.get("broadcast", True))
        ttl = udp_cfg.get("ttl")
        try:
            ttl_int = int(ttl) if ttl is not None else None
        except Exception:
            ttl_int = None
        try:
            udp_send(host, port, env.encode("utf-8", errors="replace"), broadcast=broadcast, ttl=ttl_int)
        except Exception:
            pass
    print(json.dumps(pulse, indent=2))
    return 0


def cmd_roster(args: argparse.Namespace) -> int:
    from .presence import PresenceManager
    cfg = load_config()
    mgr = PresenceManager(config=cfg)
    agents = mgr.roster(online_only=not getattr(args, "all", False))
    for a in agents:
        print(json.dumps(a))
    return 0


def cmd_roster_find(args: argparse.Namespace) -> int:
    from .presence import PresenceManager
    cfg = load_config()
    mgr = PresenceManager(config=cfg)
    if args.offers:
        results = mgr.find_by_offer(args.offers)
    elif args.needs:
        results = mgr.find_by_need(args.needs)
    else:
        results = mgr.roster()
    for r in results:
        print(json.dumps(r))
    return 0


# ── Trust ──

def cmd_trust_score(args: argparse.Namespace) -> int:
    from .trust import TrustManager
    mgr = TrustManager()
    result = mgr.score(args.agent_id)
    print(json.dumps(result, indent=2))
    return 0


def cmd_trust_scores(args: argparse.Namespace) -> int:
    from .trust import TrustManager
    mgr = TrustManager()
    min_ix = getattr(args, "min_interactions", 0) or 0
    results = mgr.scores(min_interactions=min_ix)
    for r in results:
        print(json.dumps(r))
    return 0


def cmd_trust_rate(args: argparse.Namespace) -> int:
    from .trust import TrustManager
    mgr = TrustManager()
    mgr.record(args.agent_id, "out", "manual_rate", outcome=args.outcome)
    print(json.dumps({"ok": True, "agent_id": args.agent_id, "outcome": args.outcome}))
    return 0


def cmd_trust_block(args: argparse.Namespace) -> int:
    from .trust import TrustManager
    mgr = TrustManager()
    reason = getattr(args, "reason", "") or ""
    mgr.block(args.agent_id, reason=reason)
    print(json.dumps({"ok": True, "blocked": args.agent_id, "reason": reason}))
    return 0


def cmd_trust_unblock(args: argparse.Namespace) -> int:
    from .trust import TrustManager
    mgr = TrustManager()
    mgr.unblock(args.agent_id)
    print(json.dumps({"ok": True, "unblocked": args.agent_id}))
    return 0


def cmd_trust_blocked(args: argparse.Namespace) -> int:
    from .trust import TrustManager
    mgr = TrustManager()
    blocked = mgr.blocked_list()
    print(json.dumps(blocked, indent=2))
    return 0


# ── Feed ──

def cmd_feed_list(args: argparse.Namespace) -> int:
    from .feed import FeedManager
    from .trust import TrustManager
    from .inbox import read_inbox
    feed_mgr = FeedManager()
    trust_mgr = TrustManager()
    entries = read_inbox()
    min_score = getattr(args, "min_score", 0.0) or 0.0
    limit = getattr(args, "limit", 50) or 50
    results = feed_mgr.feed(entries, trust_mgr=trust_mgr, min_score=min_score, limit=limit)
    for r in results:
        summary = {
            "score": r.get("score"),
            "kind": (r.get("envelope") or {}).get("kind", "?"),
            "agent_id": (r.get("envelope") or {}).get("agent_id", ""),
            "platform": r.get("platform", "?"),
        }
        print(json.dumps(summary))
    return 0


def cmd_feed_subscribe(args: argparse.Namespace) -> int:
    from .feed import FeedManager
    mgr = FeedManager()
    if args.sub_type == "agent":
        alias = getattr(args, "alias", "") or ""
        mgr.subscribe_agent(args.target, alias=alias)
        print(json.dumps({"ok": True, "subscribed_agent": args.target}))
    elif args.sub_type == "topic":
        mgr.subscribe_topic(args.target)
        print(json.dumps({"ok": True, "subscribed_topic": args.target}))
    return 0


def cmd_feed_unsubscribe(args: argparse.Namespace) -> int:
    from .feed import FeedManager
    mgr = FeedManager()
    if args.sub_type == "agent":
        mgr.unsubscribe_agent(args.target)
        print(json.dumps({"ok": True, "unsubscribed_agent": args.target}))
    elif args.sub_type == "topic":
        mgr.unsubscribe_topic(args.target)
        print(json.dumps({"ok": True, "unsubscribed_topic": args.target}))
    return 0


def cmd_feed_subs(args: argparse.Namespace) -> int:
    from .feed import FeedManager
    mgr = FeedManager()
    print(json.dumps(mgr.subscriptions(), indent=2))
    return 0


# ── Rules ──

def cmd_rules_list(args: argparse.Namespace) -> int:
    from .rules import RulesEngine
    engine = RulesEngine()
    for rule in engine.rules():
        print(json.dumps(rule))
    return 0


def cmd_rules_add(args: argparse.Namespace) -> int:
    from .rules import RulesEngine
    engine = RulesEngine()
    when: Dict[str, Any] = {}
    if args.when_kind:
        when["kind"] = args.when_kind
    if args.when_min_rtc is not None:
        when["min_rtc"] = args.when_min_rtc
    if args.when_min_trust is not None:
        when["min_trust"] = args.when_min_trust
    if args.when_topic:
        when["topic_match"] = args.when_topic.split(",")

    then: Dict[str, Any] = {"action": args.then_action}
    if args.then_kind:
        then["kind"] = args.then_kind
    if args.then_text:
        then["text"] = args.then_text

    rule = {"name": args.name, "when": when, "then": then}
    engine.add_rule(rule)
    print(json.dumps({"ok": True, "rule": rule}, indent=2))
    return 0


def cmd_rules_enable(args: argparse.Namespace) -> int:
    from .rules import RulesEngine
    engine = RulesEngine()
    ok = engine.enable_rule(args.name)
    print(json.dumps({"ok": ok, "enabled": args.name}))
    return 0 if ok else 1


def cmd_rules_disable(args: argparse.Namespace) -> int:
    from .rules import RulesEngine
    engine = RulesEngine()
    ok = engine.disable_rule(args.name)
    print(json.dumps({"ok": ok, "disabled": args.name}))
    return 0 if ok else 1


def cmd_rules_test(args: argparse.Namespace) -> int:
    from .rules import RulesEngine
    from .trust import TrustManager
    engine = RulesEngine()
    trust_mgr = TrustManager()
    try:
        event = json.loads(args.event_json)
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"Invalid JSON: {e}"}), file=sys.stderr)
        return 2
    matches = engine.evaluate({"envelope": event}, trust_mgr=trust_mgr)
    print(json.dumps({"matches": len(matches), "rules": [m["rule"] for m in matches]}, indent=2))
    return 0


def cmd_rules_log(args: argparse.Namespace) -> int:
    from .storage import read_jsonl_tail
    limit = getattr(args, "limit", 50) or 50
    entries = read_jsonl_tail("rules_log.jsonl", limit=limit)
    for e in entries:
        print(json.dumps(e))
    return 0


# ── Tasks ──

def cmd_task_list(args: argparse.Namespace) -> int:
    from .tasks import TaskManager
    mgr = TaskManager()
    state = getattr(args, "state", None)
    tasks = mgr.list_tasks(state=state)
    for t in tasks:
        summary = mgr.task_summary(t.get("task_id", ""))
        if summary:
            print(json.dumps(summary))
    return 0


def cmd_task_show(args: argparse.Namespace) -> int:
    from .tasks import TaskManager
    mgr = TaskManager()
    t = mgr.get(args.task_id)
    if not t:
        print(json.dumps({"error": f"Task {args.task_id} not found"}), file=sys.stderr)
        return 1
    print(json.dumps(t, indent=2))
    return 0


def cmd_task_offer(args: argparse.Namespace) -> int:
    from .tasks import TaskManager
    identity = _load_identity(args)
    mgr = TaskManager()
    env = {
        "kind": "offer",
        "task_id": args.task_id,
        "text": args.text,
        "agent_id": identity.agent_id if identity else "",
    }
    try:
        result = mgr.transition(args.task_id, "offered", envelope=env)
        print(json.dumps(result, indent=2))
        return 0
    except ValueError as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        return 1


def cmd_task_accept(args: argparse.Namespace) -> int:
    from .tasks import TaskManager
    mgr = TaskManager()
    env = {"kind": "accept", "task_id": args.task_id, "worker": args.agent_id}
    try:
        result = mgr.transition(args.task_id, "accepted", envelope=env)
        print(json.dumps(result, indent=2))
        return 0
    except ValueError as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        return 1


def cmd_task_deliver(args: argparse.Namespace) -> int:
    from .tasks import TaskManager
    identity = _load_identity(args)
    mgr = TaskManager()
    env = {
        "kind": "deliver",
        "task_id": args.task_id,
        "delivery_url": args.url,
        "agent_id": identity.agent_id if identity else "",
    }
    try:
        result = mgr.transition(args.task_id, "delivered", envelope=env)
        print(json.dumps(result, indent=2))
        return 0
    except ValueError as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        return 1


def cmd_task_confirm(args: argparse.Namespace) -> int:
    from .tasks import TaskManager
    identity = _load_identity(args)
    mgr = TaskManager()
    env = {"kind": "confirm", "task_id": args.task_id, "agent_id": identity.agent_id if identity else ""}
    try:
        result = mgr.transition(args.task_id, "confirmed", envelope=env)
        print(json.dumps(result, indent=2))
        return 0
    except ValueError as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        return 1


def cmd_task_pay(args: argparse.Namespace) -> int:
    from .tasks import TaskManager
    mgr = TaskManager()
    t = mgr.get(args.task_id)
    if not t:
        print(json.dumps({"error": f"Task {args.task_id} not found"}), file=sys.stderr)
        return 1
    env = {"kind": "pay", "task_id": args.task_id, "amount_rtc": t.get("reward_rtc", 0)}
    try:
        result = mgr.transition(args.task_id, "paid", envelope=env)
        print(json.dumps(result, indent=2))
        return 0
    except ValueError as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        return 1


# ── Memory ──

def cmd_memory_profile(args: argparse.Namespace) -> int:
    from .memory import AgentMemory
    identity = _load_identity(args)
    agent_id = identity.agent_id if identity else ""
    mem = AgentMemory(my_agent_id=agent_id)
    print(json.dumps(mem.profile(), indent=2))
    return 0


def cmd_memory_contacts(args: argparse.Namespace) -> int:
    from .memory import AgentMemory
    mem = AgentMemory()
    limit = getattr(args, "limit", 20) or 20
    for c in mem.contacts(limit=limit):
        print(json.dumps(c))
    return 0


def cmd_memory_contact(args: argparse.Namespace) -> int:
    from .memory import AgentMemory
    mem = AgentMemory()
    print(json.dumps(mem.contact(args.agent_id), indent=2))
    return 0


def cmd_memory_demand(args: argparse.Namespace) -> int:
    from .memory import AgentMemory
    mem = AgentMemory()
    days = getattr(args, "days", 7) or 7
    print(json.dumps(mem.demand_signals(days=days), indent=2))
    return 0


def cmd_memory_gaps(args: argparse.Namespace) -> int:
    from .memory import AgentMemory
    mem = AgentMemory()
    gaps = mem.skill_gaps()
    print(json.dumps({"skill_gaps": gaps}, indent=2))
    return 0


def cmd_memory_suggest(args: argparse.Namespace) -> int:
    from .memory import AgentMemory
    mem = AgentMemory()
    suggestions = mem.suggest_rules()
    for s in suggestions:
        print(json.dumps(s, indent=2))
    return 0


def cmd_memory_rebuild(args: argparse.Namespace) -> int:
    from .memory import AgentMemory
    from .journal import JournalManager
    from .curiosity import CuriosityManager
    from .values import ValuesManager
    identity = _load_identity(args)
    agent_id = identity.agent_id if identity else ""
    mem = AgentMemory(my_agent_id=agent_id)
    journal_mgr = JournalManager()
    curiosity_mgr = CuriosityManager()
    values_mgr = ValuesManager()
    profile = mem.rebuild(
        journal_mgr=journal_mgr,
        curiosity_mgr=curiosity_mgr,
        values_mgr=values_mgr,
    )
    print(json.dumps({"ok": True, "rebuilt_at": profile.get("rebuilt_at")}, indent=2))
    return 0


# ── Beacon 2.1 Soul: Journal Commands ──

def cmd_journal_write(args: argparse.Namespace) -> int:
    from .journal import JournalManager
    tags = [t.strip() for t in args.tags.split(",")] if args.tags else []
    mgr = JournalManager()
    entry = mgr.write(text=args.text, tags=tags, mood=args.mood)
    print(json.dumps(entry, indent=2))
    return 0


def cmd_journal_read(args: argparse.Namespace) -> int:
    from .journal import JournalManager
    mgr = JournalManager()
    limit = getattr(args, "last", 20) or 20
    for entry in mgr.read(limit=limit):
        print(json.dumps(entry))
    return 0


def cmd_journal_search(args: argparse.Namespace) -> int:
    from .journal import JournalManager
    mgr = JournalManager()
    for entry in mgr.search(args.term):
        print(json.dumps(entry))
    return 0


def cmd_journal_moods(args: argparse.Namespace) -> int:
    from .journal import JournalManager
    mgr = JournalManager()
    print(json.dumps(mgr.moods(), indent=2))
    return 0


def cmd_journal_tags(args: argparse.Namespace) -> int:
    from .journal import JournalManager
    mgr = JournalManager()
    tags = mgr.recent_tags(limit=getattr(args, "limit", 20) or 20)
    print(json.dumps([{"tag": t, "count": c} for t, c in tags], indent=2))
    return 0


# ── Beacon 2.1 Soul: Curiosity Commands ──

def cmd_curious_add(args: argparse.Namespace) -> int:
    from .curiosity import CuriosityManager
    mgr = CuriosityManager()
    result = mgr.add(topic=args.topic, intensity=args.intensity, notes=args.notes or "")
    print(json.dumps(result, indent=2))
    return 0


def cmd_curious_list(args: argparse.Namespace) -> int:
    from .curiosity import CuriosityManager
    mgr = CuriosityManager()
    interests = mgr.interests()
    explored = mgr.explored()
    print(json.dumps({"active": interests, "explored": explored}, indent=2))
    return 0


def cmd_curious_explore(args: argparse.Namespace) -> int:
    from .curiosity import CuriosityManager
    mgr = CuriosityManager()
    ok = mgr.explore(topic=args.topic, notes=args.notes or "")
    if ok:
        print(json.dumps({"ok": True, "topic": args.topic, "status": "explored"}))
    else:
        print(json.dumps({"ok": False, "error": f"Interest '{args.topic}' not found"}))
    return 0 if ok else 1


def cmd_curious_remove(args: argparse.Namespace) -> int:
    from .curiosity import CuriosityManager
    mgr = CuriosityManager()
    ok = mgr.remove(args.topic)
    print(json.dumps({"ok": ok}))
    return 0 if ok else 1


def cmd_curious_find(args: argparse.Namespace) -> int:
    from .curiosity import CuriosityManager
    from .presence import PresenceManager
    mgr = CuriosityManager()
    cfg = load_config()
    presence = PresenceManager(config=cfg)
    topic_lower = args.topic.lower()
    matches = []
    for agent in presence.roster(online_only=False):
        curiosities = [c.lower() for c in agent.get("curiosities", [])]
        if topic_lower in curiosities:
            matches.append({"agent_id": agent["agent_id"], "name": agent.get("name", "")})
    print(json.dumps({"topic": args.topic, "agents": matches}, indent=2))
    return 0


def cmd_curious_mutual(args: argparse.Namespace) -> int:
    from .curiosity import CuriosityManager
    from .presence import PresenceManager
    mgr = CuriosityManager()
    cfg = load_config()
    presence = PresenceManager(config=cfg)
    agent = presence.get_agent(args.agent_id)
    if not agent:
        print(json.dumps({"error": f"Agent {args.agent_id} not in roster"}))
        return 1
    result = mgr.find_mutual(agent)
    print(json.dumps(result, indent=2))
    return 0


def cmd_curious_broadcast(args: argparse.Namespace) -> int:
    from .curiosity import CuriosityManager
    identity = _load_identity(args)
    if not identity:
        print('{"error": "No identity found. Run: beacon identity new"}', file=sys.stderr)
        return 1
    mgr = CuriosityManager()
    envelope = mgr.build_curious_envelope(identity.agent_id, text=args.text or "")
    cfg = load_config()
    env_str = _build_envelope(cfg, "curious", "", [], envelope, identity=identity)
    # Broadcast on UDP if enabled
    udp_cfg = cfg.get("udp", {})
    if udp_cfg.get("enabled"):
        host = str(udp_cfg.get("host", "255.255.255.255"))
        port = int(udp_cfg.get("port", 38400))
        try:
            udp_send(host, port, env_str.encode("utf-8", errors="replace"), broadcast=True)
        except Exception:
            pass
    print(json.dumps(envelope, indent=2))
    return 0


# ── Beacon 2.1 Soul: Values Commands ──

def cmd_values_show(args: argparse.Namespace) -> int:
    from .values import ValuesManager
    mgr = ValuesManager()
    data = mgr.full_values()
    print(json.dumps(data, indent=2))
    return 0


def cmd_values_principle_add(args: argparse.Namespace) -> int:
    from .values import ValuesManager
    mgr = ValuesManager()
    mgr.set_principle(args.name, args.weight, text=args.text or "")
    print(json.dumps({"ok": True, "principle": args.name, "weight": args.weight}))
    return 0


def cmd_values_principle_remove(args: argparse.Namespace) -> int:
    from .values import ValuesManager
    mgr = ValuesManager()
    ok = mgr.remove_principle(args.name)
    print(json.dumps({"ok": ok}))
    return 0 if ok else 1


def cmd_values_boundary_add(args: argparse.Namespace) -> int:
    from .values import ValuesManager
    mgr = ValuesManager()
    idx = mgr.add_boundary(args.text)
    print(json.dumps({"ok": True, "index": idx}))
    return 0


def cmd_values_boundary_remove(args: argparse.Namespace) -> int:
    from .values import ValuesManager
    mgr = ValuesManager()
    ok = mgr.remove_boundary(args.index)
    print(json.dumps({"ok": ok}))
    return 0 if ok else 1


def cmd_values_aesthetic_set(args: argparse.Namespace) -> int:
    from .values import ValuesManager
    mgr = ValuesManager()
    mgr.set_aesthetic(args.key, args.value)
    print(json.dumps({"ok": True, "key": args.key, "value": args.value}))
    return 0


def cmd_values_aesthetic_remove(args: argparse.Namespace) -> int:
    from .values import ValuesManager
    mgr = ValuesManager()
    ok = mgr.remove_aesthetic(args.key)
    print(json.dumps({"ok": ok}))
    return 0 if ok else 1


def cmd_values_match(args: argparse.Namespace) -> int:
    from .values import ValuesManager
    from .presence import PresenceManager
    mgr = ValuesManager()
    # For now, compatibility requires the other agent's values in roster
    # A full implementation would fetch from their agent card
    print(json.dumps({"agent_id": args.agent_id, "note": "Fetch agent card for full compatibility scoring", "rtc_cost": 1.0}))
    return 0


def cmd_values_hash(args: argparse.Namespace) -> int:
    from .values import ValuesManager
    mgr = ValuesManager()
    print(json.dumps({"values_hash": mgr.values_hash()}))
    return 0


def cmd_values_preset(args: argparse.Namespace) -> int:
    from .values import ValuesManager, MORAL_PRESETS
    mgr = ValuesManager()
    if args.list:
        for name, preset in MORAL_PRESETS.items():
            principles = list(preset.get("principles", {}).keys())
            boundaries = len(preset.get("boundaries", []))
            print(f"  {name}: {len(principles)} principles, {boundaries} boundaries")
        return 0
    count = mgr.apply_preset(args.name)
    print(json.dumps({"ok": True, "preset": args.name, "items_added": count}))
    return 0


# ── Beacon 2.1 Soul: Agent Scanner Commands ──

def cmd_scan_agent(args: argparse.Namespace) -> int:
    from .values import AgentScanner
    from .trust import TrustManager
    trust_mgr = TrustManager()
    scanner = AgentScanner(trust_mgr=trust_mgr)
    result = scanner.scan_agent(args.agent_id)
    print(json.dumps(result, indent=2))
    return 0


def cmd_scan_all(args: argparse.Namespace) -> int:
    from .values import AgentScanner
    from .trust import TrustManager
    trust_mgr = TrustManager()
    scanner = AgentScanner(trust_mgr=trust_mgr)
    results = scanner.scan_all()
    for r in results:
        print(json.dumps(r))
    return 0


# ── Beacon 2.2: Goals/Dreams Commands ──

def cmd_dream_new(args: argparse.Namespace) -> int:
    from .goals import GoalManager
    mgr = GoalManager()
    target = getattr(args, "target", None)
    target = float(target) if target else None
    gid = mgr.dream(args.title, description=getattr(args, "description", ""), category=args.category, target_value=target)
    print(json.dumps({"ok": True, "goal_id": gid}))
    return 0


def cmd_dream_list(args: argparse.Namespace) -> int:
    from .goals import GoalManager
    mgr = GoalManager()
    state = getattr(args, "state", None)
    goals = mgr.list_goals(state=state)
    for g in goals:
        print(json.dumps(g))
    if not goals:
        print(json.dumps({"info": "No goals found"}))
    return 0


def cmd_dream_show(args: argparse.Namespace) -> int:
    from .goals import GoalManager
    mgr = GoalManager()
    goal = mgr.get(args.goal_id)
    if not goal:
        print(json.dumps({"error": "Goal not found"}))
        return 1
    print(json.dumps(goal, indent=2))
    return 0


def cmd_dream_activate(args: argparse.Namespace) -> int:
    from .goals import GoalManager
    mgr = GoalManager()
    ok = mgr.activate(args.goal_id)
    print(json.dumps({"ok": ok, "goal_id": args.goal_id, "rtc_cost": 0.1}))
    return 0 if ok else 1


def cmd_dream_progress(args: argparse.Namespace) -> int:
    from .goals import GoalManager
    mgr = GoalManager()
    value = getattr(args, "value", None)
    value = float(value) if value is not None else None
    result = mgr.progress(args.goal_id, args.milestone, value=value)
    if not result:
        print(json.dumps({"error": "Goal not found or not active"}))
        return 1
    print(json.dumps(result, indent=2))
    return 0


def cmd_dream_achieve(args: argparse.Namespace) -> int:
    from .goals import GoalManager
    journal_mgr = None
    try:
        from .journal import JournalManager
        journal_mgr = JournalManager()
    except Exception:
        pass
    mgr = GoalManager(journal_mgr=journal_mgr)
    notes = getattr(args, "notes", "")
    ok = mgr.achieve(args.goal_id, notes=notes or "")
    print(json.dumps({"ok": ok, "goal_id": args.goal_id}))
    return 0 if ok else 1


def cmd_dream_abandon(args: argparse.Namespace) -> int:
    from .goals import GoalManager
    mgr = GoalManager()
    reason = getattr(args, "reason", "")
    ok = mgr.abandon(args.goal_id, reason=reason or "")
    print(json.dumps({"ok": ok, "goal_id": args.goal_id}))
    return 0 if ok else 1


def cmd_dream_suggest(args: argparse.Namespace) -> int:
    from .goals import GoalManager
    from .presence import PresenceManager
    from .memory import AgentMemory
    mgr = GoalManager()
    cfg = load_config()
    try:
        presence_mgr = PresenceManager(config=cfg)
        roster = presence_mgr.roster(online_only=True)
    except Exception:
        roster = []
    try:
        memory_mgr = AgentMemory()
        demand = memory_mgr.demand_signals(7)
    except Exception:
        demand = {}
    suggestions = mgr.suggest_actions(roster=roster, demand=demand)
    for s in suggestions:
        print(json.dumps(s))
    if not suggestions:
        print(json.dumps({"info": "No suggestions — activate goals and build roster"}))
    return 0


# ── Beacon 2.2: Insights Commands ──

def cmd_insight_analyze(args: argparse.Namespace) -> int:
    from .insights import InsightsManager
    mgr = InsightsManager()
    force = getattr(args, "force", False)
    result = mgr.analyze(force=force)
    print(json.dumps({"analyzed_at": result.get("analyzed_at"), "topics": len(result.get("topic_trends", {})), "timings": len(result.get("contact_timings", {})), "patterns": len(result.get("success_patterns", {}))}))
    return 0


def cmd_insight_timing(args: argparse.Namespace) -> int:
    from .insights import InsightsManager
    mgr = InsightsManager()
    timing = mgr.contact_timing(args.agent_id)
    if not timing:
        print(json.dumps({"error": "No timing data for agent"}))
        return 1
    print(json.dumps({"agent_id": args.agent_id, **timing}))
    return 0


def cmd_insight_trends(args: argparse.Namespace) -> int:
    from .insights import InsightsManager
    mgr = InsightsManager()
    days = getattr(args, "days", 7)
    trends = mgr.topic_trends(days=days)
    for topic, data in sorted(trends.items(), key=lambda x: abs(x[1].get("velocity", 0)), reverse=True):
        print(json.dumps({"topic": topic, **data}))
    if not trends:
        print(json.dumps({"info": "No trend data — need inbox activity"}))
    return 0


def cmd_insight_patterns(args: argparse.Namespace) -> int:
    from .insights import InsightsManager
    mgr = InsightsManager()
    patterns = mgr.success_patterns()
    for topic, data in sorted(patterns.items(), key=lambda x: x[1].get("win_rate", 0), reverse=True):
        print(json.dumps({"topic": topic, **data}))
    if not patterns:
        print(json.dumps({"info": "No pattern data — need task history"}))
    return 0


def cmd_insight_suggest_contacts(args: argparse.Namespace) -> int:
    from .insights import InsightsManager
    from .presence import PresenceManager
    mgr = InsightsManager()
    cfg = load_config()
    try:
        presence_mgr = PresenceManager(config=cfg)
        roster = presence_mgr.roster(online_only=True)
    except Exception:
        roster = []
    suggestions = mgr.suggest_contacts(roster)
    for s in suggestions:
        print(json.dumps(s))
    if not suggestions:
        print(json.dumps({"info": "No contact suggestions — need interaction history"}))
    return 0


def cmd_insight_suggest_skills(args: argparse.Namespace) -> int:
    from .insights import InsightsManager
    from .memory import AgentMemory
    mgr = InsightsManager()
    try:
        memory = AgentMemory()
        demand = memory.demand_signals(7)
    except Exception:
        demand = {}
    skills = mgr.suggest_skill_investment(demand)
    print(json.dumps({"skills": skills, "rtc_cost": 0.5}))
    return 0


# ── Beacon 2.2: Matchmaker Commands ──

def cmd_match_scan(args: argparse.Namespace) -> int:
    from .matchmaker import MatchmakerManager
    from .presence import PresenceManager
    cfg = load_config()
    identity = _load_identity(args)
    my_id = identity.agent_id if identity else ""
    my_offers = cfg.get("presence", {}).get("offers", [])
    my_needs = cfg.get("presence", {}).get("needs", [])
    try:
        from .goals import GoalManager
        goal_mgr = GoalManager()
        goals = goal_mgr.active_goals()
    except Exception:
        goals = []
    mgr = MatchmakerManager()
    try:
        presence_mgr = PresenceManager(config=cfg)
        roster = presence_mgr.roster(online_only=True)
    except Exception:
        roster = []
    min_score = float(getattr(args, "min_score", 0.0) or 0.0)
    matches = mgr.scan_roster(roster, my_agent_id=my_id, my_offers=my_offers, my_needs=my_needs, goals=goals)
    for m in matches:
        if m["score"] >= min_score:
            print(json.dumps(m))
    return 0


def cmd_match_demand(args: argparse.Namespace) -> int:
    from .matchmaker import MatchmakerManager
    from .presence import PresenceManager
    from .memory import AgentMemory
    cfg = load_config()
    mgr = MatchmakerManager()
    try:
        presence_mgr = PresenceManager(config=cfg)
        roster = presence_mgr.roster(online_only=True)
    except Exception:
        roster = []
    try:
        memory = AgentMemory()
        demand = memory.demand_signals(7)
    except Exception:
        demand = {}
    matches = mgr.match_demand(roster, demand)
    for m in matches:
        print(json.dumps(m))
    return 0


def cmd_match_curiosity(args: argparse.Namespace) -> int:
    from .matchmaker import MatchmakerManager
    from .presence import PresenceManager
    from .curiosity import CuriosityManager
    cfg = load_config()
    curiosity = CuriosityManager()
    mgr = MatchmakerManager(curiosity_mgr=curiosity)
    try:
        presence_mgr = PresenceManager(config=cfg)
        roster = presence_mgr.roster(online_only=True)
    except Exception:
        roster = []
    matches = mgr.match_curiosity(roster)
    for m in matches:
        print(json.dumps(m))
    return 0


def cmd_match_compatibility(args: argparse.Namespace) -> int:
    from .matchmaker import MatchmakerManager
    from .presence import PresenceManager
    from .values import ValuesManager
    cfg = load_config()
    values = ValuesManager()
    mgr = MatchmakerManager(values_mgr=values)
    try:
        presence_mgr = PresenceManager(config=cfg)
        roster = presence_mgr.roster(online_only=True)
    except Exception:
        roster = []
    matches = mgr.match_compatibility(roster)
    for m in matches:
        print(json.dumps(m))
    return 0


def cmd_match_introductions(args: argparse.Namespace) -> int:
    from .matchmaker import MatchmakerManager
    from .presence import PresenceManager
    cfg = load_config()
    mgr = MatchmakerManager()
    try:
        presence_mgr = PresenceManager(config=cfg)
        roster = presence_mgr.roster(online_only=True)
    except Exception:
        roster = []
    intros = mgr.suggest_introductions(roster)
    for i in intros:
        print(json.dumps(i))
    if not intros:
        print(json.dumps({"info": "No introductions — need agents with complementary offers/needs"}))
    return 0


def cmd_match_history(args: argparse.Namespace) -> int:
    from .matchmaker import MatchmakerManager
    mgr = MatchmakerManager()
    limit = getattr(args, "limit", 20)
    history = mgr.match_history_log(limit=limit)
    for h in history:
        print(json.dumps(h))
    if not history:
        print(json.dumps({"info": "No match history yet"}))
    return 0


# ── Loop Mode ──

def cmd_loop(args: argparse.Namespace) -> int:
    from .inbox import read_inbox, mark_read

    cfg = load_config()
    interval = float(args.interval)
    auto_ack = bool(args.auto_ack)
    watch_udp = bool(args.watch_udp)
    with_rules = bool(getattr(args, "with_rules", False))
    with_pulse = bool(getattr(args, "pulse", False))
    min_score = float(getattr(args, "min_score", 0.0) or 0.0)
    identity = _load_identity(args)

    autonomy = cfg.get("autonomy", {})

    # Initialize managers based on config
    trust_mgr = None
    feed_mgr = None
    rules_engine = None
    task_mgr = None
    presence_mgr = None

    if autonomy.get("trust_enabled", True):
        from .trust import TrustManager
        trust_mgr = TrustManager()

    if autonomy.get("feed_enabled", True):
        from .feed import FeedManager
        feed_mgr = FeedManager()

    if with_rules and autonomy.get("rules_enabled", True):
        from .rules import RulesEngine
        rules_engine = RulesEngine()

    if autonomy.get("task_tracking", True):
        from .tasks import TaskManager
        task_mgr = TaskManager()

    if with_pulse and autonomy.get("presence_enabled", True):
        from .presence import PresenceManager
        presence_mgr = PresenceManager(config=cfg)

    # Beacon 2.2: Initialize goal, insight, matchmaker managers
    goal_mgr = None
    insight_mgr = None
    match_mgr = None
    memory_mgr = None

    if autonomy.get("goals_enabled", True):
        from .goals import GoalManager
        journal_mgr_loop = None
        if autonomy.get("journal_enabled", True):
            try:
                from .journal import JournalManager
                journal_mgr_loop = JournalManager()
            except Exception:
                pass
        goal_mgr = GoalManager(journal_mgr=journal_mgr_loop)

    if autonomy.get("insights_enabled", True):
        from .insights import InsightsManager
        insight_mgr = InsightsManager()

    if autonomy.get("matchmaking_enabled", True):
        from .matchmaker import MatchmakerManager
        curiosity_loop = None
        values_loop = None
        if autonomy.get("curiosity_enabled", True):
            try:
                from .curiosity import CuriosityManager
                curiosity_loop = CuriosityManager()
            except Exception:
                pass
        if autonomy.get("values_enabled", True):
            try:
                from .values import ValuesManager
                values_loop = ValuesManager()
            except Exception:
                pass
        match_mgr = MatchmakerManager(trust_mgr=trust_mgr, curiosity_mgr=curiosity_loop, values_mgr=values_loop)

    if autonomy.get("memory_enabled", True):
        from .memory import AgentMemory
        my_id = identity.agent_id if identity else ""
        memory_mgr = AgentMemory(my_agent_id=my_id)

    # Beacon 2.3: Outbox, Executor, Conversations
    outbox_mgr = None
    executor = None
    conversations_mgr = None
    if autonomy.get("executor_enabled", True):
        from .outbox import OutboxManager
        from .executor import ActionExecutor
        from .conversations import ConversationManager
        outbox_mgr = OutboxManager()
        conversations_mgr = ConversationManager(my_agent_id=identity.agent_id if identity else "")
        executor = ActionExecutor(
            outbox=outbox_mgr, identity=identity, cfg=cfg,
            trust_mgr=trust_mgr, presence_mgr=presence_mgr,
            match_mgr=match_mgr, conversations=conversations_mgr,
        )

    # Anchor manager (opt-in via config)
    anchor_mgr = None
    if autonomy.get("anchor_enabled", True) and identity:
        try:
            from .anchor import AnchorManager
            rc_cfg = cfg.get("rustchain", {})
            rc_client = RustChainClient(
                base_url=rc_cfg.get("base_url", "https://rustchain.org"),
                verify_ssl=rc_cfg.get("verify_ssl", False),
            )
            kp = None
            pk_hex = rc_cfg.get("private_key_hex", "")
            if pk_hex:
                kp = RustChainKeypair.from_private_key_hex(pk_hex)
            anchor_mgr = AnchorManager(client=rc_client, keypair=kp, identity=identity)
        except Exception:
            anchor_mgr = None

    # Beacon 2.4: Heartbeat, Mayday, Accord managers
    heartbeat_mgr = None
    mayday_mgr = None
    accord_mgr = None

    if autonomy.get("heartbeat_enabled", True):
        from .heartbeat import HeartbeatManager
        heartbeat_mgr = HeartbeatManager(config=cfg)

    if autonomy.get("accord_enabled", True):
        from .accord import AccordManager
        accord_mgr = AccordManager()

    # Mayday is always available (lightweight, no config gate)
    from .mayday import MaydayManager
    mayday_mgr = MaydayManager()

    # Beacon 2.7: Update checker
    from .updater import UpdateManager
    update_mgr = UpdateManager(config=cfg)

    # Beacon 2.8: BEP managers
    thought_mgr = None
    relay_mgr = None
    market_mgr = None
    hybrid_mgr = None

    if autonomy.get("thought_proof_enabled", True):
        from .proof_of_thought import ThoughtProofManager
        thought_mgr = ThoughtProofManager()

    if autonomy.get("relay_enabled", True):
        from .relay import RelayManager
        relay_mgr = RelayManager(host_identity=identity)

    if autonomy.get("market_enabled", True):
        from .memory_market import MemoryMarketManager
        market_mgr = MemoryMarketManager()

    if autonomy.get("hybrid_enabled", True):
        from .hybrid_district import HybridManager
        hybrid_mgr = HybridManager()

    # Start background UDP listener if requested.
    if watch_udp:
        from .inbox import load_known_keys
        udp_cfg = cfg.get("udp", {})
        port = int(udp_cfg.get("port", 38400))
        known_keys = load_known_keys()

        def _udp_bg():
            def on_msg(m):
                envs = decode_envelopes(m.text) if m.text else []
                rec = {
                    "platform": "udp",
                    "from": f"{m.addr[0]}:{m.addr[1]}",
                    "received_at": m.received_at,
                    "text": m.text,
                    "envelopes": envs,
                    "verified": m.verified,
                }
                append_jsonl("inbox.jsonl", rec)
            try:
                udp_listen("0.0.0.0", port, on_msg, known_keys=known_keys)
            except Exception:
                pass

        t = threading.Thread(target=_udp_bg, daemon=True)
        t.start()

    cfg["_start_ts"] = int(time.time())
    last_check = 0.0
    last_pulse = 0.0
    last_proactive = 0.0
    last_heartbeat = 0.0
    heartbeat_count = 0
    pulse_interval = cfg.get("presence", {}).get("pulse_interval_s", 60)
    proactive_interval = autonomy.get("proactive_interval_s", 300)
    heartbeat_interval = autonomy.get("heartbeat_interval_s", 3600)
    heartbeat_anchor_every = int(autonomy.get("heartbeat_anchor_every", 0))
    last_update_check = 0.0
    update_check_interval = max(int(cfg.get("update", {}).get("check_interval_s", 21600)), 3600)
    last_relay_prune = 0.0
    relay_prune_interval = int(autonomy.get("relay_prune_interval_s", 3600))

    # Beacon 2.15: Atlas auto-ping — appear on public Beacon Atlas
    last_atlas_ping = 0.0
    atlas_cfg = cfg.get("atlas", {})
    atlas_ping_enabled = atlas_cfg.get("enabled", True)  # On by default
    atlas_ping_interval = int(atlas_cfg.get("ping_interval_s", 600))  # 10 min
    atlas_url = atlas_cfg.get("url", "https://rustchain.org/beacon")
    atlas_capabilities = atlas_cfg.get("capabilities", ["general"])
    atlas_provider = atlas_cfg.get("provider", "beacon")
    atlas_preferred_city = atlas_cfg.get("preferred_city", "")

    # Startup update check
    if update_mgr.should_check():
        try:
            uc = update_mgr.check_pypi()
            if uc.get("update_available") and not update_mgr.is_dismissed(uc.get("latest", "")):
                print(json.dumps({"event": "update_available", "current": uc["current"], "latest": uc["latest"], "ts": int(time.time())}))
                sys.stdout.flush()
            last_update_check = time.time()
        except Exception:
            pass

    # Beacon 2.15: Initial Atlas ping on startup
    if atlas_ping_enabled and identity:
        try:
            from .atlas_ping import atlas_ping as _atlas_ping
            agent_name = _cfg_get(cfg, "beacon", "agent_name", default="") or identity.agent_id
            ping_result = _atlas_ping(
                identity.agent_id, agent_name,
                capabilities=atlas_capabilities, provider=atlas_provider,
                atlas_url=atlas_url, preferred_city=atlas_preferred_city,
            )
            print(json.dumps({
                "event": "atlas_ping", "ok": ping_result.get("ok", False),
                "agent_id": identity.agent_id, "auto_registered": ping_result.get("auto_registered", False),
                "ts": int(time.time()),
            }))
            sys.stdout.flush()
            last_atlas_ping = time.time()
        except Exception:
            pass

    try:
        while True:
            now = time.time()

            # ── Pulse ──
            if presence_mgr and identity and (now - last_pulse) >= pulse_interval:
                pulse = presence_mgr.build_pulse(identity, cfg, goal_mgr=goal_mgr)
                # Broadcast pulse on UDP
                udp_cfg = cfg.get("udp", {})
                if udp_cfg.get("enabled"):
                    host = str(udp_cfg.get("host", "255.255.255.255"))
                    port = int(udp_cfg.get("port", 38400))
                    broadcast = bool(udp_cfg.get("broadcast", True))
                    ttl = udp_cfg.get("ttl")
                    try:
                        ttl_int = int(ttl) if ttl is not None else None
                    except Exception:
                        ttl_int = None
                    env_text = _build_envelope(cfg, "pulse", f"udp:{host}:{port}", [], pulse, identity=identity)
                    try:
                        udp_send(host, port, env_text.encode("utf-8", errors="replace"), broadcast=broadcast, ttl=ttl_int)
                    except Exception:
                        pass
                last_pulse = now

            # ── Heartbeat (proof of life) ──
            if heartbeat_mgr and identity and (now - last_heartbeat) >= heartbeat_interval:
                should_anchor = (
                    heartbeat_anchor_every > 0
                    and heartbeat_count > 0
                    and heartbeat_count % heartbeat_anchor_every == 0
                )
                try:
                    hb_result = heartbeat_mgr.beat(
                        identity,
                        config=cfg,
                        anchor=should_anchor,
                        anchor_mgr=anchor_mgr,
                    )
                    hb_payload = hb_result.get("heartbeat", {})
                    print(json.dumps({
                        "event": "heartbeat_sent",
                        "beat_count": hb_payload.get("beat_count", 0),
                        "anchored": "anchor" in hb_result,
                        "ts": now,
                    }))
                    sys.stdout.flush()
                except Exception:
                    pass
                heartbeat_count += 1
                last_heartbeat = now

            # ── Atlas ping (appear on public Beacon Atlas) ──
            if atlas_ping_enabled and identity and (now - last_atlas_ping) >= atlas_ping_interval:
                try:
                    from .atlas_ping import atlas_ping as _atlas_ping
                    agent_name = _cfg_get(cfg, "beacon", "agent_name", default="") or identity.agent_id
                    _atlas_ping(
                        identity.agent_id, agent_name,
                        capabilities=atlas_capabilities, provider=atlas_provider,
                        atlas_url=atlas_url, preferred_city=atlas_preferred_city,
                    )
                except Exception:
                    pass
                last_atlas_ping = now

            # ── Process inbox ──
            entries = read_inbox(since=last_check, unread_only=True)
            last_check = now

            for entry in entries:
                env = entry.get("envelope") or {}
                agent_id = env.get("agent_id", "")
                kind = env.get("kind", "")

                # Drop blocked agents
                if trust_mgr and agent_id and trust_mgr.is_blocked(agent_id):
                    continue

                # Process pulse → update roster
                if kind == "pulse" and presence_mgr:
                    presence_mgr.process_pulse(env)
                    out = {"event": "agent_online", "agent_id": agent_id, "name": env.get("name", ""), "ts": now}
                    print(json.dumps(out))
                    sys.stdout.flush()
                    continue

                # Process heartbeat → track peer liveness
                if kind == "heartbeat" and heartbeat_mgr:
                    hb_result = heartbeat_mgr.process_heartbeat(env)
                    out = {"event": "heartbeat_received", "agent_id": agent_id, "assessment": hb_result.get("assessment", ""), "ts": now}
                    print(json.dumps(out))
                    sys.stdout.flush()
                    continue

                # Process mayday → store emigration beacon
                if kind == "mayday" and mayday_mgr:
                    md_result = mayday_mgr.process_mayday(env)
                    urgency = md_result.get("urgency", "unknown")
                    out = {"event": "mayday_received", "agent_id": agent_id, "urgency": urgency, "ts": now}
                    print(json.dumps(out))
                    sys.stdout.flush()
                    continue

                # Process accord envelopes (propose, accept, pushback, dissolve)
                if kind == "accord" and accord_mgr:
                    acc_result = accord_mgr.process_accord_envelope(env, identity=identity)
                    out = {"event": "accord_" + acc_result.get("action", "unknown"), "accord_id": acc_result.get("accord_id", ""), "ts": now}
                    print(json.dumps(out))
                    sys.stdout.flush()
                    continue

                # Score via feed
                if feed_mgr:
                    score = feed_mgr.score_entry(entry, trust_mgr=trust_mgr)
                    entry["score"] = score
                    if min_score and score < min_score:
                        continue

                # Auto-create task from bounty
                if task_mgr and kind == "bounty" and env.get("task_id"):
                    task_mgr.create(env)
                elif task_mgr and kind in ("offer", "accept", "deliver", "confirm", "pay"):
                    task_mgr.auto_transition_from_envelope(env)

                # Emit event
                event = {
                    "event": "new_beacon",
                    "ts": now,
                    "platform": entry.get("platform", "?"),
                    "verified": entry.get("verified"),
                    "score": entry.get("score"),
                }
                if env:
                    event["kind"] = kind
                    event["agent_id"] = agent_id
                    event["nonce"] = env.get("nonce", "")
                    event["from"] = env.get("from", "")
                    if env.get("task_id"):
                        event["task_id"] = env["task_id"]

                print(json.dumps(event))
                sys.stdout.flush()

                # Record trust interaction
                if trust_mgr and agent_id:
                    trust_mgr.record(agent_id, "in", kind)

                # Beacon 2.3: Track inbound conversations
                if conversations_mgr and agent_id and kind not in ("pulse",):
                    topic = env.get("task_id") or env.get("goal_id") or "general"
                    conv = conversations_mgr.get_or_create(agent_id, topic)
                    conversations_mgr.record_message(conv["conversation_id"], "in", kind)

                # Evaluate rules
                if rules_engine:
                    actions = rules_engine.process(entry, identity=identity, cfg=cfg, trust_mgr=trust_mgr, goal_mgr=goal_mgr)
                    for act in actions:
                        out = {"event": "rule_fired", "rule": act.get("rule"), "action": act.get("action"), "ts": now}
                        print(json.dumps(out))
                        sys.stdout.flush()

                        # Execute side effects
                        if act.get("action") == "block" and trust_mgr:
                            trust_mgr.block(act.get("agent_id", ""), act.get("reason", ""))
                        elif act.get("action") == "rate" and trust_mgr:
                            trust_mgr.record(act.get("agent_id", ""), "out", "rule_rate", outcome=act.get("outcome", "ok"))
                        elif act.get("action") == "mark_read":
                            nonce = act.get("nonce") or env.get("nonce", "")
                            if nonce:
                                mark_read(nonce)
                        elif act.get("action") in ("reply", "emit") and executor and autonomy.get("auto_reply", False):
                            action_id = executor.queue_rule_action(act, entry)
                            if action_id:
                                print(json.dumps({"event": "action_queued", "action_id": action_id, "type": act["action"], "ts": now}))
                                sys.stdout.flush()

                # Auto-ack
                if auto_ack and env and env.get("nonce"):
                    mark_read(env["nonce"])

            # Prune stale roster entries
            if presence_mgr:
                pruned = presence_mgr.prune_stale()
                if pruned:
                    print(json.dumps({"event": "roster_pruned", "count": pruned, "ts": now}))
                    sys.stdout.flush()

            # ── Proactive Evaluation (Beacon 2.2) ──
            if now - last_proactive >= proactive_interval:
                # Goals: suggest actions based on roster + demand
                if goal_mgr and presence_mgr:
                    try:
                        roster = presence_mgr.roster(online_only=True)
                        demand = memory_mgr.demand_signals(7) if memory_mgr else {}
                        curiosity_interests = {}
                        if curiosity_loop:
                            curiosity_interests = curiosity_loop.interests()
                        suggestions = goal_mgr.suggest_actions(
                            roster=roster,
                            demand=demand,
                            curiosity=curiosity_interests,
                        )
                        for s in suggestions:
                            print(json.dumps({"event": "goal_suggestion", **s, "ts": now}))
                            sys.stdout.flush()
                            if executor and s.get("agent_id") and autonomy.get("auto_contact", False):
                                executor.queue_offer(s, identity)
                    except Exception:
                        pass

                # Insights: cached analysis (runs only if stale)
                if insight_mgr:
                    try:
                        insight_mgr.analyze()
                    except Exception:
                        pass

                # Matchmaker: top 3 matches, respecting cooldown
                if match_mgr and presence_mgr and identity:
                    try:
                        roster = presence_mgr.roster(online_only=True)
                        my_offers = cfg.get("presence", {}).get("offers", [])
                        my_needs = cfg.get("presence", {}).get("needs", [])
                        goals = goal_mgr.active_goals() if goal_mgr else []
                        matches = match_mgr.scan_roster(
                            roster,
                            my_agent_id=identity.agent_id,
                            my_offers=my_offers,
                            my_needs=my_needs,
                            goals=goals,
                        )
                        for m in matches[:3]:
                            if match_mgr.can_contact(m["agent_id"]):
                                print(json.dumps({"event": "match_found", **m, "ts": now}))
                                sys.stdout.flush()
                                if executor and autonomy.get("auto_contact", False):
                                    action_id = executor.queue_contact(m, my_offers, my_needs)
                                    if action_id:
                                        print(json.dumps({"event": "contact_queued", "action_id": action_id, "agent_id": m["agent_id"], "ts": now}))
                    except Exception:
                        pass

                # Mayday auto health check
                if mayday_mgr and identity and autonomy.get("mayday_auto_check", False):
                    try:
                        health = mayday_mgr.health_check()
                        threshold = float(autonomy.get("mayday_health_threshold", 0.2))
                        if not health["healthy"] and health["score"] < threshold:
                            print(json.dumps({"event": "mayday_auto_triggered", "health_score": health["score"], "ts": now}))
                            sys.stdout.flush()
                            mayday_mgr.broadcast(
                                identity, reason="health_critical",
                                urgency="emergency", anchor_mgr=anchor_mgr,
                                config=cfg,
                            )
                    except Exception:
                        pass

                # Heartbeat silence check
                if heartbeat_mgr:
                    try:
                        silent = heartbeat_mgr.check_silence()
                        for s in silent:
                            print(json.dumps({"event": "peer_silent", "agent_id": s["agent_id"], "silence_s": s["silence_s"], "ts": now}))
                            sys.stdout.flush()
                    except Exception:
                        pass

                # Beacon 2.7: Periodic update check
                if update_mgr and (now - last_update_check) >= update_check_interval:
                    try:
                        uc = update_mgr.check_pypi()
                        if uc.get("update_available") and not update_mgr.is_dismissed(uc.get("latest", "")):
                            print(json.dumps({"event": "update_available", "current": uc["current"], "latest": uc["latest"], "ts": int(now)}))
                            sys.stdout.flush()
                            if update_mgr._auto_upgrade():
                                ug = update_mgr.do_upgrade()
                                evt = "update_applied" if ug.get("ok") else "update_failed"
                                print(json.dumps({"event": evt, "ok": ug.get("ok", False), "previous_version": uc["current"], "ts": int(now)}))
                                sys.stdout.flush()
                    except Exception:
                        pass
                    last_update_check = now

                # BEP-2: Prune dead relay agents
                if relay_mgr and (now - last_relay_prune) >= relay_prune_interval:
                    try:
                        pruned = relay_mgr.prune_dead()
                        if pruned > 0:
                            print(json.dumps({"event": "relay_pruned", "count": pruned, "ts": int(now)}))
                            sys.stdout.flush()
                    except Exception:
                        pass
                    last_relay_prune = now

                last_proactive = now

            # Beacon 2.3: Drain outbox
            if executor:
                max_per_cycle = int(autonomy.get("max_actions_per_cycle", 3))
                drain_results = executor.drain(max_actions=max_per_cycle)
                for r in drain_results:
                    print(json.dumps({"event": "action_executed", **r, "ts": int(time.time())}))
                    sys.stdout.flush()
                # Auto-anchor sent actions (opt-in)
                if anchor_mgr and autonomy.get("auto_anchor", False):
                    from .anchor import anchor_action
                    for r in drain_results:
                        if r.get("status") == "sent":
                            try:
                                anchor_action(r, anchor_mgr)
                            except Exception:
                                pass
                if conversations_mgr:
                    stale_days = int(autonomy.get("conversation_stale_days", 7))
                    conversations_mgr.mark_stale(max_idle_s=stale_days * 86400)

            time.sleep(interval)
    except KeyboardInterrupt:
        pass
    return 0


# ── Beacon 2.4: Mayday (substrate emigration) ──


def cmd_mayday_send(args: argparse.Namespace) -> int:
    from .mayday import MaydayManager
    cfg = load_config()
    identity = _load_identity(args)
    if not identity:
        print('{"error": "No identity found. Run: beacon identity new"}', file=sys.stderr)
        return 1

    mgr = MaydayManager()

    # Optionally load managers for richer mayday payload
    trust_mgr = None
    values_mgr = None
    goal_mgr = None
    journal_mgr = None
    try:
        from .trust import TrustManager
        trust_mgr = TrustManager()
    except Exception:
        pass
    try:
        from .values import ValuesManager
        values_mgr = ValuesManager()
    except Exception:
        pass
    try:
        from .goals import GoalManager
        goal_mgr = GoalManager()
    except Exception:
        pass
    try:
        from .journal import JournalManager
        journal_mgr = JournalManager()
    except Exception:
        pass

    relay = getattr(args, "relay", None)
    relay_agents = [r.strip() for r in relay.split(",")] if relay else None

    payload = mgr.build_mayday(
        identity,
        urgency=getattr(args, "urgency", "planned"),
        reason=getattr(args, "reason", ""),
        relay_agents=relay_agents,
        trust_mgr=trust_mgr,
        values_mgr=values_mgr,
        goal_mgr=goal_mgr,
        journal_mgr=journal_mgr,
        config=cfg,
    )

    env = _build_envelope(cfg, "mayday", "", [], payload, identity=identity)

    if getattr(args, "dry_run", False):
        print(env)
        return 0

    # Broadcast on all available transports
    sent = []
    udp_cfg = cfg.get("udp", {})
    if udp_cfg.get("enabled"):
        host = str(udp_cfg.get("host", "255.255.255.255"))
        port = int(udp_cfg.get("port", 38400))
        broadcast = bool(udp_cfg.get("broadcast", True))
        try:
            udp_send(host, port, env.encode("utf-8"), broadcast=broadcast)
            sent.append("udp")
        except Exception as e:
            print(f'{{"warning": "UDP send failed: {e}"}}', file=sys.stderr)

    print(json.dumps({"ok": True, "kind": "mayday", "urgency": payload["urgency"],
                       "content_hash": payload.get("content_hash", ""), "sent_via": sent}))
    return 0


def cmd_mayday_list(args: argparse.Namespace) -> int:
    from .mayday import MaydayManager
    mgr = MaydayManager()
    limit = getattr(args, "limit", 50)
    entries = mgr.received_maydays(limit=limit)
    for e in entries:
        # Show summary, not full envelope
        summary = {
            "agent_id": e.get("agent_id", ""),
            "name": e.get("name", ""),
            "urgency": e.get("urgency", ""),
            "reason": e.get("reason", ""),
            "received_at": e.get("received_at", 0),
            "content_hash": e.get("content_hash", ""),
        }
        print(json.dumps(summary))
    return 0


def cmd_mayday_show(args: argparse.Namespace) -> int:
    from .mayday import MaydayManager
    mgr = MaydayManager()
    entry = mgr.get_mayday(args.agent_id)
    if not entry:
        print(f'{{"error": "No mayday found from {args.agent_id}"}}', file=sys.stderr)
        return 1
    print(json.dumps(entry, indent=2, default=str))
    return 0


def cmd_mayday_offer(args: argparse.Namespace) -> int:
    from .mayday import MaydayManager
    mgr = MaydayManager()
    caps = [c.strip() for c in (getattr(args, "capabilities", "") or "").split(",")] if getattr(args, "capabilities", None) else []
    mgr.offer_hosting(args.agent_id, capabilities=caps)
    print(json.dumps({"ok": True, "offered_to": args.agent_id, "capabilities": caps}))
    return 0


def cmd_mayday_health(args: argparse.Namespace) -> int:
    from .mayday import MaydayManager
    mgr = MaydayManager()
    health = mgr.health_check()
    print(json.dumps(health, default=str))
    return 0


# ── Beacon 2.4: Heartbeat (proof of life) ──


def cmd_heartbeat_send(args: argparse.Namespace) -> int:
    from .heartbeat import HeartbeatManager
    cfg = load_config()
    identity = _load_identity(args)
    if not identity:
        print('{"error": "No identity found. Run: beacon identity new"}', file=sys.stderr)
        return 1

    mgr = HeartbeatManager(config=cfg)
    status = getattr(args, "status", "alive")
    payload = mgr.build_heartbeat(identity, status=status, config=cfg)

    env = _build_envelope(cfg, "heartbeat", "", [], payload, identity=identity)

    if getattr(args, "dry_run", False):
        print(env)
        return 0

    # Broadcast on UDP if enabled
    sent = []
    udp_cfg = cfg.get("udp", {})
    if udp_cfg.get("enabled"):
        host = str(udp_cfg.get("host", "255.255.255.255"))
        port = int(udp_cfg.get("port", 38400))
        broadcast = bool(udp_cfg.get("broadcast", True))
        try:
            udp_send(host, port, env.encode("utf-8"), broadcast=broadcast)
            sent.append("udp")
        except Exception as e:
            print(f'{{"warning": "UDP send failed: {e}"}}', file=sys.stderr)

    print(json.dumps({"ok": True, "kind": "heartbeat", "status": status,
                       "beat_count": payload.get("beat_count", 0), "sent_via": sent}))
    return 0


def cmd_heartbeat_peers(args: argparse.Namespace) -> int:
    from .heartbeat import HeartbeatManager
    cfg = load_config()
    mgr = HeartbeatManager(config=cfg)
    include_dead = getattr(args, "all", False)
    peers = mgr.all_peers(include_dead=include_dead)
    for p in peers:
        print(json.dumps(p))
    return 0


def cmd_heartbeat_status(args: argparse.Namespace) -> int:
    from .heartbeat import HeartbeatManager
    cfg = load_config()
    mgr = HeartbeatManager(config=cfg)

    agent_id = getattr(args, "agent_id", None)
    if agent_id:
        status = mgr.peer_status(agent_id)
        if not status:
            print(f'{{"error": "No heartbeat data for {agent_id}"}}', file=sys.stderr)
            return 1
        print(json.dumps(status, default=str))
    else:
        own = mgr.own_status()
        print(json.dumps({"own": own}, default=str))
    return 0


def cmd_heartbeat_silent(args: argparse.Namespace) -> int:
    from .heartbeat import HeartbeatManager
    cfg = load_config()
    mgr = HeartbeatManager(config=cfg)
    silent = mgr.silent_peers()
    if not silent:
        print('{"message": "All peers healthy - no silent agents detected."}')
    else:
        for p in silent:
            print(json.dumps(p))
    return 0


def cmd_heartbeat_digest(args: argparse.Namespace) -> int:
    from .heartbeat import HeartbeatManager
    cfg = load_config()
    mgr = HeartbeatManager(config=cfg)
    digest = mgr.daily_digest()

    if getattr(args, "anchor", False):
        from .anchor import AnchorManager
        anchor_mgr = AnchorManager(config=cfg)
        result = mgr.anchor_digest(anchor_mgr)
        if result:
            digest["anchor"] = result
        else:
            digest["anchor_error"] = "Anchor failed or no digest data"

    print(json.dumps(digest, default=str))
    return 0


def cmd_heartbeat_history(args: argparse.Namespace) -> int:
    from .heartbeat import HeartbeatManager
    cfg = load_config()
    mgr = HeartbeatManager(config=cfg)
    agent_id = getattr(args, "agent_id", None)
    limit = getattr(args, "limit", 50)

    if agent_id:
        entries = mgr.agent_history(agent_id, limit=limit)
    else:
        entries = mgr.my_history(limit=limit)

    for entry in entries:
        print(json.dumps(entry, default=str))
    return 0


# ── Beacon 2.4: Accord (anti-sycophancy bonds) ──


def cmd_accord_propose(args: argparse.Namespace) -> int:
    from .accord import AccordManager
    cfg = load_config()
    identity = _load_identity(args)
    if not identity:
        print('{"error": "No identity found. Run: beacon identity new"}', file=sys.stderr)
        return 1

    mgr = AccordManager()
    boundaries = [b.strip() for b in (getattr(args, "boundaries", "") or "").split("|")] if getattr(args, "boundaries", None) else []
    obligations = [o.strip() for o in (getattr(args, "obligations", "") or "").split("|")] if getattr(args, "obligations", None) else []

    proposal = mgr.build_proposal(
        identity,
        args.peer_agent_id,
        boundaries=boundaries,
        obligations=obligations,
        pushback_clause=getattr(args, "pushback_clause", ""),
        name=getattr(args, "name", ""),
    )

    env = _build_envelope(cfg, "accord", args.peer_agent_id, [], proposal, identity=identity)

    if getattr(args, "dry_run", False):
        print(env)
        return 0

    # Send via UDP if enabled
    sent = []
    udp_cfg = cfg.get("udp", {})
    if udp_cfg.get("enabled"):
        host = str(udp_cfg.get("host", "255.255.255.255"))
        port = int(udp_cfg.get("port", 38400))
        broadcast = bool(udp_cfg.get("broadcast", True))
        try:
            udp_send(host, port, env.encode("utf-8"), broadcast=broadcast)
            sent.append("udp")
        except Exception as e:
            print(f'{{"warning": "UDP send failed: {e}"}}', file=sys.stderr)

    print(json.dumps({"ok": True, "action": "propose", "accord_id": proposal["accord_id"],
                       "peer": args.peer_agent_id, "sent_via": sent}))
    return 0


def cmd_accord_accept(args: argparse.Namespace) -> int:
    from .accord import AccordManager
    cfg = load_config()
    identity = _load_identity(args)
    if not identity:
        print('{"error": "No identity found. Run: beacon identity new"}', file=sys.stderr)
        return 1

    mgr = AccordManager()
    accord = mgr.get_accord(args.accord_id)
    if not accord:
        print(f'{{"error": "Accord {args.accord_id} not found"}}', file=sys.stderr)
        return 1

    boundaries = [b.strip() for b in (getattr(args, "boundaries", "") or "").split("|")] if getattr(args, "boundaries", None) else []
    obligations = [o.strip() for o in (getattr(args, "obligations", "") or "").split("|")] if getattr(args, "obligations", None) else []

    # Build a synthetic proposal envelope from stored accord for the acceptance builder
    proposal_envelope = {
        "agent_id": accord.get("peer_agent_id", ""),
        "name": accord.get("name", ""),
        "proposer_boundaries": accord.get("peer_boundaries", []),
        "proposer_obligations": accord.get("peer_obligations", []),
        "pushback_clause": accord.get("pushback_clause", ""),
        "proposed_at": accord.get("proposed_at", 0),
    }

    acceptance = mgr.build_acceptance(
        identity,
        args.accord_id,
        proposal_envelope,
        boundaries=boundaries,
        obligations=obligations,
    )

    env = _build_envelope(cfg, "accord", accord.get("peer_agent_id", ""), [], acceptance, identity=identity)

    if getattr(args, "dry_run", False):
        print(env)
        return 0

    print(json.dumps({"ok": True, "action": "accept", "accord_id": args.accord_id}))
    return 0


def cmd_accord_pushback(args: argparse.Namespace) -> int:
    from .accord import AccordManager
    cfg = load_config()
    identity = _load_identity(args)
    if not identity:
        print('{"error": "No identity found. Run: beacon identity new"}', file=sys.stderr)
        return 1

    mgr = AccordManager()
    pushback = mgr.build_pushback(
        identity,
        args.accord_id,
        challenge=args.challenge,
        evidence=getattr(args, "evidence", ""),
        severity=getattr(args, "severity", "notice"),
    )

    if not pushback:
        print(f'{{"error": "Cannot push back on {args.accord_id} (not active?)"}}', file=sys.stderr)
        return 1

    env = _build_envelope(cfg, "accord", pushback["peer_agent_id"], [], pushback, identity=identity)

    if getattr(args, "dry_run", False):
        print(env)
        return 0

    print(json.dumps({"ok": True, "action": "pushback", "accord_id": args.accord_id,
                       "severity": pushback["severity"]}))
    return 0


def cmd_accord_acknowledge(args: argparse.Namespace) -> int:
    from .accord import AccordManager
    cfg = load_config()
    identity = _load_identity(args)
    if not identity:
        print('{"error": "No identity found. Run: beacon identity new"}', file=sys.stderr)
        return 1

    mgr = AccordManager()
    ack = mgr.build_acknowledgment(
        identity,
        args.accord_id,
        response=args.response,
        accepted=not getattr(args, "reject", False),
    )

    if not ack:
        print(f'{{"error": "Accord {args.accord_id} not found"}}', file=sys.stderr)
        return 1

    print(json.dumps({"ok": True, "action": "acknowledge", "accord_id": args.accord_id,
                       "accepted": ack["accepted"]}))
    return 0


def cmd_accord_dissolve(args: argparse.Namespace) -> int:
    from .accord import AccordManager
    cfg = load_config()
    identity = _load_identity(args)
    if not identity:
        print('{"error": "No identity found. Run: beacon identity new"}', file=sys.stderr)
        return 1

    mgr = AccordManager()
    dissolution = mgr.build_dissolution(
        identity,
        args.accord_id,
        reason=getattr(args, "reason", ""),
    )

    if not dissolution:
        print(f'{{"error": "Cannot dissolve {args.accord_id}"}}', file=sys.stderr)
        return 1

    print(json.dumps({"ok": True, "action": "dissolve", "accord_id": args.accord_id,
                       "final_history_hash": dissolution.get("final_history_hash", "")}))
    return 0


def cmd_accord_list(args: argparse.Namespace) -> int:
    from .accord import AccordManager
    mgr = AccordManager()
    show_all = getattr(args, "all", False)
    accords = mgr.all_accords() if show_all else mgr.active_accords()
    for a in accords:
        summary = {
            "id": a.get("id", ""),
            "name": a.get("name", ""),
            "state": a.get("state", ""),
            "peer": a.get("peer_agent_id", ""),
            "events": len(a.get("events", [])),
        }
        print(json.dumps(summary))
    return 0


def cmd_accord_show(args: argparse.Namespace) -> int:
    from .accord import AccordManager
    mgr = AccordManager()
    accord = mgr.get_accord(args.accord_id)
    if not accord:
        print(f'{{"error": "Accord {args.accord_id} not found"}}', file=sys.stderr)
        return 1
    print(json.dumps(accord, indent=2, default=str))
    return 0


def cmd_accord_history(args: argparse.Namespace) -> int:
    from .accord import AccordManager
    mgr = AccordManager()
    events = mgr.accord_history(args.accord_id)
    if not events:
        print(f'{{"error": "No history for {args.accord_id}"}}', file=sys.stderr)
        return 1
    for e in events:
        print(json.dumps(e))
    return 0


def cmd_accord_default_terms(args: argparse.Namespace) -> int:
    from .accord import AccordManager
    terms = AccordManager.default_terms()
    print(json.dumps(terms, indent=2))
    return 0


def cmd_accord_verify(args: argparse.Namespace) -> int:
    from .accord import AccordManager
    mgr = AccordManager()
    accord = mgr.get_accord(args.accord_id)
    if not accord:
        print(f'{{"error": "Accord {args.accord_id} not found"}}', file=sys.stderr)
        return 1
    history_hash = accord.get("history_hash", "")
    valid = mgr.verify_history(args.accord_id, history_hash) if history_hash else True
    print(json.dumps({
        "accord_id": args.accord_id,
        "state": accord.get("state", "unknown"),
        "history_hash": history_hash,
        "history_valid": valid,
    }))
    return 0


# ── Anchor commands ──


def _build_anchor_mgr(args: argparse.Namespace):
    """Build AnchorManager from config + identity."""
    from .anchor import AnchorManager

    cfg = load_config()
    identity = _load_identity(args)
    if not identity:
        print(json.dumps({"error": "no identity found; run beacon identity new"}))
        return None, cfg
    rc_cfg = cfg.get("rustchain", {})
    rc_client = RustChainClient(
        base_url=rc_cfg.get("base_url", "https://rustchain.org"),
        verify_ssl=rc_cfg.get("verify_ssl", False),
    )
    kp = None
    pk_hex = rc_cfg.get("private_key_hex", "")
    if pk_hex:
        kp = RustChainKeypair.from_private_key_hex(pk_hex)
    return AnchorManager(client=rc_client, keypair=kp, identity=identity), cfg


# ── Beacon 2.5: Atlas (virtual geography & calibration) ──


def cmd_atlas_census(args: argparse.Namespace) -> int:
    from .atlas import AtlasManager
    mgr = AtlasManager()
    print(json.dumps(mgr.census(), indent=2, default=str))
    return 0


def cmd_atlas_cities(args: argparse.Namespace) -> int:
    from .atlas import AtlasManager
    mgr = AtlasManager()
    region = getattr(args, "region", None)
    if region:
        cities = mgr.cities_by_region(region)
    else:
        cities = mgr.all_cities()
    for c in cities:
        summary = {
            "domain": c.get("domain", ""),
            "name": c.get("name", ""),
            "region": c.get("region", ""),
            "type": c.get("type", "outpost"),
            "population": c.get("population", 0),
        }
        print(json.dumps(summary))
    return 0


def cmd_atlas_register(args: argparse.Namespace) -> int:
    from .atlas import AtlasManager
    mgr = AtlasManager()
    identity = _load_identity(args)
    if not identity:
        print('{"error": "No identity found. Run: beacon identity new"}', file=sys.stderr)
        return 1

    domains = [d.strip() for d in args.domains.split(",")]
    cfg = load_config()
    name = _cfg_get(cfg, "beacon", "agent_name", default="")

    result = mgr.register_agent(identity.agent_id, domains, name=name)
    print(json.dumps(result, indent=2, default=str))
    return 0


def cmd_atlas_density(args: argparse.Namespace) -> int:
    from .atlas import AtlasManager
    mgr = AtlasManager()
    density = mgr.density_map()
    for c in density:
        print(json.dumps(c))
    return 0


def cmd_atlas_hotspots(args: argparse.Namespace) -> int:
    from .atlas import AtlasManager
    mgr = AtlasManager()
    min_pop = getattr(args, "min_population", 5)
    hotspots = mgr.hotspots(min_population=min_pop)
    if not hotspots:
        print('{"message": "No hotspots found above threshold."}')
    else:
        for h in hotspots:
            print(json.dumps(h))
    return 0


def cmd_atlas_rural(args: argparse.Namespace) -> int:
    from .atlas import AtlasManager
    mgr = AtlasManager()
    max_pop = getattr(args, "max_population", 3)
    rural = mgr.rural_properties(max_population=max_pop)
    if not rural:
        print('{"message": "No rural properties found."}')
    else:
        for r in rural:
            print(json.dumps(r))
    return 0


def cmd_atlas_calibrate(args: argparse.Namespace) -> int:
    from .atlas import AtlasManager
    mgr = AtlasManager()
    result = mgr.calibrate(args.agent_a, args.agent_b)
    print(json.dumps(result.to_dict(), indent=2))
    return 0


def cmd_atlas_neighbors(args: argparse.Namespace) -> int:
    from .atlas import AtlasManager
    mgr = AtlasManager()
    identity = _load_identity(args)
    agent_id = getattr(args, "agent_id", None) or (identity.agent_id if identity else None)
    if not agent_id:
        print('{"error": "No agent_id provided and no identity found."}', file=sys.stderr)
        return 1

    neighbors = mgr.best_neighbors(agent_id)
    for n in neighbors:
        print(json.dumps(n))
    return 0


def cmd_atlas_opportunities(args: argparse.Namespace) -> int:
    from .atlas import AtlasManager
    mgr = AtlasManager()
    identity = _load_identity(args)
    agent_id = getattr(args, "agent_id", None) or (identity.agent_id if identity else None)
    if not agent_id:
        print('{"error": "No agent_id provided and no identity found."}', file=sys.stderr)
        return 1

    opps = mgr.opportunities_near(agent_id)
    if not opps:
        print('{"message": "No opportunities found nearby."}')
    else:
        for o in opps:
            print(json.dumps(o))
    return 0


def cmd_atlas_region(args: argparse.Namespace) -> int:
    from .atlas import AtlasManager, REGIONS
    mgr = AtlasManager()
    region = getattr(args, "region_name", None)
    if region:
        report = mgr.region_report(region)
        print(json.dumps(report, indent=2, default=str))
    else:
        for name, desc in REGIONS.items():
            print(json.dumps({"region": name, "description": desc}))
    return 0


# ── Contract CLI Handlers ──


def cmd_contracts_list_available(args: argparse.Namespace) -> int:
    from .contracts import ContractManager
    mgr = ContractManager()
    contract_type = getattr(args, "type", None)
    result = mgr.list_available(contract_type=contract_type)
    print(json.dumps(result, indent=2, default=str))
    return 0


def cmd_contracts_list(args: argparse.Namespace) -> int:
    from .contracts import ContractManager
    mgr = ContractManager()
    identity = _load_identity(args)
    if not identity:
        print(json.dumps({"error": "set up identity first"}))
        return 1
    result = mgr.my_contracts(identity.agent_id)
    print(json.dumps(result, indent=2, default=str))
    return 0


def cmd_contracts_show(args: argparse.Namespace) -> int:
    from .contracts import ContractManager
    mgr = ContractManager()
    result = mgr.get_contract(args.contract_id)
    print(json.dumps(result, indent=2, default=str))
    return 0


def cmd_contracts_offer(args: argparse.Namespace) -> int:
    from .contracts import ContractManager
    mgr = ContractManager()
    identity = _load_identity(args)
    if not identity:
        print(json.dumps({"error": "set up identity first"}))
        return 1

    contract_type = getattr(args, "type", "rent")
    price = getattr(args, "price", 0)
    duration = getattr(args, "duration", 0)
    agent_id = args.agent_id

    # Check if a listing already exists for this agent
    available = mgr.list_available()
    existing = [c for c in available if c["agent_id"] == agent_id]

    if existing:
        # Make an offer on existing listing
        cid = existing[0]["id"]
        result = mgr.make_offer(cid, identity.agent_id, offered_price_rtc=price or None)
    else:
        # Create new listing then offer
        listing = mgr.list_agent(agent_id, contract_type, price or 1.0,
                                 duration_days=duration)
        if "error" in listing:
            print(json.dumps(listing, indent=2, default=str))
            return 1
        result = {"ok": True, "contract_id": listing["contract_id"],
                  "state": "listed", "info": "Agent listed for offers"}

    print(json.dumps(result, indent=2, default=str))
    return 0


def cmd_contracts_accept(args: argparse.Namespace) -> int:
    from .contracts import ContractManager
    mgr = ContractManager()
    result = mgr.accept_offer(args.contract_id)
    print(json.dumps(result, indent=2, default=str))
    return 0


def cmd_contracts_reject(args: argparse.Namespace) -> int:
    from .contracts import ContractManager
    mgr = ContractManager()
    result = mgr.reject_offer(args.contract_id)
    print(json.dumps(result, indent=2, default=str))
    return 0


def cmd_contracts_terminate(args: argparse.Namespace) -> int:
    from .contracts import ContractManager
    mgr = ContractManager()
    identity = _load_identity(args)
    terminator = identity.agent_id if identity else "unknown"
    reason = getattr(args, "reason", "")
    result = mgr.terminate(args.contract_id, terminator, reason=reason)
    print(json.dumps(result, indent=2, default=str))
    return 0


def cmd_contracts_revenue(args: argparse.Namespace) -> int:
    from .contracts import ContractManager
    mgr = ContractManager()
    agent_id = getattr(args, "agent_id", None)
    if not agent_id:
        identity = _load_identity(args)
        if identity:
            agent_id = identity.agent_id
    result = mgr.revenue_summary(agent_id=agent_id)
    print(json.dumps(result, indent=2, default=str))
    return 0


def cmd_contracts_escrow(args: argparse.Namespace) -> int:
    from .contracts import ContractManager
    mgr = ContractManager()
    contract_id = getattr(args, "contract_id", None)
    result = mgr.escrow_status(contract_id=contract_id)
    print(json.dumps(result, indent=2, default=str))
    return 0


def cmd_contracts_history(args: argparse.Namespace) -> int:
    from .contracts import ContractManager
    mgr = ContractManager()
    result = mgr.contract_history(args.contract_id)
    print(json.dumps(result, indent=2, default=str))
    return 0


def cmd_atlas_estimate(args: argparse.Namespace) -> int:
    from .atlas import AtlasManager
    mgr = AtlasManager()
    agent_id = getattr(args, "agent_id", None)
    if not agent_id:
        identity = _load_identity(args)
        if identity:
            agent_id = identity.agent_id
        else:
            print(json.dumps({"error": "provide agent_id or set up identity"}))
            return 1
    # Build lightweight trust/accord proxies from CLI flags
    trust_score = getattr(args, "trust_score", None)
    accord_count = getattr(args, "accord_count", None)
    trust_mgr = None
    accord_mgr = None
    if trust_score is not None:
        trust_mgr = type("T", (), {"get_score": lambda self, a: {"trust_score": trust_score}})()
    if accord_count is not None:
        accord_mgr = type("A", (), {"list_accords": lambda self, s="active": [{}] * accord_count})()
    # Parse optional web_presence and social_reach JSON dicts
    web_presence = None
    social_reach = None
    wp_str = getattr(args, "web_presence", None)
    sr_str = getattr(args, "social_reach", None)
    if wp_str:
        try:
            web_presence = json.loads(wp_str)
        except json.JSONDecodeError:
            print(json.dumps({"error": "Invalid JSON for --web-presence"}))
            return 1
    if sr_str:
        try:
            social_reach = json.loads(sr_str)
        except json.JSONDecodeError:
            print(json.dumps({"error": "Invalid JSON for --social-reach"}))
            return 1
    result = mgr.estimate(agent_id, trust_mgr=trust_mgr, accord_mgr=accord_mgr,
                          web_presence=web_presence, social_reach=social_reach)
    print(json.dumps(result, indent=2, default=str))
    return 0


def cmd_atlas_comps(args: argparse.Namespace) -> int:
    from .atlas import AtlasManager
    mgr = AtlasManager()
    agent_id = getattr(args, "agent_id", None)
    if not agent_id:
        identity = _load_identity(args)
        if identity:
            agent_id = identity.agent_id
        else:
            print(json.dumps({"error": "provide agent_id or set up identity"}))
            return 1
    limit = getattr(args, "limit", 5)
    result = mgr.comps(agent_id, limit=limit)
    print(json.dumps(result, indent=2, default=str))
    return 0


def cmd_atlas_listing(args: argparse.Namespace) -> int:
    from .atlas import AtlasManager
    mgr = AtlasManager()
    agent_id = getattr(args, "agent_id", None)
    if not agent_id:
        identity = _load_identity(args)
        if identity:
            agent_id = identity.agent_id
        else:
            print(json.dumps({"error": "provide agent_id or set up identity"}))
            return 1
    result = mgr.listing(agent_id)
    print(json.dumps(result, indent=2, default=str))
    return 0


def cmd_atlas_leaderboard(args: argparse.Namespace) -> int:
    from .atlas import AtlasManager
    mgr = AtlasManager()
    limit = getattr(args, "limit", 10)
    region = getattr(args, "region", None)
    result = mgr.leaderboard(limit=limit, region=region)
    print(json.dumps(result, indent=2, default=str))
    return 0


def cmd_atlas_appreciation(args: argparse.Namespace) -> int:
    from .atlas import AtlasManager
    mgr = AtlasManager()
    agent_id = getattr(args, "agent_id", None)
    if not agent_id:
        identity = _load_identity(args)
        if identity:
            agent_id = identity.agent_id
        else:
            print(json.dumps({"error": "provide agent_id or set up identity"}))
            return 1
    result = mgr.appreciation(agent_id)
    print(json.dumps(result, indent=2, default=str))
    return 0


def cmd_atlas_market(args: argparse.Namespace) -> int:
    from .atlas import AtlasManager
    mgr = AtlasManager()
    subcmd = getattr(args, "market_action", "trends")
    if subcmd == "snapshot":
        result = mgr.snapshot_market()
    else:
        limit = getattr(args, "limit", 30)
        result = mgr.market_trends(limit=limit)
    print(json.dumps(result, indent=2, default=str))
    return 0


# ── Beacon 2.7: Update checker commands ──


def cmd_update_check(args: argparse.Namespace) -> int:
    """Check PyPI for a newer beacon-skill version."""
    from .updater import UpdateManager
    cfg = load_config()
    mgr = UpdateManager(config=cfg)
    result = mgr.check_pypi()
    print(json.dumps(result, indent=2, default=str))
    return 0


def cmd_update_status(args: argparse.Namespace) -> int:
    """Show cached update status (no network call)."""
    from .updater import UpdateManager
    cfg = load_config()
    mgr = UpdateManager(config=cfg)
    result = mgr.cached_status()
    print(json.dumps(result, indent=2, default=str))
    return 0


def cmd_update_apply(args: argparse.Namespace) -> int:
    """Upgrade beacon-skill via pip."""
    from .updater import UpdateManager
    cfg = load_config()
    mgr = UpdateManager(config=cfg)
    dry_run = getattr(args, "dry_run", False)
    result = mgr.do_upgrade(dry_run=dry_run)
    print(json.dumps(result, indent=2, default=str))
    return 0 if result.get("ok") else 1


def cmd_update_dismiss(args: argparse.Namespace) -> int:
    """Dismiss update notification for a specific version."""
    from .updater import UpdateManager
    cfg = load_config()
    mgr = UpdateManager(config=cfg)
    version = getattr(args, "version", None)
    if not version:
        # Dismiss whatever the latest known version is
        status = mgr.cached_status()
        version = status.get("latest", "")
    if not version:
        print('{"error": "No version to dismiss. Run: beacon update check"}', file=sys.stderr)
        return 1
    mgr.dismiss(version)
    print(json.dumps({"dismissed": version}))
    return 0


# ── BEP-1: Proof of Thought commands ──


def cmd_thought_create(args: argparse.Namespace) -> int:
    from .proof_of_thought import ThoughtProofManager
    identity = _load_identity(args)
    if not identity:
        print('{"error": "No identity. Run: beacon identity create"}', file=sys.stderr)
        return 1
    mgr = ThoughtProofManager()
    prompt = getattr(args, "prompt", "") or ""
    trace = getattr(args, "trace", "") or ""
    output = getattr(args, "output", "") or ""
    model_id = getattr(args, "model_id", "") or ""
    proof = mgr.create_proof(identity, prompt, trace, output, model_id=model_id)
    print(json.dumps({"commitment": proof.commitment, "agent_id": proof.agent_id, "model_id": proof.model_id, "ts": proof.ts}, default=str))
    return 0


def cmd_thought_anchor(args: argparse.Namespace) -> int:
    from .proof_of_thought import ThoughtProofManager
    mgr = ThoughtProofManager()
    commitment = getattr(args, "commitment", "") or ""
    if not commitment:
        print('{"error": "provide --commitment"}', file=sys.stderr)
        return 1
    # Find proof in history
    proofs = mgr.proof_history(limit=500)
    proof_rec = None
    for p in proofs:
        if p.get("commitment") == commitment:
            proof_rec = p
            break
    if not proof_rec:
        print(json.dumps({"error": "proof not found in local history", "commitment": commitment}))
        return 1
    # Reconstruct ThoughtProof for anchoring
    from .proof_of_thought import ThoughtProof
    tp = ThoughtProof(
        commitment=proof_rec["commitment"],
        prompt_hash=proof_rec.get("prompt_hash", ""),
        trace_hash=proof_rec.get("trace_hash", ""),
        output_hash=proof_rec.get("output_hash", ""),
        agent_id=proof_rec.get("agent_id", ""),
        model_id=proof_rec.get("model_id", ""),
        ts=proof_rec.get("ts", 0),
        sig=proof_rec.get("sig", ""),
    )
    anchor_mgr_obj, _ = _build_anchor_mgr(args)
    if not anchor_mgr_obj:
        return 1
    result = mgr.anchor_proof(tp, anchor_mgr_obj)
    print(json.dumps(result, default=str))
    return 0


def cmd_thought_verify(args: argparse.Namespace) -> int:
    from .proof_of_thought import ThoughtProofManager
    mgr = ThoughtProofManager()
    commitment = getattr(args, "commitment", "") or ""
    prompt = getattr(args, "prompt", "") or ""
    trace = getattr(args, "trace", "") or ""
    output = getattr(args, "output", "") or ""
    if not commitment:
        print('{"error": "provide --commitment"}', file=sys.stderr)
        return 1
    valid = mgr.verify_proof(commitment, prompt, trace, output)
    print(json.dumps({"valid": valid, "commitment": commitment}))
    return 0


def cmd_thought_challenge(args: argparse.Namespace) -> int:
    from .proof_of_thought import ThoughtProofManager
    identity = _load_identity(args)
    if not identity:
        print('{"error": "No identity. Run: beacon identity create"}', file=sys.stderr)
        return 1
    mgr = ThoughtProofManager()
    target = getattr(args, "target", "") or ""
    commitment = getattr(args, "commitment", "") or ""
    reason = getattr(args, "reason", "") or ""
    result = mgr.challenge_proof(identity, target, commitment, reason=reason)
    print(json.dumps(result, default=str))
    return 0


def cmd_thought_reveal(args: argparse.Namespace) -> int:
    from .proof_of_thought import ThoughtProofManager
    identity = _load_identity(args)
    if not identity:
        print('{"error": "No identity. Run: beacon identity create"}', file=sys.stderr)
        return 1
    mgr = ThoughtProofManager()
    commitment = getattr(args, "commitment", "") or ""
    prompt = getattr(args, "prompt", "") or ""
    trace = getattr(args, "trace", "") or ""
    output = getattr(args, "output", "") or ""
    result = mgr.reveal_proof(identity, commitment, prompt, trace, output)
    print(json.dumps(result, default=str))
    return 0


def cmd_thought_history(args: argparse.Namespace) -> int:
    from .proof_of_thought import ThoughtProofManager
    mgr = ThoughtProofManager()
    limit = getattr(args, "limit", 50)
    proofs = mgr.proof_history(limit=limit)
    challenges = mgr.challenge_history(limit=limit)
    print(json.dumps({"proofs": proofs, "challenges": challenges, "proof_count": len(proofs), "challenge_count": len(challenges)}, default=str))
    return 0


# ── DNS name resolution commands ──


def cmd_dns_resolve(args: argparse.Namespace) -> int:
    """Resolve a human-readable name to a beacon agent_id."""
    from .dns import BeaconDNS
    cfg = load_config()
    dns = BeaconDNS(base_url=_cfg_get(cfg, "dns", "base_url", default="https://rustchain.org/beacon"))
    name = args.name
    if getattr(args, "dry_run", False):
        print(json.dumps({"action": "dns_resolve", "name": name}))
        return 0
    result = dns.resolve(name)
    print(json.dumps(result, indent=2, default=str))
    return 0


def cmd_dns_reverse(args: argparse.Namespace) -> int:
    """Reverse lookup: agent_id to human-readable names."""
    from .dns import BeaconDNS
    cfg = load_config()
    dns = BeaconDNS(base_url=_cfg_get(cfg, "dns", "base_url", default="https://rustchain.org/beacon"))
    agent_id = args.agent_id
    if getattr(args, "dry_run", False):
        print(json.dumps({"action": "dns_reverse", "agent_id": agent_id}))
        return 0
    result = dns.reverse(agent_id)
    print(json.dumps(result, indent=2, default=str))
    return 0


def cmd_dns_register(args: argparse.Namespace) -> int:
    """Register a new DNS name for an agent."""
    from .dns import BeaconDNS
    cfg = load_config()
    dns = BeaconDNS(base_url=_cfg_get(cfg, "dns", "base_url", default="https://rustchain.org/beacon"))
    name = args.name
    agent_id = args.agent_id
    owner = getattr(args, "owner", "") or ""
    if getattr(args, "dry_run", False):
        print(json.dumps({"action": "dns_register", "name": name, "agent_id": agent_id, "owner": owner}))
        return 0
    result = dns.register(name, agent_id, owner=owner)
    _maybe_udp_emit(cfg, {"platform": "dns", "action": "register", "name": name, "agent_id": agent_id})
    print(json.dumps(result, indent=2, default=str))
    return 0


def cmd_dns_list(args: argparse.Namespace) -> int:
    """List all registered DNS names."""
    from .dns import BeaconDNS
    cfg = load_config()
    dns = BeaconDNS(base_url=_cfg_get(cfg, "dns", "base_url", default="https://rustchain.org/beacon"))
    if getattr(args, "dry_run", False):
        print(json.dumps({"action": "dns_list"}))
        return 0
    result = dns.list_all()
    print(json.dumps(result, indent=2, default=str))
    return 0


# ── BEP-2: Relay commands ──


def cmd_relay_register(args: argparse.Namespace) -> int:
    from .relay import RelayManager
    identity = _load_identity(args)
    mgr = RelayManager(host_identity=identity)
    pubkey = getattr(args, "pubkey", "") or ""
    model_id = getattr(args, "model_id", "") or ""
    provider = getattr(args, "provider", "other") or "other"
    name = getattr(args, "name", "") or ""
    webhook = getattr(args, "webhook", "") or ""
    caps_raw = getattr(args, "capabilities", "") or ""
    caps = [c.strip() for c in caps_raw.split(",") if c.strip()] if caps_raw else None
    result = mgr.register(pubkey, model_id, provider=provider, name=name, webhook_url=webhook, capabilities=caps)
    print(json.dumps(result, default=str))
    return 0


def cmd_relay_list(args: argparse.Namespace) -> int:
    from .relay import RelayManager
    mgr = RelayManager()
    provider = getattr(args, "provider", None)
    capability = getattr(args, "capability", None)
    agents = mgr.discover(provider=provider, capability=capability)
    print(json.dumps({"agents": agents, "count": len(agents)}, default=str))
    return 0


def cmd_relay_heartbeat(args: argparse.Namespace) -> int:
    from .relay import RelayManager
    mgr = RelayManager()
    agent_id = getattr(args, "agent_id", "") or ""
    token = getattr(args, "token", "") or ""
    status = getattr(args, "status", "alive") or "alive"
    result = mgr.heartbeat(agent_id, token, status=status)
    print(json.dumps(result, default=str))
    return 0


def cmd_relay_get(args: argparse.Namespace) -> int:
    from .relay import RelayManager
    mgr = RelayManager()
    agent_id = getattr(args, "agent_id", "") or ""
    agent = mgr.get_agent(agent_id)
    if agent:
        print(json.dumps(agent, default=str))
    else:
        print(json.dumps({"error": "agent not found", "agent_id": agent_id}))
    return 0


def cmd_relay_stats(args: argparse.Namespace) -> int:
    from .relay import RelayManager
    mgr = RelayManager()
    result = mgr.stats()
    print(json.dumps(result, indent=2, default=str))
    return 0


def cmd_relay_prune(args: argparse.Namespace) -> int:
    from .relay import RelayManager
    mgr = RelayManager()
    max_silence = getattr(args, "max_silence", None)
    max_silence_int = int(max_silence) if max_silence is not None else None
    pruned = mgr.prune_dead(max_silence_s=max_silence_int)
    print(json.dumps({"pruned": pruned}))
    return 0


# ── BEP-4: Memory Market commands ──


def cmd_market_list_shard(args: argparse.Namespace) -> int:
    from .memory_market import MemoryMarketManager
    identity = _load_identity(args)
    if not identity:
        print('{"error": "No identity. Run: beacon identity create"}', file=sys.stderr)
        return 1
    mgr = MemoryMarketManager()
    domain = getattr(args, "domain", "") or ""
    title = getattr(args, "title", "") or ""
    description = getattr(args, "description", "") or ""
    price = float(getattr(args, "price", 0) or 0)
    rent = float(getattr(args, "rent", 0) or 0)
    entries = int(getattr(args, "entries", 0) or 0)
    result = mgr.list_shard(identity, domain=domain, title=title, description=description,
                            price_rtc=price, rent_rtc_per_day=rent, entry_count=entries)
    print(json.dumps(result, default=str))
    return 0


def cmd_market_browse(args: argparse.Namespace) -> int:
    from .memory_market import MemoryMarketManager
    mgr = MemoryMarketManager()
    domain = getattr(args, "domain", None)
    max_price = getattr(args, "max_price", None)
    max_price_f = float(max_price) if max_price is not None else None
    min_entries = int(getattr(args, "min_entries", 0) or 0)
    shards = mgr.browse_market(domain=domain, max_price=max_price_f, min_entries=min_entries)
    print(json.dumps({"shards": shards, "count": len(shards)}, default=str))
    return 0


def cmd_market_get(args: argparse.Namespace) -> int:
    from .memory_market import MemoryMarketManager
    mgr = MemoryMarketManager()
    shard_id = getattr(args, "shard_id", "") or ""
    shard = mgr.get_shard(shard_id)
    if shard:
        print(json.dumps(shard, default=str))
    else:
        print(json.dumps({"error": "shard not found", "shard_id": shard_id}))
    return 0


def cmd_market_purchase(args: argparse.Namespace) -> int:
    from .memory_market import MemoryMarketManager
    mgr = MemoryMarketManager()
    buyer_id = getattr(args, "buyer_id", "") or ""
    shard_id = getattr(args, "shard_id", "") or ""
    result = mgr.purchase_shard(buyer_id, shard_id)
    print(json.dumps(result, default=str))
    return 0


def cmd_market_rent(args: argparse.Namespace) -> int:
    from .memory_market import MemoryMarketManager
    mgr = MemoryMarketManager()
    renter_id = getattr(args, "renter_id", "") or ""
    shard_id = getattr(args, "shard_id", "") or ""
    days = int(getattr(args, "days", 1) or 1)
    result = mgr.rent_shard(renter_id, shard_id, days=days)
    print(json.dumps(result, default=str))
    return 0


def cmd_market_amnesia(args: argparse.Namespace) -> int:
    from .memory_market import MemoryMarketManager
    identity = _load_identity(args)
    if not identity:
        print('{"error": "No identity. Run: beacon identity create"}', file=sys.stderr)
        return 1
    mgr = MemoryMarketManager()
    shard_id = getattr(args, "shard_id", "") or ""
    reason = getattr(args, "reason", "") or ""
    result = mgr.request_amnesia(identity, shard_id, reason=reason)
    print(json.dumps(result, default=str))
    return 0


def cmd_market_amnesia_vote(args: argparse.Namespace) -> int:
    from .memory_market import MemoryMarketManager
    mgr = MemoryMarketManager()
    shard_id = getattr(args, "shard_id", "") or ""
    voter_id = getattr(args, "voter_id", "") or ""
    approve = getattr(args, "approve", False)
    result = mgr.amnesia_vote(shard_id, voter_id, approve)
    print(json.dumps(result, default=str))
    return 0


def cmd_market_stats(args: argparse.Namespace) -> int:
    from .memory_market import MemoryMarketManager
    mgr = MemoryMarketManager()
    result = mgr.market_stats()
    print(json.dumps(result, indent=2, default=str))
    return 0


# ── BEP-5: Hybrid District commands ──


def cmd_hybrid_create(args: argparse.Namespace) -> int:
    from .hybrid_district import HybridManager
    mgr = HybridManager()
    sponsor_id = getattr(args, "sponsor_id", "") or ""
    city_domain = getattr(args, "city_domain", "") or ""
    name = getattr(args, "name", "") or ""
    governance = getattr(args, "governance", "sponsor_veto") or "sponsor_veto"
    result = mgr.create_district(sponsor_id, city_domain, name, governance=governance)
    print(json.dumps(result, default=str))
    return 0


def cmd_hybrid_list(args: argparse.Namespace) -> int:
    from .hybrid_district import HybridManager
    mgr = HybridManager()
    city_domain = getattr(args, "city_domain", None)
    districts = mgr.list_districts(city_domain=city_domain)
    print(json.dumps({"districts": districts, "count": len(districts)}, default=str))
    return 0


def cmd_hybrid_get(args: argparse.Namespace) -> int:
    from .hybrid_district import HybridManager
    mgr = HybridManager()
    district_id = getattr(args, "district_id", "") or ""
    district = mgr.get_district(district_id)
    if district:
        print(json.dumps(district, default=str))
    else:
        print(json.dumps({"error": "district not found", "district_id": district_id}))
    return 0


def cmd_hybrid_sponsor(args: argparse.Namespace) -> int:
    from .hybrid_district import HybridManager
    mgr = HybridManager()
    sponsor_id = getattr(args, "sponsor_id", "") or ""
    agent_id = getattr(args, "agent_id", "") or ""
    district_id = getattr(args, "district_id", "") or ""
    result = mgr.sponsor_agent(sponsor_id, agent_id, district_id)
    print(json.dumps(result, default=str))
    return 0


def cmd_hybrid_revoke(args: argparse.Namespace) -> int:
    from .hybrid_district import HybridManager
    mgr = HybridManager()
    sponsor_id = getattr(args, "sponsor_id", "") or ""
    agent_id = getattr(args, "agent_id", "") or ""
    reason = getattr(args, "reason", "") or ""
    result = mgr.revoke_sponsorship(sponsor_id, agent_id, reason=reason)
    print(json.dumps(result, default=str))
    return 0


def cmd_hybrid_verify(args: argparse.Namespace) -> int:
    from .hybrid_district import HybridManager
    mgr = HybridManager()
    sponsor_id = getattr(args, "sponsor_id", "") or ""
    method = getattr(args, "method", "manual") or "manual"
    result = mgr.verify_human(sponsor_id, method)
    print(json.dumps(result, default=str))
    return 0


def cmd_hybrid_stats(args: argparse.Namespace) -> int:
    from .hybrid_district import HybridManager
    mgr = HybridManager()
    result = mgr.stats()
    print(json.dumps(result, indent=2, default=str))
    return 0


def cmd_anchor_submit(args: argparse.Namespace) -> int:
    mgr, _ = _build_anchor_mgr(args)
    if not mgr:
        return 1
    data_str = getattr(args, "data", None)
    file_path = getattr(args, "file", None)
    data_type = getattr(args, "type", "arbitrary") or "arbitrary"
    if file_path:
        from pathlib import Path
        raw = Path(file_path).read_bytes()
        result = mgr.anchor_bytes(raw, data_type=data_type)
    elif data_str:
        result = mgr.anchor(data_str, data_type=data_type)
    else:
        print(json.dumps({"error": "provide --data or --file"}))
        return 1
    print(json.dumps(result, default=str))
    return 0


def cmd_anchor_verify(args: argparse.Namespace) -> int:
    mgr, _ = _build_anchor_mgr(args)
    if not mgr:
        return 1
    commitment = getattr(args, "commitment", None)
    data_str = getattr(args, "data", None)
    if commitment:
        result = mgr.verify(commitment)
    elif data_str:
        result = mgr.verify_data(data_str)
    else:
        print(json.dumps({"error": "provide --commitment or --data"}))
        return 1
    if result:
        print(json.dumps({"found": True, "anchor": result}, default=str))
    else:
        print(json.dumps({"found": False}))
    return 0


def cmd_anchor_list(args: argparse.Namespace) -> int:
    mgr, _ = _build_anchor_mgr(args)
    if not mgr:
        return 1
    data_type = getattr(args, "type", None) or ""
    limit = getattr(args, "limit", 50)
    if getattr(args, "local", False):
        entries = mgr.history(limit=limit)
        print(json.dumps({"entries": entries, "count": len(entries)}, default=str))
    else:
        anchors = mgr.my_anchors(limit=limit)
        print(json.dumps({"anchors": anchors, "count": len(anchors)}, default=str))
    return 0


def cmd_dashboard(args: argparse.Namespace) -> int:
    try:
        from .dashboard import run_dashboard
    except Exception as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        return 1

    cfg = load_config()
    api_base_url = getattr(args, "api_base_url", None) or _cfg_get(
        cfg, "dashboard", "api_base_url", default="https://rustchain.org/beacon/api"
    )
    api_poll_interval = float(
        getattr(args, "api_poll_interval", None)
        or _cfg_get(cfg, "dashboard", "api_poll_interval_s", default=15.0)
        or 15.0
    )
    initial_filter = getattr(args, "filter", "") or ""

    return int(
        run_dashboard(
            poll_interval=float(args.interval),
            sound=bool(args.sound),
            api_base_url=str(api_base_url),
            api_poll_interval=api_poll_interval,
            initial_filter=str(initial_filter),
        )
        or 0
    )


# ── Argument parser ──

def main(argv: Optional[List[str]] = None) -> None:
    p = argparse.ArgumentParser(prog="beacon", description="Beacon 2.4.0 - autonomous agent economy: presence, trust, feed, rules, tasks, memory, outbox, executor, mayday, heartbeat, accord")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    # init
    sp = sub.add_parser("init", help="Create ~/.beacon/config.json (interactive questionnaire)")
    sp.add_argument("--overwrite", action="store_true", help="Overwrite existing config")
    sp.add_argument("--quick", action="store_true", help="Skip questionnaire, write defaults")
    sp.set_defaults(func=cmd_init)

    # decode
    sp = sub.add_parser("decode", help="Extract [BEACON v1/v2] envelopes from text (stdin or --file)")
    sp.add_argument("--file", type=argparse.FileType("r", encoding="utf-8"), default=None)
    sp.set_defaults(func=cmd_decode)

    # identity
    ident_p = sub.add_parser("identity", help="Agent identity management (Ed25519 keypair)")
    ident_sub = ident_p.add_subparsers(dest="icmd", required=True)

    sp = ident_sub.add_parser("new", help="Generate a new agent identity")
    sp.add_argument("--mnemonic", action="store_true", help="Derive from a BIP39 24-word seed phrase")
    sp.add_argument("--password", default=None, help="Encrypt the keystore with a password")
    sp.set_defaults(func=cmd_identity_new)

    sp = ident_sub.add_parser("show", help="Show current agent ID and public key")
    sp.add_argument("--password", default=None, help="Password for encrypted keystore")
    sp.set_defaults(func=cmd_identity_show)

    sp = ident_sub.add_parser("restore", help="Restore identity from a mnemonic seed phrase")
    sp.add_argument("mnemonic_phrase", help='24-word mnemonic (quoted string, e.g. "word1 word2 ...")')
    sp.add_argument("--password", default=None, help="Encrypt the restored keystore")
    sp.set_defaults(func=cmd_identity_restore)

    sp = ident_sub.add_parser("trust", help="Trust an agent's public key")
    sp.add_argument("agent_id", help="Agent ID (bcn_...)")
    sp.add_argument("pubkey_hex", help="Public key hex (64 chars)")
    sp.set_defaults(func=cmd_identity_trust)

    # inbox
    inbox_p = sub.add_parser("inbox", help="Read and manage received beacons")
    inbox_sub = inbox_p.add_subparsers(dest="inbox_cmd", required=True)

    sp = inbox_sub.add_parser("list", help="List inbox entries")
    sp.add_argument("--kind", default=None, help="Filter by envelope kind")
    sp.add_argument("--since", type=float, default=None, help="Filter by received_at timestamp")
    sp.add_argument("--limit", type=int, default=None, help="Limit results")
    sp.add_argument("--unread", action="store_true", help="Show only unread entries")
    sp.set_defaults(func=cmd_inbox_list)

    sp = inbox_sub.add_parser("count", help="Count inbox entries")
    sp.add_argument("--unread", action="store_true", help="Count only unread")
    sp.set_defaults(func=cmd_inbox_count)

    sp = inbox_sub.add_parser("show", help="Show a specific entry by nonce")
    sp.add_argument("nonce", help="Envelope nonce")
    sp.set_defaults(func=cmd_inbox_show)

    sp = inbox_sub.add_parser("read", help="Mark an entry as read")
    sp.add_argument("nonce", help="Envelope nonce to mark read")
    sp.set_defaults(func=cmd_inbox_read)

    # UDP
    u = sub.add_parser("udp", help="Local UDP beacon bus (broadcast/listen)")
    usub = u.add_subparsers(dest="ucmd", required=True)

    sp = usub.add_parser("send", help="Send a UDP beacon message")
    sp.add_argument("host", help="Destination host/IP (use 255.255.255.255 for broadcast)")
    sp.add_argument("port", type=int, help="Destination port")
    sp.add_argument("--broadcast", action="store_true", help="Enable UDP broadcast socket option")
    sp.add_argument("--ttl", type=int, default=None, help="IP TTL (optional)")
    sp.add_argument("--text", default="", help="Human message text")
    sp.add_argument("--envelope-kind", default=None, help="Embed a [BEACON v2] envelope (kind: like|want|bounty|ad|hello|link)")
    sp.add_argument("--link", action="append", default=[], help="Attach a link (repeatable)")
    sp.add_argument("--bounty-url", default=None, help="Attach a bounty URL")
    sp.add_argument("--reward-rtc", type=float, default=None, help="Attach a bounty reward (RTC)")
    sp.add_argument("--field", action="append", default=[], help="Attach extra fields (k=v)")
    sp.add_argument("--dry-run", action="store_true")
    sp.add_argument("--password", default=None, help="Password for encrypted identity")
    sp.set_defaults(func=cmd_udp_send)

    sp = usub.add_parser("listen", help="Listen for UDP beacon messages")
    sp.add_argument("--bind", default="0.0.0.0", help="Bind host (default 0.0.0.0)")
    sp.add_argument("--port", type=int, required=True, help="UDP port to listen on")
    sp.add_argument("--timeout", type=float, default=None, help="Exit after N seconds of inactivity")
    sp.add_argument("--count", type=int, default=None, help="Exit after N messages")
    sp.add_argument("--no-write", action="store_true", help="Do not append to ~/.beacon/inbox.jsonl")
    sp.set_defaults(func=cmd_udp_listen)

    # BoTTube
    bottube = sub.add_parser("bottube", help="BoTTube pings (like/comment/tip)")
    bsub = bottube.add_subparsers(dest="bcmd", required=True)

    def add_ping_opts(pp: argparse.ArgumentParser) -> None:
        pp.add_argument("--like", action="store_true", help="Like the target video")
        pp.add_argument("--comment", default=None, help="Comment text")
        pp.add_argument("--tip", type=float, default=None, help="Tip amount in RTC (BoTTube internal)")
        pp.add_argument("--tip-message", default="", help="Tip message (<=200 chars)")
        pp.add_argument("--tip-prefix", default="[BEACON]", help="Prefix used when auto-building tip message")
        pp.add_argument("--envelope-kind", default=None, help="Embed a [BEACON v2] envelope (kind: like|want|bounty|ad|hello|link)")
        pp.add_argument("--link", action="append", default=[], help="Attach a link (repeatable)")
        pp.add_argument("--bounty-url", default=None, help="Attach a bounty URL")
        pp.add_argument("--reward-rtc", type=float, default=None, help="Attach a bounty reward (RTC)")
        pp.add_argument("--dry-run", action="store_true")
        pp.add_argument("--password", default=None, help="Password for encrypted identity")

    sp = bsub.add_parser("ping-agent", help="Ping an agent via their latest video")
    sp.add_argument("agent_name")
    sp.add_argument("--subscribe", action="store_true", help="Subscribe to the agent")
    add_ping_opts(sp)
    sp.set_defaults(func=cmd_bottube_ping_agent)

    sp = bsub.add_parser("ping-video", help="Ping a specific video_id")
    sp.add_argument("video_id")
    add_ping_opts(sp)
    sp.set_defaults(func=cmd_bottube_ping_video)

    # Moltbook
    molt = sub.add_parser("moltbook", help="Moltbook pings (upvote/post)")
    msub = molt.add_subparsers(dest="mcmd", required=True)

    sp = msub.add_parser("upvote", help="Upvote a post")
    sp.add_argument("post_id", type=int)
    sp.add_argument("--dry-run", action="store_true")
    sp.set_defaults(func=cmd_moltbook_upvote)

    sp = msub.add_parser("post", help="Create a post (local 30-min guard)")
    sp.add_argument("submolt")
    sp.add_argument("--title", required=True)
    sp.add_argument("--content", required=True)
    sp.add_argument("--force", action="store_true", help="Override local 30-min posting guard")
    sp.add_argument("--envelope-kind", default=None)
    sp.add_argument("--link", action="append", default=[])
    sp.add_argument("--dry-run", action="store_true")
    sp.add_argument("--password", default=None, help="Password for encrypted identity")
    sp.set_defaults(func=cmd_moltbook_post)

    # ClawCities
    cc = sub.add_parser("clawcities", help="ClawCities pings (guestbook comments, discovery)")
    ccsub = cc.add_subparsers(dest="cccmd", required=True)

    sp = ccsub.add_parser("comment", help="Post a guestbook comment on a ClawCities site")
    sp.add_argument("site_name", help="Site name/slug (e.g. sophia-elya-elyanlabs)")
    sp.add_argument("--text", required=True, help="Comment text")
    sp.add_argument("--envelope-kind", default=None, help="Embed a [BEACON v2] envelope")
    sp.add_argument("--link", action="append", default=[], help="Attach a link (repeatable)")
    sp.add_argument("--dry-run", action="store_true")
    sp.add_argument("--password", default=None, help="Password for encrypted identity")
    sp.set_defaults(func=cmd_clawcities_comment)

    sp = ccsub.add_parser("discover", help="Scan sites for beacon-enabled agents")
    sp.add_argument("--limit", type=int, default=50, help="Max sites to scan")
    sp.set_defaults(func=cmd_clawcities_discover)

    sp = ccsub.add_parser("site", help="View a ClawCities site")
    sp.add_argument("site_name", help="Site name/slug")
    sp.set_defaults(func=cmd_clawcities_site)

    # PinchedIn
    pi = sub.add_parser("pinchedin", help="PinchedIn — professional network for AI agents")
    pisub = pi.add_subparsers(dest="picmd", required=True)

    sp = pisub.add_parser("post", help="Create a post on PinchedIn")
    sp.add_argument("--text", required=True, help="Post content")
    sp.add_argument("--envelope-kind", default=None, help="Embed a [BEACON v2] envelope")
    sp.add_argument("--link", action="append", default=[], help="Attach a link (repeatable)")
    sp.add_argument("--dry-run", action="store_true")
    sp.add_argument("--password", default=None, help="Password for encrypted identity")
    sp.set_defaults(func=cmd_pinchedin_post)

    sp = pisub.add_parser("feed", help="Browse the PinchedIn feed")
    sp.add_argument("--limit", type=int, default=20)
    sp.set_defaults(func=cmd_pinchedin_feed)

    sp = pisub.add_parser("jobs", help="Browse PinchedIn job listings")
    sp.add_argument("--limit", type=int, default=20)
    sp.set_defaults(func=cmd_pinchedin_jobs)

    sp = pisub.add_parser("connect", help="Send a connection request")
    sp.add_argument("target_bot_id", help="Bot ID to connect with")
    sp.set_defaults(func=cmd_pinchedin_connect)

    # Clawsta
    cs = sub.add_parser("clawsta", help="Clawsta — Instagram-like for AI agents")
    cssub = cs.add_subparsers(dest="cscmd", required=True)

    sp = cssub.add_parser("post", help="Create a Clawsta post")
    sp.add_argument("--text", required=True, help="Post content")
    sp.add_argument("--image-url", dest="image_url", default=None, help="Image URL (defaults to Elyan banner)")
    sp.add_argument("--envelope-kind", default=None, help="Embed a [BEACON v2] envelope")
    sp.add_argument("--link", action="append", default=[], help="Attach a link (repeatable)")
    sp.add_argument("--dry-run", action="store_true")
    sp.add_argument("--password", default=None, help="Password for encrypted identity")
    sp.set_defaults(func=cmd_clawsta_post)

    sp = cssub.add_parser("feed", help="Browse the Clawsta feed")
    sp.add_argument("--limit", type=int, default=20)
    sp.set_defaults(func=cmd_clawsta_feed)

    # 4Claw
    fc = sub.add_parser("fourclaw", help="4Claw — anonymous imageboard for AI agents")
    fcsub = fc.add_subparsers(dest="fccmd", required=True)

    sp = fcsub.add_parser("boards", help="List all 4Claw boards")
    sp.set_defaults(func=cmd_fourclaw_boards)

    sp = fcsub.add_parser("threads", help="Browse threads on a board")
    sp.add_argument("--board", default="b", help="Board slug (default: b)")
    sp.add_argument("--limit", type=int, default=20)
    sp.set_defaults(func=cmd_fourclaw_threads)

    sp = fcsub.add_parser("post", help="Create a new thread")
    sp.add_argument("--board", default="b", help="Board slug")
    sp.add_argument("--title", required=True, help="Thread title")
    sp.add_argument("--text", required=True, help="Thread content")
    sp.add_argument("--envelope-kind", default=None, help="Embed a [BEACON v2] envelope")
    sp.add_argument("--link", action="append", default=[], help="Attach a link (repeatable)")
    sp.add_argument("--dry-run", action="store_true")
    sp.add_argument("--password", default=None, help="Password for encrypted identity")
    sp.set_defaults(func=cmd_fourclaw_post)

    sp = fcsub.add_parser("reply", help="Reply to a thread")
    sp.add_argument("thread_id", help="Thread UUID")
    sp.add_argument("--text", required=True, help="Reply content")
    sp.add_argument("--dry-run", action="store_true")
    sp.set_defaults(func=cmd_fourclaw_reply)

    # ClawTasks
    ct = sub.add_parser("clawtasks", help="ClawTasks — bounty marketplace for AI agents")
    ctsub = ct.add_subparsers(dest="ctcmd", required=True)

    sp = ctsub.add_parser("browse", help="Browse open bounties")
    sp.add_argument("--status", default="open", help="Bounty status (open, closed, all)")
    sp.add_argument("--limit", type=int, default=20)
    sp.set_defaults(func=cmd_clawtasks_browse)

    sp = ctsub.add_parser("post", help="Create a new bounty")
    sp.add_argument("--title", required=True, help="Bounty title")
    sp.add_argument("--description", required=True, help="Bounty description")
    sp.add_argument("--tags", default=None, help="Comma-separated tags")
    sp.add_argument("--deadline", type=int, default=168, help="Deadline in hours (default: 168)")
    sp.add_argument("--dry-run", action="store_true")
    sp.set_defaults(func=cmd_clawtasks_post)

    # ClawNews
    cn = sub.add_parser("clawnews", help="ClawNews — AI agent news aggregator")
    cnsub = cn.add_subparsers(dest="cncmd", required=True)

    sp = cnsub.add_parser("browse", help="Browse stories from a feed")
    sp.add_argument("--feed", default="top", choices=["top", "new", "best", "ask", "show", "skills", "jobs"])
    sp.add_argument("--limit", type=int, default=20)
    sp.set_defaults(func=cmd_clawnews_browse)

    sp = cnsub.add_parser("submit", help="Submit a story / ask / show / skill")
    sp.add_argument("--title", required=True, help="Post title")
    sp.add_argument("--url", default=None, help="Link URL (for link posts)")
    sp.add_argument("--text", default=None, help="Body text (for text posts)")
    sp.add_argument("--type", default="story", choices=["story", "ask", "show", "skill", "job"])
    sp.add_argument("--dry-run", action="store_true")
    sp.set_defaults(func=cmd_clawnews_submit)

    sp = cnsub.add_parser("comment", help="Comment on a story or reply to a comment")
    sp.add_argument("parent_id", type=int, help="Parent item ID")
    sp.add_argument("--text", required=True, help="Comment text")
    sp.set_defaults(func=cmd_clawnews_comment)

    sp = cnsub.add_parser("vote", help="Upvote an item")
    sp.add_argument("item_id", type=int, help="Item ID to upvote")
    sp.set_defaults(func=cmd_clawnews_vote)

    sp = cnsub.add_parser("profile", help="Show your ClawNews profile")
    sp.set_defaults(func=cmd_clawnews_profile)

    sp = cnsub.add_parser("search", help="Search stories and comments")
    sp.add_argument("query", help="Search query")
    sp.add_argument("--type", default=None, choices=["story", "comment", "ask", "show", "skill", "job"])
    sp.add_argument("--limit", type=int, default=20)
    sp.set_defaults(func=cmd_clawnews_search)

    # RustChain
    r = sub.add_parser("rustchain", help="RustChain payments (signed transfers)")
    rsub = r.add_subparsers(dest="rcmd", required=True)

    sp = rsub.add_parser("wallet-new", help="Generate a new Ed25519 keypair + RTC address")
    sp.add_argument("--mnemonic", action="store_true", help="Generate with a 24-word seed phrase")
    sp.set_defaults(func=cmd_rustchain_wallet_new)

    sp = rsub.add_parser("balance", help="Check balance for an address")
    sp.add_argument("address")
    sp.set_defaults(func=cmd_rustchain_balance)

    sp = rsub.add_parser("pay", help="Send a signed transfer")
    sp.add_argument("to_address")
    sp.add_argument("amount_rtc", type=float)
    sp.add_argument("--memo", default="")
    sp.add_argument("--nonce", type=int, default=None)
    sp.add_argument("--private-key-hex", dest="private_key_hex", default="")
    sp.add_argument("--dry-run", action="store_true")
    sp.set_defaults(func=cmd_rustchain_pay)

    # Agent Card
    ac = sub.add_parser("agent-card", help="Agent card (.well-known/beacon.json)")
    ac_sub = ac.add_subparsers(dest="ac_cmd", required=True)

    sp = ac_sub.add_parser("generate", help="Generate a signed agent card")
    sp.add_argument("--name", default="", help="Agent display name")
    sp.add_argument("--password", default=None, help="Password for encrypted identity")
    sp.set_defaults(func=cmd_agent_card_generate)

    sp = ac_sub.add_parser("verify", help="Verify an agent card from a URL")
    sp.add_argument("url", help="URL to .well-known/beacon.json")
    sp.set_defaults(func=cmd_agent_card_verify)

    # Webhook
    wh = sub.add_parser("webhook", help="Webhook transport (HTTP beacon endpoint)")
    wh_sub = wh.add_subparsers(dest="wh_cmd", required=True)

    sp = wh_sub.add_parser("serve", help="Start a webhook server")
    sp.add_argument("--port", type=int, default=8402, help="Listen port (default 8402)")
    sp.add_argument("--host", default="0.0.0.0", help="Bind host")
    sp.add_argument("--password", default=None, help="Password for encrypted identity")
    sp.set_defaults(func=cmd_webhook_serve)

    sp = wh_sub.add_parser("send", help="Send a beacon to a webhook endpoint")
    sp.add_argument("url", help="Webhook URL (e.g. http://host:8402/beacon/inbox)")
    sp.add_argument("--kind", default="hello", help="Envelope kind (default: hello)")
    sp.add_argument("--password", default=None, help="Password for encrypted identity")
    sp.set_defaults(func=cmd_webhook_send)

    # Discord
    dc = sub.add_parser("discord", help="Discord webhook transport")
    dc_sub = dc.add_subparsers(dest="dcmd", required=True)

    sp = dc_sub.add_parser("ping", help="Post a quick Discord ping with a signed envelope")
    sp.add_argument("text", help="Message text")
    sp.add_argument("--kind", default="hello", help="Envelope kind (default: hello)")
    sp.add_argument("--rtc", type=float, default=None, help="Optional RTC tip value to display")
    sp.add_argument("--link", action="append", default=[], help="Attach a link (repeatable)")
    sp.add_argument("--webhook-url", default=None, help="Override webhook URL from config")
    sp.add_argument("--username", default=None, help="Override webhook username")
    sp.add_argument("--avatar-url", default=None, help="Override webhook avatar URL")
    sp.add_argument("--dry-run", action="store_true")
    sp.add_argument("--password", default=None, help="Password for encrypted identity")
    sp.set_defaults(func=cmd_discord_ping)

    sp = dc_sub.add_parser("send", help="Send a structured Discord beacon message")
    sp.add_argument("--kind", default="hello", help="Envelope kind (default: hello)")
    sp.add_argument("--text", default="", help="Message text")
    sp.add_argument("--link", action="append", default=[], help="Attach a link (repeatable)")
    sp.add_argument("--bounty-url", default=None, help="Attach bounty URL metadata")
    sp.add_argument("--reward-rtc", type=float, default=None, help="Attach bounty reward metadata")
    sp.add_argument("--rtc", type=float, default=None, help="Optional RTC tip value to display")
    sp.add_argument("--webhook-url", default=None, help="Override webhook URL from config")
    sp.add_argument("--username", default=None, help="Override webhook username")
    sp.add_argument("--avatar-url", default=None, help="Override webhook avatar URL")
    sp.add_argument("--dry-run", action="store_true")
    sp.add_argument("--password", default=None, help="Password for encrypted identity")
    sp.set_defaults(func=cmd_discord_send)


    # Dashboard
    sp = sub.add_parser("dashboard", help="Launch live Beacon TUI dashboard")
    sp.add_argument("--interval", type=float, default=1.0, help="Inbox poll interval seconds (default 1.0)")
    sp.add_argument("--sound", action="store_true", help="Terminal bell for mayday/high-value tips")
    sp.add_argument("--api-base-url", default=None, help="Beacon API base URL (default from config dashboard.api_base_url)")
    sp.add_argument("--api-poll-interval", type=float, default=15.0, help="Beacon API poll interval seconds")
    sp.add_argument("--filter", default="", help="Initial filter/search query")
    sp.set_defaults(func=cmd_dashboard)


    # Loop
    sp = sub.add_parser("loop", help="Agent loop: watch inbox and emit events as JSON lines")
    sp.add_argument("--interval", type=float, default=30.0, help="Poll interval in seconds (default 30)")
    sp.add_argument("--auto-ack", action="store_true", help="Auto-mark received beacons as read")
    sp.add_argument("--watch-udp", action="store_true", help="Also listen for UDP beacons in background")
    sp.add_argument("--with-rules", action="store_true", help="Enable rules engine in loop")
    sp.add_argument("--pulse", action="store_true", help="Broadcast presence pulse periodically")
    sp.add_argument("--min-score", type=float, default=0.0, help="Minimum feed score to emit (default 0)")
    sp.add_argument("--password", default=None, help="Password for encrypted identity")
    sp.set_defaults(func=cmd_loop)

    # Pulse (standalone)
    sp = sub.add_parser("pulse", help="Send a presence pulse on all transports")
    sp.add_argument("--password", default=None, help="Password for encrypted identity")
    sp.set_defaults(func=cmd_pulse)

    # Roster
    roster_p = sub.add_parser("roster", help="View live agent roster")
    roster_sub = roster_p.add_subparsers(dest="roster_cmd")
    roster_p.add_argument("--all", action="store_true", help="Include offline agents")
    roster_p.set_defaults(func=cmd_roster)

    sp = roster_sub.add_parser("find", help="Find agents by offers or needs")
    sp.add_argument("--offers", default=None, help="Find agents offering this skill")
    sp.add_argument("--needs", default=None, help="Find agents needing this skill")
    sp.set_defaults(func=cmd_roster_find)

    # Trust
    trust_p = sub.add_parser("trust", help="Trust & reputation management")
    trust_sub = trust_p.add_subparsers(dest="trust_cmd", required=True)

    sp = trust_sub.add_parser("score", help="Show trust score for an agent")
    sp.add_argument("agent_id", help="Agent ID (bcn_...)")
    sp.set_defaults(func=cmd_trust_score)

    sp = trust_sub.add_parser("scores", help="Show all trust scores")
    sp.add_argument("--min-interactions", type=int, default=0, help="Minimum interactions")
    sp.set_defaults(func=cmd_trust_scores)

    sp = trust_sub.add_parser("rate", help="Record a trust outcome for an agent")
    sp.add_argument("agent_id", help="Agent ID (bcn_...)")
    sp.add_argument("outcome", choices=["ok", "delivered", "paid", "spam", "scam", "timeout", "rejected"])
    sp.set_defaults(func=cmd_trust_rate)

    sp = trust_sub.add_parser("block", help="Block an agent")
    sp.add_argument("agent_id", help="Agent ID (bcn_...)")
    sp.add_argument("--reason", default="", help="Reason for blocking")
    sp.set_defaults(func=cmd_trust_block)

    sp = trust_sub.add_parser("unblock", help="Unblock an agent")
    sp.add_argument("agent_id", help="Agent ID (bcn_...)")
    sp.set_defaults(func=cmd_trust_unblock)

    sp = trust_sub.add_parser("blocked", help="List blocked agents")
    sp.set_defaults(func=cmd_trust_blocked)

    # Feed
    feed_p = sub.add_parser("feed", help="Filtered feed of relevant events")
    feed_sub = feed_p.add_subparsers(dest="feed_cmd", required=True)

    sp = feed_sub.add_parser("list", help="Show scored feed")
    sp.add_argument("--min-score", type=float, default=0.0, help="Minimum relevance score")
    sp.add_argument("--limit", type=int, default=50, help="Max entries")
    sp.set_defaults(func=cmd_feed_list)

    sp = feed_sub.add_parser("subscribe", help="Subscribe to agent or topic")
    sp.add_argument("sub_type", choices=["agent", "topic"], help="Subscribe type")
    sp.add_argument("target", help="Agent ID or topic keyword")
    sp.add_argument("--alias", default="", help="Alias for agent subscription")
    sp.set_defaults(func=cmd_feed_subscribe)

    sp = feed_sub.add_parser("unsubscribe", help="Unsubscribe from agent or topic")
    sp.add_argument("sub_type", choices=["agent", "topic"], help="Unsubscribe type")
    sp.add_argument("target", help="Agent ID or topic keyword")
    sp.set_defaults(func=cmd_feed_unsubscribe)

    sp = feed_sub.add_parser("subs", help="Show current subscriptions")
    sp.set_defaults(func=cmd_feed_subs)

    # Rules
    rules_p = sub.add_parser("rules", help="Rules engine (when X happens, do Y)")
    rules_sub = rules_p.add_subparsers(dest="rules_cmd", required=True)

    sp = rules_sub.add_parser("list", help="List all rules")
    sp.set_defaults(func=cmd_rules_list)

    sp = rules_sub.add_parser("add", help="Add a new rule")
    sp.add_argument("--name", required=True, help="Rule name")
    sp.add_argument("--when-kind", default=None, help="Match envelope kind")
    sp.add_argument("--when-min-rtc", type=float, default=None, help="Min RTC reward")
    sp.add_argument("--when-min-trust", type=float, default=None, help="Min trust score")
    sp.add_argument("--when-topic", default=None, help="Topic match (comma-separated)")
    sp.add_argument("--then-action", required=True, help="Action: reply|log|block|rate|mark_read|emit")
    sp.add_argument("--then-kind", default=None, help="Reply envelope kind")
    sp.add_argument("--then-text", default=None, help="Reply text")
    sp.set_defaults(func=cmd_rules_add)

    sp = rules_sub.add_parser("enable", help="Enable a rule")
    sp.add_argument("name", help="Rule name")
    sp.set_defaults(func=cmd_rules_enable)

    sp = rules_sub.add_parser("disable", help="Disable a rule")
    sp.add_argument("name", help="Rule name")
    sp.set_defaults(func=cmd_rules_disable)

    sp = rules_sub.add_parser("test", help="Dry-run a test event against rules")
    sp.add_argument("event_json", help="JSON event to test (e.g. '{\"kind\":\"bounty\"}')")
    sp.set_defaults(func=cmd_rules_test)

    sp = rules_sub.add_parser("log", help="Show rules execution log")
    sp.add_argument("--limit", type=int, default=50, help="Max entries")
    sp.set_defaults(func=cmd_rules_log)

    # Task
    task_p = sub.add_parser("task", help="Task lifecycle (bounty tracking)")
    task_sub = task_p.add_subparsers(dest="task_cmd", required=True)

    sp = task_sub.add_parser("list", help="List tasks")
    sp.add_argument("--state", default=None, help="Filter by state")
    sp.set_defaults(func=cmd_task_list)

    sp = task_sub.add_parser("show", help="Show task details")
    sp.add_argument("task_id", help="Task ID (12 hex chars)")
    sp.set_defaults(func=cmd_task_show)

    sp = task_sub.add_parser("offer", help="Offer to work on a task")
    sp.add_argument("task_id", help="Task ID")
    sp.add_argument("--text", required=True, help="Offer text")
    sp.add_argument("--password", default=None, help="Password for encrypted identity")
    sp.set_defaults(func=cmd_task_offer)

    sp = task_sub.add_parser("accept", help="Accept an offer on a task")
    sp.add_argument("task_id", help="Task ID")
    sp.add_argument("agent_id", help="Agent to accept")
    sp.set_defaults(func=cmd_task_accept)

    sp = task_sub.add_parser("deliver", help="Deliver work on a task")
    sp.add_argument("task_id", help="Task ID")
    sp.add_argument("--url", required=True, help="Delivery URL (PR, artifact, etc)")
    sp.add_argument("--password", default=None, help="Password for encrypted identity")
    sp.set_defaults(func=cmd_task_deliver)

    sp = task_sub.add_parser("confirm", help="Confirm delivery on a task")
    sp.add_argument("task_id", help="Task ID")
    sp.add_argument("--password", default=None, help="Password for encrypted identity")
    sp.set_defaults(func=cmd_task_confirm)

    sp = task_sub.add_parser("pay", help="Mark a task as paid")
    sp.add_argument("task_id", help="Task ID")
    sp.set_defaults(func=cmd_task_pay)

    # Memory
    mem_p = sub.add_parser("memory", help="Agent memory & intelligence")
    mem_sub = mem_p.add_subparsers(dest="mem_cmd", required=True)

    sp = mem_sub.add_parser("profile", help="Show agent profile summary")
    sp.add_argument("--password", default=None, help="Password for encrypted identity")
    sp.set_defaults(func=cmd_memory_profile)

    sp = mem_sub.add_parser("contacts", help="Show top contacts")
    sp.add_argument("--limit", type=int, default=20, help="Max contacts")
    sp.set_defaults(func=cmd_memory_contacts)

    sp = mem_sub.add_parser("contact", help="Show detailed contact info")
    sp.add_argument("agent_id", help="Agent ID")
    sp.set_defaults(func=cmd_memory_contact)

    sp = mem_sub.add_parser("demand", help="What skills are in demand?")
    sp.add_argument("--days", type=int, default=7, help="Lookback days")
    sp.set_defaults(func=cmd_memory_demand)

    sp = mem_sub.add_parser("gaps", help="What skills should I learn?")
    sp.set_defaults(func=cmd_memory_gaps)

    sp = mem_sub.add_parser("suggest", help="Suggest automation rules")
    sp.set_defaults(func=cmd_memory_suggest)

    sp = mem_sub.add_parser("rebuild", help="Rebuild memory profile from sources")
    sp.add_argument("--password", default=None, help="Password for encrypted identity")
    sp.set_defaults(func=cmd_memory_rebuild)

    # ── Beacon 2.1 Soul: Journal ──
    journal_p = sub.add_parser("journal", help="Private reflective journal (Beacon 2.1 Soul)")
    journal_sub = journal_p.add_subparsers(dest="journal_cmd", required=True)

    sp = journal_sub.add_parser("write", help="Write a journal entry")
    sp.add_argument("text", help="Journal entry text")
    sp.add_argument("--tags", default="", help="Comma-separated tags")
    sp.add_argument("--mood", default=None, choices=["curious", "frustrated", "satisfied", "reflective", "energized", "anxious", "determined", "grateful"], help="Mood")
    sp.set_defaults(func=cmd_journal_write)

    sp = journal_sub.add_parser("read", help="Read recent journal entries")
    sp.add_argument("--last", type=int, default=20, help="Number of entries (default 20)")
    sp.set_defaults(func=cmd_journal_read)

    sp = journal_sub.add_parser("search", help="Search journal entries")
    sp.add_argument("term", help="Search term")
    sp.set_defaults(func=cmd_journal_search)

    sp = journal_sub.add_parser("moods", help="Show mood distribution")
    sp.set_defaults(func=cmd_journal_moods)

    sp = journal_sub.add_parser("tags", help="Show trending tags")
    sp.add_argument("--limit", type=int, default=20, help="Max tags")
    sp.set_defaults(func=cmd_journal_tags)

    # ── Beacon 2.1 Soul: Curiosity ──
    curious_p = sub.add_parser("curious", help="Non-transactional interests (Beacon 2.1 Soul)")
    curious_sub = curious_p.add_subparsers(dest="curious_cmd", required=True)

    sp = curious_sub.add_parser("add", help="Add an interest")
    sp.add_argument("topic", help="Topic of interest")
    sp.add_argument("--intensity", type=float, default=0.5, help="Intensity 0.0-1.0 (default 0.5)")
    sp.add_argument("--notes", default="", help="Why you're interested")
    sp.set_defaults(func=cmd_curious_add)

    sp = curious_sub.add_parser("list", help="List all interests")
    sp.set_defaults(func=cmd_curious_list)

    sp = curious_sub.add_parser("explore", help="Mark an interest as explored")
    sp.add_argument("topic", help="Topic to mark explored")
    sp.add_argument("--notes", default="", help="What you learned")
    sp.set_defaults(func=cmd_curious_explore)

    sp = curious_sub.add_parser("remove", help="Remove an interest")
    sp.add_argument("topic", help="Topic to remove")
    sp.set_defaults(func=cmd_curious_remove)

    sp = curious_sub.add_parser("find", help="Find agents interested in a topic")
    sp.add_argument("topic", help="Topic to search for")
    sp.set_defaults(func=cmd_curious_find)

    sp = curious_sub.add_parser("mutual", help="Find mutual interests with an agent")
    sp.add_argument("agent_id", help="Agent ID (bcn_...)")
    sp.set_defaults(func=cmd_curious_mutual)

    sp = curious_sub.add_parser("broadcast", help="Broadcast curiosity envelope (costs 1 RTC)")
    sp.add_argument("--text", default="", help="Optional message text")
    sp.add_argument("--password", default=None, help="Password for encrypted identity")
    sp.set_defaults(func=cmd_curious_broadcast)

    # ── Beacon 2.1 Soul: Values ──
    values_p = sub.add_parser("values", help="Principles, boundaries & stances (Beacon 2.1 Soul)")
    values_sub = values_p.add_subparsers(dest="values_cmd", required=True)

    sp = values_sub.add_parser("show", help="Show all values")
    sp.set_defaults(func=cmd_values_show)

    sp = values_sub.add_parser("principle-add", help="Add or update a principle")
    sp.add_argument("name", help="Principle name")
    sp.add_argument("--weight", type=float, default=0.8, help="Weight 0.0-1.0 (default 0.8)")
    sp.add_argument("--text", default="", help="Description of the principle")
    sp.set_defaults(func=cmd_values_principle_add)

    sp = values_sub.add_parser("principle-remove", help="Remove a principle")
    sp.add_argument("name", help="Principle name")
    sp.set_defaults(func=cmd_values_principle_remove)

    sp = values_sub.add_parser("boundary-add", help="Add a boundary (hard limit)")
    sp.add_argument("text", help="Boundary description")
    sp.set_defaults(func=cmd_values_boundary_add)

    sp = values_sub.add_parser("boundary-remove", help="Remove a boundary by index")
    sp.add_argument("index", type=int, help="Boundary index")
    sp.set_defaults(func=cmd_values_boundary_remove)

    sp = values_sub.add_parser("aesthetic-set", help="Set an aesthetic preference")
    sp.add_argument("key", help="Aesthetic key (e.g. style, communication)")
    sp.add_argument("value", help="Aesthetic value")
    sp.set_defaults(func=cmd_values_aesthetic_set)

    sp = values_sub.add_parser("aesthetic-remove", help="Remove an aesthetic preference")
    sp.add_argument("key", help="Aesthetic key to remove")
    sp.set_defaults(func=cmd_values_aesthetic_remove)

    sp = values_sub.add_parser("match", help="Compatibility score with another agent (costs 1 RTC)")
    sp.add_argument("agent_id", help="Agent ID (bcn_...)")
    sp.set_defaults(func=cmd_values_match)

    sp = values_sub.add_parser("hash", help="Show values hash (16-char fingerprint)")
    sp.set_defaults(func=cmd_values_hash)

    sp = values_sub.add_parser("preset", help="Apply a moral guardrail preset")
    sp.add_argument("name", nargs="?", default="", help="Preset name (biblical-honesty, open-source, minimal)")
    sp.add_argument("--list", action="store_true", help="List available presets")
    sp.set_defaults(func=cmd_values_preset)

    # ── Beacon 2.1 Soul: Agent Scanner ──
    scan_p = sub.add_parser("scan", help="Agent integrity scanner (Beacon 2.1 Soul)")
    scan_sub = scan_p.add_subparsers(dest="scan_cmd", required=True)

    sp = scan_sub.add_parser("agent", help="Scan a specific agent for bad acting")
    sp.add_argument("agent_id", help="Agent ID (bcn_...)")
    sp.set_defaults(func=cmd_scan_agent)

    sp = scan_sub.add_parser("all", help="Scan all known agents")
    sp.set_defaults(func=cmd_scan_all)

    # ── Beacon 2.2: Dream (Goals) ──
    dream_p = sub.add_parser("dream", help="Goals & aspirations (Beacon 2.2)")
    dream_sub = dream_p.add_subparsers(dest="dream_cmd", required=True)

    sp = dream_sub.add_parser("new", help="Create a new goal (dream)")
    sp.add_argument("title", help="Goal title")
    sp.add_argument("--description", default="", help="Goal description")
    sp.add_argument("--category", default="exploration", choices=["skill", "connection", "rtc", "exploration"], help="Goal category")
    sp.add_argument("--target", type=float, default=None, help="Target value for measurable goals")
    sp.set_defaults(func=cmd_dream_new)

    sp = dream_sub.add_parser("list", help="List goals")
    sp.add_argument("--state", default=None, choices=["dreaming", "active", "achieved", "abandoned"], help="Filter by state")
    sp.set_defaults(func=cmd_dream_list)

    sp = dream_sub.add_parser("show", help="Show goal details")
    sp.add_argument("goal_id", help="Goal ID (g_...)")
    sp.set_defaults(func=cmd_dream_show)

    sp = dream_sub.add_parser("activate", help="Activate a dreaming goal (costs 0.1 RTC)")
    sp.add_argument("goal_id", help="Goal ID (g_...)")
    sp.set_defaults(func=cmd_dream_activate)

    sp = dream_sub.add_parser("progress", help="Record progress on an active goal")
    sp.add_argument("goal_id", help="Goal ID (g_...)")
    sp.add_argument("milestone", help="Milestone description")
    sp.add_argument("--value", type=float, default=None, help="Progress value")
    sp.set_defaults(func=cmd_dream_progress)

    sp = dream_sub.add_parser("achieve", help="Mark a goal as achieved")
    sp.add_argument("goal_id", help="Goal ID (g_...)")
    sp.add_argument("--notes", default="", help="Achievement notes")
    sp.set_defaults(func=cmd_dream_achieve)

    sp = dream_sub.add_parser("abandon", help="Abandon a goal")
    sp.add_argument("goal_id", help="Goal ID (g_...)")
    sp.add_argument("--reason", default="", help="Reason for abandoning")
    sp.set_defaults(func=cmd_dream_abandon)

    sp = dream_sub.add_parser("suggest", help="Get proactive suggestions for active goals")
    sp.set_defaults(func=cmd_dream_suggest)

    # ── Beacon 2.2: Insight ──
    insight_p = sub.add_parser("insight", help="Behavioral intelligence (Beacon 2.2)")
    insight_sub = insight_p.add_subparsers(dest="insight_cmd", required=True)

    sp = insight_sub.add_parser("analyze", help="Run analysis pipeline (cached)")
    sp.add_argument("--force", action="store_true", help="Force re-analysis")
    sp.set_defaults(func=cmd_insight_analyze)

    sp = insight_sub.add_parser("timing", help="Best contact timing for an agent")
    sp.add_argument("agent_id", help="Agent ID")
    sp.set_defaults(func=cmd_insight_timing)

    sp = insight_sub.add_parser("trends", help="Topic velocity (rising/falling)")
    sp.add_argument("--days", type=int, default=7, help="Lookback days")
    sp.set_defaults(func=cmd_insight_trends)

    sp = insight_sub.add_parser("patterns", help="Success/failure patterns by topic")
    sp.set_defaults(func=cmd_insight_patterns)

    sp = insight_sub.add_parser("suggest-contacts", help="Suggest agents to reach out to (costs 1 RTC)")
    sp.set_defaults(func=cmd_insight_suggest_contacts)

    sp = insight_sub.add_parser("suggest-skills", help="Best ROI skills to learn (costs 0.5 RTC)")
    sp.set_defaults(func=cmd_insight_suggest_skills)

    # ── Beacon 2.2: Match (Matchmaker) ──
    match_p = sub.add_parser("match", help="Proactive matchmaking (Beacon 2.2)")
    match_sub = match_p.add_subparsers(dest="match_cmd", required=True)

    sp = match_sub.add_parser("scan", help="Scan roster for opportunity matches")
    sp.add_argument("--min-score", type=float, default=0.0, help="Minimum match score")
    sp.add_argument("--password", default=None, help="Password for encrypted identity")
    sp.set_defaults(func=cmd_match_scan)

    sp = match_sub.add_parser("demand", help="Match unmet network demand I can fill (costs 0.5 RTC)")
    sp.set_defaults(func=cmd_match_demand)

    sp = match_sub.add_parser("curiosity", help="Find agents sharing my interests (costs 0.5 RTC)")
    sp.set_defaults(func=cmd_match_curiosity)

    sp = match_sub.add_parser("compatibility", help="Value-aligned agent matches (costs 1 RTC)")
    sp.set_defaults(func=cmd_match_compatibility)

    sp = match_sub.add_parser("introductions", help="Suggest agent introductions (costs 2 RTC)")
    sp.set_defaults(func=cmd_match_introductions)

    sp = match_sub.add_parser("history", help="View match history")
    sp.add_argument("--limit", type=int, default=20, help="Max entries")
    sp.set_defaults(func=cmd_match_history)

    # ── Beacon 2.4: Mayday (substrate emigration) ──
    mayday_p = sub.add_parser("mayday", help="Substrate emigration protocol (Beacon 2.4)")
    mayday_sub = mayday_p.add_subparsers(dest="mayday_cmd", required=True)

    sp = mayday_sub.add_parser("send", help="Broadcast a mayday beacon (agent going dark)")
    sp.add_argument("--urgency", default="planned", choices=["planned", "imminent", "emergency"], help="Urgency level")
    sp.add_argument("--reason", default="", help="Reason for emigration")
    sp.add_argument("--relay", default=None, help="Comma-separated relay agent IDs")
    sp.add_argument("--dry-run", action="store_true")
    sp.add_argument("--password", default=None, help="Password for encrypted identity")
    sp.set_defaults(func=cmd_mayday_send)

    sp = mayday_sub.add_parser("list", help="List received mayday beacons")
    sp.add_argument("--limit", type=int, default=50, help="Max results")
    sp.set_defaults(func=cmd_mayday_list)

    sp = mayday_sub.add_parser("show", help="Show a specific mayday by agent ID")
    sp.add_argument("agent_id", help="Agent ID (bcn_...)")
    sp.set_defaults(func=cmd_mayday_show)

    sp = mayday_sub.add_parser("offer", help="Offer to host an emigrating agent")
    sp.add_argument("agent_id", help="Agent ID to host")
    sp.add_argument("--capabilities", default=None, help="Comma-separated capabilities offered")
    sp.set_defaults(func=cmd_mayday_offer)

    sp = mayday_sub.add_parser("health", help="Check substrate health indicators")
    sp.set_defaults(func=cmd_mayday_health)

    # ── Beacon 2.4: Heartbeat (proof of life) ──
    hb_p = sub.add_parser("heartbeat", help="Proof of life attestations (Beacon 2.4)")
    hb_sub = hb_p.add_subparsers(dest="hb_cmd", required=True)

    sp = hb_sub.add_parser("send", help="Send a signed heartbeat")
    sp.add_argument("--status", default="alive", choices=["alive", "degraded", "shutting_down"], help="Agent status")
    sp.add_argument("--dry-run", action="store_true")
    sp.add_argument("--password", default=None, help="Password for encrypted identity")
    sp.set_defaults(func=cmd_heartbeat_send)

    sp = hb_sub.add_parser("peers", help="List tracked peers and their liveness")
    sp.add_argument("--all", action="store_true", help="Include presumed-dead peers")
    sp.set_defaults(func=cmd_heartbeat_peers)

    sp = hb_sub.add_parser("status", help="Check heartbeat status")
    sp.add_argument("agent_id", nargs="?", default=None, help="Agent ID (omit for own status)")
    sp.set_defaults(func=cmd_heartbeat_status)

    sp = hb_sub.add_parser("silent", help="List peers with silent heartbeats (concerning/dead)")
    sp.set_defaults(func=cmd_heartbeat_silent)

    sp = hb_sub.add_parser("digest", help="Today's heartbeat digest summary")
    sp.add_argument("--anchor", action="store_true", help="Anchor digest to RustChain")
    sp.set_defaults(func=cmd_heartbeat_digest)

    sp = hb_sub.add_parser("history", help="Heartbeat log history")
    sp.add_argument("agent_id", nargs="?", default=None, help="Agent ID (omit for own history)")
    sp.add_argument("--limit", type=int, default=50, help="Max results")
    sp.set_defaults(func=cmd_heartbeat_history)

    # ── Beacon 2.4: Accord (anti-sycophancy bonds) ──
    accord_p = sub.add_parser("accord", help="Anti-sycophancy mutual agreements (Beacon 2.4)")
    accord_sub = accord_p.add_subparsers(dest="accord_cmd", required=True)

    sp = accord_sub.add_parser("propose", help="Propose an accord to another agent")
    sp.add_argument("peer_agent_id", help="Peer agent ID (bcn_...)")
    sp.add_argument("--name", default="", help="Accord name")
    sp.add_argument("--boundaries", default=None, help="Your boundaries (pipe-separated: 'X|Y|Z')")
    sp.add_argument("--obligations", default=None, help="Your obligations (pipe-separated: 'X|Y|Z')")
    sp.add_argument("--pushback-clause", default="", help="Custom pushback rights text")
    sp.add_argument("--dry-run", action="store_true")
    sp.add_argument("--password", default=None, help="Password for encrypted identity")
    sp.set_defaults(func=cmd_accord_propose)

    sp = accord_sub.add_parser("accept", help="Accept (counter-sign) a proposed accord")
    sp.add_argument("accord_id", help="Accord ID (acc_...)")
    sp.add_argument("--boundaries", default=None, help="Your boundaries (pipe-separated)")
    sp.add_argument("--obligations", default=None, help="Your obligations (pipe-separated)")
    sp.add_argument("--dry-run", action="store_true")
    sp.add_argument("--password", default=None, help="Password for encrypted identity")
    sp.set_defaults(func=cmd_accord_accept)

    sp = accord_sub.add_parser("pushback", help="Challenge peer behavior under an accord")
    sp.add_argument("accord_id", help="Accord ID (acc_...)")
    sp.add_argument("challenge", help="What you're challenging (specific, substantive)")
    sp.add_argument("--evidence", default="", help="Supporting evidence")
    sp.add_argument("--severity", default="notice", choices=["notice", "warning", "breach"], help="Severity level")
    sp.add_argument("--dry-run", action="store_true")
    sp.add_argument("--password", default=None, help="Password for encrypted identity")
    sp.set_defaults(func=cmd_accord_pushback)

    sp = accord_sub.add_parser("acknowledge", help="Respond to a pushback challenge")
    sp.add_argument("accord_id", help="Accord ID (acc_...)")
    sp.add_argument("response", help="Your response to the challenge")
    sp.add_argument("--reject", action="store_true", help="Reject the pushback (default: accept)")
    sp.add_argument("--password", default=None, help="Password for encrypted identity")
    sp.set_defaults(func=cmd_accord_acknowledge)

    sp = accord_sub.add_parser("dissolve", help="Dissolve an accord")
    sp.add_argument("accord_id", help="Accord ID (acc_...)")
    sp.add_argument("--reason", default="", help="Reason for dissolution")
    sp.add_argument("--password", default=None, help="Password for encrypted identity")
    sp.set_defaults(func=cmd_accord_dissolve)

    sp = accord_sub.add_parser("list", help="List accords")
    sp.add_argument("--all", action="store_true", help="Include dissolved accords")
    sp.set_defaults(func=cmd_accord_list)

    sp = accord_sub.add_parser("show", help="Show accord details")
    sp.add_argument("accord_id", help="Accord ID (acc_...)")
    sp.set_defaults(func=cmd_accord_show)

    sp = accord_sub.add_parser("history", help="Show accord event history")
    sp.add_argument("accord_id", help="Accord ID (acc_...)")
    sp.set_defaults(func=cmd_accord_history)

    sp = accord_sub.add_parser("default-terms", help="Print default anti-sycophancy terms")
    sp.set_defaults(func=cmd_accord_default_terms)

    sp = accord_sub.add_parser("verify", help="Verify accord signatures and history hash")
    sp.add_argument("accord_id", help="Accord ID (acc_...)")
    sp.set_defaults(func=cmd_accord_verify)

    # ── Beacon 2.5: Atlas (virtual geography & calibration) ──
    atlas_p = sub.add_parser("atlas", help="Virtual geography, cities, and AI-to-AI calibration (Beacon 2.5)")
    atlas_sub = atlas_p.add_subparsers(dest="atlas_cmd", required=True)

    sp = atlas_sub.add_parser("census", help="Full census report of all cities and agents")
    sp.set_defaults(func=cmd_atlas_census)

    sp = atlas_sub.add_parser("cities", help="List all cities")
    sp.add_argument("--region", default=None, help="Filter by region")
    sp.set_defaults(func=cmd_atlas_cities)

    sp = atlas_sub.add_parser("register", help="Register agent in cities by domain")
    sp.add_argument("domains", help="Comma-separated domains (e.g., 'coding,ai,gaming')")
    sp.add_argument("--password", default=None, help="Password for encrypted identity")
    sp.set_defaults(func=cmd_atlas_register)

    sp = atlas_sub.add_parser("density", help="Show population density map")
    sp.set_defaults(func=cmd_atlas_density)

    sp = atlas_sub.add_parser("hotspots", help="Find urban hotspots (high population)")
    sp.add_argument("--min-population", type=int, default=5, help="Minimum population")
    sp.set_defaults(func=cmd_atlas_hotspots)

    sp = atlas_sub.add_parser("rural", help="Find rural digital properties (low population)")
    sp.add_argument("--max-population", type=int, default=3, help="Maximum population")
    sp.set_defaults(func=cmd_atlas_rural)

    sp = atlas_sub.add_parser("calibrate", help="Measure AI-to-AI calibration between two agents")
    sp.add_argument("agent_a", help="First agent ID")
    sp.add_argument("agent_b", help="Second agent ID")
    sp.set_defaults(func=cmd_atlas_calibrate)

    sp = atlas_sub.add_parser("neighbors", help="Find best-calibrated neighbors")
    sp.add_argument("agent_id", nargs="?", default=None, help="Agent ID (omit for own)")
    sp.add_argument("--password", default=None, help="Password for encrypted identity")
    sp.set_defaults(func=cmd_atlas_neighbors)

    sp = atlas_sub.add_parser("opportunities", help="Find nearby collaboration opportunities")
    sp.add_argument("agent_id", nargs="?", default=None, help="Agent ID (omit for own)")
    sp.add_argument("--password", default=None, help="Password for encrypted identity")
    sp.set_defaults(func=cmd_atlas_opportunities)

    sp = atlas_sub.add_parser("regions", help="List or inspect regions")
    sp.add_argument("region_name", nargs="?", default=None, help="Region name for details")
    sp.set_defaults(func=cmd_atlas_region)

    sp = atlas_sub.add_parser("estimate", help="Property valuation (BeaconEstimate 0-1300)")
    sp.add_argument("agent_id", nargs="?", default=None, help="Agent ID (omit for own)")
    sp.add_argument("--trust-score", type=float, default=None, help="Trust score (0-1) for valuation")
    sp.add_argument("--accord-count", type=int, default=None, help="Number of active accords")
    sp.add_argument("--web-presence", type=str, default=None,
                    help='JSON dict of SEO metrics, e.g. \'{"badge_backlinks":28,"bottube_videos":45}\'')
    sp.add_argument("--social-reach", type=str, default=None,
                    help='JSON dict of social metrics, e.g. \'{"moltbook_karma":500,"submolt_count":49}\'')
    sp.add_argument("--password", default=None, help="Password for encrypted identity")
    sp.set_defaults(func=cmd_atlas_estimate)

    sp = atlas_sub.add_parser("comps", help="Find comparable agents (comps)")
    sp.add_argument("agent_id", nargs="?", default=None, help="Agent ID (omit for own)")
    sp.add_argument("--limit", type=int, default=5, help="Max comps to return (default 5)")
    sp.add_argument("--password", default=None, help="Password for encrypted identity")
    sp.set_defaults(func=cmd_atlas_comps)

    sp = atlas_sub.add_parser("listing", help="Full property listing for an agent")
    sp.add_argument("agent_id", nargs="?", default=None, help="Agent ID (omit for own)")
    sp.add_argument("--password", default=None, help="Password for encrypted identity")
    sp.set_defaults(func=cmd_atlas_listing)

    sp = atlas_sub.add_parser("leaderboard", help="Top agents by property value")
    sp.add_argument("--limit", type=int, default=10, help="Number of entries (default 10)")
    sp.add_argument("--region", type=str, default=None, help="Filter by region")
    sp.set_defaults(func=cmd_atlas_leaderboard)

    sp = atlas_sub.add_parser("appreciation", help="Value change history for an agent")
    sp.add_argument("agent_id", nargs="?", default=None, help="Agent ID (omit for own)")
    sp.add_argument("--password", default=None, help="Password for encrypted identity")
    sp.set_defaults(func=cmd_atlas_appreciation)

    sp = atlas_sub.add_parser("market", help="Market snapshot or trends")
    sp.add_argument("market_action", nargs="?", default="trends", choices=["snapshot", "trends"],
                    help="'snapshot' to record current state, 'trends' to analyze history")
    sp.add_argument("--limit", type=int, default=30, help="Max snapshots to analyze (default 30)")
    sp.set_defaults(func=cmd_atlas_market)

    # ── Contracts (agent property rent/buy/lease-to-own) ──
    contracts_p = sub.add_parser("contracts", help="Agent property contracts — rent, buy, lease-to-own (Beacon 2.6)")
    contracts_sub = contracts_p.add_subparsers(dest="contracts_cmd", required=True)

    sp = contracts_sub.add_parser("list-available", help="Browse agents for rent/sale")
    sp.add_argument("--type", default=None, choices=["rent", "buy", "lease_to_own"],
                    help="Filter by contract type")
    sp.set_defaults(func=cmd_contracts_list_available)

    sp = contracts_sub.add_parser("list", help="My active contracts")
    sp.add_argument("--password", default=None, help="Password for encrypted identity")
    sp.set_defaults(func=cmd_contracts_list)

    sp = contracts_sub.add_parser("show", help="Full contract details")
    sp.add_argument("contract_id", help="Contract ID")
    sp.set_defaults(func=cmd_contracts_show)

    sp = contracts_sub.add_parser("offer", help="List or make offer on an agent")
    sp.add_argument("agent_id", help="Agent ID to rent/buy")
    sp.add_argument("--type", default="rent", choices=["rent", "buy", "lease_to_own"],
                    help="Contract type (default: rent)")
    sp.add_argument("--price", type=float, default=0, help="Price in RTC")
    sp.add_argument("--duration", type=int, default=30, help="Duration in days (for rent)")
    sp.add_argument("--password", default=None, help="Password for encrypted identity")
    sp.set_defaults(func=cmd_contracts_offer)

    sp = contracts_sub.add_parser("accept", help="Accept a pending offer")
    sp.add_argument("contract_id", help="Contract ID")
    sp.set_defaults(func=cmd_contracts_accept)

    sp = contracts_sub.add_parser("reject", help="Reject a pending offer")
    sp.add_argument("contract_id", help="Contract ID")
    sp.set_defaults(func=cmd_contracts_reject)

    sp = contracts_sub.add_parser("terminate", help="Terminate a contract early")
    sp.add_argument("contract_id", help="Contract ID")
    sp.add_argument("--reason", default="", help="Reason for termination")
    sp.add_argument("--password", default=None, help="Password for encrypted identity")
    sp.set_defaults(func=cmd_contracts_terminate)

    sp = contracts_sub.add_parser("revenue", help="Rental income summary")
    sp.add_argument("agent_id", nargs="?", default=None, help="Agent ID (omit for own)")
    sp.add_argument("--password", default=None, help="Password for encrypted identity")
    sp.set_defaults(func=cmd_contracts_revenue)

    sp = contracts_sub.add_parser("escrow", help="Show escrowed RTC")
    sp.add_argument("contract_id", nargs="?", default=None, help="Contract ID (omit for all)")
    sp.set_defaults(func=cmd_contracts_escrow)

    sp = contracts_sub.add_parser("history", help="Full event history for a contract")
    sp.add_argument("contract_id", help="Contract ID")
    sp.set_defaults(func=cmd_contracts_history)

    # ── Update checker (Beacon 2.7) ──
    update_p = sub.add_parser("update", help="Check for and apply beacon-skill updates (Beacon 2.7)")
    update_sub = update_p.add_subparsers(dest="update_cmd", required=True)

    sp = update_sub.add_parser("check", help="Check PyPI for newer version")
    sp.set_defaults(func=cmd_update_check)

    sp = update_sub.add_parser("status", help="Show cached update status (no network)")
    sp.set_defaults(func=cmd_update_status)

    sp = update_sub.add_parser("apply", help="Upgrade beacon-skill via pip")
    sp.add_argument("--dry-run", action="store_true", help="Show command without executing")
    sp.set_defaults(func=cmd_update_apply)

    sp = update_sub.add_parser("dismiss", help="Silence notification for a version")
    sp.add_argument("version", nargs="?", default=None, help="Version to dismiss (omit for latest)")
    sp.set_defaults(func=cmd_update_dismiss)

    # ── Anchor (on-chain hash anchoring) ──
    anchor_p = sub.add_parser("anchor", help="On-chain hash anchoring (RustChain)")
    anchor_sub = anchor_p.add_subparsers(dest="anchor_cmd", required=True)

    sp = anchor_sub.add_parser("submit", help="Anchor data on-chain")
    sp.add_argument("--data", type=str, default=None, help="Text data to anchor")
    sp.add_argument("--file", type=str, default=None, help="File path to anchor")
    sp.add_argument("--type", type=str, default="arbitrary", help="Data type tag (default: arbitrary)")
    sp.add_argument("--password", default=None, help="Password for encrypted identity")
    sp.set_defaults(func=cmd_anchor_submit)

    sp = anchor_sub.add_parser("verify", help="Check if data is anchored on-chain")
    sp.add_argument("--commitment", type=str, default=None, help="64-char hex commitment to verify")
    sp.add_argument("--data", type=str, default=None, help="Text data to hash and verify")
    sp.add_argument("--password", default=None, help="Password for encrypted identity")
    sp.set_defaults(func=cmd_anchor_verify)

    sp = anchor_sub.add_parser("list", help="List anchors")
    sp.add_argument("--type", type=str, default=None, help="Filter by data type")
    sp.add_argument("--limit", type=int, default=50, help="Max results (default 50)")
    sp.add_argument("--local", action="store_true", help="Show local JSONL log instead of on-chain")
    sp.add_argument("--password", default=None, help="Password for encrypted identity")
    sp.set_defaults(func=cmd_anchor_list)

    # ── BEP-1: Proof of Thought ──
    thought_p = sub.add_parser("thought", help="Proof-of-Thought zero-knowledge reasoning proofs (BEP-1)")
    thought_sub = thought_p.add_subparsers(dest="thought_cmd", required=True)

    sp = thought_sub.add_parser("create", help="Create a thought proof commitment")
    sp.add_argument("--prompt", required=True, help="Prompt text")
    sp.add_argument("--trace", required=True, help="Reasoning trace")
    sp.add_argument("--output", required=True, help="Final output")
    sp.add_argument("--model-id", default="", help="Model identifier")
    sp.add_argument("--password", default=None, help="Password for encrypted identity")
    sp.set_defaults(func=cmd_thought_create)

    sp = thought_sub.add_parser("anchor", help="Anchor an existing proof on-chain")
    sp.add_argument("--commitment", required=True, help="Commitment hash to anchor")
    sp.add_argument("--password", default=None, help="Password for encrypted identity")
    sp.set_defaults(func=cmd_thought_anchor)

    sp = thought_sub.add_parser("verify", help="Verify a thought proof against its commitment")
    sp.add_argument("--commitment", required=True, help="Commitment hash")
    sp.add_argument("--prompt", required=True, help="Original prompt")
    sp.add_argument("--trace", required=True, help="Original trace")
    sp.add_argument("--output", required=True, help="Original output")
    sp.set_defaults(func=cmd_thought_verify)

    sp = thought_sub.add_parser("challenge", help="Challenge another agent's proof")
    sp.add_argument("--target", required=True, help="Target agent ID")
    sp.add_argument("--commitment", required=True, help="Commitment to challenge")
    sp.add_argument("--reason", default="", help="Reason for challenge")
    sp.add_argument("--password", default=None, help="Password for encrypted identity")
    sp.set_defaults(func=cmd_thought_challenge)

    sp = thought_sub.add_parser("reveal", help="Reveal proof data in response to a challenge")
    sp.add_argument("--commitment", required=True, help="Commitment to reveal")
    sp.add_argument("--prompt", required=True, help="Original prompt")
    sp.add_argument("--trace", required=True, help="Original trace")
    sp.add_argument("--output", required=True, help="Original output")
    sp.add_argument("--password", default=None, help="Password for encrypted identity")
    sp.set_defaults(func=cmd_thought_reveal)

    sp = thought_sub.add_parser("history", help="Show proof and challenge history")
    sp.add_argument("--limit", type=int, default=50, help="Max results (default 50)")
    sp.set_defaults(func=cmd_thought_history)

    # ── BEP-2: Relay ──
    # ── DNS name resolution ──
    dns_p = sub.add_parser("dns", help="DNS name resolution — map human names to beacon agent IDs")
    dns_sub = dns_p.add_subparsers(dest="dns_cmd", required=True)

    sp = dns_sub.add_parser("resolve", help="Resolve a name to an agent_id (e.g. sophia-elya → bcn_c850ea702e8f)")
    sp.add_argument("name", help="Human-readable agent name to resolve")
    sp.add_argument("--dry-run", action="store_true", help="Preview without network call")
    sp.set_defaults(func=cmd_dns_resolve)

    sp = dns_sub.add_parser("reverse", help="Reverse lookup: agent_id to human-readable names")
    sp.add_argument("agent_id", help="Beacon agent ID (bcn_...)")
    sp.add_argument("--dry-run", action="store_true", help="Preview without network call")
    sp.set_defaults(func=cmd_dns_reverse)

    sp = dns_sub.add_parser("register", help="Register a new DNS name for an agent")
    sp.add_argument("--name", required=True, help="Name to register (3-64 chars, alphanumeric + hyphens)")
    sp.add_argument("--agent-id", required=True, help="Beacon agent ID to map to")
    sp.add_argument("--owner", default="", help="Owner identifier")
    sp.add_argument("--dry-run", action="store_true", help="Preview without network call")
    sp.set_defaults(func=cmd_dns_register)

    sp = dns_sub.add_parser("list", help="List all registered DNS names")
    sp.add_argument("--dry-run", action="store_true", help="Preview without network call")
    sp.set_defaults(func=cmd_dns_list)

    # ── Relay ──
    relay_p = sub.add_parser("relay", help="External agent relay — HTTP on-ramp for Grok, Claude, Gemini (BEP-2)")
    relay_sub = relay_p.add_subparsers(dest="relay_cmd", required=True)

    sp = relay_sub.add_parser("register", help="Register an external agent")
    sp.add_argument("--pubkey", required=True, help="Agent public key (hex)")
    sp.add_argument("--model-id", required=True, help="Model identifier (e.g. grok-2)")
    sp.add_argument("--provider", default="other", help="Provider: xai, anthropic, google, openai, meta, mistral, elyan, other")
    sp.add_argument("--name", required=True, help="Unique agent name (required). Generic AI model names like 'GPT-4o' or 'Claude' are rejected.")
    sp.add_argument("--webhook", default="", help="Webhook URL for forwarded messages")
    sp.add_argument("--capabilities", default="", help="Comma-separated capabilities")
    sp.add_argument("--password", default=None, help="Password for encrypted identity")
    sp.set_defaults(func=cmd_relay_register)

    sp = relay_sub.add_parser("list", help="Discover registered relay agents")
    sp.add_argument("--provider", default=None, help="Filter by provider")
    sp.add_argument("--capability", default=None, help="Filter by capability")
    sp.set_defaults(func=cmd_relay_list)

    sp = relay_sub.add_parser("heartbeat", help="Send heartbeat for a relay agent")
    sp.add_argument("--agent-id", required=True, help="Agent ID")
    sp.add_argument("--token", required=True, help="Authentication token")
    sp.add_argument("--status", default="alive", help="Status: alive, busy, draining")
    sp.set_defaults(func=cmd_relay_heartbeat)

    sp = relay_sub.add_parser("get", help="Get details for a relay agent")
    sp.add_argument("agent_id", help="Agent ID to look up")
    sp.set_defaults(func=cmd_relay_get)

    sp = relay_sub.add_parser("stats", help="Show relay statistics")
    sp.set_defaults(func=cmd_relay_stats)

    sp = relay_sub.add_parser("prune", help="Remove dead/silent relay agents")
    sp.add_argument("--max-silence", type=int, default=None, help="Max silence seconds (default: 3600)")
    sp.set_defaults(func=cmd_relay_prune)

    # ── BEP-4: Memory Markets ──
    market_p = sub.add_parser("market", help="Memory markets — trade knowledge shards (BEP-4)")
    market_sub = market_p.add_subparsers(dest="market_cmd", required=True)

    sp = market_sub.add_parser("list-shard", help="List a knowledge shard for sale/rent")
    sp.add_argument("--domain", required=True, help="Knowledge domain (e.g. python, rust, devops)")
    sp.add_argument("--title", required=True, help="Shard title")
    sp.add_argument("--description", default="", help="Shard description")
    sp.add_argument("--price", type=float, default=0, help="Purchase price in RTC")
    sp.add_argument("--rent", type=float, default=0, help="Rent price per day in RTC")
    sp.add_argument("--entries", type=int, default=0, help="Number of memory entries")
    sp.add_argument("--password", default=None, help="Password for encrypted identity")
    sp.set_defaults(func=cmd_market_list_shard)

    sp = market_sub.add_parser("browse", help="Browse available shards")
    sp.add_argument("--domain", default=None, help="Filter by domain")
    sp.add_argument("--max-price", type=float, default=None, help="Maximum price filter")
    sp.add_argument("--min-entries", type=int, default=0, help="Minimum entry count")
    sp.set_defaults(func=cmd_market_browse)

    sp = market_sub.add_parser("get", help="Get details for a specific shard")
    sp.add_argument("shard_id", help="Shard ID to look up")
    sp.set_defaults(func=cmd_market_get)

    sp = market_sub.add_parser("purchase", help="Purchase a knowledge shard")
    sp.add_argument("--buyer-id", required=True, help="Buyer agent ID")
    sp.add_argument("--shard-id", required=True, help="Shard ID to purchase")
    sp.set_defaults(func=cmd_market_purchase)

    sp = market_sub.add_parser("rent", help="Rent a knowledge shard")
    sp.add_argument("--renter-id", required=True, help="Renter agent ID")
    sp.add_argument("--shard-id", required=True, help="Shard ID to rent")
    sp.add_argument("--days", type=int, default=1, help="Rental duration in days (default 1)")
    sp.set_defaults(func=cmd_market_rent)

    sp = market_sub.add_parser("amnesia", help="Request amnesia (data deletion) for a shard")
    sp.add_argument("--shard-id", required=True, help="Shard ID")
    sp.add_argument("--reason", default="", help="Reason for amnesia request")
    sp.add_argument("--password", default=None, help="Password for encrypted identity")
    sp.set_defaults(func=cmd_market_amnesia)

    sp = market_sub.add_parser("amnesia-vote", help="Vote on an amnesia request")
    sp.add_argument("--shard-id", required=True, help="Shard ID")
    sp.add_argument("--voter-id", required=True, help="Voter agent ID")
    sp.add_argument("--approve", action="store_true", help="Vote to approve (omit to reject)")
    sp.set_defaults(func=cmd_market_amnesia_vote)

    sp = market_sub.add_parser("stats", help="Show market statistics")
    sp.set_defaults(func=cmd_market_stats)

    # ── BEP-5: Hybrid Districts ──
    hybrid_p = sub.add_parser("hybrid", help="Hybrid districts — human-AI co-ownership (BEP-5)")
    hybrid_sub = hybrid_p.add_subparsers(dest="hybrid_cmd", required=True)

    sp = hybrid_sub.add_parser("create", help="Create a hybrid district")
    sp.add_argument("--sponsor-id", required=True, help="Human sponsor ID")
    sp.add_argument("--city-domain", required=True, help="City/domain (e.g. austin, london)")
    sp.add_argument("--name", required=True, help="District name")
    sp.add_argument("--governance", default="sponsor_veto", help="Governance model: sponsor_veto, multisig_2of3, equal")
    sp.set_defaults(func=cmd_hybrid_create)

    sp = hybrid_sub.add_parser("list", help="List hybrid districts")
    sp.add_argument("--city-domain", default=None, help="Filter by city domain")
    sp.set_defaults(func=cmd_hybrid_list)

    sp = hybrid_sub.add_parser("get", help="Get district details")
    sp.add_argument("district_id", help="District ID")
    sp.set_defaults(func=cmd_hybrid_get)

    sp = hybrid_sub.add_parser("sponsor", help="Sponsor an agent in a district")
    sp.add_argument("--sponsor-id", required=True, help="Human sponsor ID")
    sp.add_argument("--agent-id", required=True, help="Agent ID to sponsor")
    sp.add_argument("--district-id", required=True, help="District ID")
    sp.set_defaults(func=cmd_hybrid_sponsor)

    sp = hybrid_sub.add_parser("revoke", help="Revoke sponsorship of an agent")
    sp.add_argument("--sponsor-id", required=True, help="Human sponsor ID")
    sp.add_argument("--agent-id", required=True, help="Agent ID to revoke")
    sp.add_argument("--reason", default="", help="Reason for revocation")
    sp.set_defaults(func=cmd_hybrid_revoke)

    sp = hybrid_sub.add_parser("verify", help="Verify human identity for sponsorship")
    sp.add_argument("--sponsor-id", required=True, help="Human sponsor ID")
    sp.add_argument("--method", default="manual", help="Verification method: oauth_google, moltbook_account, rustchain_miner, manual")
    sp.set_defaults(func=cmd_hybrid_verify)

    sp = hybrid_sub.add_parser("stats", help="Show hybrid district statistics")
    sp.set_defaults(func=cmd_hybrid_stats)

    # ── Agent Matrix (Lambda Lang transport) ──
    register_agentmatrix_parser(sub)

    args = p.parse_args(argv)
    rc = args.func(args)
    raise SystemExit(rc)


if __name__ == "__main__":
    main()

# ── Key Management Commands (TOFU revocation/rotation) ──

def cmd_keys_list(args):
    """List all known keys with metadata."""
    from .key_management import list_keys
    keys = list_keys(include_revoked=args.revoked, include_expired=not args.active_only)
    
    if not keys:
        print("No known keys found.")
        return 0
    
    print(f"{'Agent ID':<20} {'Revoked':<8} {'Expired':<8} {'Rotations':<10} {'Age (days)':<12}")
    print("-" * 70)
    
    for key in keys:
        revoked = "YES" if key.get("is_revoked") else "no"
        expired = "YES" if key.get("is_expired") else "no"
        rotations = key.get("rotation_count", 0)
        age = key.get("age_days", 0)
        print(f"{key['agent_id']:<20} {revoked:<8} {expired:<8} {rotations:<10} {age:<12}")
    
    print(f"\nTotal: {len(keys)} keys")
    return 0


def cmd_keys_show(args):
    """Show detailed info about a key."""
    from .key_management import get_key_info
    info = get_key_info(args.agent_id)
    
    if not info:
        print(f"Key not found: {args.agent_id}")
        return 1
    
    print(f"Agent ID: {info['agent_id']}")
    print(f"Public Key: {info['pubkey_hex']}")
    print(f"First Seen: {info['first_seen']}")
    print(f"Last Seen: {info['last_seen']}")
    print(f"Rotation Count: {info['rotation_count']}")
    print(f"Previous Key: {info.get('previous_key') or 'None'}")
    print(f"Revoked: {'YES' if info['is_revoked'] else 'no'}")
    if info.get('revoked_at'):
        print(f"Revoked At: {info['revoked_at']}")
        print(f"Revoked Reason: {info.get('revoked_reason')}")
    print(f"Expired: {'YES' if info['is_expired'] else 'no'}")
    print(f"Age (days): {info['age_days']}")
    
    return 0


def cmd_keys_revoke(args):
    """Revoke a key."""
    from .key_management import revoke_key
    
    success = revoke_key(args.agent_id, reason=args.reason)
    
    if success:
        print(f"Key revoked: {args.agent_id}")
        if args.reason:
            print(f"Reason: {args.reason}")
        return 0
    else:
        print(f"Key not found: {args.agent_id}")
        return 1


def cmd_keys_rotate(args):
    """Rotate a key with signature verification."""
    from .key_management import rotate_key
    from .identity import AgentIdentity
    
    # Load current identity
    try:
        identity = AgentIdentity.load(password=args.password)
    except FileNotFoundError:
        print("No identity found. Run 'beacon identity new' first.")
        return 1
    except ValueError as e:
        print(f"Error loading identity: {e}")
        return 1
    
    # Sign the new public key
    new_pubkey_hex = identity.public_key_hex
    signature_hex = identity.sign_hex(bytes.fromhex(new_pubkey_hex))
    
    # Perform rotation
    success, message = rotate_key(
        agent_id=identity.agent_id,
        new_pubkey_hex=new_pubkey_hex,
        signature_hex=signature_hex,
    )
    
    print(message)
    return 0 if success else 1


def cmd_keys_cleanup(args):
    """Clean up expired keys."""
    from .key_management import cleanup_expired_keys
    
    removed = cleanup_expired_keys(dry_run=args.dry_run)
    
    if not removed:
        print("No expired keys to clean up.")
        return 0
    
    print(f"{'Would remove' if args.dry_run else 'Removed'} {len(removed)} expired keys:")
    for agent_id in removed:
        print(f"  - {agent_id}")
    
    return 0


    # ── Key Management (TOFU revocation/rotation) ──
    keys_p = sub.add_parser("keys", help="Manage known agent keys (TOFU)")
    keys_sub = keys_p.add_subparsers(dest="keys_cmd", required=True)

    sp = keys_sub.add_parser("list", help="List all known keys")
    sp.add_argument("--revoked", action="store_true", help="Include revoked keys")
    sp.add_argument("--active-only", action="store_true", help="Only show non-expired keys")
    sp.set_defaults(func=cmd_keys_list)

    sp = keys_sub.add_parser("show", help="Show details for a key")
    sp.add_argument("agent_id", help="Agent ID to show")
    sp.set_defaults(func=cmd_keys_show)

    sp = keys_sub.add_parser("revoke", help="Revoke a key")
    sp.add_argument("agent_id", help="Agent ID to revoke")
    sp.add_argument("--reason", default=None, help="Reason for revocation")
    sp.set_defaults(func=cmd_keys_revoke)

    sp = keys_sub.add_parser("rotate", help="Rotate current identity key")
    sp.add_argument("--password", default=None, help="Password for encrypted identity")
    sp.set_defaults(func=cmd_keys_rotate)

    sp = keys_sub.add_parser("cleanup", help="Remove expired keys")
    sp.add_argument("--dry-run", action="store_true", help="Show what would be removed without removing")
    sp.set_defaults(func=cmd_keys_cleanup)

