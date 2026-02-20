# Discord Transport

This transport supports:
- Webhook send path (`beacon discord ping`, `beacon discord send`)
- Optional bot-token listener path (`beacon discord listen`)

## Setup

1. Create a Discord webhook in your target channel.
2. Optional: create a Discord bot with `Read Message History` + `View Channel` permissions for listener mode.
3. Configure `~/.beacon/config.json`:

```json
{
  "discord": {
    "webhook_url": "https://discord.com/api/webhooks/...",
    "bot_token": "YOUR_BOT_TOKEN",
    "channel_id": "123456789012345678",
    "timeout_s": 20,
    "max_attempts": 3,
    "base_delay_s": 1.0
  }
}
```

## Commands

```bash
# Send signed beacon to webhook
beacon discord ping "hello from beacon" --kind hello --rtc 1.0

# Structured send
beacon discord send --kind bounty --text "new bounty live" --reward-rtc 120

# Listener read path (bot token)
beacon discord listen --limit 20
```

## Retry + Error Handling

- `429` (rate limit): retries using `retry_after` from Discord response.
- `5xx`: retries with exponential backoff.
- `4xx`: returns parsed error immediately (no retry).

## Troubleshooting

- `HTTP 401/403`: invalid bot token or missing channel permissions.
- `HTTP 404`: wrong webhook URL or `channel_id`.
- Repeated `429`: lower send frequency or raise interval between bursts.
- `Discord webhook_url required`: set `discord.webhook_url` or pass `--webhook-url`.
