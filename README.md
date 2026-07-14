# ling-video-bot

MaiBot 插件 — QQ 群内自动识别 B站/抖音分享链接（含小程序卡片），下载高清视频，AI 视觉分析 + 人设风格点评。

## 功能

- 🔗 识别 B站/抖音链接（短链、完整链接、分享口令）
- 🃏 识别小程序卡片（QQ 分享卡片中的 B站/抖音链接）
- 📹 B站视频：下载 1080P 原画质（视频+音频自动合并）
- 🎵 抖音视频：短链解析 + 双跳转支持 + 去水印下载
- 🤖 AI 点评：视频抽帧 → 火山方舟视觉模型分析 → 人设风格点评
- 🖼️ 图文：视觉模型分析图片内容 + 配文 → 智能点评（不发图，原平台直接看）
- 📝 纯文字：抓取文本注入消息 → Planner 自然回复
- 🗜️ 视频压缩：超过 80MB 自动单次压缩（按比例选 CRF，veryfast 预设）
- ⚡ 防重复：同群同链接 2 分钟内不重复处理
- 🔧 服从 MaiBot 群黑白名单

## 架构

```
QQ 消息 → MaiBot 插件钩子（阻塞 <1s）
  ├─ 纯文字：抓取内容 → 注入消息 → 交给 Planner
  └─ 视频/图文：立即 abort → 后台异步任务
       ├─ 下载 → 压缩(如需) → 发送(仅视频)
       ├─ 视觉 API：doubao-seed-2-0-pro-260215
       └─ 文本 LLM：doubao-seed-2-1-turbo-260628 → 发送点评
```

AI 点评人设**自动读取** MaiBot 宿主配置（`bot_config.toml` 的 `[personality]`），无需在插件内写死。

## 依赖

- MaiBot（需启用插件运行时）
- ffmpeg（视频处理和抽帧）
- 火山方舟 API（视觉 + 文本模型）
- B站 SESSDATA Cookie（1080P 下载需要）
- Python 包：`aiohttp` `aiofiles` `bilibili-api-python` `Pillow`

## 安装

```bash
# 复制到 MaiBot 插件目录
cp -r ling-video-bot/ E:\bot\1.0\MaiBot\plugins\ling-video-bot

# 安装依赖
pip install aiohttp aiofiles bilibili-api-python Pillow
```

## 配置

编辑 `config.toml`：

```toml
[cookies]
bilibili = "你的-SESSDATA-cookie"   # B站 1080P 下载必填
douyin = ""                        # 抖音 Cookie（可选）

[parser]
max_video_size_mb = 80             # 超过此大小自动压缩
debounce_seconds = 120             # 同链接冷却时间

[api]
host = "127.0.0.1"                 # OneBot HTTP 地址
port = 3010

[volcengine]
api_key = "你的-火山方舟-API-Key"   # 视觉分析 + 点评生成
```

然后在 MaiBot 中启用插件，重启即可。

## 许可

MIT
