"""Markdown report rendering and writing."""

from __future__ import annotations

import datetime as dt
import pathlib
import re
import urllib.parse
from collections import Counter, defaultdict
from email.utils import parsedate_to_datetime
from typing import Any

from ai_news_skill.pipeline.dedup import category_order_key
from ai_news_skill.pipeline.openclaw import OPENCLAW_LEADERBOARD_URL, build_openclaw_purpose_text
from ai_news_skill.pipeline.utils import looks_mostly_english, now_local, strip_html


def to_beijing_time_label(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return "未知"
    dt_obj = None
    try:
        dt_obj = parsedate_to_datetime(text)
    except Exception:
        dt_obj = None
    if dt_obj is None:
        try:
            dt_obj = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
        except Exception:
            return text
    if dt_obj.tzinfo is None:
        dt_obj = dt_obj.replace(tzinfo=dt.timezone.utc)
    bj = dt_obj.astimezone(dt.timezone(dt.timedelta(hours=8)))
    return bj.strftime("%a, %d %b %Y %H:%M:%S CST")


def format_source_desc(item: dict) -> str:
    source = item.get("source", "未知信源")
    category = item.get("category", "行业动态")
    return f"{source}的{category}动态"


def fallback_title_cn(item: dict, idx: int) -> str:
    title = (item.get("title") or "").strip()
    summary = strip_html((item.get("summary") or "").strip())
    category = item.get("category", "资讯")
    if title and not looks_mostly_english(title):
        return title
    if summary and not looks_mostly_english(summary):
        head = summary[:28].rstrip("，。,:;；")
        if head:
            return f"{head}（{category}）"
    if title:
        link = (item.get("link") or "").strip()
        slug = ""
        if link:
            path = urllib.parse.urlparse(link).path.strip("/")
            if path:
                slug = path.split("/")[-1].replace("-", " ").strip()
        if slug:
            return f"英文资讯：{slug}（{category}）"
        return f"英文资讯：{title[:40]}（{category}）"
    return f"第{idx}条资讯"


def build_fallback_detail(item: dict) -> str:
    summary = (item.get("summary") or "").strip()
    if summary:
        base = summary if len(summary) <= 220 else summary[:217] + "..."
    else:
        base = "原文摘要信息较少，建议打开原文链接查看完整上下文。"
    return f"核心内容：{base}\n应用价值：可作为相关产品路线和竞品动态的输入信号。"


def render_markdown(
    date_label: str,
    items: list[dict],
    errors: list[str],
    llm_overview: str = "",
    llm_core_points: list[str] | None = None,
    llm_values: list[str] | None = None,
    llm_titles_cn: list[str] | None = None,
    openclaw_top: list[dict] | None = None,
    openclaw_focus: dict | None = None,
    openclaw_asof: str = "",
) -> str:
    if llm_core_points is None:
        llm_core_points = []
    if llm_values is None:
        llm_values = []
    if llm_titles_cn is None:
        llm_titles_cn = []
    categories = Counter(it.get("category", "其他") for it in items)

    lines = [
        f"# AI 资讯周报 - {date_label}",
        "",
        "## 小结",
    ]
    if items:
        overview_text = llm_overview.strip()
        lines.extend(
            [
                f"- 研判小结：{overview_text or '今日主要增量集中在模型发布、工具链演进与论文更新。'}",
                "",
            ]
        )
    else:
        lines.extend(
            [
                "- 今天没有抓取到有效资讯，建议检查网络或信源地址可用性。",
                "",
            ]
        )

    lines.extend(["## 正文", ""])

    if not items:
        lines.extend(["- 今日无可用条目（可检查网络或信源地址）", ""])
    else:
        grouped: dict[str, list[dict]] = defaultdict(list)
        for it in items:
            grouped[it.get("category", "其他")].append(it)

        idx = 1
        for cat_name in sorted(grouped.keys(), key=category_order_key):
            lines.append(f"### {cat_name}")
            lines.append("")
            for it in grouped[cat_name]:
                core_cn = ""
                if idx - 1 < len(llm_core_points):
                    core_cn = llm_core_points[idx - 1].strip()
                value_cn = ""
                if idx - 1 < len(llm_values):
                    value_cn = llm_values[idx - 1].strip()
                title_cn = ""
                if idx - 1 < len(llm_titles_cn):
                    title_cn = llm_titles_cn[idx - 1].strip()
                if not title_cn:
                    title_cn = fallback_title_cn(it, idx)
                beijing_time = to_beijing_time_label(it.get("published", "未知"))
                source_desc = format_source_desc(it)
                fallback_core = strip_html((it.get("summary") or "")[:400])
                if not fallback_core:
                    fallback_core = strip_html((it.get("content_excerpt") or "")[:900])
                if not fallback_core:
                    fallback_core = "原文摘要不足，建议查看原文链接。"
                lines.extend(
                    [
                        f"#### {idx}. {title_cn}",
                        f"- 发布日期：{beijing_time}",
                        f"- 发布来源：{source_desc}",
                        f"- 核心内容：{core_cn or fallback_core}",
                        f"- 应用价值：{value_cn or '可用于评估对业务流程、产品能力和技术选型的实际影响。'}",
                        f"- 原文链接：{it.get('link', '')}",
                        "",
                    ]
                )
                idx += 1

    if openclaw_top:
        lines.extend(
            [
                "### OpenClaw 技能热榜（社区热度 · 辅助参考）",
                "",
                "以下榜单按 Star 排序，用于对照工具生态关注度；**技术事实与版本动态请以正文中 GitHub/官方条目为准。**",
                "",
            ]
        )
        lines.append(
            f"快照更新时间：{openclaw_asof or '未知'}（最近一周区间内可获取的最新榜单）。"
        )
        lines.append("")
        for i, row in enumerate(openclaw_top, 1):
            purpose = build_openclaw_purpose_text(row["skill_name"], row.get("summary", ""))
            lines.extend(
                [
                    f"#### 热榜第{i}名：{row['skill_name']}",
                    f"该技能由 {row['author']} 发布，当前 Stars 约为 {row['stars_text']}。"
                    f"主要用途：{purpose}",
                    "",
                ]
            )
        if openclaw_focus:
            lines.append(
                f"你关注的技能「{openclaw_focus['skill_name']}」当前排名第 {openclaw_focus['rank']}，"
                f"发布者为 {openclaw_focus['author']}，Stars 约 {openclaw_focus['stars_text']}。"
            )
            lines.append("")

        lines.extend(["## OpenClaw 链接", ""])
        lines.append(f"- 榜单来源页：{OPENCLAW_LEADERBOARD_URL}")
        for i, row in enumerate(openclaw_top, 1):
            lines.append(f"- 热榜第{i}名 {row['skill_name']}：{row['skill_url']}")
            if row.get("author_url"):
                lines.append(f"- 热榜第{i}名发布者 {row['author']}：{row['author_url']}")
        if openclaw_focus:
            lines.append(
                f"- 关注技能 {openclaw_focus['skill_name']}：{openclaw_focus['skill_url']}"
            )
        lines.append("")

    lines.extend(["## 信源与原文链接", ""])
    for i, it in enumerate(items, 1):
        lines.append(
            f"- [{i}] {it.get('source', '未知信源')}（发布日期：{it.get('published', '未知')}）：{it.get('link', '')}"
        )
    lines.append("")

    lines.extend(
        [
            "## 生成信息",
            f"- 生成时间：{now_local().strftime('%Y-%m-%d %H:%M:%S %z')}",
            f"- 总条目：{len(items)}",
            f"- 信源数：{len(set(i['source'] for i in items)) if items else 0}",
            f"- 分类数：{len(categories)}",
            "",
        ]
    )

    lines.extend(["## 抓取异常", ""])
    if errors:
        lines.extend([f"- {err}" for err in errors])
    else:
        lines.append("- 无")
    lines.append("")
    return "\n".join(lines)


def write_doc(output_dir: pathlib.Path, content: str) -> pathlib.Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    name = f"ai_weekly_{now_local().strftime('%Y%m%d')}.md"
    out_path = output_dir / name
    out_path.write_text(content, encoding="utf-8")
    return out_path
