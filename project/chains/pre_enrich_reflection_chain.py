"""Collect 之后、Enrich 之前：对原始条目做结构化评估（不写周报、仅供日志/trace）。"""

from __future__ import annotations

import json
import os
import re
from collections import defaultdict
from typing import Any

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from model.llm_factory import build_chat_openai

PRE_REFLECT_SYSTEM_PROMPT = """你是资讯采集质检助手。输入为待写入周报的**原始条目**（尚无模型生成解读）。
请对每条从「来源与分类、发布时间、摘要/摘录是否够用、标题与摘要是否清晰」做粗评，不要求联网核验正文。

评分维度（每项 1–5 分，整数）：
1) 来源可靠性：结合 category 与 source 名称判断信息源层级（官方/主流媒体/论文/社区等）。
2) 时效性：published 是否可读、是否明显过旧或与 digest_date 语境不符。
3) 摘录充分性：summary 与 excerpt 是否足以支撑后续摘要（过短、乱码、反爬提示则低分）。
4) 标题与摘要可读性：标题/摘要是否清楚传达主题（语言混杂仅作可读性扣分，不要求译成中文）。

overall_risk：综合本轮条目给出 low / medium / high（大量低分或摘录不可用可标 high）。

只输出评估，不重写条目正文。必须输出严格 JSON，与 schema 一致。"""


class PreReflectPerItem(BaseModel):
    idx: int
    source_reliability: int = Field(ge=1, le=5)
    timeliness: int = Field(ge=1, le=5)
    excerpt_sufficiency: int = Field(ge=1, le=5)
    title_summary_clarity: int = Field(ge=1, le=5)
    issues: list[str] = Field(default_factory=list)


class PreEnrichReflectionOutput(BaseModel):
    batch_note: str = Field(default="", description="对本轮采集的一句话备注")
    overall_risk: str = Field(default="low", description="low | medium | high")
    items: list[PreReflectPerItem] = Field(default_factory=list)


def _strip_json_fence(text: str) -> str:
    s = (text or "").strip()
    m = re.match(r"^```(?:json)?\s*([\s\S]*?)\s*```$", s)
    if m:
        return m.group(1).strip()
    return s


def build_pre_reflect_payload(
    items: list[dict[str, Any]],
    *,
    digest_date: str,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for idx, it in enumerate(items, 1):
        excerpt = (it.get("content_excerpt") or it.get("summary") or "")[:2200]
        rows.append(
            {
                "idx": idx,
                "category": it.get("category", ""),
                "source": it.get("source", ""),
                "title": (it.get("title") or "")[:500],
                "published": it.get("published", ""),
                "link": (it.get("link") or "")[:500],
                "summary": (it.get("summary") or "")[:800],
                "excerpt": excerpt,
            }
        )
    return {"digest_date": digest_date, "items": rows}


def invoke_pre_enrich_reflection_chain(
    *,
    payload: dict[str, Any],
    api_key: str,
    model: str,
    base_url: str,
    allow_insecure_fallback: bool,
    item_count: int,
) -> PreEnrichReflectionOutput | None:
    llm = build_chat_openai(
        api_key=api_key,
        model=model,
        chat_completions_url=base_url,
        allow_insecure_fallback=allow_insecure_fallback,
    )
    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", "{system_prompt}"),
            (
                "human",
                "以下为 payload JSON。请评估并输出 JSON 对象。\n{user_json}",
            ),
        ]
    )
    user_json = json.dumps(payload, ensure_ascii=False)
    chain_input = {"system_prompt": PRE_REFLECT_SYSTEM_PROMPT, "user_json": user_json}
    try:
        structured = llm.with_structured_output(PreEnrichReflectionOutput)
        runnable = prompt | structured
        raw_out = runnable.invoke(chain_input)
        parsed = PreEnrichReflectionOutput.model_validate(raw_out)
        return _normalize_pre(parsed, item_count)
    except Exception:
        try:
            base = prompt | llm
            msg = base.invoke(chain_input)
            text = getattr(msg, "content", str(msg))
            raw = _strip_json_fence(text)
            data = json.loads(raw)
            parsed = PreEnrichReflectionOutput.model_validate(data)
            return _normalize_pre(parsed, item_count)
        except Exception:
            return None


def _normalize_pre(parsed: PreEnrichReflectionOutput, item_count: int) -> PreEnrichReflectionOutput:
    by_idx = {r.idx: r for r in parsed.items if 1 <= r.idx <= item_count}
    out_items: list[PreReflectPerItem] = []
    for i in range(1, item_count + 1):
        if i in by_idx:
            r = by_idx[i]
            out_items.append(
                PreReflectPerItem(
                    idx=i,
                    source_reliability=max(1, min(5, int(r.source_reliability))),
                    timeliness=max(1, min(5, int(r.timeliness))),
                    excerpt_sufficiency=max(1, min(5, int(r.excerpt_sufficiency))),
                    title_summary_clarity=max(1, min(5, int(r.title_summary_clarity))),
                    issues=list(r.issues)[:6],
                )
            )
        else:
            out_items.append(
                PreReflectPerItem(
                    idx=i,
                    source_reliability=3,
                    timeliness=3,
                    excerpt_sufficiency=3,
                    title_summary_clarity=3,
                    issues=["（审稿未返回该条，已占位）"],
                )
            )
    risk = (parsed.overall_risk or "low").strip().lower()
    if risk not in ("low", "medium", "high"):
        risk = "low"
    return PreEnrichReflectionOutput(
        batch_note=(parsed.batch_note or "").strip(),
        overall_risk=risk,
        items=out_items,
    )


def pre_reflect_report_dict(obj: PreEnrichReflectionOutput) -> dict[str, Any]:
    return {
        "batch_note": obj.batch_note,
        "overall_risk": obj.overall_risk,
        "items": [i.model_dump() for i in obj.items],
    }


def _row_min_score(row: dict[str, Any]) -> float:
    return min(
        float(row.get("source_reliability", 3)),
        float(row.get("timeliness", 3)),
        float(row.get("excerpt_sufficiency", 3)),
        float(row.get("title_summary_clarity", 3)),
    )


def reflection_item_min_score(row: dict[str, Any]) -> float:
    """单条四个维度中的最低分，供剔除低分条目使用。"""
    return _row_min_score(row)


def aggregate_pre_reflect_by_source(
    items: list[dict[str, Any]],
    report_items: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """按信源聚合：条数、均分、最差条目的最低维分。"""
    by_idx: dict[int, dict[str, Any]] = {}
    for r in report_items:
        try:
            ix = int(r.get("idx") or 0)
        except (TypeError, ValueError):
            continue
        if ix >= 1:
            by_idx[ix] = r
    per_source: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for i, it in enumerate(items, 1):
        row = by_idx.get(i)
        if not row:
            continue
        dims = [
            float(row.get("source_reliability", 3)),
            float(row.get("timeliness", 3)),
            float(row.get("excerpt_sufficiency", 3)),
            float(row.get("title_summary_clarity", 3)),
        ]
        item_avg = sum(dims) / 4.0
        item_min = min(dims)
        src = str(it.get("source") or "未知信源")
        per_source[src].append((item_avg, item_min))
    out: dict[str, dict[str, Any]] = {}
    for src, rows in per_source.items():
        avgs = [x[0] for x in rows]
        mins = [x[1] for x in rows]
        out[src] = {
            "n": len(rows),
            "source_avg": sum(avgs) / len(avgs),
            "worst_item_min": min(mins),
            "best_item_min": max(mins),
        }
    return out


def select_sources_for_partial_rss(
    rollup: dict[str, dict[str, Any]],
    sources_with_no_items_left: set[str],
    config: dict[str, Any],
) -> list[str]:
    """
    仅对部分信源做 RSS 补拉（加宽窗口在 runtime 里做），避免整轮 collect 重跑仍得到同一批。
    优先级：① 本条全被删光的信源；② 均分低或 worst_item_min 处于灰区的信源。
    """
    raw_max = config.get("pre_reflect_partial_max_sources")
    if raw_max is not None:
        max_n = max(0, int(raw_max))
    else:
        try:
            max_n = max(0, int(os.getenv("PRE_REFLECT_PARTIAL_MAX_SOURCES", "5")))
        except ValueError:
            max_n = 5
    if max_n <= 0:
        return []

    def _f(cfg_key: str, env_key: str, default: str) -> float:
        v = config.get(cfg_key)
        if v is not None:
            return float(v)
        try:
            return float(os.getenv(env_key, default))
        except ValueError:
            return float(default)

    avg_lt = _f("pre_reflect_partial_source_avg_lt", "PRE_REFLECT_PARTIAL_SOURCE_AVG_LT", "3.5")
    grey_lo = _f("pre_reflect_partial_worst_min_grey_lo", "PRE_REFLECT_PARTIAL_WORST_MIN_GREY_LO", "2.0")
    grey_hi = _f("pre_reflect_partial_worst_min_grey_hi", "PRE_REFLECT_PARTIAL_WORST_MIN_GREY_HI", "3.25")

    picked: list[str] = []
    seen: set[str] = set()
    for src in sorted(sources_with_no_items_left):
        if src not in seen:
            picked.append(src)
            seen.add(src)
    grey: list[tuple[float, str]] = []
    for src, st in rollup.items():
        if src in seen:
            continue
        sa = float(st.get("source_avg", 0))
        w = float(st.get("worst_item_min", 0))
        if sa < avg_lt or (grey_lo <= w <= grey_hi):
            grey.append((sa, src))
    grey.sort(key=lambda x: x[0])
    for _, src in grey:
        if src not in seen:
            picked.append(src)
            seen.add(src)
            if len(picked) >= max_n:
                break
    return picked[:max_n]


def pre_enrich_reflection_toggle(config: dict[str, Any]) -> bool:
    """默认关闭，避免额外 LLM 延时；显式打开：环境变量或 config。"""
    raw = (os.getenv("PRE_ENRICH_REFLECTION_ENABLED") or "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return bool(config.get("pre_enrich_reflection", False))


def pre_reflect_partial_rss_toggle(config: dict[str, Any]) -> bool:
    """是否对选定信源做局部 RSS 补拉（非整图重跑 collect）。"""
    raw = (os.getenv("PRE_REFLECT_PARTIAL_RSS") or "true").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    if raw in ("1", "true", "yes", "on"):
        return True
    return bool(config.get("pre_reflect_partial_rss", True))


def pre_enrich_reflect_fetch_excerpts() -> bool:
    raw = (os.getenv("PRE_ENRICH_REFLECT_FETCH_EXCERPTS") or "").strip().lower()
    return raw in ("1", "true", "yes", "on")
