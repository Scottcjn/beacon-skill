# Demo Video Recording Guide

## Quick Recording (5 minutes)

### Option 1: Terminal Recording with asciinema
```bash
# Install
pip install asciinema

# Record
asciinema rec demo.cast

# In another terminal, run:
cd /tmp/bounty-2890/generated
python ../demo/run_demo.py

# Stop recording (Ctrl+D)

# Convert to MP4 (requires agg)
asciinema upload demo.cast  # Uploads to asciinema.org
# OR convert to GIF
agg demo.cast demo.gif
```

### Option 2: Screen Recording (Easiest)
1. Open terminal with Tokyo Night theme, JetBrains Mono 16px
2. Window size: 120×40
3. Run: `cd /tmp/bounty-2890/generated && python ../demo/run_demo.py`
4. Record with OBS / QuickTime / SimpleScreenRecorder
5. Trim to 90 seconds
6. Add background music (lo-fi / ambient)

### Option 3: Terminal + Narration
1. Record terminal output as GIF (Option 1)
2. Record voice narration separately
3. Combine in any video editor:
   - Terminal GIF as main visual
   - Voice narration as audio track
   - Add title card and end card

## Recommended Terminal Setup
```json
{
  "theme": "Tokyo Night",
  "font": "JetBrains Mono",
  "font_size": 16,
  "window_width": 120,
  "window_height": 40,
  "cursor": "block",
  "cursor_blink": false,
  "scrollback": 10000
}
```

## Video Structure (90 seconds)
| Time | Content |
|------|---------|
| 0:00-0:15 | Problem statement (Moltbook acquisition) |
| 0:15-0:30 | Solution overview (Beacon + SATP) |
| 0:30-0:55 | Live terminal demo (run_demo.py) |
| 0:55-1:05 | MCP endpoint query |
| 1:05-1:15 | Real API verification |
| 1:15-1:30 | Call to action (GitHub PR, landing page) |

## Upload Targets
- **YouTube**: Unlisted, embed in PR description
- **GitHub**: Upload as .mp4 attachment to Issue #2890
- **Twitter/X**: Trim to 60s clip with caption
- **Dev.to**: Embed in blog post

## File Checklist
- [ ] `demo/run_demo.py` — Live terminal demo script ✅
- [ ] `demo/demo_script.md` — Storyboard/narration script ✅
- [ ] `demo/demo_video_guide.md` — This file ✅
- [ ] Recording (MP4/GIF) — To be recorded by user
