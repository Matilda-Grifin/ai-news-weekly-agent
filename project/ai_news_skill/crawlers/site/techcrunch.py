from __future__ import annotations

import re
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Any

from bs4 import BeautifulSoup

from ai_news_skill.core.http_client import urlopen_with_outbound_proxy
from ai_news_skill.pipeline.utils import strip_html

from .base import CrawledItem, playwright_chromium_launch_kwargs


class TechCrunchAICrawler:
    # Match `sources.json` name exactly for routing.
    name = "TechCrunch AI"

    LIST_URL = "https://techcrunch.com/tag/artificial-intelligence/"

    def crawl(
        self,
        *,
        per_source: int,
        window_hours: int,
        allow_insecure_fallback: bool,
    ) -> list[CrawledItem]:
        return self._crawl_impl(
            per_source=per_source,
            window_hours=window_hours,
            allow_insecure_fallback=allow_insecure_fallback,
            intent_keywords=[],
            memory_keywords=[],
            query_mode="generic",
            max_detail_items=10,
        )

    def crawl_with_context(
        self,
        *,
        per_source: int,
        window_hours: int,
        allow_insecure_fallback: bool,
        intent_keywords: list[str],
        memory_keywords: list[str],
        query_mode: str,
        max_detail_items: int = 10,
    ) -> list[CrawledItem]:
        return self._crawl_impl(
            per_source=per_source,
            window_hours=window_hours,
            allow_insecure_fallback=allow_insecure_fallback,
            intent_keywords=intent_keywords,
            memory_keywords=memory_keywords,
            query_mode=query_mode,
            max_detail_items=max_detail_items,
        )

    def _crawl_impl(
        self,
        *,
        per_source: int,
        window_hours: int,
        allow_insecure_fallback: bool,
        intent_keywords: list[str],
        memory_keywords: list[str],
        query_mode: str,
        max_detail_items: int,
    ) -> list[CrawledItem]:
        detail_cap = max(1, min(int(max_detail_items or 10), max(1, per_source), 10))
        now_dt = datetime.now(timezone.utc).astimezone()
        cutoff = now_dt - timedelta(hours=max(1, window_hours))

        candidates: list[dict[str, str]] = []
        seen_links: set[str] = set()
        list_urls = _build_list_urls(
            base_list_url=self.LIST_URL,
            intent_keywords=intent_keywords,
            query_mode=query_mode,
        )
        for u in list_urls:
            html = _fetch_html_playwright_scroll(
                u,
                timeout_ms=30000,
                max_scrolls=10,
                scroll_pause_ms=650,
                click_load_more=True,
            ) or ""
            if not html.strip():
                html = _fetch_html(u, timeout=22, allow_insecure_fallback=allow_insecure_fallback)
            for c in _extract_techcrunch_article_candidates(html):
                link = c.get("link", "")
                if not link or link in seen_links:
                    continue
                seen_links.add(link)
                candidates.append(c)
            if len(candidates) >= max(30, detail_cap * 3):
                break

        if not candidates:
            return []

        filtered: list[dict[str, str]] = []
        for c in candidates:
            pub_dt = _parse_dt(c.get("published", ""))
            if pub_dt and (pub_dt < cutoff or pub_dt > now_dt + timedelta(minutes=10)):
                continue
            filtered.append(c)
        candidates = filtered
        if not candidates:
            return []

        intent_l = [k.strip().lower() for k in intent_keywords if k and k.strip()][:8]
        mem_l = [k.strip().lower() for k in memory_keywords if k and k.strip()][:8]
        pref_quota = min(3, max(0, detail_cap // 3))

        def _text(c: dict[str, str]) -> str:
            return " ".join([c.get("title", ""), c.get("link", "")]).lower()

        if query_mode == "explicit" and intent_l:
            candidates = [c for c in candidates if any(k in _text(c) for k in intent_l)]
            if not candidates:
                return []

        def _score(c: dict[str, str]) -> tuple[int, int]:
            txt = _text(c)
            intent_score = sum(2 for k in intent_l if k in txt)
            pref_score = sum(1 for k in mem_l if k in txt)
            has_list_date = 1 if _parse_dt(c.get("published", "")) else 0
            return (intent_score + pref_score, has_list_date)

        candidates.sort(key=_score, reverse=True)
        chosen: list[dict[str, str]] = []
        if query_mode == "generic" and mem_l and pref_quota > 0:
            pref = [c for c in candidates if any(k in _text(c) for k in mem_l)]
            non_pref = [c for c in candidates if c not in pref]
            chosen.extend(pref[:pref_quota])
            for c in non_pref:
                if len(chosen) >= detail_cap:
                    break
                chosen.append(c)
        else:
            chosen = candidates[:detail_cap]
        chosen = chosen[:detail_cap]

        out: list[CrawledItem] = []
        for c in chosen:
            href = c.get("link", "")
            if not href:
                continue
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
                if len(out) >= detail_cap:
                    break
            except Exception:
                continue
        return out

    def _fetch_detail(self, url: str, *, allow_insecure_fallback: bool) -> dict[str, Any] | None:
        html = _fetch_html(url, timeout=22, allow_insecure_fallback=allow_insecure_fallback)
        if not html.strip():
            html = _fetch_html_playwright(url, timeout_ms=30000) or ""
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
            html2 = _fetch_html_playwright(url, timeout_ms=30000) or ""
            if html2 and len(html2) > len(html):
                soup2 = BeautifulSoup(html2, "html.parser")
                h1_2 = soup2.select_one("h1")
                if h1_2:
                    title = strip_html(h1_2.get_text(" ", strip=True))
                if not published:
                    meta2 = soup2.select_one("meta[property='article:published_time'], meta[name='parsely-pub-date']")
                    if meta2 and meta2.get("content"):
                        published = str(meta2.get("content")).strip()
                if not summary:
                    md2 = soup2.select_one("meta[name='description'], meta[property='og:description']")
                    if md2 and md2.get("content"):
                        summary = strip_html(str(md2.get("content"))[:500])
                if not content_excerpt:
                    body_text2 = soup2.get_text(" ", strip=True)
                    body_text2 = re.sub(r"\s+", " ", body_text2).strip()
                    content_excerpt = body_text2[:4000] if body_text2 else ""

        # Cloudflare/anti-bot interstitial pages are not valid article content.
        if _looks_like_block_page(title, summary, content_excerpt):
            html3 = _fetch_html_playwright(url, timeout_ms=30000) or ""
            soup3 = BeautifulSoup(html3, "html.parser")
            h1_3 = soup3.select_one("h1")
            title3 = strip_html(h1_3.get_text(" ", strip=True)) if h1_3 else ""
            md3 = soup3.select_one("meta[name='description'], meta[property='og:description']")
            summary3 = strip_html(str(md3.get("content"))[:500]) if md3 and md3.get("content") else ""
            body3 = re.sub(r"\s+", " ", soup3.get_text(" ", strip=True)).strip()[:4000]
            if _looks_like_block_page(title3, summary3, body3):
                return None
            if title3:
                title = title3
            if summary3:
                summary = summary3
            if body3:
                content_excerpt = body3

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
    low = text.lower()
    now = datetime.now(timezone.utc).astimezone()
    m = re.search(r"(\d+)\s+minute[s]?\s+ago", low)
    if m:
        return now - timedelta(minutes=max(0, int(m.group(1))))
    m = re.search(r"(\d+)\s+hour[s]?\s+ago", low)
    if m:
        return now - timedelta(hours=max(0, int(m.group(1))))
    m = re.search(r"(\d+)\s+day[s]?\s+ago", low)
    if m:
        return now - timedelta(days=max(0, int(m.group(1))))
    try:
        norm = text.replace("Z", "+00:00")
        d = datetime.fromisoformat(norm)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone()
    except Exception:
        pass
    for fmt in ("%B %d, %Y", "%b %d, %Y"):
        try:
            d2 = datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
            return d2.astimezone()
        except Exception:
            continue
    return None


def _is_article_url(url: str) -> bool:
    href = (url or "").strip()
    if not (href.startswith("https://techcrunch.com/") or href.startswith("http://techcrunch.com/")):
        return False
    if "/tag/" in href or "/category/" in href or "/author/" in href:
        return False
    return bool(re.search(r"https?://techcrunch\.com/20\d{2}/", href))


def _extract_techcrunch_article_candidates(html: str) -> list[dict[str, str]]:
    soup = BeautifulSoup(html or "", "html.parser")
    out: list[dict[str, str]] = []
    seen: set[str] = set()

    # Primary structure from current TC list/search pages.
    for art in soup.select("article"):
        a = art.select_one("a.loop-card__title-link, a.loop-card__title, h3 a, h2 a, a[href]")
        if not a:
            continue
        href = (a.get("href") or "").strip()
        if not _is_article_url(href):
            continue
        if href in seen:
            continue
        title = strip_html(a.get_text(" ", strip=True))
        t_node = art.select_one("time")
        published = ""
        if t_node:
            published = (t_node.get("datetime") or t_node.get_text(" ", strip=True) or "").strip()
        if not published:
            txt = art.get_text(" ", strip=True)
            m = re.search(r"\b\d+\s+(?:minute|hour|day)s?\s+ago\b", txt, flags=re.I)
            if m:
                published = m.group(0)
        out.append({"link": href, "title": title[:300], "published": published})
        seen.add(href)

    # Fallback: recover canonical article URLs by regex.
    if len(out) < 8:
        for m in re.findall(r"https://techcrunch\.com/20\d{2}/\d{2}/\d{2}/[a-z0-9\-]+/?", html or "", flags=re.I):
            href = m.rstrip("/")
            if href not in seen:
                out.append({"link": href, "title": "", "published": ""})
                seen.add(href)
    return out


def _build_list_urls(*, base_list_url: str, intent_keywords: list[str], query_mode: str) -> list[str]:
    urls: list[str] = []
    mode = (query_mode or "").strip().lower()
    if mode == "explicit":
        for kw in [k.strip() for k in (intent_keywords or []) if k and k.strip()][:3]:
            q = urllib.parse.quote(kw, safe="")
            urls.append(f"https://techcrunch.com/?s={q}")
        # Then prefer fresh homepage feed before tag archive.
        urls.append("https://techcrunch.com/")
        urls.append(base_list_url)
        return urls
    # Generic mode: prioritize homepage (fresh relative-time cards), then AI tag page.
    urls.append("https://techcrunch.com/")
    urls.append(base_list_url)
    return urls


def _looks_like_block_page(title: str, summary: str, content_excerpt: str) -> bool:
    t = " ".join([(title or ""), (summary or ""), (content_excerpt or "")]).lower()
    if not t:
        return False
    markers = [
        "checking your browser",
        "just a moment",
        "enable javascript",
        "verify you are human",
        "ddos protection",
    ]
    return any(m in t for m in markers)


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
        with urlopen_with_outbound_proxy(
            req, timeout=timeout, context=context, proxy_scope="site_crawler"
        ) as resp:
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
    except (urllib.error.URLError, TimeoutError, OSError):
        return ""


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
            page.wait_for_timeout(1000)
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
        "button:has-text('See more')",
        "a:has-text('Load more')",
        "a:has-text('See More')",
    ]
    for sel in candidates:
        try:
            btn = page.locator(sel).first
            if btn and btn.is_visible():
                btn.click(timeout=1200)
                page.wait_for_timeout(450)
                return
        except Exception:
            continue

