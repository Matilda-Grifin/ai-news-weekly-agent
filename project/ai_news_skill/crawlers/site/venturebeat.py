from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

from bs4 import BeautifulSoup

from run_daily_digest import strip_html

from .base import CrawledItem


class VentureBeatAICrawler:
    name = "VentureBeat AI"

    LIST_URL = "https://venturebeat.com/category/ai/"
    BASE = "https://venturebeat.com"

    def crawl(
        self,
        *,
        per_source: int,
        window_hours: int,
        allow_insecure_fallback: bool,
    ) -> list[CrawledItem]:
        html = _fetch_html(self.LIST_URL, timeout=22, allow_insecure_fallback=allow_insecure_fallback)
        if not html.strip():
            html = _fetch_html_playwright_scroll(
                self.LIST_URL,
                timeout_ms=30000,
                max_scrolls=14,
                scroll_pause_ms=650,
                click_load_more=True,
            ) or ""
        else:
            # Even when HTML is non-empty, VentureBeat category pages often lazy-load.
            html_scrolled = _fetch_html_playwright_scroll(
                self.LIST_URL,
                timeout_ms=30000,
                max_scrolls=10,
                scroll_pause_ms=600,
                click_load_more=True,
            )
            if html_scrolled and len(html_scrolled) > len(html):
                html = html_scrolled
        soup = BeautifulSoup(html, "html.parser")

        links: list[str] = []
        for a in soup.select("a[href]"):
            href_raw = (a.get("href") or "").strip()
            if not href_raw:
                continue
            href = href_raw
            if href.startswith("/"):
                href = self.BASE.rstrip("/") + href
            href = href.split("#", 1)[0].rstrip("/")
            if not href.startswith("https://venturebeat.com/"):
                continue
            if "/category/" in href or href.rstrip("/") == self.LIST_URL.rstrip("/"):
                continue
            if re.search(
                r"https://venturebeat\.com/(author|category|contact|terms|privacy|press-releases|guest-posts)(/|$)",
                href,
            ):
                continue
            if not re.search(r"https://venturebeat\.com/(orchestration|technology|infrastructure|data|security)/", href):
                continue
            if href.rstrip("/").count("/") < 4:
                continue
            if href not in links:
                links.append(href)

        now_dt = datetime.now(timezone.utc).astimezone()
        cutoff = now_dt - timedelta(hours=max(1, window_hours))

        out: list[CrawledItem] = []
        for href in links:
            if len(out) >= max(1, per_source):
                break
            try:
                item = self._fetch_detail(href, allow_insecure_fallback=allow_insecure_fallback)
                if not item:
                    continue
                pub = _parse_dt(item.get("published", ""))
                if pub and (pub < cutoff or pub > now_dt + timedelta(minutes=10)):
                    continue
                out.append(
                    CrawledItem(
                        source=self.name,
                        category="行业资讯",
                        title=item.get("title", ""),
                        link=href,
                        summary=item.get("summary", ""),
                        published=item.get("published", ""),
                        content_excerpt=item.get("content_excerpt", ""),
                    )
                )
            except Exception:
                continue
        return out

    def _fetch_detail(self, url: str, *, allow_insecure_fallback: bool) -> dict[str, Any] | None:
        html = _fetch_html(url, timeout=22, allow_insecure_fallback=allow_insecure_fallback)
        soup = BeautifulSoup(html, "html.parser")

        title = ""
        h1 = soup.select_one("h1")
        if h1:
            title = strip_html(h1.get_text(" ", strip=True))

        published = ""
        meta = soup.select_one("meta[property='article:published_time'], meta[name='parsely-pub-date']")
        if meta and meta.get("content"):
            published = str(meta.get("content")).strip()
        if not published:
            t = soup.select_one("time")
            if t and t.get("datetime"):
                published = str(t.get("datetime")).strip()

        summary = ""
        md = soup.select_one("meta[name='description'], meta[property='og:description']")
        if md and md.get("content"):
            summary = strip_html(str(md.get("content"))[:500])

        body_text = soup.get_text(" ", strip=True)
        body_text = re.sub(r"\s+", " ", body_text).strip()
        content_excerpt = body_text[:4000] if body_text else ""

        if not title:
            return None
        return {
            "title": title[:300],
            "published": published,
            "summary": summary,
            "content_excerpt": content_excerpt,
        }


def _parse_dt(raw: str) -> datetime | None:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        norm = text.replace("Z", "+00:00")
        d = datetime.fromisoformat(norm)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone()
    except Exception:
        return None


def _fetch_html(url: str, *, timeout: int, allow_insecure_fallback: bool) -> str:
    import ssl
    import urllib.error
    import urllib.request

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        },
    )

    def _read(context: ssl.SSLContext) -> str:
        with urllib.request.urlopen(req, timeout=timeout, context=context) as resp:
            return resp.read().decode("utf-8", errors="ignore")

    try:
        return _read(ssl.create_default_context())
    except ssl.SSLCertVerificationError:
        if not allow_insecure_fallback:
            raise
        return _read(ssl._create_unverified_context())
    except urllib.error.HTTPError as ex:
        if ex.code in (403, 429):
            return ""
        raise


def _fetch_html_playwright(url: str, *, timeout_ms: int) -> str | None:
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.set_default_timeout(timeout_ms)
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_timeout(1500)
            html = page.content()
            browser.close()
            return html
    except Exception:
        return None


def _fetch_html_playwright_scroll(
    url: str,
    *,
    timeout_ms: int,
    max_scrolls: int,
    scroll_pause_ms: int,
    click_load_more: bool,
) -> str | None:
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return None

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1440, "height": 900},
            )
            page = context.new_page()
            page.set_default_timeout(timeout_ms)
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_timeout(900)

            prev_height = 0
            stable_rounds = 0
            for _ in range(max_scrolls):
                if click_load_more:
                    _try_click_load_more(page)
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(scroll_pause_ms)
                height = int(page.evaluate("document.body.scrollHeight") or 0)
                if height <= prev_height + 10:
                    stable_rounds += 1
                else:
                    stable_rounds = 0
                prev_height = max(prev_height, height)
                if stable_rounds >= 3:
                    break

            html = page.content()
            context.close()
            browser.close()
            return html
    except Exception:
        return None


def _try_click_load_more(page: Any) -> None:
    candidates = [
        "button:has-text('Load more')",
        "button:has-text('More')",
        "a:has-text('Load more')",
        "a:has-text('More')",
    ]
    for sel in candidates:
        try:
            btn = page.locator(sel).first
            if btn and btn.is_visible():
                btn.click(timeout=1200)
                page.wait_for_timeout(500)
                return
        except Exception:
            continue

