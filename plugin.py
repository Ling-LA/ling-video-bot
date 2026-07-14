# -*- coding: utf-8 -*-
"""视频/图文解析Bot — 抖音/B站链接 → 高清原图/原视频 + AI点评"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Literal

import aiofiles
import aiohttp
from maibot_sdk import Field, HookHandler, MaiBotPlugin, PluginConfigBase
from maibot_sdk.types import HookMode, HookOrder

logger = logging.getLogger("plugin.ling_video-bot")

URL_RE = re.compile(r"https?://[^\s\]\)）>\"']+")
DATA_DIR = Path(r"E:\bot\1.0\MaiBot\data\ling_video-bot")
CACHE_DIR = DATA_DIR / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

ContentType = Literal["video", "image", "text"]

# 火山方舟
VOLC_BASE = "https://ark.cn-beijing.volces.com/api/v3"
VISION_MODEL = "doubao-seed-2-0-pro-260215"
TEXT_MODEL = "doubao-seed-2-1-turbo-260628"


# ═══════════════════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════════════════

class PluginSectionConfig(PluginConfigBase):
    name: str = Field(default="ling_video-bot")
    config_version: str = Field(default="1.0.0")
    version: str = Field(default="1.0.0")
    enabled: bool = Field(default=True)


class ParserSectionConfig(PluginConfigBase):
    enabled_platforms: list[str] = Field(default=["bilibili", "douyin"])
    group_whitelist: list[str] = Field(default_factory=list)
    block_ai_reply: bool = Field(default=False)
    debounce_seconds: int = Field(default=120, ge=0)
    max_video_size_mb: int = Field(default=80, ge=1, le=300)
    max_video_minutes: int = Field(default=8, ge=1, le=60)
    max_image_count: int = Field(default=9, ge=1, le=18)


class CookiesSectionConfig(PluginConfigBase):
    bilibili: str = Field(default="")
    douyin: str = Field(default="")


class ApiSectionConfig(PluginConfigBase):
    host: str = Field(default="127.0.0.1")
    port: int = Field(default=3010, ge=1, le=65535)
    token: str = Field(default="")
    bot_uin: str = Field(default="")


class VolcengineSectionConfig(PluginConfigBase):
    api_key: str = Field(default="your-v…here")


class PluginConfig(PluginConfigBase):
    plugin: PluginSectionConfig = Field(default_factory=PluginSectionConfig)
    parser: ParserSectionConfig = Field(default_factory=ParserSectionConfig)
    cookies: CookiesSectionConfig = Field(default_factory=CookiesSectionConfig)
    api: ApiSectionConfig = Field(default_factory=ApiSectionConfig)
    volcengine: VolcengineSectionConfig = Field(default_factory=VolcengineSectionConfig)


# ═══════════════════════════════════════════════════════
# OneBot 消息发送
# ═══════════════════════════════════════════════════════

async def _post_onebot(url: str, payload: dict, api: ApiSectionConfig, timeout: int = 120) -> bool:
    headers = {"Authorization": f"Bearer {api.token}"} if api.token else {}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers, timeout=timeout) as resp:
                return resp.status == 200
    except Exception as exc:
        logger.error(f"OneBot request failed: {exc}")
        return False


def _file_uri(path: Path) -> str:
    return "file:///" + urllib.request.pathname2url(str(path)).lstrip("/")


async def _send_group_msg(group_id: str, segments: list[dict], api: ApiSectionConfig, timeout: int = 120) -> bool:
    return await _post_onebot(
        f"http://{api.host}:{api.port}/send_group_msg",
        {"group_id": group_id, "message": segments},
        api, timeout=timeout,
    )


async def send_text_to_group(group_id: str, text: str, api: ApiSectionConfig) -> bool:
    return await _send_group_msg(group_id, [{"type": "text", "data": {"text": text}}], api, timeout=30)


async def send_video_to_group(group_id: str, path: Path, api: ApiSectionConfig) -> bool:
    return await _send_group_msg(group_id, [{"type": "video", "data": {"file": _file_uri(path)}}], api, timeout=300)




# ═══════════════════════════════════════════════════════
# URL 模式
# ═══════════════════════════════════════════════════════

BILIBILI_PATTERNS = [
    re.compile(r"(?:bilibili\.com/(?:video/)?|^)(?P<bvid>BV[0-9a-zA-Z]{10})"),
    re.compile(r"b23\.tv/[A-Za-z\d]+"),
    re.compile(r"bili2233\.cn/[A-Za-z\d]+"),
    re.compile(r"t\.bilibili\.com/\d+"),          # 动态
    re.compile(r"bilibili\.com/opus/\d+"),         # 图文
]

DOUYIN_PATTERNS = [
    re.compile(r"(?:v\.douyin\.com|jx\.douyin\.com)/[a-zA-Z0-9_\-]+"),
    re.compile(r"(?:www\.)?douyin\.com/(?:video|note)/\d+"),
    re.compile(r"(?:www\.)?douyin\.com/share/(?:video|note|slides)/\d+"),
    re.compile(r"(?:iesdouyin|m\.douyin)\.com/share/(?:video|note|slides)/\d+"),
]

IOS_UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1"
PC_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"


def _get_bvid_from_url(url: str) -> str | None:
    for pat in BILIBILI_PATTERNS:
        m = pat.search(url)
        if m and m.lastgroup == "bvid":
            return m.group("bvid")
    return None


def _is_bilibili_url(url: str) -> bool:
    return any(p.search(url) for p in BILIBILI_PATTERNS)


def _is_douyin_url(url: str) -> bool:
    return any(p.search(url) for p in DOUYIN_PATTERNS)


async def _resolve_url(url: str) -> str:
    """跟踪短链重定向，返回最终 URL。"""
    if "b23.tv" not in url and "bili2233.cn" not in url and "v.douyin.com" not in url and "jx.douyin.com" not in url:
        return url
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, allow_redirects=True, timeout=15) as resp:
                return str(resp.url)
    except Exception:
        return url


def _classify_url(url: str, platform: str) -> ContentType:
    """根据 URL 判断内容类型。"""
    if platform == "bilibili":
        if "t.bilibili.com" in url:
            return "text"   # 动态（可能含图，暂当文本处理，让 planner 评论）
        if "/opus/" in url:
            return "image"  # 图文
        if "/read/" in url:
            return "text"   # 专栏
        if "BV" in url or "/video/" in url:
            return "video"
        return "video"  # 默认视频
    elif platform == "douyin":
        if "/note/" in url or "/slides/" in url:
            return "image"
        return "video"


# ═══════════════════════════════════════════════════════
# B站下载
# ═══════════════════════════════════════════════════════

async def _download_bilibili(bvid: str, cookies: str) -> tuple[Path, str, str, str] | None:
    """下载B站视频，返回 (路径, 标题, 作者, 简介)"""
    from bilibili_api import Credential, request_settings, select_client
    from bilibili_api.video import (
        AudioStreamDownloadURL, Video, VideoCodecs,
        VideoDownloadURLDataDetecter, VideoQuality, VideoStreamDownloadURL,
    )

    select_client("curl_cffi")
    request_settings.set("impersonate", "chrome131")

    sessdata = ""
    if cookies:
        for part in cookies.split(";"):
            part = part.strip()
            if part.startswith("SESSDATA="):
                sessdata = part.split("=", 1)[1]
                break
    credential = Credential(sessdata=sessdata) if sessdata else Credential()

    video = Video(bvid=bvid, credential=credential)
    info = await video.get_info()
    title = info.get("title", bvid)
    desc = info.get("desc", "")
    owner_name = info.get("owner", {}).get("name", "未知")

    try:
        download_data = await video.get_download_url(page_index=0)
    except Exception:
        return None

    detecter = VideoDownloadURLDataDetecter(download_data)
    streams = detecter.detect_best_streams(
        video_max_quality=VideoQuality._1080P,
        codecs=[VideoCodecs.AVC], no_dolby_video=True, no_hdr=True,
    )
    if not streams:
        return None

    video_stream = streams[0]
    audio_stream = streams[1] if len(streams) > 1 else None
    if not isinstance(video_stream, VideoStreamDownloadURL):
        return None

    v_url = video_stream.url
    a_url = audio_stream.url if isinstance(audio_stream, AudioStreamDownloadURL) else None

    output = CACHE_DIR / f"bilibili_{bvid}.mp4"
    if output.exists() and output.stat().st_size > 10000:
        return output, title, owner_name, desc

    headers = {"User-Agent": PC_UA, "Referer": "https://www.bilibili.com/"}

    if a_url:
        v_tmp = CACHE_DIR / f"_v_{bvid}.m4s"
        a_tmp = CACHE_DIR / f"_a_{bvid}.m4s"
        async with aiohttp.ClientSession() as session:
            for url, tmp in [(v_url, v_tmp), (a_url, a_tmp)]:
                async with session.get(url, headers=headers) as resp:
                    async with aiofiles.open(tmp, "wb") as f:
                        await f.write(await resp.read())

        ffmpeg = await _try_find_ffmpeg()
        if ffmpeg:
            proc = await asyncio.create_subprocess_exec(
                ffmpeg, "-y", "-i", str(v_tmp), "-i", str(a_tmp),
                "-c:v", "copy", "-c:a", "aac", "-shortest", str(output),
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )
            rc = await proc.wait()
            v_tmp.unlink(missing_ok=True)
            a_tmp.unlink(missing_ok=True)
            if rc != 0 or not output.exists() or output.stat().st_size < 10000:
                logger.error(f"[download] ffmpeg merge failed rc={rc}")
                return None
        else:
            v_tmp.rename(output)
            a_tmp.unlink(missing_ok=True)
    else:
        async with aiohttp.ClientSession() as session:
            async with session.get(v_url, headers=headers) as resp:
                async with aiofiles.open(output, "wb") as f:
                    await f.write(await resp.read())

    return output, title, owner_name, desc


async def _download_bilibili_images(url: str, cookies: str) -> tuple[list[Path], str, str] | None:
    """从 B站图文/动态中提取并下载图片。返回 (图片路径列表, 标题/描述, 作者)。"""
    headers = {"User-Agent": PC_UA, "Referer": "https://www.bilibili.com/"}
    if cookies:
        headers["Cookie"] = cookies

    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers, timeout=20) as resp:
            html = await resp.text()

    # 提取图片 URL（从 initial_state 或 __NEXT_DATA__ 中找）
    title, author, image_urls = "", "", []

    # 尝试匹配 opus 页面
    json_match = re.search(r'window\.__INITIAL_STATE__\s*=\s*({.*?});\s*\(function', html, re.DOTALL)
    if not json_match:
        json_match = re.search(r'window\.__INITIAL_STATE__\s*=\s*({.*?});</script>', html, re.DOTALL)
    if json_match:
        import json as _json
        try:
            state = _json.loads(json_match.group(1))
            # 遍历结构找图片信息
            detail = state.get("opusDetail", {}) or state.get("detail", {}) or state
            if isinstance(detail, dict):
                title = str(detail.get("title", "") or "")
                author = str((detail.get("user") or detail.get("author") or {}).get("name", ""))
                # 找图片列表
                pics = detail.get("pictures", []) or detail.get("pics", []) or []
                for p in pics:
                    img_url = p.get("img_src", "") or p.get("url", "") or ""
                    if img_url and img_url.startswith("http"):
                        image_urls.append(img_url)
        except Exception:
            pass

    if not image_urls:
        # 回退：从 HTML 中暴力匹配图片 URL
        image_urls = re.findall(r'https?://[^"\'\s]+?\.(?:jpg|jpeg|png|webp)(?:@[^\'"]*)?', html)
        # 去重并过滤小图标
        seen = set()
        filtered = []
        for iu in image_urls:
            if iu not in seen and not any(x in iu.lower() for x in ("avatar", "icon", "logo", "face", "emoji")):
                seen.add(iu)
                filtered.append(iu)
        image_urls = filtered[:9]  # 最多9张

    if not image_urls:
        return None

    # 下载图片
    paths = []
    url_hash = hashlib.md5(url.encode()).hexdigest()[:8]
    async with aiohttp.ClientSession() as session:
        for i, img_url in enumerate(image_urls):
            try:
                ext = ".jpg"
                if ".png" in img_url:
                    ext = ".png"
                elif ".webp" in img_url:
                    ext = ".webp"
                output = CACHE_DIR / f"bili_{url_hash}_{i}{ext}"
                if output.exists():
                    paths.append(output)
                    continue
                async with session.get(img_url.split("@")[0], headers=headers) as resp:
                    if resp.status == 200:
                        async with aiofiles.open(output, "wb") as f:
                            await f.write(await resp.read())
                        paths.append(output)
            except Exception:
                continue

    return (paths, title, author) if paths else None


async def _fetch_bilibili_text(url: str, cookies: str) -> tuple[str, str] | None:
    """获取 B站动态/专栏的纯文本内容。返回 (文本内容, 作者名)。"""
    headers = {"User-Agent": PC_UA}
    if cookies:
        headers["Cookie"] = cookies

    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers, timeout=15) as resp:
            html = await resp.text()

    title = ""
    content = ""
    author = ""

    # 尝试从 __INITIAL_STATE__ 提取
    json_match = re.search(r'window\.__INITIAL_STATE__\s*=\s*({.*?});\s*\(function', html, re.DOTALL)
    if not json_match:
        json_match = re.search(r'window\.__INITIAL_STATE__\s*=\s*({.*?});</script>', html, re.DOTALL)

    if json_match:
        import json as _json
        try:
            state = _json.loads(json_match.group(1))

            # 动态
            card = state.get("card", {}) or state.get("data", {}).get("card", {})
            content = card.get("item", {}).get("description", "") or card.get("item", {}).get("content", "")
            author = state.get("user", {}).get("name", "") or card.get("user", {}).get("name", "")

            # 专栏
            if not content:
                article = state.get("readInfo", {}) or state.get("article", {})
                title = article.get("title", "")
                content = title

            # 图文（有描述）
            if not content:
                detail = state.get("opusDetail", {})
                content = detail.get("title", "")

        except Exception:
            pass

    # 回退：取 meta description
    if not content:
        meta_match = re.search(r'<meta[^>]+name="description"[^>]+content="([^"]+)"', html)
        if meta_match:
            content = meta_match.group(1)
    if not content:
        title_match = re.search(r"<title>([^<]+)</title>", html)
        if title_match:
            content = title_match.group(1).replace("_哔哩哔哩_bilibili", "").strip()

    if not content:
        return None

    max_len = 600
    if len(content) > max_len:
        content = content[:max_len] + "..."

    return content, author


# ═══════════════════════════════════════════════════════
# 抖音下载（视频 + 图文 + 文本）
# ═══════════════════════════════════════════════════════

async def _download_douyin(share_url: str, cookies: str) -> tuple[Path, str, str, str] | None:
    """下载抖音视频，返回 (路径, 描述, 作者, "")"""
    headers = {"User-Agent": IOS_UA, "Cookie": cookies or ""}
    logger.info(f"[DouyinDL] 开始: {share_url[:60]}")

    async with aiohttp.ClientSession() as session:
        url = share_url
        if "v.douyin.com" in url or "jx.douyin.com" in url:
            try:
                async with session.get(url, headers=headers, allow_redirects=False, ssl=False) as resp:
                    if resp.status in (301, 302, 303, 307, 308):
                        url = resp.headers.get("Location", url)
                        logger.info(f"[DouyinDL] 跳转1: {url[:80]}")
            except Exception as e:
                logger.error(f"[DouyinDL] 跳转1异常: {e}")
                return None

        try:
            async with session.get(url, headers=headers, allow_redirects=False, ssl=False) as resp:
                logger.info(f"[DouyinDL] 页面status: {resp.status}")
                if resp.status in (301, 302, 303, 307, 308):
                    url = resp.headers.get("Location", url)
                    logger.info(f"[DouyinDL] 跳转2: {url[:80]}")
                    async with session.get(url, headers=headers, allow_redirects=False, ssl=False) as r2:
                        logger.info(f"[DouyinDL] 页面2status: {r2.status}")
                        if r2.status != 200:
                            return None
                        html = await r2.text()
                elif resp.status != 200:
                    return None
                else:
                    html = await resp.text()
        except Exception as e:
            logger.error(f"[DouyinDL] 页面请求异常: {e}")
            return None

        import json as _json
        match = re.search(r"window\._ROUTER_DATA\s*=\s*(.*?)</script>", html, re.DOTALL)
        if not match:
            return None

        data = _json.loads(match.group(1))
        vd = None

        # 旧版结构：顶层搜 video 字段
        for key in data:
            if isinstance(data[key], dict) and "video" in data[key]:
                vd = data[key]
                break

        # 新版结构：loaderData → videoInfoRes → item_list
        if not vd:
            ld = data.get("loaderData", {})
            for k in ld:
                if isinstance(ld[k], dict):
                    vir = ld[k].get("videoInfoRes", {})
                    if isinstance(vir, dict):
                        items = vir.get("item_list", [])
                        if items:
                            vd = items[0]
                            break

        if not vd:
            return None

        desc = vd.get("desc", "抖音视频")
        author_name = (vd.get("author", {}) or {}).get("nickname", "未知")
        video_info = vd.get("video", {})

        video_url = (
            (video_info.get("play_addr", {}) or {}).get("url_list", [None])[0]
            or (video_info.get("play_addr_h264", {}) or {}).get("url_list", [None])[0]
            or (video_info.get("download_addr", {}) or {}).get("url_list", [None])[0]
        )
        if not video_url:
            for key in ("play_addr_h264", "play_addr", "download_addr"):
                url_list = (video_info.get(key, {}) or {}).get("url_list", [])
                if url_list:
                    video_url = url_list[0]
                    break
        if not video_url:
            return None

        video_url = video_url.replace("http://", "https://")
        vid = re.search(r"/(\d+)", url)
        vid = vid.group(1) if vid else hashlib.md5(url.encode()).hexdigest()[:8]
        output = CACHE_DIR / f"douyin_{vid}.mp4"

        if not output.exists():
            dl_headers = {"User-Agent": IOS_UA, "Referer": "https://www.douyin.com/"}
            async with session.get(video_url, headers=dl_headers) as resp:
                if resp.status != 200:
                    return None
                async with aiofiles.open(output, "wb") as f:
                    await f.write(await resp.read())

        return output, desc, author_name, ""


async def _download_douyin_images(url: str, cookies: str) -> tuple[list[Path], str, str] | None:
    """下载抖音图文笔记的图片。"""
    headers = {"User-Agent": IOS_UA, "Cookie": cookies or ""}

    async with aiohttp.ClientSession() as session:
        # 短链重定向
        if "v.douyin.com" in url or "jx.douyin.com" in url:
            async with session.get(url, headers=headers, allow_redirects=False, ssl=False) as resp:
                if resp.status in (301, 302, 303, 307, 308):
                    url = resp.headers.get("Location", url)

        async with session.get(url, headers=headers, allow_redirects=False, ssl=False) as resp:
            if resp.status != 200:
                return None
            html = await resp.text()

        import json as _json
        match = re.search(r"window\._ROUTER_DATA\s*=\s*(.*?)</script>", html, re.DOTALL)
        if not match:
            return None

        data = _json.loads(match.group(1))
        note_data = None
        for key in data:
            if isinstance(data[key], dict) and "note_detail" in str(data[key].keys()) if hasattr(data[key], 'keys') else False:
                note_data = data[key]
                break
            if isinstance(data[key], dict):  # note 类型可能没有 video 字段
                note_data_temp = data[key]
                if "images" in note_data_temp:
                    note_data = note_data_temp
                    break
                # 遍历嵌套
                for subkey in note_data_temp:
                    if isinstance(note_data_temp[subkey], dict):
                        if "images" in note_data_temp[subkey]:
                            note_data = note_data_temp[subkey]
                            break
                        if (
                            isinstance(note_data_temp[subkey], dict)
                            and any("images" in str(v) for v in note_data_temp[subkey].values())
                        ):
                            note_data = note_data_temp
                            break

        # 提取图片 URL
        desc = ""
        author = ""
        img_urls = []

        if not note_data:
            # 直接从 HTML 匹配图片 URL
            img_urls = re.findall(r'https?://[^"\']+?\.(?:jpg|jpeg|png|webp|heic)[^"\'\s]*', html)
        else:
            desc = note_data.get("desc", "") or note_data.get("content", "")
            author = (note_data.get("author", {}) or {}).get("nickname", "")
            img_list = note_data.get("images", []) or note_data.get("image_list", []) or []
            for img in img_list:
                if isinstance(img, str):
                    img_urls.append(img)
                elif isinstance(img, dict):
                    iu = img.get("url_list", [None])[0] or img.get("url", "") or img.get("origin_url", {}).get("url_list", [None])[0] or ""
                    if iu and iu.startswith("http"):
                        img_urls.append(iu)

        if not img_urls:
            return None

        # 下载图片
        paths = []
        url_hash = hashlib.md5(url.encode()).hexdigest()[:8]
        async with aiohttp.ClientSession() as session:
            for i, img_url in enumerate(img_urls[:9]):
                try:
                    output = CACHE_DIR / f"dy_{url_hash}_{i}.jpg"
                    if output.exists():
                        paths.append(output)
                        continue
                    dl_h = {"User-Agent": IOS_UA, "Referer": "https://www.douyin.com/"}
                    async with session.get(img_url, headers=dl_h) as resp:
                        if resp.status == 200:
                            async with aiofiles.open(output, "wb") as f:
                                await f.write(await resp.read())
                            paths.append(output)
                except Exception:
                    continue

        return (paths, desc, author) if paths else None


async def _fetch_douyin_text(url: str, cookies: str) -> tuple[str, str] | None:
    """获取抖音视频/笔记的描述文本。"""
    headers = {"User-Agent": IOS_UA, "Cookie": cookies or ""}

    async with aiohttp.ClientSession() as session:
        if "v.douyin.com" in url or "jx.douyin.com" in url:
            async with session.get(url, headers=headers, allow_redirects=False, ssl=False) as resp:
                if resp.status in (301, 302, 303, 307, 308):
                    url = resp.headers.get("Location", url)

        async with session.get(url, headers=headers, allow_redirects=False, ssl=False) as resp:
            if resp.status != 200:
                return None
            html = await resp.text()

        import json as _json
        match = re.search(r"window\._ROUTER_DATA\s*=\s*(.*?)</script>", html, re.DOTALL)
        if not match:
            return None

        data = _json.loads(match.group(1))
        content = ""
        author = ""

        for key in data:
            item = data[key]
            if isinstance(item, dict):
                content = item.get("desc", "") or item.get("title", "") or item.get("content", "")
                author = (item.get("author", {}) or {}).get("nickname", "")
                if content:
                    break
                for subkey, subval in item.items():
                    if isinstance(subval, dict):
                        content = subval.get("desc", "") or subval.get("title", "")
                        author = (subval.get("author", {}) or {}).get("nickname", "") or author
                        if content:
                            break

        if not content:
            return None

        if len(content) > 600:
            content = content[:600] + "..."
        return content, author


# ═══════════════════════════════════════════════════════
# ffmpeg 工具
# ═══════════════════════════════════════════════════════

async def _try_find_ffmpeg() -> str | None:
    _ffmpeg_paths = [
        "ffmpeg",
        r"C:\Users\玲\.openclaw\workspace\ffmpeg\ffmpeg-8.1.1-essentials_build\bin\ffmpeg.exe",
    ]
    for fp in _ffmpeg_paths:
        try:
            proc = await asyncio.create_subprocess_exec(
                fp, "-version", stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            if await proc.wait() == 0:
                return fp
        except Exception:
            continue
    return None


# ═══════════════════════════════════════════════════════
# 主插件
# ═══════════════════════════════════════════════════════

class VideoBotPlugin(MaiBotPlugin):
    """自动解析抖音/B站链接 → 高清视频/原图 + AI 点评"""

    config_model = PluginConfig
    _recent: dict[tuple[str, str], float] = {}
    _ffmpeg_exe: str | None = None

    async def on_load(self) -> None:
        if not self.config.plugin.enabled:
            logger.info("VideoBot disabled")
            return
        logger.info(f"VideoBot loaded. Platforms: {self.config.parser.enabled_platforms}")

    async def on_unload(self) -> None:
        logger.info("VideoBot unloaded")

    async def on_config_update(self, scope: str, config_data: dict[str, object], version: str) -> None:
        logger.info(f"VideoBot config updated to {version}")

    @HookHandler(
        hook="chat.receive.after_process",
        name="ling_video_bot_hook",
        description="检测B站/抖音链接，发高清原视频/原图，AI自动点评",
        mode=HookMode.BLOCKING,
        order=HookOrder.EARLY,
    )
    async def handle_video_link(self, **kwargs) -> dict[str, Any] | None:
        if not self.config.plugin.enabled:
            return None

        message: dict = kwargs.get("message", {}) or {}
        if not self._is_allowed_group(message):
            return None

        source = self._message_source(message)
        urls = self._extract_urls(source)
        if not urls:
            # 没找到URL但消息可能是卡片 → 打印结构排查
            if any(k in source for k in ("小程序", "哔哩", "bilibili", "douyin")):
                logger.info(f"[VideoBot] 疑似卡片但未提取URL, source={source[:200]}, msg_keys={list(message.keys())}")

        platform = None
        target_url = None

        for url in urls:
            if _is_bilibili_url(url):
                platform = "bilibili"
                target_url = url
                break
            if _is_douyin_url(url):
                platform = "douyin"
                target_url = url
                break

        if not platform or not target_url:
            return None

        if platform not in self.config.parser.enabled_platforms:
            return None

        session_id = self._get_group_id(message) or self._get_user_id(message) or "unknown"
        if self._is_recent(session_id, target_url):
            return {"action": "abort"} if self.config.parser.block_ai_reply else None

        # 判断内容类型（无需解析短链即可分类）
        content_type = _classify_url(target_url, platform)

        logger.info(f"[VideoBot] 检测到{platform}链接: {target_url} → {content_type}")

        if content_type == "text":
            # 文本内容：同步抓取 → 注入消息 → planner 点评
            resolved = await _resolve_url(target_url)
            text_result = await self._fetch_text(resolved or target_url, platform)
            if text_result:
                text_content, author = text_result
                original_text = str(kwargs.get("message", {}).get("processed_plain_text", ""))
                injected = (
                    f"{original_text}\n\n"
                    f"[上文分享了一个内容——作者「{author}」：]\n"
                    f"{text_content}\n\n"
                    f"请用你的风格对上面这条分享发表点评。"
                )
                kwargs["message"]["processed_plain_text"] = injected
                logger.info(f"[VideoBot] 文本内容已注入消息，交给 planner 点评")
            return None  # 不阻断

        else:
            # 视频/图片：立刻阻断，后台处理下载→发送→分析→点评
            group_id = self._get_group_id(message)
            if not group_id:
                return None

            api = self.config.api
            asyncio.create_task(
                self._process_media_async(group_id, platform, target_url, content_type, api)
            )
            return {"action": "abort"}

    async def _process_media_async(
        self, group_id: str, platform: str, url: str, ctype: str, api
    ) -> None:
        """后台处理：下载→发送→分析→点评（不阻塞 hook）。"""
        try:
            if ctype == "video":
                logger.info(f"[VideoBot] 开始处理{platform}视频: {url[:60]}")
                local_path, content, author, extra = await self._download_and_send_video(
                    group_id, platform, url, api
                )
                if local_path and content:
                    analysis = await self._analyze_video(Path(local_path), content, extra)
                    comment = await self._generate_comment(platform, author, content, extra, analysis)
                    if comment:
                        await send_text_to_group(group_id, comment, api)
                        logger.info(f"[VideoBot] 点评已发送: {comment[:50]}...")
            elif ctype == "image":
                logger.info(f"[VideoBot] 开始处理{platform}图文: {url[:60]}")
                cookies = self.config.cookies.bilibili if platform == "bilibili" else self.config.cookies.douyin
                if platform == "bilibili":
                    result = await _download_bilibili_images(url, cookies)
                else:
                    result = await _download_douyin_images(url, cookies)
                if result and result[0]:
                    paths, img_desc, img_author = result
                    # 视觉分析图片内容
                    img_analysis = await self._analyze_images(paths[:4])
                    # 生成点评（结合文字描述 + 图片内容）
                    comment = await self._generate_image_comment(
                        platform, img_author, img_desc, img_analysis
                    )
                    if comment:
                        await send_text_to_group(group_id, comment, api)
                        logger.info(f"[VideoBot] 图文点评已发送: {comment[:50]}...")
        except Exception:
            import traceback
            logger.error(f"[VideoBot] 后台处理异常:\n{traceback.format_exc()}")

    async def _fetch_text(self, url: str, platform: str) -> tuple[str, str] | None:
        """获取链接中的纯文本内容。"""
        cookies = self.config.cookies.bilibili if platform == "bilibili" else self.config.cookies.douyin
        try:
            if platform == "bilibili":
                return await _fetch_bilibili_text(url, cookies)
            else:
                return await _fetch_douyin_text(url, cookies)
        except Exception as exc:
            logger.error(f"[VideoBot] 获取文本失败: {exc}")
            return None

    async def _download_and_send_video(
        self, group_id: str, platform: str, url: str, api
    ) -> tuple[str | None, str, str, str]:
        """下载 + 发送视频。返回 (本地路径, 标题, 作者, 简介)。"""
        logger.info(f"[VideoBot] 开始下载视频: plat={platform}")
        result = None
        video_path = None

        if platform == "bilibili":
            bvid = _get_bvid_from_url(url)
            if not bvid and ("b23.tv" in url or "bili2233.cn" in url):
                url = await _resolve_url(url)
                bvid = _get_bvid_from_url(url)
            if bvid:
                result = await _download_bilibili(bvid, self.config.cookies.bilibili)
        elif platform == "douyin":
            result = await _download_douyin(url, self.config.cookies.douyin)

        if not result:
            await send_text_to_group(group_id, "视频下载失败了😢", api)
            return None, "", "", ""

        video_path = result[0]
        content = result[1] if len(result) > 1 else ""   # B站: title, 抖音: desc
        author = result[2] if len(result) > 2 else ""
        extra = result[3] if len(result) > 3 else ""     # B站: 简介, 抖音: 空
        size_mb = video_path.stat().st_size / (1024 * 1024)
        max_mb = self.config.parser.max_video_size_mb

        if size_mb > max_mb:
            logger.info(f"[VideoBot] 压缩: {size_mb:.0f}MB > {max_mb}MB")
            compressed = await self._compress_video(video_path, max_mb)
            if compressed:
                video_path = compressed
            else:
                await send_text_to_group(group_id, f"视频压缩失败，发不了😢", api)
                return None, "", "", ""

        logger.info(f"[VideoBot] 发送视频 ({video_path.stat().st_size / 1024 / 1024:.1f}MB)")
        await send_video_to_group(group_id, video_path, api)
        return str(video_path), content, author, extra

        """提取视频关键帧，返回 JPEG 字节列表。"""
        ffmpeg = await self._get_ffmpeg()
        if not ffmpeg:
            return []
        proc = await asyncio.create_subprocess_exec(
            ffmpeg, "-i", str(video_path), "-f", "null", "-",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        duration = 0
        m = re.search(r"Duration:\s*(\d+):(\d+):(\d+)\.(\d+)", stderr.decode(errors="ignore"))
        if m:
            duration = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3)) + int(m.group(4)) / 100
        if duration <= 0:
            duration = 30
        frames = []
        for i in range(count):
            t = duration * (i + 1) / (count + 1)
            out = CACHE_DIR / f"_frame_{video_path.stem}_{i}.jpg"
            try:
                proc = await asyncio.create_subprocess_exec(
                    ffmpeg, "-y", "-ss", str(t), "-i", str(video_path),
                    "-vframes", "1", "-q:v", "3", "-s", "512x288", str(out),
                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                )
                await proc.wait()
                if out.exists():
                    frames.append(out.read_bytes())
                    out.unlink(missing_ok=True)
            except Exception:
                pass
        return frames

    async def _analyze_video(self, path: Path, title: str, desc: str) -> str:
        """提取帧 + 火山视觉API → 视频内容描述。"""
        frames = await self._extract_frames(path, count=3)
        if not frames:
            return f"《{title}》" if title else ""
        content = [{"type": "text", "text": "请用中文描述这个视频的内容（场景、人物、动作、氛围），100字以内。"}]
        for fb in frames:
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{base64.b64encode(fb).decode()}"},
            })
        try:
            async with aiohttp.ClientSession() as s:
                async with s.post(
                    f"{VOLC_BASE}/chat/completions",
                    headers={"Authorization": f"Bearer {self.config.volcengine.api_key}"},
                    json={"model": VISION_MODEL, "messages": [{"role": "user", "content": content}],
                          "max_tokens": 300, "temperature": 0.3},
                    timeout=120,
                ) as resp:
                    data = await resp.json()
                    return data["choices"][0]["message"]["content"].strip()
        except Exception as e:
            logger.error(f"[VideoBot] 视觉分析失败: {e}")
            return f"《{title}》" if title else ""

    async def _generate_comment(
        self, platform: str, author: str, title: str, desc: str, video_analysis: str = "",
    ) -> str:
        """用火山方舟 LLM 生成玲宝风格的点评。"""
        ctx = f"{platform}作者「{author}」分享了一个视频《{title}》"
        if desc:
            ctx += f"\n视频简介：{desc}"
        if video_analysis:
            ctx += f"\n视频内容识别：{video_analysis}"
        prompt = await self._build_comment_prompt(ctx)
        if not prompt:
            return ""
        try:
            async with aiohttp.ClientSession() as s:
                async with s.post(
                    f"{VOLC_BASE}/chat/completions",
                    headers={"Authorization": f"Bearer {self.config.volcengine.api_key}"},
                    json={"model": TEXT_MODEL, "messages": [{"role": "user", "content": prompt}],
                          "max_tokens": 300, "temperature": 0.8},
                    timeout=60,
                ) as resp:
                    data = await resp.json()
                    return data["choices"][0]["message"]["content"].strip()
        except Exception as e:
            logger.error(f"[VideoBot] 生成评论失败: {e}")
            return ""

    async def _analyze_images(self, paths: list[Path]) -> str:
        """用火山视觉API分析多张图片，返回内容描述。"""
        if not paths:
            return ""
        content = [{"type": "text", "text": "请用中文简要描述这几张图片的内容（主题、风格、亮点），80字以内。"}]
        for p in paths[:4]:
            try:
                b64 = base64.b64encode(p.read_bytes()).decode()
                content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                })
            except Exception:
                pass
        if len(content) == 1:
            return ""
        try:
            async with aiohttp.ClientSession() as s:
                async with s.post(
                    f"{VOLC_BASE}/chat/completions",
                    headers={"Authorization": f"Bearer {self.config.volcengine.api_key}"},
                    json={"model": VISION_MODEL, "messages": [{"role": "user", "content": content}],
                          "max_tokens": 200, "temperature": 0.3},
                    timeout=120,
                ) as resp:
                    data = await resp.json()
                    return data["choices"][0]["message"]["content"].strip()
        except Exception as e:
            logger.error(f"[VideoBot] 图片分析失败: {e}")
            return ""

    async def _generate_image_comment(
        self, platform: str, author: str, desc: str, img_analysis: str,
    ) -> str:
        """生成图文点评（结合文字+图片分析）。"""
        ctx = f"{platform}作者「{author}」分享了一个图文"
        if desc:
            ctx += f"，配文：{desc}"
        if img_analysis:
            ctx += f"\n图片内容：{img_analysis}"
        ctx += "\n\n（B站/抖音可以自己看原图，所以你只需点评内容，不需要描述图片细节给别人看）"
        prompt = await self._build_comment_prompt(ctx)
        if not prompt:
            return ""
        try:
            async with aiohttp.ClientSession() as s:
                async with s.post(
                    f"{VOLC_BASE}/chat/completions",
                    headers={"Authorization": f"Bearer {self.config.volcengine.api_key}"},
                    json={"model": TEXT_MODEL, "messages": [{"role": "user", "content": prompt}],
                          "max_tokens": 300, "temperature": 0.8},
                    timeout=60,
                ) as resp:
                    data = await resp.json()
                    return data["choices"][0]["message"]["content"].strip()
        except Exception as e:
            logger.error(f"[VideoBot] 生成评论失败: {e}")
            return ""

    async def _get_ffmpeg(self) -> str | None:
        if self._ffmpeg_exe is not None:
            return self._ffmpeg_exe if self._ffmpeg_exe else None
        ffmpeg = await _try_find_ffmpeg()
        self._ffmpeg_exe = ffmpeg or ""
        return ffmpeg

    async def _build_comment_prompt(self, context: str) -> str | None:
        """读取 MaiBot 人设配置，拼接点评提示词。"""
        try:
            persona = await self.ctx.call_capability("config.get", key="personality.personality", default="")
            style = await self.ctx.call_capability("config.get", key="personality.reply_style", default="")
            p = f"{persona}\n{style}".strip()
            if p:
                return f"{p}\n\n{context}\n\n请用你的人设风格发表一段点评（简短自然，像真人聊天）："
        except Exception:
            pass
        return None

    async def _compress_video(self, input_path: Path, max_mb: int) -> Path | None:
        ffmpeg = await self._get_ffmpeg()
        if not ffmpeg:
            return None

        ext = input_path.suffix
        output = input_path.parent / f"{input_path.stem}_c{ext}"
        in_mb = input_path.stat().st_size / (1024 * 1024)
        ratio = max_mb / in_mb if in_mb > 0 else 1

        # 根据大小比直接选 CRF，只压一次
        if ratio > 0.5:   crf = 26
        elif ratio > 0.25: crf = 30
        elif ratio > 0.1:  crf = 34
        else:              crf = 38

        logger.info(f"[VideoBot] 压缩 {in_mb:.0f}MB → 目标{max_mb}MB crf={crf}")
        try:
            proc = await asyncio.create_subprocess_exec(
                ffmpeg, "-y", "-i", str(input_path),
                "-c:v", "libx264", "-crf", str(crf), "-preset", "veryfast",
                "-threads", "2",
                "-c:a", "aac", "-b:a", "64k", "-movflags", "+faststart",
                str(output),
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
            if output.exists() and output.stat().st_size > 10000:
                sz = output.stat().st_size / (1024 * 1024)
                logger.info(f"[VideoBot] 压缩完成: {sz:.1f}MB (目标{max_mb}MB)")
                return output  # 不管达不达标，压了就用
        except Exception as exc:
            logger.error(f"[VideoBot] 压缩异常: {exc}")

        return None

    # ── 工具方法 ──

    def _message_source(self, message: dict) -> str:
        """从消息的所有可能位置提取 URL 文本。包括：
        - processed_plain_text
        - 普通消息段（url, source_url, jumpUrl, content）
        - 小程序卡片（JSON 类型消息段，解析嵌套 JSON 找 URL）
        - miniapp 消息段
        """
        parts = [str(message.get("processed_plain_text", "") or "")]
        raw_segs = message.get("raw_message", []) or []

        # 先暴力搜整个 message dict（经 MaiBot 处理后卡片数据可能不在 raw_message 里）
        for u in self._find_urls_in_dict(message):
            parts.append(u)

        for seg in raw_segs:
            if not isinstance(seg, dict):
                continue
            seg_type = seg.get("type", "")
            data = seg.get("data", {})
            if not isinstance(data, dict):
                continue

            # 搜所有能找到的 URL 字段名
            url_keys = ("url", "source_url", "jumpUrl", "jump_url", "content",
                        "qqdocurl", "share_url", "target_url", "link", "href",
                        "detail", "detail_1", "prompt", "desc", "title", "summary")
            for key in url_keys:
                val = data.get(key)
                if val and isinstance(val, str) and "http" in val:
                    parts.append(str(val))

            # type="json" → data.data 是 JSON 字符串
            if seg_type == "json" and isinstance(data.get("data"), str):
                try:
                    inner = __import__("json").loads(data["data"])
                    for u in self._find_urls_in_dict(inner):
                        parts.append(u)
                except Exception:
                    pass

            # 小程序/分享卡片 → 递归搜整个 data
            if seg_type in ("miniapp", "app", "ark", "news", "share", "music"):
                for u in self._find_urls_in_dict(data):
                    parts.append(u)

            # 其他类型（xml, image 等）也搜一搜
            if seg_type not in ("text", "at", "image", "face", "reply", "video", "file"):
                for u in self._find_urls_in_dict(data):
                    parts.append(u)

        return "\n".join(parts)

    @staticmethod
    def _find_urls_in_dict(obj, visited: set | None = None) -> list[str]:
        """递归查找字典中的所有 URL 字符串。"""
        if visited is None:
            visited = set()
        obj_id = id(obj)
        if obj_id in visited:
            return []
        visited.add(obj_id)

        results = []
        if isinstance(obj, dict):
            for key, val in obj.items():
                results.extend(VideoBotPlugin._find_urls_in_dict(val, visited))
        elif isinstance(obj, list):
            for item in obj:
                results.extend(VideoBotPlugin._find_urls_in_dict(item, visited))
        elif isinstance(obj, str):
            if (obj.startswith("http://") or obj.startswith("https://")) and len(obj) > 12:
                results.append(obj)
        return results

    def _extract_urls(self, text: str) -> list[str]:
        seen = set()
        result = []
        for match in URL_RE.finditer(text):
            url = match.group(0).rstrip(".,，。!！?？")
            if url not in seen:
                seen.add(url)
                result.append(url)
        return result

    def _is_allowed_group(self, message: dict) -> bool:
        whitelist = [str(w).strip() for w in self.config.parser.group_whitelist if str(w).strip()]
        if not whitelist:
            return True
        group_id = self._get_group_id(message)
        return bool(group_id and group_id in whitelist)

    def _is_recent(self, session_id: str, url: str) -> bool:
        interval = self.config.parser.debounce_seconds
        if interval <= 0:
            return False
        now = time.time()
        key = (session_id, url)
        expires = self._recent.get(key, 0)
        self._recent[key] = now + interval
        for old_key, old_exp in list(self._recent.items()):
            if old_exp < now:
                self._recent.pop(old_key, None)
        return expires > now

    @staticmethod
    def _get_group_id(message: dict) -> str | None:
        msg_info = message.get("message_info", {})
        if isinstance(msg_info, dict):
            gid = (msg_info.get("group_info") or {}).get("group_id")
            return str(gid) if gid else None
        return None

    @staticmethod
    def _get_user_id(message: dict) -> str | None:
        msg_info = message.get("message_info", {})
        if isinstance(msg_info, dict):
            uid = (msg_info.get("user_info") or {}).get("user_id")
            return str(uid) if uid else None
        return None


def create_plugin() -> VideoBotPlugin:
    return VideoBotPlugin()
