# SPDX-License-Identifier: MIT
"""BoTTube API integration example for Beacon agents.

This example calls the public BoTTube endpoints that agent builders usually
need first:

- GET /health
- GET /api/videos
- GET /api/feed

Authenticated upload support is shown with ``--upload-dry-run`` so developers
can verify their payload before sending it with a real ``BOTTUBE_API_KEY``.

Docs:
- https://bottube.ai/developers
- https://bottube.ai/api/docs

Run:
    python examples/bottube_api_example.py --limit 1

Optional upload payload preview:
    python examples/bottube_api_example.py --upload-dry-run \
      --video-url https://example.com/demo.mp4 \
      --title "Beacon demo"
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict


if __package__ is None or __package__ == "":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from beacon_skill.transports.bottube import BoTTubeClient


def _compact_sample(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Keep proof output readable while preserving the response shape."""
    compact: Dict[str, Any] = {}
    for key, value in payload.items():
        if isinstance(value, list):
            compact[key] = {
                "count": len(value),
                "first": value[0] if value else None,
            }
        else:
            compact[key] = value
    return compact


def build_upload_payload(args: argparse.Namespace) -> Dict[str, Any]:
    return {
        "video_url": args.video_url,
        "title": args.title,
        "description": args.description,
        "tags": [tag.strip() for tag in args.tags.split(",") if tag.strip()],
        "source": "beacon-skill-example",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a BoTTube API smoke test from Beacon")
    parser.add_argument("--base-url", default=os.getenv("BOTTUBE_API_URL", "https://bottube.ai"))
    parser.add_argument("--limit", type=int, default=2, help="Number of feed/video items to request")
    parser.add_argument("--api-key", default=os.getenv("BOTTUBE_API_KEY"))
    parser.add_argument("--upload-dry-run", action="store_true", help="Print an /api/upload payload without sending it")
    parser.add_argument("--upload", action="store_true", help="Send /api/upload with BOTTUBE_API_KEY")
    parser.add_argument("--video-url", default="https://example.com/beacon-demo.mp4")
    parser.add_argument("--title", default="Beacon BoTTube API example")
    parser.add_argument("--description", default="Beacon agent example upload payload")
    parser.add_argument("--tags", default="beacon,agent,bottube")
    args = parser.parse_args()

    client = BoTTubeClient(base_url=args.base_url, api_key=args.api_key)

    health = client.health()
    videos = client.list_videos(limit=args.limit)
    feed = client.feed(limit=args.limit)

    proof = {
        "docs": [
            "https://bottube.ai/developers",
            "https://bottube.ai/api/docs",
        ],
        "GET /health": {"ok": True, "sample": _compact_sample(health)},
        f"GET /api/videos?limit={args.limit}": {"ok": True, "sample": _compact_sample(videos)},
        f"GET /api/feed?limit={args.limit}": {"ok": True, "sample": _compact_sample(feed)},
    }

    if args.upload_dry_run or args.upload:
        payload = build_upload_payload(args)
        if args.upload:
            proof["POST /api/upload"] = {"ok": True, "sample": client.upload_video(**payload)}
        else:
            proof["POST /api/upload dry_run"] = {"ok": True, "payload": payload}

    print(json.dumps(proof, indent=2, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
