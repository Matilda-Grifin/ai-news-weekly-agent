#!/usr/bin/env python3
"""
Structured public HTTP APIs as optional digest sources.

Taxonomy (maps onto sources.json-style categories):
- major_outlets  → category「行业资讯」: NY Times, The Guardian, FMP（财经）
- vertical_fun   → category「垂直与趣味」: Spaceflight News, NASA APOD
- programming    → category「开源与工具」: Hacker News, GitHub releases, Dev.to, Product Hunt
- papers_api     → category「论文研究」: arXiv Atom API（与 RSS 去重靠 link）

Enable with env PUBLIC_API_FEEDS=comma-separated ids, or config key public_api_feeds.
Ids: nyt, guardian, fmp, spaceflight, nasa, hackernews, github, arxiv, devto, producthunt

Keys (when required):
- NYTIMES_API_KEY or NYT_API_KEY
- GUARDIAN_API_KEY
- FMP_API_KEY
- NASA_API_KEY (optional; defaults to DEMO_KEY)
- GITHUB_TOKEN (optional; raises rate limit)
- GITHUB_RELEASE_REPOS=owner/repo,owner/repo
- PRODUCT_HUNT_TOKEN (Developer token; OAuth app)
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import timedelta
from typing import Any

from run_daily_digest import (
    USER_AGENT,
    dedupe_items,
    fetch_json_url,
    fetch_text,
    now_local,
    urlopen_with_outbound_proxy,
)

# --- taxonomy for docs / UI (not imported at runtime elsewhere) ---
FEED_GROUPS: dict[str, tuple[str, ...]] = {
    "major_outlets": ("nyt", "guardian", "fmp"),
    "vertical_fun": ("spaceflight", "nasa"),
    "programming": ("hackernews", "github", "devto", "producthunt"),
    "papers_api": ("arxiv",),
}

FEED_TO_CATEGORY: dict[str, str] = {
    "nyt": "行业资讯",
    "guardian": "行业资讯",
    "fmp": "行业资讯",
    "spaceflight": "垂直与趣味",
    "nasa": "垂直与趣味",
    "hackernews": "开源与工具",
    "github": "开源与工具",
    "devto": "开源与工具",
    "producthunt": "开源与工具",
    "arxiv": "论文研究",
}

_ALIASES: dict[str, str] = {
    "newyorktimes": "nyt",
    "theguardian": "guardian",
    "financialmodelingprep": "fmp",
    "snapi": "spaceflight",
    "hn": "hackernews",
    "gh": "github",
    "ph": "producthunt",
}


def _normalize_feed_id(raw: str) -> str | None:
    s = (raw or "").strip().lower()
    if not s:
        return None
    return _ALIASES.get(s, s)


def _enabled_set(config: dict[str, Any] | None) -> set[str]:
    raw = ""
    if config:
        raw = str(config.get("public_api_feeds", "")).strip()
    if not raw:
        raw = os.getenv("PUBLIC_API_FEEDS", "").strip()
    out: set[str] = set()
    for part in raw.split(","):
        nid = _normalize_feed_id(part)
        if nid and nid in FEED_TO_CATEGORY:
            out.add(nid)
    return out


def _parse_iso_window(
    raw: str, *, now_dt, cutoff
):
    import datetime as dt

    text = (raw or "").strip()
    if not text:
        return None
    try:
        norm = text.replace("Z", "+00:00")
        d2 = dt.datetime.fromisoformat(norm)
        if d2.tzinfo is None:
            d2 = d2.replace(tzinfo=dt.timezone.utc)
        return d2.astimezone(now_dt.tzinfo)
    except Exception:
        return None


def _in_window(pub_dt, now_dt, cutoff) -> bool:
    if pub_dt is None:
        return False
    return cutoff <= pub_dt <= now_dt + timedelta(minutes=10)


def fetch_json_any(url: str, *, timeout: int = 25, allow_insecure_fallback: bool) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    import ssl

    def _read(ctx: ssl.SSLContext) -> Any:
        with urlopen_with_outbound_proxy(
            req, timeout=timeout, context=ctx, proxy_scope="public_feeds"
        ) as resp:
            body = resp.read().decode("utf-8", errors="ignore")
        return json.loads(body)

    try:
        return _read(ssl.create_default_context())
    except ssl.SSLCertVerificationError:
        if not allow_insecure_fallback:
            raise
        return _read(ssl._create_unverified_context())
    except urllib.error.URLError as ex:
        if not allow_insecure_fallback:
            raise
        if isinstance(ex.reason, ssl.SSLCertVerificationError):
            return _read(ssl._create_unverified_context())
        raise


def _item(
    *,
    source: str,
    category: str,
    title: str,
    link: str,
    summary: str,
    published: str,
) -> dict[str, Any]:
    return {
        "source": source,
        "category": category,
        "title": (title or "").strip(),
        "link": (link or "").strip(),
        "summary": (summary or "").strip(),
        "published": (published or "").strip(),
    }


def _fetch_nyt(
    *, api_key: str, window_hours: int, max_per: int, allow_cb: bool
) -> tuple[list[dict], str | None]:
    now_dt = now_local()
    cutoff = now_dt - timedelta(hours=max(1, window_hours))
    end = now_dt.strftime("%Y%m%d")
    start = cutoff.strftime("%Y%m%d")
    q = (os.getenv("NYT_SEARCH_Q") or "artificial intelligence").strip()
    params = {
        "q": q,
        "api-key": api_key.strip(),
        "begin_date": start,
        "end_date": end,
        "sort": "newest",
    }
    url = f"https://api.nytimes.com/svc/search/v2/articlesearch.json?{urllib.parse.urlencode(params)}"
    try:
        data = fetch_json_url(
            url, timeout=25, allow_insecure_fallback=allow_cb, proxy_scope="public_feeds"
        )
    except Exception as ex:  # noqa: BLE001
        return [], f"nyt: {type(ex).__name__} {ex}"
    docs = (data.get("response") or {}).get("docs") if isinstance(data, dict) else None
    if not isinstance(docs, list):
        return [], "nyt: bad response"
    out: list[dict] = []
    cat = FEED_TO_CATEGORY["nyt"]
    for d in docs[: max_per]:
        if not isinstance(d, dict):
            continue
        url_u = (d.get("web_url") or "").strip()
        headline = ((d.get("headline") or {}).get("main") if isinstance(d.get("headline"), dict) else "") or ""
        headline = str(headline).strip()
        abstract = (d.get("abstract") or "").strip()
        pub = (d.get("pub_date") or "").strip()
        if not url_u or not headline:
            continue
        if pub and len(pub) == 8 and pub.isdigit():
            published = f"{pub[:4]}-{pub[4:6]}-{pub[6:8]}T12:00:00"
        else:
            published = pub
        pd = _parse_iso_window(published, now_dt=now_dt, cutoff=cutoff)
        if not _in_window(pd, now_dt, cutoff):
            continue
        out.append(
            _item(
                source="The New York Times (API)",
                category=cat,
                title=headline,
                link=url_u,
                summary=abstract,
                published=published,
            )
        )
    return out, None


def _fetch_guardian(
    *, api_key: str, window_hours: int, max_per: int, allow_cb: bool
) -> tuple[list[dict], str | None]:
    now_dt = now_local()
    cutoff = now_dt - timedelta(hours=max(1, window_hours))
    q = (os.getenv("GUARDIAN_SEARCH_Q") or "technology OR artificial intelligence").strip()
    params = {
        "q": q,
        "api-key": api_key.strip(),
        "from-date": cutoff.date().isoformat(),
        "to-date": now_dt.date().isoformat(),
        "page-size": str(min(50, max_per * 2)),
        "show-fields": "trailText,headline",
        "order-by": "newest",
    }
    url = f"https://content.guardianapis.com/search?{urllib.parse.urlencode(params)}"
    try:
        data = fetch_json_url(
            url, timeout=25, allow_insecure_fallback=allow_cb, proxy_scope="public_feeds"
        )
    except Exception as ex:  # noqa: BLE001
        return [], f"guardian: {type(ex).__name__} {ex}"
    resp = data.get("response") if isinstance(data, dict) else None
    results = (resp or {}).get("results") if isinstance(resp, dict) else None
    if not isinstance(results, list):
        return [], "guardian: bad response"
    cat = FEED_TO_CATEGORY["guardian"]
    out: list[dict] = []
    for r in results[: max_per * 2]:
        if not isinstance(r, dict):
            continue
        link = (r.get("webUrl") or "").strip()
        title = (r.get("webTitle") or "").strip()
        fields = r.get("fields") if isinstance(r.get("fields"), dict) else {}
        summary = (fields.get("trailText") or "").strip()
        pub = (r.get("webPublicationDate") or "").strip()
        if not link or not title:
            continue
        pd = _parse_iso_window(pub, now_dt=now_dt, cutoff=cutoff)
        if not _in_window(pd, now_dt, cutoff):
            continue
        out.append(
            _item(
                source="The Guardian (API)",
                category=cat,
                title=title,
                link=link,
                summary=summary,
                published=pub,
            )
        )
        if len(out) >= max_per:
            break
    return out, None


def _fetch_fmp(
    *, api_key: str, window_hours: int, max_per: int, allow_cb: bool
) -> tuple[list[dict], str | None]:
    now_dt = now_local()
    cutoff = now_dt - timedelta(hours=max(1, window_hours))
    url = f"https://financialmodelingprep.com/api/v3/stock_news?limit={min(100, max_per * 4)}&apikey={urllib.parse.quote(api_key.strip())}"
    try:
        data = fetch_json_any(url, allow_insecure_fallback=allow_cb)
    except Exception as ex:  # noqa: BLE001
        return [], f"fmp: {type(ex).__name__} {ex}"
    if not isinstance(data, list):
        return [], "fmp: expected list"
    cat = FEED_TO_CATEGORY["fmp"]
    out: list[dict] = []
    for row in data:
        if not isinstance(row, dict):
            continue
        title = (row.get("title") or "").strip()
        link = (row.get("url") or "").strip() or (row.get("link") or "").strip()
        text = (row.get("text") or "").strip()
        pub = (row.get("publishedDate") or row.get("date") or "").strip()
        if not title:
            continue
        pd = _parse_iso_window(pub, now_dt=now_dt, cutoff=cutoff)
        if pd is None or not _in_window(pd, now_dt, cutoff):
            continue
        if not link:
            sym = (row.get("symbol") or "").strip()
            link = f"https://financialmodelingprep.com/financial-summary/{sym}" if sym else ""
        out.append(
            _item(
                source="FMP Stock News (API)",
                category=cat,
                title=title,
                link=link or "https://financialmodelingprep.com/",
                summary=text[:500],
                published=pub,
            )
        )
        if len(out) >= max_per:
            break
    return out, None


def _fetch_spaceflight(
    *, window_hours: int, max_per: int, allow_cb: bool
) -> tuple[list[dict], str | None]:
    now_dt = now_local()
    cutoff = now_dt - timedelta(hours=max(1, window_hours))
    params = {"limit": min(50, max_per * 3)}
    url = f"https://api.spaceflightnewsapi.net/v4/articles/?{urllib.parse.urlencode(params)}"
    try:
        data = fetch_json_url(
            url, timeout=25, allow_insecure_fallback=allow_cb, proxy_scope="public_feeds"
        )
    except Exception as ex:  # noqa: BLE001
        return [], f"spaceflight: {type(ex).__name__} {ex}"
    results = data.get("results") if isinstance(data, dict) else None
    if not isinstance(results, list):
        return [], "spaceflight: bad response"
    cat = FEED_TO_CATEGORY["spaceflight"]
    out: list[dict] = []
    for r in results:
        if not isinstance(r, dict):
            continue
        title = (r.get("title") or "").strip()
        link = (r.get("url") or "").strip()
        summary = (r.get("summary") or "").strip()
        pub = (r.get("published_at") or "").strip()
        if not title or not link:
            continue
        pd = _parse_iso_window(pub, now_dt=now_dt, cutoff=cutoff)
        if not _in_window(pd, now_dt, cutoff):
            continue
        out.append(
            _item(
                source="Spaceflight News (API)",
                category=cat,
                title=title,
                link=link,
                summary=summary,
                published=pub,
            )
        )
        if len(out) >= max_per:
            break
    return out, None


def _fetch_nasa_apod(
    *, api_key: str, window_hours: int, allow_cb: bool
) -> tuple[list[dict], str | None]:
    now_dt = now_local()
    cutoff = now_dt - timedelta(hours=max(1, window_hours))
    key = (api_key or "DEMO_KEY").strip()
    url = f"https://api.nasa.gov/planetary/apod?api_key={urllib.parse.quote(key)}"
    try:
        data = fetch_json_url(
            url, timeout=25, allow_insecure_fallback=allow_cb, proxy_scope="public_feeds"
        )
    except Exception as ex:  # noqa: BLE001
        return [], f"nasa: {type(ex).__name__} {ex}"
    if not isinstance(data, dict):
        return [], "nasa: bad response"
    title = (data.get("title") or "").strip()
    explanation = (data.get("explanation") or "").strip()
    hd = (data.get("hdurl") or data.get("url") or "").strip()
    pub = (data.get("date") or "").strip()
    if not title:
        return [], None
    published = f"{pub}T12:00:00" if pub else ""
    pd = _parse_iso_window(published, now_dt=now_dt, cutoff=cutoff)
    if pd is None or not _in_window(pd, now_dt, cutoff):
        return [], None
    return (
        [
            _item(
                source="NASA APOD (API)",
                category=FEED_TO_CATEGORY["nasa"],
                title=title,
                link=hd or "https://apod.nasa.gov/apod/",
                summary=explanation[:800],
                published=published,
            )
        ],
        None,
    )


def _fetch_hackernews(
    *, window_hours: int, max_per: int, allow_cb: bool
) -> tuple[list[dict], str | None]:
    import datetime as dt

    now_dt = now_local()
    cutoff = now_dt - timedelta(hours=max(1, window_hours))
    base = "https://hacker-news.firebaseio.com/v0"
    try:
        ids = fetch_json_any(f"{base}/topstories.json", allow_insecure_fallback=allow_cb)
    except Exception as ex:  # noqa: BLE001
        return [], f"hackernews: {type(ex).__name__} {ex}"
    if not isinstance(ids, list):
        return [], "hackernews: bad id list"
    cat = FEED_TO_CATEGORY["hackernews"]
    out: list[dict] = []
    for iid in ids[: min(80, max_per * 8)]:
        try:
            it = fetch_json_any(f"{base}/item/{int(iid)}.json", allow_insecure_fallback=allow_cb)
        except Exception:
            continue
        if not isinstance(it, dict) or it.get("type") != "story":
            continue
        title = (it.get("title") or "").strip()
        t_unix = int(it.get("time") or 0)
        if not title or not t_unix:
            continue
        pub_dt = dt.datetime.fromtimestamp(t_unix, tz=dt.timezone.utc).astimezone(now_dt.tzinfo)
        if not _in_window(pub_dt, now_dt, cutoff):
            continue
        link = (it.get("url") or "").strip()
        if not link:
            link = f"https://news.ycombinator.com/item?id={iid}"
        published = pub_dt.isoformat()
        out.append(
            _item(
                source="Hacker News",
                category=cat,
                title=title,
                link=link,
                summary="",
                published=published,
            )
        )
        if len(out) >= max_per:
            break
    return out, None


def _fetch_github_releases(
    *, token: str, window_hours: int, max_per: int, allow_cb: bool
) -> tuple[list[dict], str | None]:
    now_dt = now_local()
    cutoff = now_dt - timedelta(hours=max(1, window_hours))
    raw = (os.getenv("GITHUB_RELEASE_REPOS") or "").strip()
    repos = [x.strip() for x in raw.split(",") if x.strip()]
    if not repos:
        return [], None
    headers = {"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"}
    if token.strip():
        headers["Authorization"] = f"Bearer {token.strip()}"
    cat = FEED_TO_CATEGORY["github"]
    out: list[dict] = []
    import ssl

    def get_releases(owner_repo: str) -> list[dict]:
        o, _, r = owner_repo.partition("/")
        if not o or not r:
            return []
        u = f"https://api.github.com/repos/{o}/{r}/releases?per_page=5"
        req = urllib.request.Request(u, headers=headers)
        try:

            def _read(ctx: ssl.SSLContext) -> str:
                with urlopen_with_outbound_proxy(
                    req, timeout=25, context=ctx, proxy_scope="public_feeds"
                ) as resp:
                    return resp.read().decode("utf-8", errors="ignore")

            try:
                body = _read(ssl.create_default_context())
            except ssl.SSLCertVerificationError:
                if not allow_cb:
                    raise
                body = _read(ssl._create_unverified_context())
            except urllib.error.URLError as ex:
                if not allow_cb:
                    raise
                if isinstance(ex.reason, ssl.SSLCertVerificationError):
                    body = _read(ssl._create_unverified_context())
                else:
                    raise
        except Exception:
            return []
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            return []
        return data if isinstance(data, list) else []

    for repo in repos:
        for rel in get_releases(repo):
            if not isinstance(rel, dict):
                continue
            name = (rel.get("name") or rel.get("tag_name") or "").strip()
            html_url = (rel.get("html_url") or "").strip()
            body = (rel.get("body") or "").strip()
            pub = (rel.get("published_at") or "").strip()
            if not name or not html_url:
                continue
            pd = _parse_iso_window(pub, now_dt=now_dt, cutoff=cutoff)
            if not _in_window(pd, now_dt, cutoff):
                continue
            summary = re.sub(r"\s+", " ", body)[:500]
            out.append(
                _item(
                    source=f"GitHub Releases ({repo})",
                    category=cat,
                    title=name,
                    link=html_url,
                    summary=summary,
                    published=pub,
                )
            )
            if len(out) >= max_per:
                return out, None
    return out, None


def _fetch_devto(
    *, window_hours: int, max_per: int, allow_cb: bool
) -> tuple[list[dict], str | None]:
    now_dt = now_local()
    cutoff = now_dt - timedelta(hours=max(1, window_hours))
    tag = (os.getenv("DEVTO_TAG") or "ai").strip()
    url = f"https://dev.to/api/articles?per_page={min(30, max_per * 2)}&tag={urllib.parse.quote(tag)}"
    try:
        data = fetch_json_any(url, allow_insecure_fallback=allow_cb)
    except Exception as ex:  # noqa: BLE001
        return [], f"devto: {type(ex).__name__} {ex}"
    if not isinstance(data, list):
        return [], "devto: bad list"
    cat = FEED_TO_CATEGORY["devto"]
    out: list[dict] = []
    for a in data:
        if not isinstance(a, dict):
            continue
        title = (a.get("title") or "").strip()
        link = (a.get("url") or "").strip()
        desc = (a.get("description") or "").strip()
        pub = (a.get("published_at") or "").strip()
        if not title or not link:
            continue
        pd = _parse_iso_window(pub, now_dt=now_dt, cutoff=cutoff)
        if not _in_window(pd, now_dt, cutoff):
            continue
        out.append(
            _item(
                source="DEV.to (API)",
                category=cat,
                title=title,
                link=link,
                summary=desc,
                published=pub,
            )
        )
        if len(out) >= max_per:
            break
    return out, None


def _fetch_producthunt(
    *, token: str, window_hours: int, max_per: int, allow_cb: bool
) -> tuple[list[dict], str | None]:
    now_dt = now_local()
    cutoff = now_dt - timedelta(hours=max(1, window_hours))
    query = """
    query RecentPosts($n: Int!) {
      posts(first: $n) {
        edges {
          node {
            name
            tagline
            slug
            createdAt
            url
          }
        }
      }
    }
    """
    payload = {
        "query": query,
        "variables": {"n": min(20, max_per * 2)},
    }
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        "https://api.producthunt.com/v2/api/graphql",
        data=body,
        method="POST",
        headers={
            "User-Agent": USER_AGENT,
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token.strip()}",
        },
    )
    import ssl

    try:

        def _read(ctx: ssl.SSLContext) -> dict:
            with urlopen_with_outbound_proxy(
                req, timeout=30, context=ctx, proxy_scope="public_feeds"
            ) as resp:
                raw_b = resp.read().decode("utf-8", errors="ignore")
            return json.loads(raw_b)

        try:
            data = _read(ssl.create_default_context())
        except ssl.SSLCertVerificationError:
            if not allow_cb:
                raise
            data = _read(ssl._create_unverified_context())
        except urllib.error.URLError as ex:
            if not allow_cb:
                raise
            if isinstance(ex.reason, ssl.SSLCertVerificationError):
                data = _read(ssl._create_unverified_context())
            else:
                raise
    except Exception as ex:  # noqa: BLE001
        return [], f"producthunt: {type(ex).__name__} {ex}"
    posts = (((data.get("data") or {}).get("posts") or {}).get("edges")) if isinstance(data, dict) else None
    if not isinstance(posts, list):
        err = data.get("errors") if isinstance(data, dict) else None
        return [], f"producthunt: {err or 'bad response'}"
    cat = FEED_TO_CATEGORY["producthunt"]
    out: list[dict] = []
    for edge in posts:
        if not isinstance(edge, dict):
            continue
        node = edge.get("node")
        if not isinstance(node, dict):
            continue
        name = (node.get("name") or "").strip()
        slug = (node.get("slug") or "").strip()
        tagline = (node.get("tagline") or "").strip()
        pub = (node.get("createdAt") or "").strip()
        url_u = (node.get("url") or "").strip()
        if not name:
            continue
        if not url_u and slug:
            url_u = f"https://www.producthunt.com/posts/{slug}"
        pd = _parse_iso_window(pub, now_dt=now_dt, cutoff=cutoff)
        if not _in_window(pd, now_dt, cutoff):
            continue
        out.append(
            _item(
                source="Product Hunt (API)",
                category=cat,
                title=name,
                link=url_u,
                summary=tagline,
                published=pub,
            )
        )
        if len(out) >= max_per:
            break
    return out, None


def _fetch_arxiv(
    *, window_hours: int, max_per: int, allow_cb: bool
) -> tuple[list[dict], str | None]:
    now_dt = now_local()
    cutoff = now_dt - timedelta(hours=max(1, window_hours))
    q = (os.getenv("ARXIV_API_QUERY") or "cat:cs.AI").strip()
    params = {
        "search_query": q,
        "start": "0",
        "max_results": str(min(30, max_per * 2)),
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }
    url = f"http://export.arxiv.org/api/query?{urllib.parse.urlencode(params)}"
    try:
        xml_text = fetch_text(
            url, timeout=30, allow_insecure_fallback=allow_cb, proxy_scope="public_feeds"
        )
    except Exception as ex:  # noqa: BLE001
        return [], f"arxiv: {type(ex).__name__} {ex}"
    ns = {"a": "http://www.w3.org/2005/Atom"}
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as ex:
        return [], f"arxiv: {ex}"
    cat = FEED_TO_CATEGORY["arxiv"]
    out: list[dict] = []
    for entry in root.findall("a:entry", ns):
        title_el = entry.find("a:title", ns)
        link_el = entry.find("a:id", ns)
        summ_el = entry.find("a:summary", ns)
        upd_el = entry.find("a:updated", ns)
        title = (title_el.text or "").strip() if title_el is not None and title_el.text else ""
        link = (link_el.text or "").strip() if link_el is not None and link_el.text else ""
        summary = (summ_el.text or "").strip() if summ_el is not None and summ_el.text else ""
        pub = (upd_el.text or "").strip() if upd_el is not None and upd_el.text else ""
        if not title or not link:
            continue
        pd = _parse_iso_window(pub, now_dt=now_dt, cutoff=cutoff)
        if not _in_window(pd, now_dt, cutoff):
            continue
        out.append(
            _item(
                source="arXiv (API)",
                category=cat,
                title=re.sub(r"\s+", " ", title),
                link=link,
                summary=re.sub(r"\s+", " ", summary)[:500],
                published=pub,
            )
        )
        if len(out) >= max_per:
            break
    return out, None


def _run_one(
    fid: str,
    *,
    window_hours: int,
    max_per: int,
    allow_cb: bool,
) -> tuple[list[dict], str | None]:
    if fid == "nyt":
        key = (os.getenv("NYTIMES_API_KEY") or os.getenv("NYT_API_KEY") or "").strip()
        if not key:
            return [], "nyt: missing NYTIMES_API_KEY or NYT_API_KEY"
        return _fetch_nyt(api_key=key, window_hours=window_hours, max_per=max_per, allow_cb=allow_cb)
    if fid == "guardian":
        key = (os.getenv("GUARDIAN_API_KEY") or "").strip()
        if not key:
            return [], "guardian: missing GUARDIAN_API_KEY"
        return _fetch_guardian(api_key=key, window_hours=window_hours, max_per=max_per, allow_cb=allow_cb)
    if fid == "fmp":
        key = (os.getenv("FMP_API_KEY") or "").strip()
        if not key:
            return [], "fmp: missing FMP_API_KEY"
        return _fetch_fmp(api_key=key, window_hours=window_hours, max_per=max_per, allow_cb=allow_cb)
    if fid == "spaceflight":
        return _fetch_spaceflight(window_hours=window_hours, max_per=max_per, allow_cb=allow_cb)
    if fid == "nasa":
        key = (os.getenv("NASA_API_KEY") or "DEMO_KEY").strip()
        return _fetch_nasa_apod(api_key=key, window_hours=window_hours, allow_cb=allow_cb)
    if fid == "hackernews":
        return _fetch_hackernews(window_hours=window_hours, max_per=max_per, allow_cb=allow_cb)
    if fid == "github":
        tok = (os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN") or "").strip()
        return _fetch_github_releases(
            token=tok, window_hours=window_hours, max_per=max_per, allow_cb=allow_cb
        )
    if fid == "devto":
        return _fetch_devto(window_hours=window_hours, max_per=max_per, allow_cb=allow_cb)
    if fid == "producthunt":
        tok = (os.getenv("PRODUCT_HUNT_TOKEN") or "").strip()
        if not tok:
            return [], "producthunt: missing PRODUCT_HUNT_TOKEN"
        return _fetch_producthunt(
            token=tok, window_hours=window_hours, max_per=max_per, allow_cb=allow_cb
        )
    if fid == "arxiv":
        return _fetch_arxiv(window_hours=window_hours, max_per=max_per, allow_cb=allow_cb)
    return [], None


def collect_public_api_feed_items(
    *,
    window_hours: int,
    allow_insecure_fallback: bool,
    config: dict[str, Any] | None = None,
    max_per_feed: int | None = None,
) -> tuple[list[dict], list[str]]:
    """
    Fetch enabled third-party API feeds in parallel.
    Returns (items, error_strings).
    """
    enabled = _enabled_set(config)
    if not enabled:
        return [], []

    try:
        mp = int(max_per_feed if max_per_feed is not None else os.getenv("PUBLIC_API_FEED_MAX", "8"))
    except ValueError:
        mp = 8
    mp = max(1, min(30, mp))

    allow_cb = bool(allow_insecure_fallback)
    items: list[dict] = []
    errors: list[str] = []

    workers = min(8, max(1, len(enabled)))
    if workers <= 1 or len(enabled) == 1:
        for fid in sorted(enabled):
            chunk, err = _run_one(fid, window_hours=window_hours, max_per=mp, allow_cb=allow_cb)
            items.extend(chunk)
            if err:
                errors.append(err)
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futs = {
                pool.submit(_run_one, fid, window_hours=window_hours, max_per=mp, allow_cb=allow_cb): fid
                for fid in sorted(enabled)
            }
            for fut in as_completed(futs):
                chunk, err = fut.result()
                items.extend(chunk)
                if err:
                    errors.append(err)

    return dedupe_items(items), errors
