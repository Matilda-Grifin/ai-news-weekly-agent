#!/usr/bin/env python3
"""
Playwright 拉取页面 HTML + trafilatura 抽正文；过短时可选本地 LLM 从 HTML 片段再抽一版。
输出到本目录下 out/ 各 md 文件。

用法（在项目根）:
  python3 test_harness_crawl/crawl_and_save.py
"""

from __future__ import annotations

import json
import os
import re
import sys
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from run_daily_digest import (  # noqa: E402
    ARK_BASE_URL,
    call_chat_completion,
    fetch_article_excerpt,
    load_dotenv,
    normalize_chat_completions_url,
)


def _slug_from_url(url: str) -> str:
    u = re.sub(r"^https?://", "", url)
    u = re.sub(r"[^\w\-.]+", "_", u)[:120]
    return u.strip("_") or "page"


def _trafilatura_from_html(html: str, url: str) -> str:
    try:
        import trafilatura

        t = trafilatura.extract(
            html,
            url=url,
            include_comments=False,
            include_tables=False,
            favor_recall=True,
        )
        if t:
            return re.sub(r"\s+", " ", t.strip())
    except Exception:
        pass
    return ""


def _playwright_html(url: str, *, timeout_ms: int = 90000) -> str:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            locale="zh-CN",
        )
        page = ctx.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        page.wait_for_timeout(2500)
        # 轻滚动，触发懒加载
        page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
        page.wait_for_timeout(800)
        html = page.content()
        ctx.close()
        browser.close()
    return html


def _llm_extract_from_html(
    html_fragment: str,
    *,
    api_key: str,
    model: str,
    base_url: str,
) -> str:
    frag = html_fragment[:24000]
    content = call_chat_completion(
        api_key=api_key,
        model=model,
        messages=[
            {
                "role": "system",
                "content": "你是网页正文提取助手。只输出文章主体纯文本，不要解释、不要 markdown。",
            },
            {
                "role": "user",
                "content": "从下列 HTML 片段中提取正文（可含小标题与代码块说明），保留技术术语：\n\n"
                + frag,
            },
        ],
        base_url=base_url,
        timeout=120,
        allow_insecure_fallback=True,
    )
    return (content or "").strip()


def crawl_one(url: str, out_dir: pathlib.Path, *, use_llm_fallback: bool) -> dict:
    result: dict = {"url": url, "method": "", "chars": 0, "path": "", "note": ""}
    text = ""
    method = ""
    html_cache = ""

    # 1) Playwright + trafilatura（适合知乎/动态站）
    try:
        html_cache = _playwright_html(url)
        text = _trafilatura_from_html(html_cache, url)
        if text:
            method = "playwright_trafilatura"
    except Exception as ex:  # noqa: BLE001
        result["note"] = f"playwright: {type(ex).__name__}: {ex}"

    # 2) HTTP + trafilatura 补试
    if len(text) < 400:
        t2 = fetch_article_excerpt(url, allow_insecure_fallback=True)
        if len(t2) > len(text):
            text = t2
            method = "httpx_trafilatura"

    # 3) LLM 回退（需 ARK_API_KEY / 接入点）
    if use_llm_fallback and len(text) < 500:
        ak = (os.getenv("ARK_API_KEY") or os.getenv("OPENAI_API_KEY") or "").strip()
        model = (
            os.getenv("ARK_ENDPOINT_ID", "").strip()
            or os.getenv("ARK_MODEL", "").strip()
            or "Doubao-Seed-1.6-lite"
        )
        if ak:
            try:
                base = normalize_chat_completions_url(os.getenv("ARK_BASE_URL", "") or ARK_BASE_URL)
                html2 = html_cache
                if not html2:
                    from run_daily_digest import fetch_text

                    try:
                        html2 = fetch_text(
                            url,
                            timeout=25,
                            allow_insecure_fallback=True,
                            proxy_scope="site_crawler",
                        )
                    except Exception as ex:  # noqa: BLE001
                        result["note"] = (result.get("note") or "") + f" | fetch_text: {ex}"
                if html2:
                    text = _llm_extract_from_html(html2, api_key=ak, model=model, base_url=base)
                    method = "llm_from_html"
            except Exception as ex:  # noqa: BLE001
                result["note"] = (result.get("note") or "") + f" | llm: {ex}"

    slug = _slug_from_url(url)
    path = out_dir / f"{slug}.md"
    body = f"# Source\n\n{url}\n\n# Extract method\n\n{method or 'none'}\n\n---\n\n{text or '(empty)'}\n"
    path.write_text(body, encoding="utf-8")
    result["method"] = method
    result["chars"] = len(text)
    result["path"] = str(path)
    return result


def main() -> int:
    load_dotenv(ROOT / ".env")
    out_dir = pathlib.Path(__file__).resolve().parent / "out"
    out_dir.mkdir(parents=True, exist_ok=True)

    urls = [
        "https://zhuanlan.zhihu.com/p/2013355209838055854",
        "https://devblogs.microsoft.com/agent-framework/agent-harness-in-agent-framework/",
        "https://martinfowler.com/articles/exploring-gen-ai/harness-engineering.html",
    ]
    reports = []
    for u in urls:
        reports.append(crawl_one(u, out_dir, use_llm_fallback=True))

    summary = out_dir / "_summary.json"
    summary.write_text(json.dumps(reports, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(reports, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
