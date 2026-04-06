#!/usr/bin/env python3
"""Load repo root .env/.ENV and run full LangGraph digest (LLM + site crawls). For local smoke tests."""
from __future__ import annotations

import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT / "project"))


def _safe_int(raw: str | None, default: int) -> int:
    try:
        return max(1, int((raw or "").strip() or str(default)))
    except ValueError:
        return default


def load_env() -> None:
    for name in (".env", ".ENV"):
        p = ROOT / name
        if not p.exists():
            continue
        for raw in p.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    if not (os.getenv("ARK_MODEL") or "").strip() and (os.getenv("ARK_Model") or "").strip():
        os.environ["ARK_MODEL"] = os.environ["ARK_Model"].strip()


def main() -> int:
    load_env()
    from ai_news_skill.orchestration.langgraph_agent import run_with_graph

    cfg = {
        "sources": "sources.json",
        "out": "daily_docs",
        "runs_dir": "runs",
        "limit": 8,
        "window_hours": 168,
        "official_window_hours": 168,
        "max_paper_ratio": 0.35,
        "min_official_items": 1,
        "intent_text": "过去一周人工智能与 AI Agent、大模型开源与发布的动态",
        "enable_openclaw": False,
        "use_llm": True,
        "llm_provider": "ark",
        "llm_intent_analysis": True,
        "ark_model": os.getenv("ARK_MODEL") or os.getenv("ARK_Model") or "",
        "ark_endpoint_id": os.getenv("ARK_ENDPOINT_ID", ""),
        "ark_api_key": os.getenv("ARK_API_KEY", ""),
        "allow_insecure_ssl": True,
        "pipeline_log": False,
        "gnews_enabled": bool((os.getenv("GNEWS_API_KEY") or "").strip()),
        "gnews_api_key": os.getenv("GNEWS_API_KEY", ""),
        "gnews_lang": "en",
        "gnews_max": 12,
        "gnews_category": "行业资讯",
        "gnews_per_keyword": True,
        "public_api_feeds": os.getenv("PUBLIC_API_FEEDS", ""),
        "allow_os_public_api_feeds": True,
        "public_api_feed_max": _safe_int(os.getenv("PUBLIC_API_FEED_MAX"), 8),
        "items_per_category_max": 20,
        "site_crawlers_enabled": True,
    }
    st = run_with_graph(config=cfg, max_retries=1)
    if st.get("result"):
        r = st["result"]
        print("STATUS", "OK")
        print("ITEMS", r.get("items"))
        print("DOC", r.get("doc_path"))
        errs = r.get("errors") or []
        print("ERRORS_COUNT", len(errs))
        for i, e in enumerate(errs[:30]):
            s = str(e)
            if len(s) > 280:
                s = s[:277] + "..."
            print(f"ERR[{i}]", s)
        return 0
    print("STATUS", "FAIL")
    print(st.get("error"))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
