"""
Optional Google Hong Kong SERP fetch via Playwright (headless Chromium).

注意：自动化访问搜索引擎可能违反服务条款，仅建议在本地调试或已获授权的场景使用。
"""

from __future__ import annotations

import re
import time
import urllib.parse
from datetime import datetime, timezone
from typing import Any


def _dedupe_by_link(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for it in items:
        link = (it.get("link") or "").strip()
        key = link or (it.get("title") or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


def fetch_google_hk_serp_items(
    query: str,
    *,
    max_results: int = 8,
    timeout_ms: int = 35000,
    headless: bool = True,
    captcha_wait_ms: int = 180000,
    captcha_poll_ms: int = 2000,
    user_data_dir: str = "",
) -> tuple[list[dict[str, Any]], str | None]:
    """
    在 https://www.google.com.hk/ 上搜索 query，解析自然结果标题与链接。
    返回与 collect_news 兼容的 item 字典列表，category 固定为「网页检索」。
    """
    q = (query or "").strip()
    if not q:
        return [], "Google HK: empty query"
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return (
            [],
            "Google HK: 未安装 playwright，请执行 pip install playwright && playwright install chromium",
        )

    pub = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")
    url = "https://www.google.com.hk/search?" + urllib.parse.urlencode(
        {"q": q, "hl": "zh-CN", "gl": "hk", "num": str(min(20, max(10, max_results + 5)))}
    )
    items: list[dict[str, Any]] = []
    try:
        with sync_playwright() as p:
            ua = (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            if not headless and user_data_dir.strip():
                ctx = p.chromium.launch_persistent_context(
                    user_data_dir.strip(),
                    headless=False,
                    user_agent=ua,
                    locale="zh-CN",
                )
                page = ctx.new_page()
                context = ctx
                browser = None
            else:
                browser = p.chromium.launch(headless=headless)
                context = browser.new_context(user_agent=ua, locale="zh-CN")
                page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)

            def _is_sorry() -> bool:
                u = (page.url or "").lower()
                if "/sorry/" in u or "sorry/index" in u:
                    return True
                body = (page.content() or "").lower()
                return ("unusual traffic" in body) or ("异常流量" in body) or ("我们的系统检测到" in body)

            if _is_sorry():
                if headless:
                    try:
                        context.close()
                    except Exception:
                        pass
                    return [], "Google HK: blocked (/sorry) in headless mode"

                # Headed mode: allow user to solve CAPTCHA, poll for results.
                start = time.time()
                while (time.time() - start) * 1000 < max(1, captcha_wait_ms):
                    try:
                        page.wait_for_timeout(max(250, int(captcha_poll_ms)))
                    except Exception:
                        pass
                    if not _is_sorry():
                        break

            # Collect results (robust selector: #search h3).
            for h3 in page.query_selector_all("#search h3"):
                try:
                    title = (h3.inner_text() or "").strip()
                    if not title:
                        continue
                    a = h3.evaluate_handle("node => node.closest('a')")
                    href = a.get_property("href").json_value() if a else ""
                    href = (href or "").strip()
                    if href.startswith("http"):
                        items.append(
                            {
                                "source": "Google HK",
                                "category": "网页检索",
                                "title": title,
                                "link": href,
                                "summary": "",
                                "published": pub,
                            }
                        )
                except Exception:
                    continue

            try:
                context.close()
            except Exception:
                pass
            try:
                if browser:
                    browser.close()
            except Exception:
                pass

    except Exception as ex:  # noqa: BLE001
        return [], f"Google HK: {type(ex).__name__} {ex}"

    items = _dedupe_by_link(items)[: max(1, max_results)]
    if not items:
        return [], "Google HK: 0 results parsed (maybe blocked/DOM changed)"
    return items, None


def fetch_page_main_text(url: str, *, headless: bool = True) -> str:
    """
    Fetch full page HTML using Playwright, then extract main text via trafilatura.
    Returns extracted text (may be empty).
    """
    u = (url or "").strip()
    if not u:
        return ""
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return ""
    try:
        import trafilatura
    except Exception:
        return ""

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=headless)
            page = browser.new_page()
            page.set_default_timeout(35000)
            page.goto(u, wait_until="domcontentloaded")
            page.wait_for_timeout(900)
            html_text = page.content() or ""
            browser.close()
    except Exception:
        return ""

    try:
        extracted = trafilatura.extract(
            html_text,
            include_comments=False,
            include_tables=False,
            favor_precision=True,
            output_format="txt",
        )
        return (extracted or "").strip()
    except Exception:
        return ""

