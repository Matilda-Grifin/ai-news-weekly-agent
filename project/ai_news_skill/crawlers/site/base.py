from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Protocol


def playwright_site_headless() -> bool:
    """
    List/detail site crawlers (OpenAI / Anthropic / HF / etc.) use Playwright.
    Set SITE_CRAWLER_HEADLESS=false to open a visible browser when a site blocks headless
    or needs one-time manual verification.
    """
    raw = (os.getenv("SITE_CRAWLER_HEADLESS") or "true").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    if raw in ("1", "true", "yes", "on"):
        return True
    return True


def playwright_chromium_launch_kwargs() -> dict[str, Any]:
    """Playwright chromium.launch(...) 参数：无头 + 可选出站代理（Webshare 受 OUTBOUND_WEBSHARE_SCOPES 约束，默认含 playwright）。"""
    from ai_news_skill.core.http_client import playwright_proxy_for_browser

    kw: dict[str, Any] = {"headless": playwright_site_headless()}
    px = playwright_proxy_for_browser()
    if px:
        kw["proxy"] = px
    return kw


@dataclass
class CrawledItem:
    source: str
    category: str
    title: str
    link: str
    summary: str = ""
    published: str = ""  # RFC2822/ISO string; keep loose for downstream parser
    content_excerpt: str = ""


class SiteCrawler(Protocol):
    name: str

    def crawl(
        self,
        *,
        per_source: int,
        window_hours: int,
        allow_insecure_fallback: bool,
    ) -> list[CrawledItem]:
        """Return recent items for one site (best-effort)."""
        raise NotImplementedError

