"""RSS parsing and news collection."""

from __future__ import annotations

import datetime as dt
import os
import time
import urllib.error
import xml.etree.ElementTree as ET
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import timedelta
from email.utils import parsedate_to_datetime
from typing import Any

from ai_news_skill.core.http_client import fetch_text
from ai_news_skill.pipeline.dedup import dedupe_items
from ai_news_skill.pipeline.utils import now_local, strip_html


def text_of(elem: ET.Element, tag: str) -> str:
    child = elem.find(tag)
    return child.text.strip() if child is not None and child.text else ""


def parse_rss(xml_text: str) -> list[dict]:
    items: list[dict] = []
    root = ET.fromstring(xml_text)

    channel = root.find("channel")
    if channel is not None:
        for node in channel.findall("item"):
            title = strip_html(text_of(node, "title"))
            link = text_of(node, "link")
            desc = strip_html(text_of(node, "description"))
            pub_date = text_of(node, "pubDate")
            if title and link:
                items.append(
                    {"title": title, "link": link, "summary": desc, "published": pub_date}
                )

    if items:
        return items

    ns = {"atom": "http://www.w3.org/2005/Atom"}
    for node in root.findall("atom:entry", ns):
        title = strip_html(text_of(node, "{http://www.w3.org/2005/Atom}title"))
        link = ""
        link_elem = node.find("{http://www.w3.org/2005/Atom}link")
        if link_elem is not None:
            link = link_elem.attrib.get("href", "")
        summary = text_of(node, "{http://www.w3.org/2005/Atom}summary")
        if not summary:
            summary = text_of(node, "{http://www.w3.org/2005/Atom}content")
        published = text_of(node, "{http://www.w3.org/2005/Atom}updated")
        if title and link:
            items.append(
                {
                    "title": strip_html(title),
                    "link": link.strip(),
                    "summary": strip_html(summary),
                    "published": published,
                }
            )
    return items


def _parse_published_dt_collect(raw: str, now_dt: dt.datetime) -> dt.datetime | None:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        d = parsedate_to_datetime(text)
        if d is None:
            return None
        if d.tzinfo is None:
            d = d.replace(tzinfo=now_dt.tzinfo)
        return d.astimezone(now_dt.tzinfo)
    except Exception:
        pass
    try:
        norm = text.replace("Z", "+00:00")
        d2 = dt.datetime.fromisoformat(norm)
        if d2.tzinfo is None:
            d2 = d2.replace(tzinfo=now_dt.tzinfo)
        return d2.astimezone(now_dt.tzinfo)
    except Exception:
        return None


def _collect_one_rss_source(
    src: dict,
    *,
    per_source: int,
    allow_insecure_fallback: bool,
    now_dt: dt.datetime,
    cutoff: dt.datetime,
) -> tuple[list[dict], list[str]]:
    out: list[dict] = []
    errs: list[str] = []
    name = src.get("name", "unknown")
    rss = src.get("rss_url", "").strip()
    category = src.get("category", "其他")
    if not rss:
        return out, [f"{name}: rss_url empty"]
    try:
        try:
            rss_to = int(os.environ.get("RSS_HTTP_TIMEOUT", "15"))
        except ValueError:
            rss_to = 15
        rss_to = max(5, min(90, rss_to))
        xml_text = fetch_text(
            rss,
            timeout=rss_to,
            allow_insecure_fallback=allow_insecure_fallback,
        )
        entries = parse_rss(xml_text)[:per_source]
        for e in entries:
            pub_dt = _parse_published_dt_collect(e.get("published", ""), now_dt)
            if pub_dt is None or pub_dt < cutoff or pub_dt > now_dt + timedelta(minutes=10):
                continue
            out.append(
                {
                    "source": name,
                    "category": category,
                    "title": e["title"],
                    "link": e["link"],
                    "summary": e.get("summary", ""),
                    "published": e.get("published", ""),
                }
            )
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ET.ParseError) as ex:
        errs.append(f"{name}: {type(ex).__name__} {ex}")
    except Exception as ex:
        errs.append(f"{name}: {type(ex).__name__} {ex}")
    return out, errs


def collect_news(
    sources: list[dict],
    per_source: int,
    allow_insecure_fallback: bool,
    window_hours: int,
    *,
    max_parallel: int | None = None,
    rss_source_hook: Callable[[str, float, int, list[str]], None] | None = None,
) -> tuple[list[dict], list[str]]:
    all_items: list[dict] = []
    errors: list[str] = []
    now_dt = now_local()
    cutoff = now_dt - timedelta(hours=max(1, window_hours))
    if not sources:
        return dedupe_items(all_items), errors

    try:
        env_w = int(os.environ.get("COLLECT_NEWS_MAX_WORKERS", "8"))
    except ValueError:
        env_w = 8
    workers = max_parallel if max_parallel is not None else env_w
    workers = max(1, min(workers, len(sources)))

    def _one(src: dict) -> tuple[list[dict], list[str]]:
        t0 = time.perf_counter()
        chunk, errs = _collect_one_rss_source(
            src,
            per_source=per_source,
            allow_insecure_fallback=allow_insecure_fallback,
            now_dt=now_dt,
            cutoff=cutoff,
        )
        if rss_source_hook is not None:
            elapsed = time.perf_counter() - t0
            rss_source_hook(str(src.get("name", "")), elapsed, len(chunk), errs)
        return chunk, errs

    if workers <= 1:
        for src in sources:
            chunk, errs = _one(src)
            all_items.extend(chunk)
            errors.extend(errs)
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futs = [pool.submit(_one, src) for src in sources]
            for fut in as_completed(futs):
                chunk, errs = fut.result()
                all_items.extend(chunk)
                errors.extend(errs)
    return dedupe_items(all_items), errors
