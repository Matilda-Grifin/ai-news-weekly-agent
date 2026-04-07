#!/usr/bin/env python3
"""Production fast preset: target 1-2 min end-to-end."""
from __future__ import annotations

import os
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT / "project"))


def _safe_int(name: str, default: int, *, min_v: int = 1, max_v: int = 10_000) -> int:
    raw = (os.getenv(name) or "").strip()
    try:
        v = int(raw) if raw else default
    except ValueError:
        v = default
    return max(min_v, min(max_v, v))


def _truthy(name: str, default: bool = False) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


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

    window_h = _safe_int("FAST_WINDOW_HOURS", 24, min_v=6, max_v=24 * 14)
    limit = _safe_int("FAST_LIMIT", 10, min_v=2, max_v=20)
    detail_cap = _safe_int("FAST_SITE_MAX_DETAIL_ITEMS", 6, min_v=2, max_v=10)
    use_gnews = _truthy("FAST_GNEWS", False)
    use_public_api = _truthy("FAST_PUBLIC_API", False)
    use_intent_llm = _truthy("FAST_LLM_INTENT", True)
    enable_site_crawlers = _truthy("FAST_ENABLE_SITE_CRAWL", False)

    cfg = {
        "sources": str(ROOT / "sources.json"),
        "out": str(ROOT / "daily_docs"),
        "runs_dir": str(ROOT / "runs"),
        "limit": limit,
        "window_hours": window_h,
        "official_window_hours": window_h,
        "max_paper_ratio": 0.2,
        "min_official_items": 1,
        "intent_text": (os.getenv("FAST_INTENT") or "AI Agent OpenAI Anthropic 模型发布与开源动态").strip(),
        "strict_intent_match": _truthy("FAST_STRICT_INTENT_MATCH", False),
        "enable_openclaw": False,
        "use_llm": True,
        "llm_provider": os.getenv("LLM_PROVIDER", "auto"),
        "llm_base_url": os.getenv("OPENAI_BASE_URL", "") or os.getenv("LLM_BASE_URL", ""),
        "allow_custom_llm_endpoint": _truthy("ALLOW_CUSTOM_LLM_ENDPOINT", False),
        "llm_intent_analysis": use_intent_llm,
        "ark_model": os.getenv("ARK_MODEL") or os.getenv("ARK_Model") or "",
        "ark_endpoint_id": os.getenv("ARK_ENDPOINT_ID", ""),
        "ark_api_key": os.getenv("ARK_API_KEY", ""),
        "allow_insecure_ssl": True,
        "pipeline_log": False,
        "site_crawlers_enabled": enable_site_crawlers,
        "site_max_detail_items": detail_cap,
        "site_crawl_max_workers": _safe_int("FAST_SITE_CRAWL_MAX_WORKERS", 3, min_v=1, max_v=6),
        "collect_news_max_workers": _safe_int("FAST_COLLECT_NEWS_MAX_WORKERS", 8, min_v=2, max_v=16),
        "excerpt_fetch_max_workers": _safe_int("FAST_EXCERPT_MAX_WORKERS", 6, min_v=2, max_v=12),
        "gnews_enabled": use_gnews and bool((os.getenv("GNEWS_API_KEY") or "").strip()),
        "gnews_api_key": os.getenv("GNEWS_API_KEY", ""),
        "gnews_lang": "en",
        "gnews_max": _safe_int("FAST_GNEWS_MAX", 6, min_v=2, max_v=20),
        "gnews_category": "行业资讯",
        "gnews_per_keyword": True,
        "gnews_max_workers": _safe_int("FAST_GNEWS_MAX_WORKERS", 4, min_v=1, max_v=8),
        "public_api_feeds": (os.getenv("PUBLIC_API_FEEDS", "") if use_public_api else ""),
        "allow_os_public_api_feeds": use_public_api,
        "public_api_feed_max": _safe_int("FAST_PUBLIC_API_MAX", 4, min_v=1, max_v=10),
        "items_per_category_max": _safe_int("FAST_ITEMS_PER_CATEGORY_MAX", 8, min_v=2, max_v=20),
        "final_items_total": _safe_int("FAST_FINAL_ITEMS_TOTAL", 10, min_v=1, max_v=50),
    }

    print(
        "FAST_CONFIG",
        {
            "window_hours": cfg["window_hours"],
            "limit": cfg["limit"],
            "site_max_detail_items": cfg["site_max_detail_items"],
            "site_crawlers_enabled": cfg["site_crawlers_enabled"],
            "site_workers": cfg["site_crawl_max_workers"],
            "gnews_enabled": cfg["gnews_enabled"],
            "public_api_enabled": cfg["allow_os_public_api_feeds"],
            "llm_intent_analysis": cfg["llm_intent_analysis"],
            "strict_intent_match": cfg["strict_intent_match"],
            "final_items_total": cfg["final_items_total"],
        },
        flush=True,
    )

    t0 = time.perf_counter()
    st = run_with_graph(config=cfg, max_retries=1)
    elapsed = time.perf_counter() - t0
    print("ELAPSED_SEC", round(elapsed, 2), flush=True)

    if st.get("result"):
        r = st["result"]
        print("STATUS", "OK", flush=True)
        print("ITEMS", r.get("items"), flush=True)
        print("DOC", r.get("doc_path"), flush=True)
        errs = r.get("errors") or []
        print("ERRORS_COUNT", len(errs), flush=True)
        for i, e in enumerate(errs[:20]):
            s = str(e)
            if len(s) > 220:
                s = s[:217] + "..."
            print(f"ERR[{i}] {s}", flush=True)
        return 0

    print("STATUS", "FAIL", flush=True)
    print("ERROR", st.get("error"), flush=True)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

