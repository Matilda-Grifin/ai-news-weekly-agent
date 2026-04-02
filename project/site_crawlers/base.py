from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


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

    def crawl(self, *, per_source: int, window_hours: int, allow_insecure_fallback: bool) -> list[CrawledItem]:
        """Return recent items for one site (best-effort)."""
        raise NotImplementedError

