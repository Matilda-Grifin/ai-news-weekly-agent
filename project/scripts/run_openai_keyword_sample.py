#!/usr/bin/env python3
"""One-off: OpenAI news list + detail crawl, filter by keyword, print markdown to stdout."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "project"))

from ai_news_skill.crawlers.site import openai_news as oa  # noqa: E402


def matches_video_model(text: str) -> bool:
    t = (text or "").lower()
    if "video model" in t:
        return True
    if "video" in t and "model" in t:
        return True
    # 常见中文表述
    if "视频" in (text or "") and "模型" in (text or ""):
        return True
    return False


def main() -> int:
    list_url = oa.OpenAINewsCrawler.LIST_URL
    links = oa._collect_openai_news_links(list_url, target=100, timeout_ms=60000)
    max_detail = 60
    max_hits = 25
    hits: list[tuple[str, dict]] = []
    scanned = 0

    for href in links:
        if len(hits) >= max_hits:
            break
        if scanned >= max_detail:
            break
        scanned += 1
        item = oa._fetch_detail(href, timeout_ms=35000)
        if not item:
            continue
        blob = " ".join(
            [
                str(item.get("title", "")),
                str(item.get("summary", "")),
                str(item.get("content_excerpt", ""))[:2000],
            ]
        )
        if matches_video_model(blob):
            hits.append((href, item))

    lines: list[str] = [
        "# OpenAI News 抓取样例：关键词 *video model*",
        "",
        f"- 列表页：{list_url}",
        "- 方式：Playwright 滚动列表 + 逐篇 Playwright 打开详情，BeautifulSoup 解析",
        f"- 列表阶段收集链接数（上限 100）：**{len(links)}**",
        f"- 详情最多扫描：**{scanned}** 篇（为控制耗时）",
        f"- 命中关键词的条目数：**{len(hits)}**（上限 {max_hits}）",
        "",
        "> 关键词规则：英文含 `video model` 或同时含 `video` 与 `model`；或中文同时含「视频」「模型」。",
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
                "可能原因：近期列表前几页无相关主题；或需增大 `target` / `max_detail` 继续向后翻页扫描。",
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

    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
