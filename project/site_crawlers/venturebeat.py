from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

from bs4 import BeautifulSoup

from ai_news_skill.crawlers.site.base import CrawledItem
from run_daily_digest import strip_html


class VentureBeatAICrawler:
    name = "VentureBeat AI"

    LIST_URL = "https://venturebeat.com/category/ai/"
    BASE = "https://venturebeat.com"

    def crawl(self, *, per_source: int, window_hours: int, allow_insecure_fallback: bool) -> list[CrawledItem]:
        html = _fetch_html(self.LIST_URL, timeout=22, allow_insecure_fallback=allow_insecure_fallback)
        if not html.strip():
            html = _fetch_html_playwright(self.LIST_URL, timeout_ms=25000) or ""
        soup = BeautifulSoup(html, "html.parser")

        links: list[str] = []
        for a in soup.select("a[href]"):
            href_raw = (a.get("href") or "").strip()
            if not href_raw:
                continue
            href = href_raw
            if href.startswith("/"):
                href = self.BASE.rstrip("/") + href
            if not href.startswith("https://venturebeat.com/"):
                continue
            if "/category/" in href or href.rstrip("/") == self.LIST_URL.rstrip("/"):
                continue
            # Heuristic: allow common article sections; avoid author/category/nav.
            if re.search(r"https://venturebeat\.com/(author|category|contact|terms|privacy|press-releases|guest-posts)(/|$)", href):
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
        return {"title": title[:300], "published": published, "summary": summary, "content_excerpt": content_excerpt}


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
    # VentureBeat is more sensitive to bot-like UAs; use browser-like headers.
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
        # 429 / 403 are common anti-bot responses; treat as "no items" for this run.
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

