#!/usr/bin/env bash
set -euo pipefail

if ! command -v beacon >/dev/null 2>&1; then
  echo "error: beacon CLI not found. Install first: pip install beacon-skill"
  exit 1
fi

TMP_HOME="$(mktemp -d)"
export HOME="$TMP_HOME"

PORT="${BEACON_SMOKE_PORT:-8402}"
MSG="loopback-smoke-$(date +%s)"

cleanup() {
  if [[ -n "${SERVER_PID:-}" ]]; then
    kill "$SERVER_PID" >/dev/null 2>&1 || true
    wait "$SERVER_PID" 2>/dev/null || true
  fi
  rm -rf "$TMP_HOME"
}
trap cleanup EXIT

echo "[1/5] creating temporary identity"
beacon identity new >/dev/null

echo "[2/5] starting webhook server on :$PORT"
beacon webhook serve --port "$PORT" >"$TMP_HOME/webhook.log" 2>&1 &
SERVER_PID=$!
sleep 2

echo "[3/5] sending signed envelope to local webhook"
beacon webhook send "http://127.0.0.1:${PORT}/beacon/inbox" --kind hello --text "$MSG" >/dev/null
sleep 1

echo "[4/5] validating inbox contains loopback message"
if beacon inbox list --limit 20 | grep -q "$MSG"; then
  echo "[5/5] success: loopback webhook smoke test passed"
else
  echo "error: message not found in inbox"
  echo "--- webhook log ---"
  cat "$TMP_HOME/webhook.log" || true
  exit 1
fi
