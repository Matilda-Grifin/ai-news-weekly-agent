from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from run_daily_digest import strip_html

from .base import CrawledItem, playwright_chromium_launch_kwargs


class HuggingFaceBlogCrawler:
    # Match `sources.json` name exactly for routing.
    name = "Hugging Face Blog"

    LIST_URL = "https://huggingface.co/blog"
    BASE = "https://huggingface.co"

    def crawl(
        self,
        *,
        per_source: int,
        window_hours: int,
        allow_insecure_fallback: bool,  # kept for protocol compatibility (unused here)
    ) -> list[CrawledItem]:
        links = _collect_hf_blog_links(
            self.LIST_URL,
            target=max(12, per_source * 4),
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
                raw_pub = str(item.get("published", "") or "").strip()
                pub = _parse_dt(raw_pub)
                if raw_pub and pub is None:
                    continue
                if pub and (pub < cutoff or pub > now_dt + timedelta(minutes=10)):
                    continue
                out.append(
                    CrawledItem(
                        source=self.name,
                        category="开源与工具",
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


def _normalize_url(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return ""
    u = u.split("#", 1)[0].strip()
    if u.startswith("https://huggingface.co/") and u != "https://huggingface.co/":
        u = u.rstrip("/")
    return u


def _is_hf_blog_article_url(url: str) -> bool:
    href = _normalize_url(url)
    if not href.startswith("https://huggingface.co/"):
        return False
    parsed = urlparse(href)
    if parsed.scheme != "https" or parsed.netloc not in {"huggingface.co", "www.huggingface.co"}:
        return False
    path = (parsed.path or "").strip("/")
    if not path.startswith("blog/"):
        return False
    # Exclude index/pagination pages
    if path == "blog" or path.startswith("blog/page/"):
        return False
    # Exclude category/landing pages like /blog/community or /blog/zh
    rest = path[len("blog/") :]
    if not rest:
        return False
    # /blog/<lang> (localized listing)
    if re.fullmatch(r"[a-z]{2,3}", rest):
        return False
    # /blog/community and other non-article sections
    if rest in {
        "community",
        "tags",
        "tag",
        "authors",
        "author",
        "categories",
        "category",
        "about",
    }:
        return False
    # Heuristic: keep /blog/<slug> or /blog/<org>/<slug>
    parts = rest.split("/")
    if len(parts) == 1:
        # single slug should be article-ish (avoid very short)
        return len(parts[0]) >= 4
    if len(parts) == 2:
        return len(parts[1]) >= 4
    return False
    return True


def _collect_hf_blog_links(url: str, *, target: int, timeout_ms: int) -> list[str]:
    html = _fetch_html_playwright_scroll(
        url,
        timeout_ms=timeout_ms,
        max_scrolls=12,
        scroll_pause_ms=600,
        click_next=True,
    )
    soup = BeautifulSoup(html or "", "html.parser")

    links: list[str] = []

    for a in soup.select("a[href^='/blog/'], a[href^='https://huggingface.co/blog/']"):
        href = (a.get("href") or "").strip()
        if not href:
            continue
        full = href
        if href.startswith("/"):
            full = "https://huggingface.co" + href
        full = _normalize_url(full)
        if _is_hf_blog_article_url(full) and full not in links:
            links.append(full)

    # Fallback: any HF link that looks like blog article.
    if len(links) < max(6, min(target, 20)):
        for a in soup.select("a[href^='https://huggingface.co/']"):
            href = _normalize_url((a.get("href") or "").strip())
            if _is_hf_blog_article_url(href) and href not in links:
                links.append(href)

    return links[: max(1, target)]


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
    for selector in ["main", "article"]:
        el = soup.select_one(selector)
        if not el:
            continue
        text = el.get_text(" ", strip=True)
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) >= 200:
            return text[:8000]
    text = soup.get_text(" ", strip=True)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:8000] if text else ""


def _extract_published_time(soup: BeautifulSoup) -> str:
    meta = soup.select_one(
        "meta[property='article:published_time'], meta[name='parsely-pub-date'], meta[name='date']"
    )
    if meta and meta.get("content"):
        return str(meta.get("content")).strip()

    t = soup.select_one("time[datetime]")
    if t and t.get("datetime"):
        return str(t.get("datetime")).strip()

    # Some HF blog pages embed JSON-LD Article.
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
    text = re.sub(r"\s+", " ", text).strip(" ,.;")
    if not text:
        return None
    m = re.search(
        r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},\s+\d{4}\b",
        text,
        flags=re.I,
    )
    if m:
        text = m.group(0)
    try:
        d0 = parsedate_to_datetime(text)
        if d0 is not None:
            if d0.tzinfo is None:
                d0 = d0.replace(tzinfo=timezone.utc)
            return d0.astimezone()
    except Exception:
        pass
    try:
        norm = text.replace("Z", "+00:00")
        d = datetime.fromisoformat(norm)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone()
    except Exception:
        pass
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%Y-%m-%d"):
        try:
            d2 = datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
            return d2.astimezone()
        except Exception:
            continue
    return None


def _fetch_html_playwright(url: str, *, timeout_ms: int) -> str | None:
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(**playwright_chromium_launch_kwargs())
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
    click_next: bool,
) -> str | None:
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return None

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(**playwright_chromium_launch_kwargs())
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
            for i in range(max_scrolls):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(scroll_pause_ms)
                height = int(page.evaluate("document.body.scrollHeight") or 0)
                if height <= prev_height + 10:
                    stable_rounds += 1
                else:
                    stable_rounds = 0
                prev_height = max(prev_height, height)

                # HF blog has pagination; try to click "Next" occasionally.
                if click_next and i in {3, 6, 9}:
                    _try_click_next(page)
                    page.wait_for_timeout(800)

                if stable_rounds >= 3:
                    break

            html = page.content()
            context.close()
            browser.close()
            return html
    except Exception:
        return None


def _try_click_next(page: Any) -> None:
    candidates = [
        "a[rel='next']",
        "a:has-text('Next')",
        "button:has-text('Next')",
    ]
    for sel in candidates:
        try:
            loc = page.locator(sel).first
            if loc and loc.is_visible():
                loc.click(timeout=1200)
                return
        except Exception:
            continue

