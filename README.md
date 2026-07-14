# ling-video-bot

MaiBot plugin for automatic Bilibili/Douyin video parsing.

## Features

- Detects Bilibili/Douyin links in QQ group messages (including mini-program share cards)
- Downloads high-quality original videos and images
- AI-powered video content analysis via Volcengine Vision API
- Generates persona-styled commentary via LLM
- Automatic video compression for oversized files
- Supports: Bilibili videos, Douyin videos, image posts (图文), mini-program cards

## Requirements

- MaiBot with plugin runtime
- NapCat/OneBot-compatible QQ bot
- ffmpeg (for video processing)
- Volcengine (火山方舟) API access

## Configuration

Edit `config.toml`:
- `[cookies].bilibili` — your Bilibili SESSDATA cookie (for high-quality downloads)
- Configure Volcengine API key in `plugin.py` (`VOLC_KEY`)
- `[api]` — OneBot HTTP API address (default: 127.0.0.1:3010)

## Install

```bash
# Copy to MaiBot plugins directory
cp -r ling-video-bot/ E:\bot\1.0\MaiBot\plugins\ling-video-bot

# Install dependencies
pip install aiohttp aiofiles bilibili-api-python Pillow
```

## License

MIT
