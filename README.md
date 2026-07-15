# ling-video-bot

MaiBot 的抖音、B站分享链接解析插件。视频内容会下载并通过本地 OneBot 接口发送；图文和纯图片内容不会重复发送原图，仅结合链接内容生成点评。

## 功能

- 识别普通链接、短链和常见分享卡片。
- 下载并发送 B站、抖音原视频；80 MB 内原文件直发，超过后按源文件体积阶梯只压缩一次。
- 对视频抽帧，对图文下载图片用于视觉分析，再结合 MaiBot 人设生成点评。
- 文本内容安全注入 MaiBot Planner，并保留后续消息链路。
- 支持群白名单、去重、下载体积限制和配置热重载。
- 流式下载、原子缓存、同链接并发锁与自动缓存清理。
- 同一 BV/作品正在处理时会明确忽略重复请求；已有有效压缩文件时直接复用，避免重复编码。

## 环境要求

- MaiBot 1.x、MaiBot Plugin SDK 2.x
- Python 3.10 或更高版本
- FFmpeg
- NapCat 或兼容 OneBot 11 的本地 HTTP API
- yt-dlp（用于兼容抖音新版页面）
- 火山方舟 API Key（如需视觉识别和自动点评）

## 安装

将插件放入 `MaiBot/plugins/ling_video-bot`，复制 `config.example.toml` 为 `config.toml` 并填写必要配置。实际配置、Cookie 和 API Key 不应提交到 Git。

## 关键配置

- `parser.max_video_size_mb`：原视频免压缩直发阈值，也是压缩阶梯的计算基准。
- `cache.max_size_mb`、`cache.retention_hours`：缓存容量和保留期限。
- `api.*`：OneBot HTTP 地址、Token 和发送超时。
- `cookies.douyin`：抖音返回验证页时必填；复制浏览器访问 `www.douyin.com` 请求中的完整 Cookie。
- `volcengine.*`：视觉与文本模型配置；关闭 `enabled` 可禁用外部 AI 分析。

## 设计说明

- 图文或纯图片分享可从原链接查看、下载，因此插件只生成点评，不重复发送原图。
- 单文件下载安全上限与 `cache.max_size_mb` 一致，避免独立的旧下载限制在压缩前误拦截大视频。
- 视频不设时长限制。以默认 80 MB 阈值为例，源文件在 80–<160、160–<320、320–<800、800 MB 以上时，分别使用 CRF 26、30、34、38。
- 每个视频最多编码一次，压缩后不再按大小触发二次编码，以控制 CPU 占用和响应时间并兼顾画质。

## 许可证

[MIT](LICENSE)
