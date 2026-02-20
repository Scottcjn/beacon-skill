# Beacon Dashboard v1.1

The `beacon dashboard` TUI now supports:
- Live transport traffic from local inbox
- Live Beacon API snapshot from:
  - `/api/agents`
  - `/api/contracts`
  - `/api/reputation`
- Filter/search input
- Snapshot export (JSON/CSV)
- Sound alerts for `mayday` and high-value RTC events

## Launch

```bash
beacon dashboard
```

Optional overrides:

```bash
beacon dashboard \
  --api-base-url http://50.28.86.131:8071 \
  --api-poll-interval 15 \
  --filter bounty
```

## Input Commands

- `/filter <text>`: apply filter/search to displayed rows.
- `/clear`: clear the active filter.
- `/export json [path]`: export current filtered view as JSON.
- `/export csv [path]`: export current filtered view as CSV.
- `/help`: show command help.

## Config

`~/.beacon/config.json`:

```json
{
  "dashboard": {
    "api_base_url": "http://50.28.86.131:8071",
    "api_poll_interval_s": 15.0
  }
}
```

## Notes

- API fetch failures are non-fatal and shown in the sidebar.
- Export writes only currently visible (filtered) rows.
