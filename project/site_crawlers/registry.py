from __future__ import annotations

from typing import Iterable

from .base import SiteCrawler
from ai_news_skill.crawlers.site.anthropic_news import AnthropicNewsCrawler
from ai_news_skill.crawlers.site.openai_news import OpenAINewsCrawler
from ai_news_skill.crawlers.site.huggingface_blog import HuggingFaceBlogCrawler
from ai_news_skill.crawlers.site.techcrunch import TechCrunchAICrawler
from ai_news_skill.crawlers.site.venturebeat import VentureBeatAICrawler


def all_crawlers() -> list[SiteCrawler]:
    # Add new crawlers here.
    return [
        OpenAINewsCrawler(),
        AnthropicNewsCrawler(),
        HuggingFaceBlogCrawler(),
        TechCrunchAICrawler(),
        VentureBeatAICrawler(),
    ]


def crawler_by_source_name(source_name: str) -> SiteCrawler | None:
    name = (source_name or "").strip().lower()
    for c in all_crawlers():
        if c.name.lower() == name:
            return c
    return None


def supported_source_names() -> Iterable[str]:
    return [c.name for c in all_crawlers()]

