"""Pydantic configuration model for the digest pipeline."""

from __future__ import annotations

import os
from typing import Any, Callable

from pydantic import BaseModel, Field


class DigestConfig(BaseModel):
    """Validated configuration for the digest pipeline.

    Construct from dict via ``DigestConfig.from_dict(config)`` which merges
    environment variables as fallbacks.
    """

    # --- Sources & input ---
    sources: str = "sources.json"
    intent_text: str = ""
    window_hours: int = 168
    out: str = "daily_docs"
    runs_dir: str = "runs"
    limit: int = 5
    final_items_total: int = 10

    # --- LLM ---
    use_llm: bool = True
    llm_provider: str = "auto"
    ark_api_key: str = ""
    ark_endpoint_id: str = ""
    ark_model: str = "Doubao-Seed-1.6-lite"
    openai_api_key: str = ""
    openai_base_url: str = ""
    openai_model: str = ""
    llm_base_url: str = ""
    allow_insecure_ssl: bool = False
    allow_custom_llm_endpoint: bool = False
    llm_intent_analysis: bool = True
    llm_prompt_variant: str = "auto"

    # --- Collection ---
    site_crawlers_enabled: bool = True
    gnews_enabled: bool = False
    gnews_api_key: str = ""
    gnews_lang: str = "en"
    gnews_max: int = 10
    gnews_per_keyword: bool = True
    gnews_per_keyword_max: int = 8
    gnews_category: str = "行业资讯"
    public_api_feeds: str = ""

    # --- Pipeline ---
    max_paper_ratio: float = 0.2
    min_official_items: int = 3
    official_window_hours: int = 168
    enable_openclaw: bool = False
    focus_skill: str = ""
    items_per_category_max: int = 5
    items_per_source_max: int = 1

    # --- Observability ---
    pipeline_log: bool = True
    strict_intent_match: bool = True

    # --- Webhook ---
    webhook_url: str = ""

    model_config = {"extra": "allow"}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "DigestConfig":
        """Build config from a raw dict, applying env-var fallbacks."""
        data = dict(raw)
        _env = _env_defaults()
        for key, env_val in _env.items():
            if key not in data or data[key] in ("", None):
                data[key] = env_val
        return cls(**{k: v for k, v in data.items() if v is not None})

    def to_dict(self) -> dict[str, Any]:
        """Export back to dict (for backward compat with existing code)."""
        return self.model_dump(exclude_none=True)


def _env_defaults() -> dict[str, Any]:
    """Read relevant environment variables as fallback values."""
    def _strip(key: str) -> str:
        return (os.getenv(key) or "").strip()

    return {
        "ark_api_key": _strip("ARK_API_KEY"),
        "ark_endpoint_id": _strip("ARK_ENDPOINT_ID"),
        "ark_model": _strip("ARK_MODEL") or None,
        "openai_api_key": _strip("OPENAI_API_KEY"),
        "openai_base_url": _strip("OPENAI_BASE_URL") or _strip("LLM_BASE_URL") or None,
        "openai_model": _strip("OPENAI_MODEL") or _strip("MODEL") or None,
        "gnews_api_key": _strip("GNEWS_API_KEY") or None,
        "gnews_lang": _strip("GNEWS_LANG") or None,
        "webhook_url": _strip("DIGEST_WEBHOOK_URL") or None,
    }
