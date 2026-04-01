"""LCEL: prompt | ChatOpenAI(with_structured_output) for weekly digest enrichment."""

from __future__ import annotations

import json
import re
from typing import Any

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from model.llm_factory import build_chat_openai


class EnrichItem(BaseModel):
    idx: int
    title_cn: str = ""
    core_cn: str = ""
    value_cn: str = ""


class EnrichOutput(BaseModel):
    overview: str = ""
    items: list[EnrichItem] = Field(default_factory=list)


def _strip_json_fence(text: str) -> str:
    s = (text or "").strip()
    m = re.match(r"^```(?:json)?\s*([\s\S]*?)\s*```$", s)
    if m:
        return m.group(1).strip()
    return s


def _parse_from_raw_llm_text(content: str, item_count: int) -> EnrichOutput:
    raw = _strip_json_fence(content)
    data = json.loads(raw)
    return EnrichOutput.model_validate(data)


def invoke_enrich_chain(
    system_prompt: str,
    user_payload: dict[str, Any],
    *,
    api_key: str,
    model: str,
    base_url: str,
    allow_insecure_fallback: bool,
    item_count: int,
) -> tuple[str, list[str], list[str], list[str]]:
    """Returns (overview, core_points, values, titles_cn) aligned to original items order."""
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
                "以下是任务输入 JSON。请严格输出 JSON 对象，字段 overview 与 items；"
                "items 为数组，元素含 idx、title_cn、core_cn、value_cn。\n{user_json}",
            ),
        ]
    )

    user_json = json.dumps(user_payload, ensure_ascii=False)
    chain_input = {"system_prompt": system_prompt, "user_json": user_json}

    parsed: EnrichOutput | None = None
    try:
        structured = llm.with_structured_output(EnrichOutput)
        runnable = prompt | structured
        raw_out = runnable.invoke(chain_input)
        if isinstance(raw_out, EnrichOutput):
            parsed = raw_out
        elif isinstance(raw_out, dict):
            parsed = EnrichOutput.model_validate(raw_out)
        else:
            parsed = raw_out
    except Exception:
        try:
            base = prompt | llm
            msg = base.invoke(chain_input)
            text = getattr(msg, "content", str(msg))
            parsed = _parse_from_raw_llm_text(text, item_count)
        except Exception:
            from run_daily_digest import call_chat_completion

            content = call_chat_completion(
                api_key=api_key,
                model=model,
                base_url=base_url,
                allow_insecure_fallback=allow_insecure_fallback,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_json},
                ],
            )
            parsed = _parse_from_raw_llm_text(content, item_count)

    overview = (parsed.overview or "").strip() or "今日资讯以产品发布与研究进展为主。"
    core_points = [""] * item_count
    values = [""] * item_count
    titles_cn = [""] * item_count
    for row in parsed.items:
        idx = int(row.idx) if row.idx is not None else -1
        if 1 <= idx <= item_count:
            core_points[idx - 1] = (row.core_cn or "").strip()
            values[idx - 1] = (row.value_cn or "").strip()
            titles_cn[idx - 1] = (row.title_cn or "").strip()
    return overview, core_points, values, titles_cn
