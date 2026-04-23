from __future__ import annotations

import re
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Any

from bs4 import BeautifulSoup

from ai_news_skill.core.http_client import urlopen_with_outbound_proxy
from ai_news_skill.pipeline.utils import strip_html

from .base import CrawledItem, playwright_chromium_launch_kwargs


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
        # Cap detail fetching to keep runtime bounded even when list candidate count is large.
        detail_cap = max(1, min(int(max_detail_items or 10), max(1, per_source), 10))
        now_dt = datetime.now(timezone.utc).astimezone()
        cutoff = now_dt - timedelta(hours=max(1, window_hours))

        candidates: list[dict[str, str]] = []
        seen_urls: set[str] = set()
        list_urls = _build_list_urls(
            base_list_url=self.LIST_URL,
            intent_keywords=intent_keywords,
            query_mode=query_mode,
        )
        for u in list_urls:
            is_category = u.rstrip("/") == self.LIST_URL.rstrip("/")
            html = _fetch_html_playwright_scroll(
                u,
                timeout_ms=30000,
                max_scrolls=14 if is_category else 8,
                scroll_pause_ms=650,
                click_load_more=is_category,
            ) or ""
            if not html.strip():
                html = _fetch_html(u, timeout=22, allow_insecure_fallback=allow_insecure_fallback)
            for c in _extract_venturebeat_article_candidates(html, base=self.BASE, list_url=self.LIST_URL):
                link = c.get("link", "")
                if not link or link in seen_urls:
                    continue
                seen_urls.add(link)
                candidates.append(c)
            if len(candidates) >= max(30, detail_cap * 3):
                break

        if not candidates:
            return []

        # Strictly respect user window when list page exposes date.
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
            # Explicit query mode is strict: keep only keyword-matching candidates.
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
            if len(out) >= detail_cap:
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
            # Some pages hide title/body behind modal overlays in plain HTML path.
            html2 = _fetch_html_playwright(url, timeout_ms=30000) or ""
            if html2 and len(html2) > len(html):
                soup = BeautifulSoup(html2, "html.parser")
                h1_2 = soup.select_one("h1")
                if h1_2:
                    title = strip_html(h1_2.get_text(" ", strip=True))
                if not published:
                    meta2 = soup.select_one("meta[property='article:published_time'], meta[name='parsely-pub-date']")
                    if meta2 and meta2.get("content"):
                        published = str(meta2.get("content")).strip()
                    if not published:
                        t2 = soup.select_one("time")
                        if t2 and t2.get("datetime"):
                            published = str(t2.get("datetime")).strip()
                if not summary:
                    md2 = soup.select_one("meta[name='description'], meta[property='og:description']")
                    if md2 and md2.get("content"):
                        summary = strip_html(str(md2.get("content"))[:500])
                if not content_excerpt:
                    body_text2 = soup.get_text(" ", strip=True)
                    body_text2 = re.sub(r"\s+", " ", body_text2).strip()
                    content_excerpt = body_text2[:4000] if body_text2 else ""
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
        pass
    for fmt in ("%B %d, %Y", "%b %d, %Y"):
        try:
            d2 = datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
            return d2.astimezone()
        except Exception:
            continue
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
        # Network resets are common on this source; let caller fallback to Playwright.
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
            _dismiss_venturebeat_overlays(page)
            page.wait_for_timeout(1500)
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
            _dismiss_venturebeat_overlays(page)
            page.wait_for_timeout(900)

            prev_height = 0
            stable_rounds = 0
            for _ in range(max_scrolls):
                _dismiss_venturebeat_overlays(page)
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
    _dismiss_venturebeat_overlays(page)
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


def _extract_venturebeat_article_candidates(html: str, *, base: str, list_url: str) -> list[dict[str, str]]:
    soup = BeautifulSoup(html or "", "html.parser")
    out: list[dict[str, str]] = []
    seen: set[str] = set()

    for article in soup.select("article"):
        a = article.select_one("a[href]")
        if not a:
            continue
        href = (a.get("href") or "").strip()
        if not href:
            continue
        if href.startswith("/"):
            href = base.rstrip("/") + href
        href = href.split("#", 1)[0].rstrip("/")
        if not _is_venturebeat_article_url(href, list_url=list_url):
            continue
        if href in seen:
            continue
        title = strip_html(a.get_text(" ", strip=True))
        if not title:
            h = article.select_one("h1, h2, h3")
            if h:
                title = strip_html(h.get_text(" ", strip=True))
        published = _extract_date_text(article.get_text(" ", strip=True))
        out.append({"link": href, "title": title[:300], "published": published})
        seen.add(href)

    # Fallback: venturebeat pages may embed links in JSON/script payloads; recover urls by regex.
    if len(out) < 8:
        for m in re.findall(
            r"https://venturebeat\.com/(?:orchestration|technology|infrastructure|data|security)/[a-z0-9-]+",
            html or "",
            flags=re.IGNORECASE,
        ):
            href = m.rstrip("/")
            if _is_venturebeat_article_url(href, list_url=list_url) and href not in seen:
                out.append({"link": href, "title": "", "published": ""})
                seen.add(href)
        for m in re.findall(
            r"/(?:orchestration|technology|infrastructure|data|security)/[a-z0-9-]+",
            html or "",
            flags=re.IGNORECASE,
        ):
            href = (base.rstrip("/") + m).rstrip("/")
            if _is_venturebeat_article_url(href, list_url=list_url) and href not in seen:
                out.append({"link": href, "title": "", "published": ""})
                seen.add(href)
    return out


def _is_venturebeat_article_url(href: str, *, list_url: str) -> bool:
    if not href.startswith("https://venturebeat.com/"):
        return False
    if "/category/" in href or href.rstrip("/") == list_url.rstrip("/"):
        return False
    if re.search(
        r"https://venturebeat\.com/(author|category|contact|terms|privacy|press-releases|guest-posts|search)(/|$)",
        href,
    ):
        return False
    if not re.search(r"https://venturebeat\.com/(orchestration|technology|infrastructure|data|security)/", href):
        return False
    if href.rstrip("/").count("/") < 4:
        return False
    return True


def _extract_date_text(text: str) -> str:
    t = (text or "").strip()
    if not t:
        return ""
    m = re.search(
        r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},\s+\d{4}\b",
        t,
        flags=re.IGNORECASE,
    )
    return m.group(0) if m else ""


def _build_list_urls(*, base_list_url: str, intent_keywords: list[str], query_mode: str) -> list[str]:
    urls = [base_list_url]
    if (query_mode or "").strip().lower() != "explicit":
        return urls
    cleaned = [k.strip() for k in (intent_keywords or []) if k and k.strip()]
    if not cleaned:
        return urls
    out = []
    for kw in cleaned[:3]:
        q = urllib.parse.quote(kw, safe="")
        out.append(f"https://venturebeat.com/search/{q}")
    out.append(base_list_url)
    return out


def _dismiss_venturebeat_overlays(page: Any) -> None:
    # 1) Consent banner often blocks interactions at bottom.
    consent_candidates = [
        "button:has-text('Accept')",
        "button:has-text('Decline')",
        "button:has-text('Preferences')",
    ]
    for sel in consent_candidates:
        try:
            btn = page.locator(sel).first
            if btn and btn.is_visible():
                btn.click(timeout=1000)
                page.wait_for_timeout(200)
        except Exception:
            continue

    # 2) Newsletter subscription modal close button ("X"), appears on list and detail pages.
    close_candidates = [
        "button[aria-label='Close dialog']",
        "button[class*='top-8'][class*='right-8'][class*='cursor-pointer']",
        "button:has-text('×')",
    ]
    for sel in close_candidates:
        try:
            btn = page.locator(sel).first
            if btn and btn.is_visible():
                btn.click(timeout=1000)
                page.wait_for_timeout(250)
                # Modal may re-render once; try twice quickly.
                if btn.is_visible():
                    btn.click(timeout=800)
                    page.wait_for_timeout(200)
                return
        except Exception:
            continue

