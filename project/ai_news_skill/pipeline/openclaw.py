"""OpenClaw leaderboard integration."""

from __future__ import annotations

import re
from typing import Any

from ai_news_skill.core.http_client import fetch_text
from ai_news_skill.pipeline.utils import parse_human_number, strip_tags

OPENCLAW_LEADERBOARD_URL = "https://topclawhubskills.com/"


def fetch_openclaw_stars_top(
    top_n: int = 3, focus_skill: str = "", allow_insecure_fallback: bool = False
) -> tuple[list[dict], dict | None, str]:
    html_text = fetch_text(
        OPENCLAW_LEADERBOARD_URL,
        timeout=25,
        allow_insecure_fallback=allow_insecure_fallback,
    )
    panel_match = re.search(
        r'<div class="panel" id="panel-stars".*?<tbody>(.*?)</tbody>',
        html_text,
        flags=re.S,
    )
    if not panel_match:
        raise ValueError("cannot find OpenClaw stars panel")
    tbody = panel_match.group(1)
    rows = re.findall(r"<tr.*?>(.*?)</tr>", tbody, flags=re.S)
    items: list[dict] = []
    for row in rows:
        tds = re.findall(r"<td.*?>(.*?)</td>", row, flags=re.S)
        if len(tds) < 5:
            continue
        rank_text = strip_tags(tds[0])
        rank = int(rank_text) if rank_text.isdigit() else 0
        skill_anchor_match = re.search(r'(<a[^>]*class="skill-name"[^>]*>.*?</a>)', tds[1], flags=re.S)
        author_match = re.search(r'href="([^"]+)"[^>]*>(.*?)</a>', tds[2], flags=re.S)
        summary_match = re.search(r'<div class="skill-summary">(.*?)</div>', tds[1], flags=re.S)
        skill_anchor = skill_anchor_match.group(1) if skill_anchor_match else ""
        skill_name = strip_tags(skill_anchor)
        skill_url_match = re.search(r'href="([^"]+)"', skill_anchor)
        skill_url = (skill_url_match.group(1) if skill_url_match else "").strip()
        author = strip_tags(author_match.group(2) if author_match else "")
        author_url = (author_match.group(1) if author_match else "").strip()
        summary = strip_tags(summary_match.group(1) if summary_match else "")
        stars_text = strip_tags(tds[3])
        downloads_text = strip_tags(tds[4])
        items.append(
            {
                "rank": rank,
                "skill_name": skill_name,
                "skill_url": skill_url,
                "author": author,
                "author_url": author_url,
                "stars_text": stars_text,
                "stars_num": parse_human_number(stars_text),
                "downloads_text": downloads_text,
                "summary": summary,
            }
        )
    items = [x for x in items if x["rank"] > 0 and x["skill_name"]]
    items.sort(key=lambda x: (-x["stars_num"], x["rank"]))

    focus_result = None
    if focus_skill:
        q = focus_skill.lower().strip()
        for row in items:
            if q in row["skill_name"].lower():
                focus_result = row
                break

    asof_match = re.search(r"As of ([^<]+)</div>", html_text, flags=re.S)
    asof_text = strip_tags(asof_match.group(1)) if asof_match else "最新可用快照"
    return items[: max(1, top_n)], focus_result, asof_text


def build_openclaw_purpose_text(skill_name: str, summary: str) -> str:
    text = (summary or "").strip()
    lower = text.lower()
    if "continuous improvement" in lower or "self-improving" in lower:
        return (
            "这个技能用于把代理在执行任务时的失败案例、修正过程和成功经验沉淀成可复用记忆，"
            "让后续同类任务少走弯路。它适合长期使用的个人工作流，价值在于持续降低重复错误率，"
            "并逐步提升任务完成质量与稳定性。"
        )
    if "google workspace" in lower or "gmail" in lower or "calendar" in lower:
        return (
            "这个技能把邮件、日历、文档、表格、网盘等 Google Workspace 操作统一成可调用能力，"
            "适合做跨工具的自动化处理。它的价值是减少手动切换和重复操作，"
            "把日程整理、信息检索、文档协作串成一条完整流程。"
        )
    if "web search" in lower or "tavily" in lower or "search" in lower:
        return (
            "这个技能用于执行面向 AI 代理的网页检索，重点是返回结构化、相关性更高的结果，"
            "方便后续摘要、比对和事实校验。它适合资讯追踪与研究场景，"
            "价值在于提高检索效率并降低无效信息噪声。"
        )
    return (
        f"这个技能主要用于 {skill_name} 相关能力扩展，帮助代理在特定场景下执行更稳定、可复用的操作。"
        "在日常使用中，建议重点评估它对你的任务链路是否能带来效率提升、错误率下降和更强的自动化闭环。"
    )
