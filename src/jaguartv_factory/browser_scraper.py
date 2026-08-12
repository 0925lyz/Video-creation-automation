from __future__ import annotations

import asyncio
import html
import json
import re
import time
import urllib.parse
from pathlib import Path
from typing import Any

from .core import workspace_dir
from .sessions import absolute_from_root, load_storage_state, parse_cookie_text


class BrowserScrapeError(RuntimeError):
    """Raised when a logged-in browser scrape cannot return usable media."""


def _latest_session_files(config: dict[str, Any], platform: str) -> tuple[Path | None, Path | None]:
    root = Path(str(config.get("_root") or ".")).resolve()
    workspace = workspace_dir(config)
    session_root = workspace / "sessions" / platform
    if not session_root.exists():
        return None, None
    manifests = sorted(session_root.glob("*/manifest.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    fallback_state: Path | None = None
    fallback_cookie: Path | None = None
    for manifest_path in manifests:
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        state_value = str(manifest.get("storage_state_path") or "").strip()
        cookie_value = str(manifest.get("cookie_file_path") or "").strip()
        state = absolute_from_root(config, state_value) if state_value else manifest_path.parent / "storage_state.json"
        cookie = absolute_from_root(config, cookie_value) if cookie_value else manifest_path.parent / "cookies.txt"
        if fallback_state is None and state.is_file():
            fallback_state = state
        if fallback_cookie is None and cookie.is_file():
            fallback_cookie = cookie
        if str(manifest.get("status") or "") in {"READY", ""} and (state.is_file() or cookie.is_file()):
            return state if state.is_file() else None, cookie if cookie.is_file() else None
    return fallback_state, fallback_cookie


def latest_cookie_file(config: dict[str, Any], platform: str) -> Path | None:
    _state, cookie = _latest_session_files(config, platform)
    return cookie


def _storage_state_from_latest_session(config: dict[str, Any], platform: str) -> dict[str, Any]:
    state, cookie = _latest_session_files(config, platform)
    if state and state.is_file():
        return load_storage_state(state)
    if cookie and cookie.is_file():
        cookies = parse_cookie_text(cookie.read_text(encoding="utf-8", errors="replace"))
        return {"cookies": cookies, "origins": []}
    return {"cookies": [], "origins": []}


def _normalize_cookie_for_playwright(cookie: dict[str, Any]) -> dict[str, Any]:
    item = dict(cookie)
    same_site = item.get("sameSite")
    if same_site not in {"Strict", "Lax", "None"}:
        item["sameSite"] = "Lax"
    try:
        expires = int(float(item.get("expires", -1)))
    except (TypeError, ValueError):
        expires = -1
    if expires <= int(time.time()) and expires > 0:
        item["expires"] = -1
    elif expires <= 0:
        item["expires"] = -1
    else:
        item["expires"] = expires
    if item.get("sameSite") == "None":
        item["secure"] = True
    item.setdefault("path", "/")
    return item


async def _new_page(config: dict[str, Any], platform: str):
    try:
        from playwright.async_api import async_playwright
    except ImportError as error:
        raise BrowserScrapeError(
            "playwright is not installed; run `python -m playwright install chromium` after installing the app"
        ) from error
    playwright = await async_playwright().start()
    try:
        browser = await playwright.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
            ),
            locale="zh-CN",
            viewport={"width": 1365, "height": 900},
            java_script_enabled=True,
        )
        state = _storage_state_from_latest_session(config, platform)
        cookies = [_normalize_cookie_for_playwright(cookie) for cookie in (state.get("cookies") or [])]
        if cookies:
            await context.add_cookies(cookies)
        page = await context.new_page()
        return playwright, browser, context, page
    except Exception:
        await playwright.stop()
        raise


def _dedupe(entries: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for entry in entries:
        key = str(entry.get("webpage_url") or entry.get("url") or entry.get("id") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(entry)
        if len(result) >= limit:
            break
    return result


def _compact_title(text: str, fallback: str) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return (text[:160] if text else fallback)


def _looks_like_tiktok_media_url(media_url: str) -> bool:
    lower = media_url.lower()
    if not lower or lower.startswith("blob:"):
        return False
    if any(marker in lower for marker in (".js", ".css", ".png", ".jpg", ".jpeg", ".webp", ".svg", ".ico")):
        return False
    return any(marker in lower for marker in (".mp4", ".m3u8", "mime_type=video", "video/tos/", "/video/tos"))


async def _search_tiktok_async(config: dict[str, Any], term: str, limit: int) -> list[dict[str, Any]]:
    playwright, browser, _context, page = await _new_page(config, "tiktok")
    try:
        url = "https://www.tiktok.com/search/video?q=" + urllib.parse.quote(term)
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        for _ in range(3):
            await page.wait_for_timeout(3500)
            await page.mouse.wheel(0, 1400)
        links = await page.evaluate(
            """() => Array.from(document.querySelectorAll('a[href*="/video/"]')).map(a => ({
              href: a.href,
              text: (a.innerText || a.getAttribute('aria-label') || '').trim()
            }))"""
        )
        entries = []
        for link in links:
            href = str(link.get("href") or "").split("?", 1)[0]
            match = re.search(r"/video/(\d+)", href)
            if not match:
                continue
            entries.append({
                "id": match.group(1),
                "title": _compact_title(str(link.get("text") or ""), f"TikTok {term}"),
                "description": str(link.get("text") or ""),
                "webpage_url": href,
                "duration": None,
                "view_count": 0,
                "extractor_key": "tiktok",
                "browser_scraper": "playwright_cookie_search",
            })
        return _dedupe(entries, limit)
    finally:
        await browser.close()
        await playwright.stop()


async def _search_douyin_async(config: dict[str, Any], term: str, limit: int) -> list[dict[str, Any]]:
    playwright, browser, _context, page = await _new_page(config, "douyin")
    try:
        url = "https://www.douyin.com/search/" + urllib.parse.quote(term) + "?type=video"
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        for _ in range(3):
            await page.wait_for_timeout(4000)
            await page.mouse.wheel(0, 1600)
        links = await page.evaluate(
            """() => Array.from(document.querySelectorAll('a')).map(a => ({
              href: a.href,
              text: (a.innerText || a.getAttribute('aria-label') || '').trim()
            })).filter(x => x.href && (x.href.includes('/video/') || x.href.includes('modal_id=')))"""
        )
        entries = []
        for link in links:
            href = str(link.get("href") or "")
            video_id = ""
            match = re.search(r"/video/(\d+)", href) or re.search(r"[?&]modal_id=(\d+)", href)
            if match:
                video_id = match.group(1)
            if not video_id:
                continue
            entries.append({
                "id": video_id,
                "title": _compact_title(str(link.get("text") or ""), f"Douyin {term}"),
                "description": str(link.get("text") or ""),
                "webpage_url": f"https://www.douyin.com/video/{video_id}",
                "duration": None,
                "view_count": 0,
                "extractor_key": "douyin",
                "browser_scraper": "playwright_cookie_search",
            })
        return _dedupe(entries, limit)
    finally:
        await browser.close()
        await playwright.stop()


async def _resolve_tiktok_async(config: dict[str, Any], url: str) -> dict[str, Any]:
    playwright, browser, _context, page = await _new_page(config, "tiktok")
    try:
        resources: list[str] = []

        def collect_media(response: Any) -> None:
            headers = getattr(response, "headers", {}) or {}
            content_type = str(headers.get("content-type") or "").lower()
            response_url = html.unescape(str(getattr(response, "url", "") or "")).replace("&amp;", "&")
            if "video" in content_type or _looks_like_tiktok_media_url(response_url):
                resources.append(response_url)

        page.on("response", collect_media)
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        for _ in range(3):
            await page.wait_for_timeout(4000)
            try:
                await page.locator("video").first.click(timeout=1200)
            except Exception:
                pass
            await page.mouse.wheel(0, 600)
        videos = await page.evaluate(
            """() => Array.from(document.querySelectorAll('video')).map(v => ({
              src: v.currentSrc || v.src,
              duration: v.duration || 0,
              poster: v.poster || ''
            }))"""
        )
        performance_urls = await page.evaluate(
            """() => performance.getEntriesByType('resource')
              .map(entry => entry.name)
              .filter(Boolean)"""
        )
        title = await page.title()
        body = ""
        try:
            body = (await page.locator("body").inner_text(timeout=5000)).strip()
        except Exception:
            body = ""
        candidates: list[str] = []
        for source in [
            *(resources or []),
            *(performance_urls or []),
            *(video.get("src") for video in videos if isinstance(video, dict)),
        ]:
            media_url = html.unescape(str(source or "")).replace("&amp;", "&")
            if _looks_like_tiktok_media_url(media_url):
                candidates.append(media_url)
        if not candidates:
            html_text = await page.content()
            for match in re.findall(r"https?:\\/\\/[^\"'<>\\s]+", html_text):
                media_url = html.unescape(match.replace("\\/", "/")).replace("&amp;", "&")
                if _looks_like_tiktok_media_url(media_url):
                    candidates.append(media_url)
        candidates = list(dict.fromkeys(candidates))
        if not candidates:
            raise BrowserScrapeError("tiktok browser page loaded but no downloadable video URL was found")
        return {
            "video_url": candidates[0],
            "title": title.replace(" | TikTok", "").strip(),
            "description": body[:1200],
            "duration": next((video.get("duration") for video in videos if isinstance(video, dict) and video.get("duration")), None),
            "candidates": candidates[:5],
        }
    finally:
        await browser.close()
        await playwright.stop()


def _facebook_video_id(href: str) -> str:
    match = re.search(r"/reel/([A-Za-z0-9_.-]+)", href)
    if match:
        return match.group(1)
    match = re.search(r"[?&]v=([A-Za-z0-9_.-]+)", href)
    if match:
        return match.group(1)
    match = re.search(r"/videos/(?:[^/?#]+/)?([A-Za-z0-9_.-]+)", href)
    if match:
        return match.group(1)
    match = re.search(r"fb\.watch/([A-Za-z0-9_.-]+)", href)
    if match:
        return match.group(1)
    return ""


async def _search_facebook_async(config: dict[str, Any], term: str, limit: int) -> list[dict[str, Any]]:
    playwright, browser, _context, page = await _new_page(config, "facebook")
    try:
        url = "https://www.facebook.com/search/videos?q=" + urllib.parse.quote(term)
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        for _ in range(4):
            await page.wait_for_timeout(3500)
            await page.mouse.wheel(0, 1400)
        links = await page.evaluate(
            """() => Array.from(document.querySelectorAll('a[href]')).map(a => ({
              href: a.href,
              text: (a.innerText || a.getAttribute('aria-label') || '').trim()
            })).filter(x => x.href && (
              x.href.includes('/reel/') ||
              x.href.includes('/watch/?v=') ||
              x.href.includes('/videos/') ||
              x.href.includes('fb.watch/')
            ))"""
        )
        entries = []
        for link in links:
            href = html.unescape(str(link.get("href") or "")).replace("&amp;", "&")
            video_id = _facebook_video_id(href)
            if not video_id:
                continue
            entries.append({
                "id": video_id,
                "title": _compact_title(str(link.get("text") or ""), f"Facebook {term}"),
                "description": str(link.get("text") or ""),
                "webpage_url": href,
                "duration": None,
                "view_count": 0,
                "extractor_key": "facebook",
                "browser_scraper": "playwright_cookie_search",
            })
        return _dedupe(entries, limit)
    finally:
        await browser.close()
        await playwright.stop()


async def _search_xhs_async(config: dict[str, Any], term: str, limit: int) -> list[dict[str, Any]]:
    playwright, browser, _context, page = await _new_page(config, "xiaohongshu")
    try:
        url = "https://www.xiaohongshu.com/search_result?keyword=" + urllib.parse.quote(term)
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        for _ in range(4):
            await page.wait_for_timeout(3500)
            await page.mouse.wheel(0, 1200)
        links = await page.evaluate(
            """() => Array.from(document.querySelectorAll('a[href*="/explore/"]')).map(a => ({
              href: a.href,
              text: (a.innerText || a.getAttribute('aria-label') || '').trim()
            }))"""
        )
        entries = []
        for link in links:
            href = html.unescape(str(link.get("href") or ""))
            match = re.search(r"/explore/([0-9a-fA-F]+)", href)
            if not match:
                continue
            entries.append({
                "id": match.group(1),
                "title": _compact_title(str(link.get("text") or ""), f"XHS {term}"),
                "description": str(link.get("text") or ""),
                "webpage_url": href,
                "duration": None,
                "view_count": 0,
                "extractor_key": "xiaohongshu",
                "browser_scraper": "playwright_cookie_search",
            })
        return _dedupe(entries, limit)
    finally:
        await browser.close()
        await playwright.stop()


async def _resolve_xhs_async(config: dict[str, Any], url: str) -> dict[str, Any]:
    playwright, browser, _context, page = await _new_page(config, "xiaohongshu")
    try:
        resources: list[str] = []
        page.on("response", lambda response: resources.append(response.url))
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(12000)
        videos = await page.evaluate(
            """() => Array.from(document.querySelectorAll('video')).map(v => ({
              src: v.currentSrc || v.src,
              duration: v.duration || 0,
              poster: v.poster || ''
            }))"""
        )
        title = await page.title()
        body = ""
        try:
            body = (await page.locator("body").inner_text(timeout=5000)).strip()
        except Exception:
            body = ""
        candidates: list[str] = []
        for source in [*(resources or []), *(video.get("src") for video in videos if isinstance(video, dict))]:
            media_url = html.unescape(str(source or "")).replace("&amp;", "&")
            lower = media_url.lower()
            if lower.startswith("blob:"):
                continue
            if any(marker in lower for marker in ("sns-video", ".mp4", ".m3u8")):
                candidates.append(media_url)
        candidates = list(dict.fromkeys(candidates))
        if not candidates:
            html_text = await page.content()
            for match in re.findall(r"https?:\\/\\/[^\"'<>\\s]+", html_text):
                media_url = html.unescape(match.replace("\\/", "/")).replace("&amp;", "&")
                if any(marker in media_url.lower() for marker in ("sns-video", ".mp4", ".m3u8")):
                    candidates.append(media_url)
        candidates = list(dict.fromkeys(candidates))
        if not candidates:
            raise BrowserScrapeError("xhs browser page loaded but no downloadable video URL was found")
        return {
            "video_url": candidates[0],
            "title": title.replace(" - 小红书", "").strip(),
            "description": body[:1200],
            "duration": next((video.get("duration") for video in videos if isinstance(video, dict) and video.get("duration")), None),
            "candidates": candidates[:5],
        }
    finally:
        await browser.close()
        await playwright.stop()


def search_tiktok(config: dict[str, Any], term: str, limit: int) -> list[dict[str, Any]]:
    return asyncio.run(_search_tiktok_async(config, term, limit))


def search_douyin(config: dict[str, Any], term: str, limit: int) -> list[dict[str, Any]]:
    return asyncio.run(_search_douyin_async(config, term, limit))


def search_facebook(config: dict[str, Any], term: str, limit: int) -> list[dict[str, Any]]:
    return asyncio.run(_search_facebook_async(config, term, limit))


def search_xiaohongshu(config: dict[str, Any], term: str, limit: int) -> list[dict[str, Any]]:
    return asyncio.run(_search_xhs_async(config, term, limit))


def resolve_xiaohongshu_video(config: dict[str, Any], url: str) -> dict[str, Any]:
    return asyncio.run(_resolve_xhs_async(config, url))


def resolve_tiktok_video(config: dict[str, Any], url: str) -> dict[str, Any]:
    return asyncio.run(_resolve_tiktok_async(config, url))
