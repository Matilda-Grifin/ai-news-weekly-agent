from __future__ import annotations

import re
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Any

from bs4 import BeautifulSoup

from ai_news_skill.core.http_client import urlopen_with_outbound_proxy
from ai_news_skill.pipeline.utils import strip_html

from .base import CrawledItem, playwright_chromium_launch_kwargs


class TechmemeCrawler:
    # Must match sources.json name exactly for runtime routing.
    name = "Techmeme"

    HOME_URL = "https://techmeme.com/"

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

        intent_l = [k.strip().lower() for k in intent_keywords if k and k.strip()][:8]
        mem_l = [k.strip().lower() for k in memory_keywords if k and k.strip()][:8]
        pref_quota = min(3, max(0, detail_cap // 3))

        list_urls = _build_list_urls(
            base_url=self.HOME_URL,
            intent_keywords=intent_l,
            query_mode=query_mode,
        )
        candidates: list[dict[str, str]] = []
        seen: set[str] = set()
        for u in list_urls:
            html = _fetch_html(u, timeout=22, allow_insecure_fallback=allow_insecure_fallback)
            if not html.strip():
                html = _fetch_html_playwright(u, timeout_ms=30000) or ""
            if not html.strip():
                continue
            if "/search/query?" in u:
                got = _extract_search_candidates(html)
            else:
                got = _extract_home_candidates(html)
            for c in got:
                link = c.get("link", "").strip()
                if not link or link in seen:
                    continue
                seen.add(link)
                candidates.append(c)
            if len(candidates) >= max(60, detail_cap * 6):
                break

        if not candidates:
            return []

        filtered: list[dict[str, str]] = []
        for c in candidates:
            pub_dt = _parse_dt(c.get("published", ""))
            # When list page has explicit time (search page), respect user window strictly.
            if pub_dt and (pub_dt < cutoff or pub_dt > now_dt + timedelta(minutes=10)):
                continue
            filtered.append(c)
        candidates = filtered
        if not candidates:
            return []

        def _text(c: dict[str, str]) -> str:
            return " ".join(
                [
                    c.get("title", ""),
                    c.get("summary", ""),
                    c.get("source_name", ""),
                    c.get("link", ""),
                ]
            ).lower()

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
            detail = _fetch_external_detail(href, allow_insecure_fallback=allow_insecure_fallback)
            # Search pages provide time; if detail is blocked we still keep list-level item.
            if not detail:
                if not c.get("title", "").strip():
                    continue
                detail = {
                    "title": c.get("title", ""),
                    "published": c.get("published", ""),
                    "summary": c.get("summary", ""),
                    "content_excerpt": "",
                }

            published = detail.get("published", "") or c.get("published", "")
            pub_dt = _parse_dt(published)
            # For home-page candidates without list time, rely on detail time if available.
            if pub_dt and (pub_dt < cutoff or pub_dt > now_dt + timedelta(minutes=10)):
                continue

            source_name = (c.get("source_name", "") or "").strip()
            source = f"Techmeme / {source_name}" if source_name else "Techmeme"
            summary = _summary_with_min_words(
                detail.get("summary", "") or c.get("summary", ""),
                detail.get("content_excerpt", ""),
                min_words=200,
                max_chars=3000,
            )
            if _word_count(summary) < 200:
                continue
            if not (published or "").strip():
                continue
            out.append(
                CrawledItem(
                    source=source,
                    category="行业资讯",
                    title=(detail.get("title", "") or c.get("title", ""))[:300],
                    link=href,
                    summary=summary,
                    published=published,
                    content_excerpt=detail.get("content_excerpt", "")[:4000],
                )
            )
            if len(out) >= detail_cap:
                break
        return out


def _build_list_urls(*, base_url: str, intent_keywords: list[str], query_mode: str) -> list[str]:
    urls: list[str] = [base_url]
    mode = (query_mode or "").strip().lower()
    kws = [k.strip() for k in (intent_keywords or []) if k and k.strip()]
    if mode == "explicit":
        for kw in kws[:3]:
            q = urllib.parse.quote(kw, safe="")
            urls.append(f"https://techmeme.com/search/query?q={q}&wm=false")
        return _dedup(urls)

    # Generic mode: homepage first, then broad search pages to ensure sufficient candidates.
    for kw in kws[:2]:
        q = urllib.parse.quote(kw, safe="")
        urls.append(f"https://techmeme.com/search/query?q={q}&wm=false")
    urls.append("https://techmeme.com/search/query?q=ai&wm=false")
    return _dedup(urls)


def _dedup(urls: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for u in urls:
        if u in seen:
            continue
        seen.add(u)
        out.append(u)
    return out


def _extract_search_candidates(html: str) -> list[dict[str, str]]:
    soup = BeautifulSoup(html or "", "html.parser")
    out: list[dict[str, str]] = []
    seen: set[str] = set()

    blocks = soup.select("div.rs, div.result, div.item, tr")
    if not blocks:
        # Fallback: derive blocks from "In context" anchors.
        in_ctx = [a for a in soup.select("a[href]") if "in context" in a.get_text(" ", strip=True).lower()]
        blocks = []
        for a in in_ctx:
            node: Any = a
            for _ in range(6):
                if node is None:
                    break
                node = getattr(node, "parent", None)
                if node is None:
                    break
                txt = node.get_text(" ", strip=True)
                if 120 <= len(txt) <= 2500:
                    blocks.append(node)
                    break

    for b in blocks:
        c = _extract_candidate_from_block(b, prefer_search=True)
        link = c.get("link", "")
        if not link or link in seen:
            continue
        seen.add(link)
        out.append(c)
    return out


def _extract_home_candidates(html: str) -> list[dict[str, str]]:
    soup = BeautifulSoup(html or "", "html.parser")
    out: list[dict[str, str]] = []
    seen: set[str] = set()

    # Try structural blocks first.
    blocks = soup.select("div.rhov, div.ii, div.item, tr")
    for b in blocks:
        c = _extract_candidate_from_block(b, prefer_search=False)
        link = c.get("link", "")
        if not link or link in seen:
            continue
        seen.add(link)
        out.append(c)

    # Fallback: external headline anchors with meaningful text.
    if len(out) < 12:
        for a in soup.select("a[href]"):
            href = str(a.get("href") or "").strip()
            title = strip_html(a.get_text(" ", strip=True))
            if not _is_external_news_url(href):
                continue
            if len(title) < 28:
                continue
            if href in seen:
                continue
            seen.add(href)
            src_name = _extract_source_name_from_block(a)
            out.append(
                {
                    "link": href,
                    "title": title[:300],
                    "summary": "",
                    "published": "",
                    "source_name": src_name,
                }
            )
            if len(out) >= 40:
                break
    return out


def _extract_candidate_from_block(block: Any, *, prefer_search: bool) -> dict[str, str]:
    anchors = list(block.select("a[href]"))
    headline = None
    for a in anchors:
        href = str(a.get("href") or "").strip()
        text = strip_html(a.get_text(" ", strip=True))
        if not _is_external_news_url(href):
            continue
        if len(text) < 20:
            continue
        # Search result blocks typically put the story headline first.
        if prefer_search:
            headline = a
            break
        if len(text) >= 28:
            headline = a
            break
    if headline is None:
        return {}

    href = str(headline.get("href") or "").strip()
    title = strip_html(headline.get_text(" ", strip=True))
    full_text = block.get_text(" ", strip=True)
    published = _extract_search_datetime(full_text) if prefer_search else ""
    source_name = _extract_source_name_from_block(block)
    summary = _extract_summary_from_block(block, title)
    return {
        "link": href,
        "title": title[:300],
        "summary": summary[:500],
        "published": published,
        "source_name": source_name,
    }


def _extract_source_name_from_block(block: Any) -> str:
    try:
        cite = block.select_one("cite a[href], cite")
        if cite:
            txt = strip_html(cite.get_text(" ", strip=True))
            if txt:
                return _normalize_source_name(txt)
    except Exception:
        pass
    text = block.get_text(" ", strip=True)
    # e.g. "Jay Peters / The Verge:"
    m = re.search(r"/\s*([^:/]{2,80})\s*:", text)
    if m:
        return _normalize_source_name(m.group(1))
    # Fallback: split "Author / Source" style strings and keep the rightmost segment.
    m2 = re.search(r"([^\n]{2,100}/[^\n]{2,100})", text)
    if m2:
        return _normalize_source_name(m2.group(1))
    return ""


def _normalize_source_name(raw: str) -> str:
    s = strip_html(raw or "")
    s = re.sub(r"\s+", " ", s).strip(" :")
    if " / " in s:
        parts = [p.strip(" :") for p in s.split("/") if p.strip(" :")]
        if parts:
            s = parts[-1]
    return s[:80]


def _extract_summary_from_block(block: Any, title: str) -> str:
    txt = block.get_text(" ", strip=True)
    if not txt:
        return ""
    t = txt.replace(title, "").strip()
    t = re.sub(r"\bIn context\b.*$", "", t, flags=re.I)
    t = re.sub(r"\s+", " ", t).strip(" -:—")
    return t[:500]


def _word_count(text: str) -> int:
    s = (text or "").strip()
    if not s:
        return 0
    return len([w for w in re.split(r"\s+", s) if w])


def _summary_with_min_words(
    primary_summary: str, fallback_excerpt: str, *, min_words: int = 200, max_chars: int = 3000
) -> str:
    s = re.sub(r"\s+", " ", (primary_summary or "").strip())
    if _word_count(s) >= min_words:
        return s[:max_chars]
    extra = re.sub(r"\s+", " ", (fallback_excerpt or "").strip())
    if not extra:
        return s[:max_chars]
    merged = (s + " " + extra).strip() if s else extra
    words = [w for w in re.split(r"\s+", merged) if w]
    if len(words) <= min_words:
        return " ".join(words)[:max_chars]
    return " ".join(words[:min_words])[:max_chars]


def _extract_search_datetime(text: str) -> str:
    t = text or ""
    m = re.search(
        r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},\s+\d{4},\s+\d{1,2}:\d{2}\s+[AP]M\b",
        t,
        flags=re.I,
    )
    return m.group(0) if m else ""


def _is_external_news_url(href: str) -> bool:
    u = (href or "").strip()
    if not (u.startswith("http://") or u.startswith("https://")):
        return False
    low = u.lower()
    if "techmeme.com/" in low:
        return False
    if any(x in low for x in ("/r2/", "doubleclick.net", "feedburner", "javascript:")):
        return False
    return True


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
    for fmt in (
        "%b %d, %Y, %I:%M %p",
        "%B %d, %Y, %I:%M %p",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
        "%b %d, %Y",
        "%B %d, %Y",
    ):
        try:
            d2 = datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
            return d2.astimezone()
        except Exception:
            continue
    return None


def _fetch_external_detail(url: str, *, allow_insecure_fallback: bool) -> dict[str, str] | None:
    html = _fetch_html(url, timeout=18, allow_insecure_fallback=allow_insecure_fallback)
    if not html.strip():
        html = _fetch_html_playwright(url, timeout_ms=22000) or ""
    if not html.strip():
        return None
    soup = BeautifulSoup(html, "html.parser")

    title = ""
    h1 = soup.select_one("h1")
    if h1:
        title = strip_html(h1.get_text(" ", strip=True))
    if not title:
        mt = soup.select_one("meta[property='og:title'], meta[name='twitter:title']")
        if mt and mt.get("content"):
            title = strip_html(str(mt.get("content")))

    published = ""
    pub_selectors = [
        "meta[property='article:published_time']",
        "meta[name='article:published_time']",
        "meta[name='parsely-pub-date']",
        "meta[name='pubdate']",
        "meta[itemprop='datePublished']",
        "time[datetime]",
    ]
    for sel in pub_selectors:
        n = soup.select_one(sel)
        if not n:
            continue
        if n.get("content"):
            published = str(n.get("content")).strip()
            break
        if n.get("datetime"):
            published = str(n.get("datetime")).strip()
            break
        txt = strip_html(n.get_text(" ", strip=True))
        if txt:
            published = txt
            break

    summary = ""
    md = soup.select_one("meta[name='description'], meta[property='og:description']")
    if md and md.get("content"):
        summary = strip_html(str(md.get("content")))

    body_text = soup.get_text(" ", strip=True)
    body_text = re.sub(r"\s+", " ", body_text).strip()
    if not published and body_text:
        published = _extract_search_datetime(body_text)
    if not published and body_text:
        m_date = re.search(
            r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},\s+\d{4}\b",
            body_text,
            flags=re.I,
        )
        if m_date:
            published = m_date.group(0)
    if not published:
        m_url_date = re.search(r"/(20\d{2})[/-](\d{1,2})[/-](\d{1,2})/", url or "")
        if m_url_date:
            published = f"{m_url_date.group(1)}-{int(m_url_date.group(2)):02d}-{int(m_url_date.group(3)):02d}"
    excerpt = body_text[:4000] if body_text else ""

    if not title and not summary and not excerpt:
        return None
    return {
        "title": title[:300],
        "published": published,
        "summary": summary[:500],
        "content_excerpt": excerpt[:4000],
    }


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
            page.wait_for_timeout(1200)
            html = page.content()
            context.close()
            browser.close()
            return html
    except Exception:
        return None

