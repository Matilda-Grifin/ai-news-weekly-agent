"""Intent keyword extraction and item ranking by intent."""

from __future__ import annotations

import re
from typing import Any

from ai_news_skill.pipeline.dedup import parse_published_dt_for_sort


def _clause_core_phrases(intent_text: str) -> list[str]:
    raw = (intent_text or "").strip()
    if not raw:
        return []
    if not re.search(r"[，,;；]", raw):
        return []
    out: list[str] = []
    for clause in re.split(r"[，,;；]+", raw):
        s = clause.strip()
        if not s:
            continue
        s = re.sub(
            r"^(我要查|我想查|查一下|还想看看|想看看|看看)+",
            "",
            s,
            flags=re.IGNORECASE,
        ).strip()
        s = re.sub(r"(的新闻|的进展|相关资讯|资讯|新闻)$", "", s).strip()
        s = re.sub(r"\s+", " ", s)
        if len(s) >= 2 and s.lower() not in {x.lower() for x in out}:
            out.append(s)
    return out[:8]


def extract_intent_keywords(intent_text: str) -> list[str]:
    text = (intent_text or "").strip().lower()
    if not text:
        return []
    clause_kw = _clause_core_phrases(intent_text)
    if clause_kw:
        return clause_kw
    raw_tokens = re.findall(r"[a-z0-9][a-z0-9\-\+\.]*|[\u4e00-\u9fff]{2,}", text)
    stopwords = {
        "新闻", "资讯", "相关", "看看", "查询", "我想", "一下", "最近",
        "关于", "生成", "ai", "我要查", "的新闻", "还想看看", "的进展",
        "还想", "我要", "一下的", "进展",
    }
    out: list[str] = []
    for tok in raw_tokens:
        if tok in stopwords or len(tok) < 2:
            continue
        if tok not in out:
            out.append(tok)
    return out[:12]


def rank_items_by_intent(items: list[dict], intent_text: str) -> list[dict]:
    keywords = extract_intent_keywords(intent_text)
    if not items or not keywords:
        return items

    def _score(it: dict) -> int:
        hay = " ".join(
            [
                str(it.get("title", "")).lower(),
                str(it.get("summary", "")).lower(),
                str(it.get("category", "")).lower(),
                str(it.get("source", "")).lower(),
            ]
        )
        score = 0
        for kw in keywords:
            if kw in hay:
                score += 3
        return score

    scored = [(it, _score(it)) for it in items]
    matched = [x for x in scored if x[1] > 0]
    if not matched:
        return items
    unmatched = [x for x in scored if x[1] == 0]
    matched.sort(
        key=lambda x: (x[1], parse_published_dt_for_sort(x[0].get("published", ""))),
        reverse=True,
    )
    unmatched.sort(key=lambda x: parse_published_dt_for_sort(x[0].get("published", "")), reverse=True)
    return [x[0] for x in matched] + [x[0] for x in unmatched]
