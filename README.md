# ling-video-bot

MaiBot plugin — auto-detect Bilibili/Douyin links & share cards in QQ groups, download videos, AI commentary.

## Features

- Detects Bilibili/Douyin links in QQ group messages (including mini-program share cards)
- Downloads high-quality original Bilibili videos (video + audio merged)
- Downloads Douyin videos (short link resolution, double-redirect support)
- **Video**: sends video to group → vision API analyzes frames → LLM generates persona-styled commentary
- **Image posts**: downloads images → vision API analyzes content → LLM comments (no re-send, B站/Douyin has original viewer)
- **Text posts**: injects content into message → MaiBot planner handles commentary
- Automatic video compression (smart single-pass CRF selection, CPU-limited)
- Debounce: same link in same group within 2 min is skipped
- Follows MaiBot group whitelist/blacklist

## Architecture

```
QQ message → MaiBot plugin hook (blocking, <1s)
  ├─ text:  fetch content → inject → return (planner handles)
  └─ video/image: return abort → background task
       ├─ download + compress + send (video) / download only (image)
       ├─ vision API: doubao-seed-2-0-pro-260215 (Volcengine)
       └─ text LLM: doubao-seed-2-1-turbo-260628 → send comment
```

## Requirements

- MaiBot with plugin runtime enabled
- NapCat (MaiBot manages OneBot internally)
- ffmpeg on PATH (for video processing)
- Volcengine (火山方舟) API access
- Bilibili SESSDATA cookie (for 1080p download)

## Configuration

Edit `config.toml` after install:
```toml
[cookies]
bilibili = "your-SESSDATA-cookie"  # required for B站 video download
douyin = ""                        # optional

[parser]
max_video_size_mb = 80             # trigger compression threshold
debounce_seconds = 120             # same-link cooldown
```

Edit `plugin.py` — set your Volcengine API key:
```python
VOLC_KEY = "your-volcengine-api-key"
```

## Install

```bash
# Copy to MaiBot plugins directory
cp -r ling-video-bot/ E:\bot\1.0\MaiBot\plugins\ling-video-bot

# Install dependencies
pip install aiohttp aiofiles bilibili-api-python Pillow
```

Enable in MaiBot config and restart.

## License

MIT
