"""Dedup, balance, cap and category ordering functions."""

from __future__ import annotations

import datetime as dt
from collections import Counter, defaultdict
from email.utils import parsedate_to_datetime
from typing import Any

from ai_news_skill.pipeline.utils import now_local


def dedupe_items(items: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for it in items:
        key = (it.get("link") or "").strip() or (it.get("title") or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


def parse_published_dt_for_sort(raw: str) -> dt.datetime:
    text = (raw or "").strip()
    if not text:
        return dt.datetime.fromtimestamp(0, tz=now_local().tzinfo)
    try:
        d = parsedate_to_datetime(text)
        if d is None:
            raise ValueError("empty datetime")
        if d.tzinfo is None:
            d = d.replace(tzinfo=now_local().tzinfo)
        return d.astimezone(now_local().tzinfo)
    except Exception:
        pass
    try:
        norm = text.replace("Z", "+00:00")
        d2 = dt.datetime.fromisoformat(norm)
        if d2.tzinfo is None:
            d2 = d2.replace(tzinfo=now_local().tzinfo)
        return d2.astimezone(now_local().tzinfo)
    except Exception:
        return dt.datetime.fromtimestamp(0, tz=now_local().tzinfo)


def category_order_key(name: str) -> tuple[int, str]:
    preferred = {
        "官方发布": 0,
        "国内厂商动态": 1,
        "开源与工具": 2,
        "行业资讯": 3,
        "垂直与趣味": 4,
        "网页检索": 5,
        "社区讨论": 6,
        "论文研究": 98,
        "其他": 99,
    }
    return (preferred.get(name, 90), name)


def balance_items(
    items: list[dict],
    max_paper_ratio: float,
    min_official_items: int,
) -> list[dict]:
    if not items:
        return items
    safe_ratio = min(1.0, max(0.0, max_paper_ratio))
    papers = [x for x in items if x.get("category") == "论文研究"]
    non_papers = [x for x in items if x.get("category") != "论文研究"]
    papers.sort(key=lambda x: parse_published_dt_for_sort(x.get("published", "")), reverse=True)
    non_papers.sort(key=lambda x: parse_published_dt_for_sort(x.get("published", "")), reverse=True)

    official = [x for x in non_papers if x.get("category") == "官方发布"]
    others = [x for x in non_papers if x.get("category") != "官方发布"]
    non_papers_sorted = official + others

    if not papers or safe_ratio >= 1.0:
        return non_papers_sorted + papers

    total_target = len(items)
    max_papers = max(0, int(total_target * safe_ratio))
    keep_papers = papers[:max_papers]
    kept = non_papers_sorted + keep_papers

    if min_official_items > 0:
        official_kept = [x for x in kept if x.get("category") == "官方发布"]
        if len(official_kept) < min_official_items and official:
            need = min_official_items - len(official_kept)
            add = official[:need]
            for x in add:
                if x not in kept:
                    kept.insert(0, x)

    return dedupe_items(kept)


def cap_papers_by_ratio(items: list[dict], max_paper_ratio: float) -> list[dict]:
    if not items:
        return items
    safe_ratio = min(1.0, max(0.0, max_paper_ratio))
    papers = [x for x in items if x.get("category") == "论文研究"]
    news = [x for x in items if x.get("category") != "论文研究"]
    if not papers:
        return items
    if not news and safe_ratio < 1.0:
        return papers[: max(1, min(5, len(papers)))]

    max_papers = int((safe_ratio / (1.0 - safe_ratio)) * len(news)) if safe_ratio < 1.0 else len(papers)
    max_papers = max(0, min(len(papers), max_papers))
    papers_sorted = sorted(
        papers,
        key=lambda x: parse_published_dt_for_sort(x.get("published", "")),
        reverse=True,
    )
    return dedupe_items(news + papers_sorted[:max_papers])


def cap_total_items(items: list[dict], max_total: int) -> list[dict]:
    if not items:
        return items
    try:
        cap = max(1, int(max_total))
    except (TypeError, ValueError):
        cap = 10
    return items[:cap]


def cap_items_per_category(items: list[dict], max_per: int = 5) -> list[dict]:
    if not items or max_per <= 0:
        return items
    grouped: dict[str, list[dict]] = defaultdict(list)
    for it in items:
        cat = str(it.get("category", "其他") or "其他")
        grouped[cat].append(it)
    out: list[dict] = []
    for cat in sorted(grouped.keys(), key=category_order_key):
        rows = grouped[cat]
        rows.sort(key=lambda x: parse_published_dt_for_sort(x.get("published", "")), reverse=True)
        out.extend(rows[:max_per])
    return dedupe_items(out)


def cap_items_per_source(items: list[dict], max_per_source: int = 1) -> list[dict]:
    if not items or max_per_source <= 0:
        return items
    seen: dict[str, int] = defaultdict(int)
    out: list[dict] = []
    for it in items:
        src = str(it.get("source", "未知信源") or "未知信源")
        if seen[src] >= max_per_source:
            continue
        seen[src] += 1
        out.append(it)
    return out
