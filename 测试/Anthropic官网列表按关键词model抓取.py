#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
一次性脚本：Anthropic 新闻列表页（Playwright 滚动 + See more）+ 详情页 Playwright + BS，
按关键词 **model**（不区分大小写；正文里含「模型」亦算命中）筛选，结果写入同目录 Markdown。

与网页管线的差异（故结果不必一致）：
- **未使用** `window_hours` / 时间窗过滤；
- 列表链路上限 target=100，详情最多扫描 max_detail=55，命中展示 max_hits=30；
- 无「严格意图过滤」、无 GNews、无每板块 cap 等与 Streamlit 相同的后续步骤。
"""
from __future__ import annotations

import sys
from pathlib import Path

# 仓库根目录（本文件在 测试/ 下）
_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "project"))

from ai_news_skill.crawlers.site import anthropic_news as an  # noqa: E402


def matches_model(text: str) -> bool:
    t = (text or "")
    low = t.lower()
    if "model" in low:
        return True
    if "模型" in t:
        return True
    return False


def main() -> int:
    list_url = an.AnthropicNewsCrawler.LIST_URL
    links = an._collect_anthropic_news_links(list_url, target=100, timeout_ms=60000)
    max_detail = 55
    max_hits = 30
    hits: list[tuple[str, dict]] = []
    scanned = 0

    for href in links:
        if len(hits) >= max_hits:
            break
        if scanned >= max_detail:
            break
        scanned += 1
        item = an._fetch_detail(href, timeout_ms=35000)
        if not item:
            continue
        blob = " ".join(
            [
                str(item.get("title", "")),
                str(item.get("summary", "")),
                str(item.get("content_excerpt", ""))[:2500],
            ]
        )
        if matches_model(blob):
            hits.append((href, item))

    lines: list[str] = [
        "# Anthropic News 抓取样例：关键词 *model* / 模型",
        "",
        f"- **列表页**：{list_url}",
        "- **方式**：Playwright 滚动列表 + 「See more」类交互；逐篇 Playwright 打开详情，BeautifulSoup 解析",
        f"- **列表阶段收集链接数**（目标上限 100）：**{len(links)}**",
        f"- **详情扫描篇数**：**{scanned}**（上限 {max_detail}，控制耗时）",
        f"- **命中关键词的条目数**：**{len(hits)}**（展示上限 {max_hits}）",
        "",
        "> **关键词规则**：英文 `model`（不区分大小写）；或中文「模型」。",
        "",
        "---",
        "",
    ]

    if not hits:
        lines.extend(
            [
                "## 命中结果",
                "",
                "（无）在本次扫描的详情页标题/摘要/正文片段中未匹配到上述规则。",
                "",
            ]
        )
    else:
        lines.append("## 命中条目")
        lines.append("")
        for i, (href, item) in enumerate(hits, 1):
            title = (item.get("title") or "").strip()
            pub = (item.get("published") or "").strip()
            summary = (item.get("summary") or "").strip()
            excerpt = (item.get("content_excerpt") or "").strip()
            if len(excerpt) > 1200:
                excerpt = excerpt[:1197] + "..."
            lines.append(f"### {i}. {title}")
            lines.append("")
            lines.append(f"- **链接**：{href}")
            lines.append(f"- **发布时间（页面解析）**：{pub or '（未解析到）'}")
            lines.append("")
            if summary:
                lines.append("**摘要**")
                lines.append("")
                lines.append(summary)
                lines.append("")
            if excerpt:
                lines.append("**正文片段**")
                lines.append("")
                lines.append(excerpt)
                lines.append("")
            lines.append("---")
            lines.append("")

    out_path = Path(__file__).resolve().parent / "Anthropic官网新闻_关键词model_抓取结果.md"
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"已写入：{out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
