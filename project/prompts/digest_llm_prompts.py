"""System prompts for news enrichment; switch by task variant (no RAG)."""

from __future__ import annotations

# 借鉴 DeerFlow deep-research / newsletter-generation：内化流程与周刊式总览→分条，不额外输出检查清单。
SUFFIX_RESEARCH_AND_NEWSLETTER = (
    " 写作前内化（不必输出）：多角度核对——同一主题是否已有官方或仓库侧可核验链接；"
    "区分事实与推断；合成前自问关键结论是否都有条目或链接支撑。"
    "叙事节奏对齐周刊：小结先给本周主线与变化，再进入逐条；避免以单条热搜代替全貌。"
)

# 开源叙事：GitHub Release / 仓库动态为主；OpenClaw 仅在有榜单时作为辅助（与 SKILL.md 一致）。
SUFFIX_GITHUB_PRIMARY = (
    " 若 news 中含 github.com 相关链接（Release、仓库公告、commits atom 等），"
    "「开源与工具链」相关表述应优先依据这些**可核验**条目展开，小结里也应先落事实再作解读。"
)

SUFFIX_OPENCLAW_AS_AUXILIARY = (
    " 已知报告正文中会附带 OpenClaw 技能热榜段落：该榜单仅代表社区热度快照，**辅助**对照工具生态；"
    "小结中最多用一两句点出与 GitHub/官方主线是否呼应，**不得**用榜单排名替代 Release 或仓库动态类事实，亦勿把热榜当本周技术主线主轴。"
)

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
        base = SYSTEM_WITH_INTENT_FOCUS if (intent_text or "").strip() else SYSTEM_NEWS_DEFAULT
    elif enable_openclaw:
        base = SYSTEM_WITH_OPENCLAW_SECTION
    else:
        base = SYSTEM_NEWS_DEFAULT

    base = base + SUFFIX_RESEARCH_AND_NEWSLETTER + SUFFIX_GITHUB_PRIMARY
    if enable_openclaw:
        base = base + SUFFIX_OPENCLAW_AS_AUXILIARY
    return base
