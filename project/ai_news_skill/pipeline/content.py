"""Article content extraction and excerpt fetching."""

from __future__ import annotations

import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from ai_news_skill.core.http_client import fetch_text
from ai_news_skill.pipeline.utils import strip_tags


def _trafilatura_extract(html: str, url: str) -> str:
    try:
        import trafilatura

        extracted = trafilatura.extract(
            html,
            url=url,
            include_comments=False,
            include_tables=False,
            favor_recall=True,
        )
        if extracted and len(extracted.strip()) >= 80:
            return re.sub(r"\s+", " ", extracted.strip())
    except Exception:
        pass
    return ""


def is_valid_article_excerpt(text: str) -> bool:
    t = (text or "").strip()
    if len(t) < 120:
        return False
    head = t[:900]
    if '{"error"' in head or '"error"' in head[:400] and '"message"' in head[:600]:
        return False
    if "暂时限制本次访问" in t or ("存在异常" in t and "知乎" in t):
        return False
    return True


def fetch_article_excerpt(url: str, allow_insecure_fallback: bool = False) -> str:
    if not url:
        return ""
    if url.lower().endswith(".pdf"):
        return ""
    try:
        page = fetch_text(url, timeout=22, allow_insecure_fallback=allow_insecure_fallback)
    except Exception:
        return ""
    tf = _trafilatura_extract(page, url)
    if len(tf) >= 120:
        return tf[:8000]
    page = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", page)
    page = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", page)
    main_match = re.search(r"(?is)<main[^>]*>(.*?)</main>", page)
    article_match = re.search(r"(?is)<article[^>]*>(.*?)</article>", page)
    best_block = (main_match.group(1) if main_match else "") or (article_match.group(1) if article_match else "")
    meta_desc = ""
    meta_match = re.search(
        r'(?is)<meta[^>]+(?:name|property)=["\'](?:description|og:description)["\'][^>]+content=["\'](.*?)["\']',
        page,
    )
    if meta_match:
        meta_desc = strip_tags(meta_match.group(1))
    text = strip_tags(best_block) if best_block else strip_tags(page)
    if len(text) < 120 and meta_desc:
        text = meta_desc
    if len(text) < 120:
        return ""
    return text[:8000]


def attach_content_excerpts_to_items(
    items: list[dict],
    allow_insecure_fallback: bool,
    *,
    max_parallel: int | None = None,
) -> None:
    to_fetch: list[dict] = []
    for it in items:
        ce = (it.get("content_excerpt") or "").strip()
        if len(ce) >= 120:
            continue
        link = (it.get("link") or "").strip()
        if not link:
            continue
        to_fetch.append(it)

    if not to_fetch:
        return

    try:
        env_w = int(os.environ.get("EXCERPT_FETCH_MAX_WORKERS", "8"))
    except ValueError:
        env_w = 8
    workers = max_parallel if max_parallel is not None else env_w
    workers = max(1, min(workers, len(to_fetch)))

    def _fetch_one(it: dict) -> tuple[dict, str]:
        link = (it.get("link") or "").strip()
        ex = fetch_article_excerpt(link, allow_insecure_fallback=allow_insecure_fallback)
        return it, ex

    if workers <= 1:
        for it in to_fetch:
            _, excerpt = _fetch_one(it)
            if excerpt:
                it["content_excerpt"] = excerpt
        return

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = [pool.submit(_fetch_one, it) for it in to_fetch]
        for fut in as_completed(futs):
            it, excerpt = fut.result()
            if excerpt:
                it["content_excerpt"] = excerpt
