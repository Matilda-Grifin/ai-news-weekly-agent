from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from run_daily_digest import strip_html

from .base import CrawledItem


class OpenAINewsCrawler:
    """
    OpenAI News crawler (list page + scroll).

    NOTE:
    - This is intentionally a "list-page source" (https://openai.com/news),
      not the RSS endpoint (https://openai.com/news/rss.xml).
    - Name matches `sources.json` for routing.
    """

    name = "OpenAI Blog"

    LIST_URL = "https://openai.com/news"
    BASE = "https://openai.com"

    def crawl(
        self,
        *,
        per_source: int,
        window_hours: int,
        allow_insecure_fallback: bool,  # kept for protocol compatibility (unused here)
    ) -> list[CrawledItem]:
        # 1) Scroll list page to collect enough links.
        links = _collect_openai_news_links(
            self.LIST_URL,
            target=max(10, per_source * 4),
            timeout_ms=30000,
        )

        now_dt = datetime.now(timezone.utc).astimezone()
        cutoff = now_dt - timedelta(hours=max(1, window_hours))

        out: list[CrawledItem] = []
        for href in links:
            if len(out) >= max(1, per_source):
                break
            try:
                item = _fetch_detail(href, timeout_ms=30000)
                if not item:
                    continue
                pub = _parse_dt(item.get("published", ""))
                if pub and (pub < cutoff or pub > now_dt + timedelta(minutes=10)):
                    continue
                out.append(
                    CrawledItem(
                        source=self.name,
                        category="官方发布",
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


def _collect_openai_news_links(url: str, *, target: int, timeout_ms: int) -> list[str]:
    html = _fetch_html_playwright_scroll(
        url,
        timeout_ms=timeout_ms,
        max_scrolls=18,
        scroll_pause_ms=650,
        click_load_more=True,
    )
    soup = BeautifulSoup(html or "", "html.parser")

    links: list[str] = []

    # Primary: OpenAI news items are usually /index/<slug>
    for a in soup.select("a[href^='/index/'], a[href^='https://openai.com/index/']"):
        href = (a.get("href") or "").strip()
        if not href:
            continue
        full = href
        if href.startswith("/"):
            full = "https://openai.com" + href
        full = _normalize_url(full)
        if _is_openai_article_url(full) and full not in links:
            links.append(full)

    # Fallback: any openai.com link that looks like an article.
    if len(links) < max(6, min(target, 20)):
        for a in soup.select("a[href^='https://openai.com/']"):
            href = (a.get("href") or "").strip()
            href = _normalize_url(href)
            if _is_openai_article_url(href) and href not in links:
                links.append(href)

    return links[: max(1, target)]


def _is_openai_article_url(url: str) -> bool:
    href = (url or "").strip()
    if not href.startswith("https://openai.com/"):
        return False
    parsed = urlparse(href)
    if parsed.scheme != "https" or parsed.netloc not in {"openai.com", "www.openai.com"}:
        return False
    path = (parsed.path or "").strip("/")
    if not path:
        return False
    # Known content families we want.
    if path.startswith("index/"):
        return True
    # Keep room for future OpenAI news URLs (conservative, avoid policies/docs).
    if path.startswith("blog/") or path.startswith("research/") or path.startswith("global-affairs/"):
        return True
    # Avoid obvious non-articles.
    if path.startswith(("policies/", "brand/", "careers/", "pricing", "api/", "docs/", "security/")):
        return False
    return False


def _normalize_url(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return ""
    # Drop fragment and trailing slash for stable dedupe keys.
    u = u.split("#", 1)[0].strip()
    if u.startswith("https://openai.com/") and u != "https://openai.com/":
        u = u.rstrip("/")
    return u


def _fetch_detail(url: str, *, timeout_ms: int) -> dict[str, Any] | None:
    url = _normalize_url(url)
    html = _fetch_html_playwright(url, timeout_ms=timeout_ms) or ""
    if len(html.strip()) < 200:
        return None
    soup = BeautifulSoup(html, "html.parser")

    title = ""
    h1 = soup.select_one("h1")
    if h1:
        title = strip_html(h1.get_text(" ", strip=True))
    if not title:
        og = soup.select_one("meta[property='og:title']")
        if og and og.get("content"):
            title = strip_html(str(og.get("content"))).strip()

    published = _extract_published_time(soup)

    summary = ""
    md = soup.select_one("meta[name='description'], meta[property='og:description']")
    if md and md.get("content"):
        summary = strip_html(str(md.get("content"))[:600])

    content_excerpt = _extract_main_text_excerpt(soup)

    if not title:
        return None
    return {
        "title": title[:300],
        "published": published,
        "summary": summary,
        "content_excerpt": content_excerpt,
    }


def _extract_main_text_excerpt(soup: BeautifulSoup) -> str:
    # Prefer main/article
    for selector in ["main", "article"]:
        el = soup.select_one(selector)
        if not el:
            continue
        text = el.get_text(" ", strip=True)
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) >= 200:
            return text[:8000]

    # Fallback: entire page text (still useful for downstream summarization)
    text = soup.get_text(" ", strip=True)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:8000] if text else ""


def _extract_published_time(soup: BeautifulSoup) -> str:
    # 1) OpenGraph/Article meta
    meta = soup.select_one(
        "meta[property='article:published_time'], meta[name='parsely-pub-date'], meta[name='article:published_time']"
    )
    if meta and meta.get("content"):
        return str(meta.get("content")).strip()

    # 2) time tag
    t = soup.select_one("time[datetime]")
    if t and t.get("datetime"):
        return str(t.get("datetime")).strip()

    # 3) JSON-LD (datePublished)
    for s in soup.select("script[type='application/ld+json']"):
        raw = (s.get_text() or "").strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue
        found = _find_date_published_in_jsonld(data)
        if found:
            return found

    return ""


def _find_date_published_in_jsonld(data: Any) -> str:
    # JSON-LD can be dict, list, or dict with @graph.
    if isinstance(data, dict):
        if isinstance(data.get("datePublished"), str) and data["datePublished"].strip():
            return data["datePublished"].strip()
        if isinstance(data.get("dateCreated"), str) and data["dateCreated"].strip():
            return data["dateCreated"].strip()
        graph = data.get("@graph")
        if graph is not None:
            return _find_date_published_in_jsonld(graph)
        for v in data.values():
            got = _find_date_published_in_jsonld(v)
            if got:
                return got
    if isinstance(data, list):
        for it in data:
            got = _find_date_published_in_jsonld(it)
            if got:
                return got
    return ""


def _parse_dt(raw: str) -> datetime | None:
    text = (raw or "").strip()
    if not text:
        return None
    # ISO first
    try:
        norm = text.replace("Z", "+00:00")
        d = datetime.fromisoformat(norm)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone()
    except Exception:
        return None


def _fetch_html_playwright(url: str, *, timeout_ms: int) -> str | None:
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
            page.wait_for_timeout(800)
            html = page.content()
            context.close()
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
    """
    Scroll down to trigger lazy-loading; optionally click common "load more" buttons.
    Returns the final HTML snapshot.
    """
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
            # "networkidle" is unreliable on modern sites (long-polling, analytics).
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
    # Best-effort: try a few common patterns.
    candidates = [
        "button:has-text('Load more')",
        "button:has-text('Show more')",
        "button:has-text('More')",
        "a:has-text('Load more')",
        "a:has-text('Show more')",
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

