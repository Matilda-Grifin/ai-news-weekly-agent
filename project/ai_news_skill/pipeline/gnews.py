"""GNews API integration."""

from __future__ import annotations

import datetime as dt
import json
import urllib.error
import urllib.parse
from datetime import timedelta
from typing import Any

from ai_news_skill.core.http_client import fetch_json_url
from ai_news_skill.pipeline.dedup import dedupe_items
from ai_news_skill.pipeline.intent import extract_intent_keywords
from ai_news_skill.pipeline.llm_client import infer_gnews_search_query_llm
from ai_news_skill.pipeline.utils import now_local

GNEWS_SEARCH_URL = "https://gnews.io/api/v4/search"


def build_gnews_query(
    intent_text: str, plan_keywords: list[str], override: str = ""
) -> str:
    if (override or "").strip():
        return (override or "").strip()[:500]
    q = (intent_text or "").strip()
    if q:
        return q[:500]
    clean_kw = [str(k).strip() for k in (plan_keywords or []) if str(k).strip()]
    if clean_kw:
        return " OR ".join(clean_kw[:8])[:500]
    return "artificial intelligence"


def infer_gnews_search_query(
    intent_text: str,
    *,
    api_key: str | None,
    model: str,
    base_url: str,
    allow_insecure_fallback: bool,
) -> str:
    text = (intent_text or "").strip()
    if not text:
        return "artificial intelligence"
    if (api_key or "").strip():
        q = infer_gnews_search_query_llm(
            text,
            api_key=api_key.strip(),  # type: ignore[union-attr]
            model=model,
            base_url=base_url,
            allow_insecure_fallback=allow_insecure_fallback,
        )
        if q.strip():
            return q.strip()
    kws = extract_intent_keywords(text)
    if kws:
        return build_gnews_query("", kws, "")
    return text[:500] if text else "artificial intelligence"


def fetch_gnews_articles(
    *,
    query: str,
    api_key: str,
    window_hours: int,
    max_articles: int,
    lang: str,
    category: str,
    allow_insecure_fallback: bool,
) -> tuple[list[dict], str | None]:
    if not query.strip() or not api_key.strip():
        return [], "GNews: empty query or api key"

    now_dt = now_local()
    cutoff = now_dt - timedelta(hours=max(1, window_hours))

    def _parse_iso_dt(raw: str) -> dt.datetime | None:
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

    params = {
        "q": query,
        "lang": lang,
        "max": str(min(max(1, max_articles), 100)),
        "apikey": api_key.strip(),
    }
    full_url = f"{GNEWS_SEARCH_URL}?{urllib.parse.urlencode(params)}"

    try:
        data = fetch_json_url(full_url, allow_insecure_fallback=allow_insecure_fallback)
    except urllib.error.HTTPError as ex:
        body = ex.read().decode("utf-8", errors="ignore")
        msg = body[:400]
        try:
            ej = json.loads(body)
            err = ej.get("errors", ej.get("message"))
            if isinstance(err, list):
                msg = "; ".join(str(x) for x in err)
            elif err:
                msg = str(err)
        except Exception:
            pass
        return [], f"GNews: HTTP {ex.code} {msg}"
    except Exception as ex:
        return [], f"GNews: {type(ex).__name__} {ex}"

    if not isinstance(data, dict):
        return [], "GNews: invalid JSON response"

    errs = data.get("errors")
    if errs:
        if isinstance(errs, list):
            return [], f"GNews: {'; '.join(str(x) for x in errs)}"
        return [], f"GNews: {errs}"

    articles = data.get("articles") or []
    out: list[dict] = []
    for art in articles:
        if not isinstance(art, dict):
            continue
        link = (art.get("url") or "").strip()
        title = (art.get("title") or "").strip()
        if not link or not title:
            continue
        src = art.get("source") if isinstance(art.get("source"), dict) else {}
        src_name = (src.get("name") if isinstance(src, dict) else "") or "GNews"
        summary = (art.get("description") or art.get("content") or "").strip()
        published = (art.get("publishedAt") or "").strip()
        pub_dt = _parse_iso_dt(published)
        if pub_dt is None or pub_dt < cutoff or pub_dt > now_dt + timedelta(minutes=10):
            continue
        out.append(
            {
                "source": f"{src_name} (GNews)",
                "category": category,
                "title": title,
                "link": link,
                "summary": summary,
                "published": published,
            }
        )
    return dedupe_items(out), None


def fetch_gnews_for_pipeline(
    *,
    intent_text: str,
    plan_keywords: list[str],
    api_key: str,
    gnews_query: str = "",
    window_hours: int,
    max_articles: int,
    lang: str,
    category: str,
    allow_insecure_fallback: bool,
) -> tuple[list[dict], str | None]:
    q = build_gnews_query(intent_text, plan_keywords, override=gnews_query)
    return fetch_gnews_articles(
        query=q,
        api_key=api_key,
        window_hours=window_hours,
        max_articles=max_articles,
        lang=lang,
        category=category,
        allow_insecure_fallback=allow_insecure_fallback,
    )
