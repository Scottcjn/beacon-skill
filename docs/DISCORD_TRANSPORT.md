# Beacon Discord Transport v2.12.1 — Setup & Troubleshooting Guide

## Overview

The Beacon Discord Transport provides reliable message delivery and event listening via Discord webhooks. This guide covers setup, rate limit handling, and operational troubleshooting.

## Setup

### Prerequisites

- Discord server with admin access
- Webhook creation permissions
- Beacon CLI installed and configured
- Network access to Discord API endpoints

### Step 1: Create a Discord Webhook

1. Open your Discord server settings
2. Navigate to **Integrations** → **Webhooks**
3. Click **New Webhook**
4. Name it (e.g., `beacon-transport`)
5. Select the target channel
6. Copy the **Webhook URL** (format: `https://discord.com/api/webhooks/{id}/{token}`)

### Step 2: Configure Beacon

```bash
beacon transport add discord \
  --name primary \
  --webhook-url "https://discord.com/api/webhooks/YOUR_ID/YOUR_TOKEN"
```

### Step 3: Test the Connection

```bash
beacon discord ping "transport-check" --webhook-url "https://discord.com/api/webhooks/YOUR_ID/YOUR_TOKEN"
# Expected output: JSON object with {"ok": true, ...}
```

### Step 4: Send a Test Message

```bash
beacon discord send \
  --transport primary \
  --message "Hello from Beacon!"
```

**Expected response:**
```json
{
  "ok": true,
  "status": 204
}
```

---

## Rate Limiting & Error Handling

### Understanding Discord Rate Limits

Discord enforces rate limits per webhook:
- **Standard**: 10 requests per 10 seconds per webhook
- **Burst tolerance**: Brief spikes up to 20 req/s
- **Response header**: `X-RateLimit-Remaining`, `X-RateLimit-Reset-After`

### How Beacon Handles Rate Limits

The transport automatically:
1. **Detects 429 responses** from Discord
2. **Extracts reset window** from `Retry-After` header
3. **Backs off exponentially** (1s → 2s → 4s → 8s)
4. **Retries** up to 5 times by default
5. **Fails gracefully** with detailed error logs

### Configuration Options

```bash
# Custom retry behavior
beacon discord send \
  --transport primary \
  --message "Important update" \
  --max-retries 7 \
  --initial-backoff-ms 500 \
  --backoff-multiplier 2
```

### Error Types

| Error Type | HTTP Code | Behavior |
|------------|-----------|----------|
| `DiscordRateLimitError` | 429 | Automatic retry with backoff |
| `DiscordClientError` | 400-499 | No retry (invalid request) |
| `DiscordServerError` | 500-599 | Automatic retry with backoff |

---

## Dry Run Mode

Test your messages without actually sending them:

```python
from beacon_skill.transports import DiscordTransport

transport = DiscordTransport(webhook_url="https://discord.com/api/webhooks/...")
result = transport.send_message("Test", dry_run=True)
# Returns: {"ok": true, "status": 200, "dry_run": true}
```

---

## Listener Mode (Optional)

Beacon Discord Transport supports lightweight listener mode for consuming inbound webhook events.

### Enable Listener (JSONL poll/read mode)

Create a local JSONL stream (one event per line):

```json
{"id":"evt-1","kind":"discord.message","content":"hello"}
```

Run listener:

```bash
beacon discord listen   --event-source /var/lib/beacon/discord-events.jsonl   --state-file /var/lib/beacon/discord-listener-state.json   --poll-interval 10
```

This prints each new event as JSON and persists cursor (`file_offset`, `last_event_id`) in `state-file`.

### Listener Configuration

```python
listener = DiscordListener(
    webhook_url="https://discord.com/api/webhooks/YOUR_ID/YOUR_TOKEN",
    poll_interval=30,           # Poll every 30 seconds
    max_backlog=100,            # Maximum events to track
    state_file="/var/lib/beacon/discord-state.json"  # Persist state
)
```

---

## Troubleshooting

### Problem: `beacon discord ping` fails with 401

**Cause**: Invalid webhook token or permissions revoked.

**Solution**:
1. Verify webhook still exists in Discord server settings
2. Regenerate the webhook (Discord will give you a new URL)
3. Update Beacon configuration

### Problem: Messages are queued but never delivered

**Cause**: Discord rate limiting, webhook revoked, or transient 5xx.

**Solution**:
1. Run dry-run shape check:
   `beacon discord send --text "probe" --dry-run`
2. Run real ping:
   `beacon discord ping "probe" --webhook-url "..."`
3. Verify Discord API status: https://status.discord.com
4. Validate webhook URL still active in server Integrations.

### Problem: Listener mode reports "state file corrupted"

**Cause**: Unexpected shutdown while writing state.

**Solution**:
1. Remove corrupted state file
2. Restart listener (state will be rebuilt)

---

## API Reference

### `DiscordTransport`

```python
from beacon_skill.transports import DiscordTransport

transport = DiscordTransport(
    webhook_url="https://discord.com/api/webhooks/...",
    timeout_s=20,
    username="Beacon Bot",
    avatar_url="https://example.com/avatar.png",
    max_retries=5,
    base_delay=1.0,
)
```

#### Methods

- `send_message(content, *, username, avatar_url, embeds, dry_run)` - Send text message
- `send_beacon(*, content, kind, agent_id, rtc_tip, signature_preview, ...)` - Send Beacon embed
- `ping(dry_run)` - Test connectivity

### `DiscordListener`

```python
from beacon_skill.transports import DiscordListener

listener = DiscordListener(
    webhook_url="https://discord.com/api/webhooks/...",
    poll_interval=30,
    state_file="/path/to/state.json",
    max_backlog=100,
)
```

#### Methods

- `start(callback)` - Start listening (async)
- `stop()` - Stop listening gracefully
- `run_sync(callback)` - Run in synchronous mode

---

## Testing

```bash
# Run Discord transport tests
pytest tests/test_discord_transport.py -v

# Test with specific test case
pytest tests/test_discord_transport.py::TestDiscordTransport::test_rate_limit_error_retry -v
```

---

## Changelog

### v2.12.1 (2026-02-20)

- ✅ Enhanced error handling with specific exception types
- ✅ Proper 429 handling using Retry-After header
- ✅ Exponential backoff with jitter
- ✅ Dry run mode for testing
- ✅ Listener mode for polling events
- ✅ State persistence for listener
- ✅ Comprehensive test coverage
