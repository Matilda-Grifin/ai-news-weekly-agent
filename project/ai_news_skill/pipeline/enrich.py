"""LLM-based enrichment of collected news items."""

from __future__ import annotations

import json
import re
from typing import Any

from ai_news_skill.pipeline.content import fetch_article_excerpt
from ai_news_skill.pipeline.llm_client import call_chat_completion
from ai_news_skill.pipeline.utils import looks_mostly_english, strip_html


def enrich_items_with_llm(
    items: list[dict],
    api_key: str,
    model: str,
    base_url: str,
    allow_insecure_fallback: bool,
    *,
    prompt_variant: str = "auto",
    intent_text: str = "",
    enable_openclaw: bool = False,
) -> tuple[str, list[str], list[str], list[str]]:
    if not items:
        return "今日暂无可分析资讯。", [], [], []

    rows: list[dict[str, Any]] = []
    for idx, it in enumerate(items, 1):
        cached = (it.get("content_excerpt") or "").strip()
        excerpt = cached if len(cached) >= 120 else fetch_article_excerpt(
            it.get("link", ""), allow_insecure_fallback=allow_insecure_fallback
        )
        rows.append(
            {
                "idx": idx,
                "category": it.get("category", "其他"),
                "source": it.get("source", ""),
                "title": it.get("title", ""),
                "summary": (it.get("summary", "") or "")[:220],
                "link": it.get("link", ""),
                "published": it.get("published", ""),
                "content_excerpt": excerpt[:2200],
            }
        )

    from prompts.digest_llm_prompts import pick_enrich_system_prompt

    pv = (prompt_variant or "auto").strip().lower()
    if pv == "auto":
        pv = "intent" if (intent_text or "").strip() else "news"
    system_prompt = pick_enrich_system_prompt(
        prompt_variant=pv,
        intent_text=intent_text,
        enable_openclaw=enable_openclaw,
    )
    user_prompt = {
        "task": "生成周报顶部小结和逐条中文解读",
        "rules": {
            "overview": "3-4句，概括本周动态、主线与重要变化",
            "per_item": (
                "每条生成 title_cn、core_cn、value_cn，全部必须为中文。"
                "title_cn：中文标题，原文英文须译成中文。"
                "core_cn：仅依据 news 中每条 content_excerpt（全文摘录）撰写，"
                "将信息压缩为约 200–300 个汉字（约 200–300 字）的核心内容；"
                "英文内容必须译为中文后再组织语言；不得少于约 200 字、不要明显超过 300 字。"
                "value_cn：该动态对业务/产品/研发的应用价值，不超过 80 字。"
                "禁止空话、禁止复述标题不写实质信息。"
            ),
            "style": "中文、自然、信息密度高，不要空话",
        },
        "format": {
            "overview": "string",
            "items": [{"idx": 1, "title_cn": "string", "core_cn": "string", "value_cn": "string"}],
        },
        "news": rows,
    }

    from chains.news_enrich_chain import invoke_enrich_chain

    overview, llm_core_points, llm_values, llm_titles_cn = invoke_enrich_chain(
        system_prompt,
        user_prompt,
        api_key=api_key,
        model=model,
        base_url=base_url,
        allow_insecure_fallback=allow_insecure_fallback,
        item_count=len(items),
    )

    # Post-process: translate remaining English items to Chinese.
    need_idxs: list[int] = []
    for i in range(len(items)):
        t = (llm_titles_cn[i] if i < len(llm_titles_cn) else "") or ""
        c = (llm_core_points[i] if i < len(llm_core_points) else "") or ""
        v = (llm_values[i] if i < len(llm_values) else "") or ""
        if not t.strip() or looks_mostly_english(t):
            need_idxs.append(i)
            continue
        if not c.strip() or looks_mostly_english(c):
            need_idxs.append(i)
            continue
        if v and looks_mostly_english(v):
            need_idxs.append(i)

    if need_idxs and api_key and (api_key or "").strip():
        payload_items: list[dict[str, Any]] = []
        for i in need_idxs:
            core_src = (rows[i].get("content_excerpt") or "").strip()
            if len(core_src) > 6000:
                core_src = core_src[:6000]
            payload_items.append(
                {
                    "idx": i + 1,
                    "title": (rows[i].get("title") or "").strip(),
                    "core_src": core_src,
                    "value_src": (rows[i].get("summary") or "").strip(),
                }
            )

        translate_system_prompt = (
            "你是中文新闻编辑。任务：把输入中每条的英文标题/正文摘要翻译成中文，并压缩成报告可用的核心内容。"
            "输出严格 JSON，不要解释，不要 markdown。"
        )
        translate_user_prompt = {
            "items": payload_items,
            "constraints": {
                "title_cn": "中文标题，不能为空，尽量不超过 60 字。",
                "core_cn": "核心内容：压缩为约 200-300 个汉字；若核心输入为英文，必须翻译后再压缩；不得少于约 200 字，不要明显超过 300 字。",
                "value_cn": "应用价值：<=80 字中文。",
            },
        }
        try:
            translate_raw = call_chat_completion(
                api_key=api_key,
                model=model,
                messages=[
                    {"role": "system", "content": translate_system_prompt},
                    {"role": "user", "content": json.dumps(translate_user_prompt, ensure_ascii=False)},
                ],
                base_url=base_url,
                timeout=120,
                allow_insecure_fallback=allow_insecure_fallback,
            )
            obj = _safe_extract_first_json_object(translate_raw)
            if obj:
                arr = obj.get("items") or obj.get("results") or []
                if isinstance(arr, list):
                    for row in arr:
                        if not isinstance(row, dict):
                            continue
                        idx = row.get("idx")
                        try:
                            idx_i = int(idx) - 1
                        except Exception:
                            continue
                        if 0 <= idx_i < len(items):
                            if isinstance(row.get("title_cn"), str) and row["title_cn"].strip():
                                llm_titles_cn[idx_i] = row["title_cn"].strip()
                            if isinstance(row.get("core_cn"), str) and row["core_cn"].strip():
                                llm_core_points[idx_i] = row["core_cn"].strip()
                            if isinstance(row.get("value_cn"), str) and row["value_cn"].strip():
                                llm_values[idx_i] = row["value_cn"].strip()
        except Exception:
            pass

    return overview, llm_core_points, llm_values, llm_titles_cn


def _safe_strip_json(text: str) -> str:
    s = (text or "").strip()
    s = re.sub(r"^```(?:json)?\s*", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s*```$", "", s)
    return s.strip()


def _safe_extract_first_json_object(text: str) -> dict[str, Any] | None:
    raw = _safe_strip_json(text)
    try:
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else None
    except Exception:
        pass
    m = re.search(r"(\{[\s\S]*\})", raw)
    if not m:
        return None
    try:
        obj = json.loads(m.group(1))
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None
