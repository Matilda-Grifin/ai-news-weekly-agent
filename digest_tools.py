"""
LangChain tools for the news digest agent (registry for routing / future create_agent).

These wrap existing pipeline primitives; RAG is intentionally not included.
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.tools import tool


@tool
def run_weekly_digest_pipeline(config_json: str) -> str:
    """
    Run the full RSS → balance → optional OpenClaw → LLM enrich → markdown report pipeline.
    config_json: JSON object with keys matching run_digest_pipeline (sources, out, limit, window_hours, intent_text, etc.).
    """
    from agent_runtime import run_digest_pipeline

    cfg: dict[str, Any] = json.loads(config_json)
    result = run_digest_pipeline(cfg)
    return json.dumps(result, ensure_ascii=False)


@tool
def fetch_openclaw_stars_tool(focus_skill: str = "") -> str:
    """Fetch OpenClaw skills leaderboard snapshot (top stars). Optional focus_skill filters ranking highlight."""
    from run_daily_digest import fetch_openclaw_stars_top

    top, focus, asof = fetch_openclaw_stars_top(
        top_n=3,
        focus_skill=focus_skill,
        allow_insecure_fallback=True,
    )
    payload = {
        "asof": asof,
        "top": top,
        "focus": focus,
    }
    return json.dumps(payload, ensure_ascii=False)


DIGEST_AGENT_TOOLS = [run_weekly_digest_pipeline, fetch_openclaw_stars_tool]
