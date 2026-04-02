from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

from bs4 import BeautifulSoup

from ai_news_skill.crawlers.site.base import CrawledItem
from run_daily_digest import strip_html


class TechCrunchAICrawler:
    # Match `sources.json` name exactly for routing.
    name = "TechCrunch AI"

    LIST_URL = "https://techcrunch.com/tag/artificial-intelligence/"

    def crawl(self, *, per_source: int, window_hours: int, allow_insecure_fallback: bool) -> list[CrawledItem]:
        html = _fetch_html(self.LIST_URL, timeout=22, allow_insecure_fallback=allow_insecure_fallback)
        soup = BeautifulSoup(html, "html.parser")

        links: list[str] = []
        for a in soup.select("a.post-block__title__link"):
            href = (a.get("href") or "").strip()
            if _is_article_url(href) and href not in links:
                links.append(href)
        if not links:
            # Fallback: heuristic link grab.
            for a in soup.select("a[href^='https://techcrunch.com/']"):
                href = (a.get("href") or "").strip()
                if "/tag/" in href or "/category/" in href:
                    continue
                if not _is_article_url(href):
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

        # Published time
        published = ""
        meta = soup.select_one("meta[property='article:published_time'], meta[name='parsely-pub-date']")
        if meta and meta.get("content"):
            published = str(meta.get("content")).strip()
        if not published:
            t = soup.select_one("time")
            if t and t.get("datetime"):
                published = str(t.get("datetime")).strip()

        # Summary
        summary = ""
        md = soup.select_one("meta[name='description'], meta[property='og:description']")
        if md and md.get("content"):
            summary = strip_html(str(md.get("content"))[:500])

        # Content excerpt: rely on downstream `fetch_article_excerpt` too, but we provide a best effort excerpt.
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
        # ISO8601
        norm = text.replace("Z", "+00:00")
        d = datetime.fromisoformat(norm)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone()
    except Exception:
        return None


def _is_article_url(url: str) -> bool:
    href = (url or "").strip()
    if not (href.startswith("https://techcrunch.com/") or href.startswith("http://techcrunch.com/")):
        return False
    # Prefer canonical article urls: /YYYY/MM/DD/...
    return bool(re.search(r"https?://techcrunch\.com/20\d{2}/", href))


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

