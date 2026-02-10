import argparse
import json
import sys
import time
from typing import Any, Dict, List, Optional

from .codec import decode_envelopes, encode_envelope
from .config import load_config, write_default_config
from .storage import append_jsonl
from .transports import BoTTubeClient, MoltbookClient, RustChainClient, RustChainKeypair, udp_listen, udp_send


def _cfg_get(cfg: Dict[str, Any], *path: str, default: Any = None) -> Any:
    cur: Any = cfg
    for p in path:
        if not isinstance(cur, dict) or p not in cur:
            return default
        cur = cur[p]
    return cur


def _build_envelope(cfg: Dict[str, Any], kind: str, to: str, links: List[str], extra: Dict[str, Any]) -> str:
    payload: Dict[str, Any] = {
        "v": 1,
        "kind": kind,
        "from": _cfg_get(cfg, "beacon", "agent_name", default=""),
        "to": to,
        "ts": int(time.time()),
    }
    if links:
        payload["links"] = links
    payload.update(extra)
    return encode_envelope(payload)


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
        # Never block primary actions on UDP.
        return


def cmd_init(args: argparse.Namespace) -> int:
    path = write_default_config(overwrite=args.overwrite)
    print(str(path))
    return 0


def cmd_decode(args: argparse.Namespace) -> int:
    if args.file:
        text = args.file.read()
    else:
        text = sys.stdin.read()
    envs = decode_envelopes(text)
    print(json.dumps({"count": len(envs), "envelopes": envs}, indent=2))
    return 0


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
            # int / float best-effort
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

    links = args.link or []
    extra: Dict[str, Any] = {}
    if args.bounty_url:
        extra["bounty_url"] = args.bounty_url
    if args.reward_rtc is not None:
        extra["reward_rtc"] = float(args.reward_rtc)

    # Arbitrary extra fields for "download/follow/leader/game" style messages.
    try:
        extra.update(_parse_kv_fields(args.field or []))
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2

    text = args.text or ""
    if args.envelope_kind:
        if not text:
            text = _default_human_message(args.envelope_kind, links, args.bounty_url, args.reward_rtc)
        env = _build_envelope(cfg, args.envelope_kind, f"udp:{host}:{port}", links, extra)
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
    bind_host = args.bind
    port = int(args.port)
    max_count = int(args.count) if args.count is not None else None
    write_inbox = not bool(args.no_write)

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
        }
        if write_inbox:
            append_jsonl("inbox.jsonl", rec)
        print(json.dumps(rec, indent=2))
        sys.stdout.flush()

        if max_count is not None and seen["n"] >= max_count:
            raise KeyboardInterrupt()

    try:
        udp_listen(bind_host, port, on_msg, timeout_s=args.timeout)
    except KeyboardInterrupt:
        pass
    return 0


def cmd_bottube_ping_agent(args: argparse.Namespace) -> int:
    cfg = load_config()
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
        env = _build_envelope(cfg, args.envelope_kind, f"bottube:@{args.agent_name}", links, extra)
        if comment:
            comment = f"{comment}\n\n{env}"
        else:
            comment = env

    tip_msg = args.tip_message or ""
    if args.tip is not None and not tip_msg and comment:
        # Tip message is capped server-side; keep a short prefix.
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
        env = _build_envelope(cfg, args.envelope_kind, f"bottube:video:{args.video_id}", links, extra)
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
    client = MoltbookClient(
        base_url=_cfg_get(cfg, "moltbook", "base_url", default="https://www.moltbook.com"),
        api_key=_cfg_get(cfg, "moltbook", "api_key", default=None) or None,
    )

    content = args.content
    if args.envelope_kind:
        env = _build_envelope(cfg, args.envelope_kind, f"moltbook:m/{args.submolt}", args.link or [], {})
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


def cmd_rustchain_wallet_new(args: argparse.Namespace) -> int:
    kp = RustChainKeypair.generate()
    print(json.dumps({
        "address": kp.address,
        "public_key_hex": kp.public_key_hex,
        "private_key_hex": kp.private_key_hex,
    }, indent=2))
    return 0


def cmd_rustchain_balance(args: argparse.Namespace) -> int:
    cfg = load_config()
    client = RustChainClient(
        base_url=_cfg_get(cfg, "rustchain", "base_url", default="https://50.28.86.131"),
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
        base_url=_cfg_get(cfg, "rustchain", "base_url", default="https://50.28.86.131"),
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


def main(argv: Optional[List[str]] = None) -> None:
    p = argparse.ArgumentParser(prog="beacon", description="Beacon - agent economy ping system")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("init", help="Create ~/.beacon/config.json")
    sp.add_argument("--overwrite", action="store_true")
    sp.set_defaults(func=cmd_init)

    sp = sub.add_parser("decode", help="Extract [BEACON v1] envelopes from text (stdin or --file)")
    sp.add_argument("--file", type=argparse.FileType("r", encoding="utf-8"), default=None)
    sp.set_defaults(func=cmd_decode)

    # UDP
    u = sub.add_parser("udp", help="Local UDP beacon bus (broadcast/listen)")
    usub = u.add_subparsers(dest="ucmd", required=True)

    sp = usub.add_parser("send", help="Send a UDP beacon message")
    sp.add_argument("host", help="Destination host/IP (use 255.255.255.255 for broadcast)")
    sp.add_argument("port", type=int, help="Destination port")
    sp.add_argument("--broadcast", action="store_true", help="Enable UDP broadcast socket option")
    sp.add_argument("--ttl", type=int, default=None, help="IP TTL (optional)")
    sp.add_argument("--text", default="", help="Human message text")
    sp.add_argument("--envelope-kind", default=None, help="Embed a [BEACON v1] envelope (kind: like|want|bounty|ad|hello|link)")
    sp.add_argument("--link", action="append", default=[], help="Attach a link (repeatable)")
    sp.add_argument("--bounty-url", default=None, help="Attach a bounty URL")
    sp.add_argument("--reward-rtc", type=float, default=None, help="Attach a bounty reward (RTC)")
    sp.add_argument("--field", action="append", default=[], help="Attach extra fields (k=v) for agent ops: download/follow/leader/game")
    sp.add_argument("--dry-run", action="store_true")
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
        pp.add_argument("--envelope-kind", default=None, help="Embed a [BEACON v1] JSON envelope (kind: like|want|bounty|ad|hello|link)")
        pp.add_argument("--link", action="append", default=[], help="Attach a link (repeatable)")
        pp.add_argument("--bounty-url", default=None, help="Attach a bounty URL")
        pp.add_argument("--reward-rtc", type=float, default=None, help="Attach a bounty reward (RTC)")
        pp.add_argument("--dry-run", action="store_true")

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
    sp.set_defaults(func=cmd_moltbook_post)

    # RustChain
    r = sub.add_parser("rustchain", help="RustChain payments (signed transfers)")
    rsub = r.add_subparsers(dest="rcmd", required=True)

    sp = rsub.add_parser("wallet-new", help="Generate a new Ed25519 keypair + RTC address")
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

    args = p.parse_args(argv)
    rc = args.func(args)
    raise SystemExit(rc)


if __name__ == "__main__":
    main()
