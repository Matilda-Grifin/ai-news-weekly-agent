"""System prompts for news enrichment; switch by task variant (no RAG)."""

from __future__ import annotations

SYSTEM_NEWS_DEFAULT = (
    "你是AI行业资讯编辑。根据用户提供的资讯数据生成结构化结果。"
    "要求客观准确、信息密度高，输出语言为中文。"
)

SYSTEM_WITH_INTENT_FOCUS = (
    SYSTEM_NEWS_DEFAULT
    + " 用户有明确关注方向时，小结与逐条解读应优先围绕该方向取舍与强调，但仍需尊重事实。"
)

SYSTEM_WITH_OPENCLAW_SECTION = (
    SYSTEM_NEWS_DEFAULT
    + " 若报告中会包含 OpenClaw 技能榜单段落，小结里可顺带一句点明榜单与新闻主线的关系（不必展开榜单细节）。"
)


def pick_enrich_system_prompt(
    *,
    prompt_variant: str,
    intent_text: str,
    enable_openclaw: bool,
) -> str:
    v = (prompt_variant or "news").strip().lower()
    if v == "intent":
        return SYSTEM_WITH_INTENT_FOCUS if (intent_text or "").strip() else SYSTEM_NEWS_DEFAULT
    if enable_openclaw:
        return SYSTEM_WITH_OPENCLAW_SECTION
    return SYSTEM_NEWS_DEFAULT
