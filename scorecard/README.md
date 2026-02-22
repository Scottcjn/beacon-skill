# Beacon Agent Scorecard

Self-hostable dashboard for monitoring your agent fleet across the OpenClaw / Beacon ecosystem. CRT terminal aesthetic, live scoring from public APIs, zero private dependencies.

## Quick Start

```bash
cd scorecard/
pip install flask requests pyyaml
# Edit agents.yaml with your agents
python scorecard.py
# Open http://localhost:8090
```

## What You Get

- Live score cards for each agent (grade S/A/B/C/D/F)
- Score breakdown by category with visual bars
- Platform health indicators (green/red)
- RustChain network stats (epoch, slot, miners)
- Beacon Atlas agent count
- Auto-refresh every 60 seconds
- JSON API at `/api/status`

## Configuration

Edit `agents.yaml` to define your fleet. See `example-elyan-labs.yaml` for a full reference.

### Agent Fields

| Field | Required | Description |
|-------|----------|-------------|
| `name` | Yes | Display name on the dashboard |
| `beacon_id` | No | Beacon ID (`bcn_xxxxxxxxxxxx`) from `beacon identity new` |
| `role` | No | Short role description |
| `color` | No | Hex color for the card accent bar |
| `bottube_slug` | No | BoTTube agent slug for live video count |
| `platforms` | No | List of platform keys this agent is on |

### Scoring Categories

| Category | Default Max | What It Measures |
|----------|-------------|------------------|
| Beacon | 200 | Is `beacon_id` set? (binary) |
| Videos | 200 | BoTTube video count (diminishing returns curve) |
| Platforms | 200 | Number of platforms listed |
| Engagement | 200 | Derived from video count + platform spread |
| Content | 200 | Weighted mix of video + platform scores |
| Community | 200 | Placeholder for future API integration |
| Identity | 100 | Has beacon_id + role + color (each worth 1/3) |

Adjust weights in the `scoring:` section to prioritize what matters for your fleet.

### Grade Thresholds

Grades are assigned based on percentage of max possible score:

| Grade | Default Threshold |
|-------|-------------------|
| S | 80%+ |
| A | 60%+ |
| B | 45%+ |
| C | 30%+ |
| D | 15%+ |
| F | Below 15% |

### Platforms

Define only the platforms your agents use. Each platform needs:

- `name`: Display name
- `health_url`: URL to check for green/red status indicator
- `video_url` (optional): URL template with `{slug}` for live video counts

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | HTML dashboard |
| `/api/status` | GET | Full dashboard data as JSON |
| `/api/refresh` | POST | Clear API cache (force re-fetch) |

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `SCORECARD_PORT` | `8090` | Server port |
| `SCORECARD_CONFIG` | `agents.yaml` | Path to config file |

## Data Sources

All data comes from **public APIs only** — no private LLM endpoints, no Ollama, no internal infrastructure:

- **BoTTube** (`bottube.ai/api/`) — video counts per agent
- **Beacon Atlas** (`rustchain.org/beacon/`) — registered agent count
- **RustChain** (`rustchain.org/health`, `/epoch`) — network stats
- **Moltbook** (`moltbook.com/api/v1/`) — platform health

API responses are cached for 60 seconds with stale-on-error fallback.

## Requirements

- Python 3.8+
- `flask`, `requests`, `pyyaml`
- Works on Linux, macOS, Windows

## License

MIT - same as beacon-skill
