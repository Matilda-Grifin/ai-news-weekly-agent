#!/usr/bin/env python3
"""
一次跑通「全开」抓取：关闭 DIGEST_FAST、启用站点 Playwright、可选 GNews + Public API。

在 load_env() 之后强制覆盖环境变量，因此即使用户 .env 里写了 DIGEST_FAST_MODE=1，
本脚本仍会开启站点爬虫（与 _e2e_codex_once 的冒烟默认相反）。

并行说明：
- 多站点 Playwright：线程池 SITE_CRAWL_MAX_WORKERS
- 多 RSS：COLLECT_NEWS_MAX_WORKERS
- GNews 多关键词：GNEWS_MAX_WORKERS
整段管线仍是「站点 → RSS → GNews → Public API」顺序；同一信源 name 在站点爬成功后会跳过 RSS（设计去重）。

用法：
  python project/scripts/run_digest_full_crawl.py
  E2E_WINDOW_DAYS=1 python project/scripts/run_digest_full_crawl.py
"""

from __future__ import annotations

import os
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT / "project"))

try:
    _E2E_DAYS = max(1, min(14, int(os.getenv("E2E_WINDOW_DAYS", "1") or "1")))
except ValueError:
    _E2E_DAYS = 1
WINDOW_HOURS = max(6, min(24 * 14, _E2E_DAYS * 24))


def _log(msg: str) -> None:
    print(f"[full {time.strftime('%H:%M:%S')}] {msg}", flush=True)


def load_env() -> None:
    _log("loading .env / .ENV …")
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


def apply_full_crawl_env() -> None:
    """在 .env 加载后覆盖：全开抓取路径。"""
    os.environ["DIGEST_FAST_MODE"] = "0"
    os.environ["DIGEST_FAST"] = "0"
    os.environ["SITE_CRAWLERS_ENABLED"] = "1"
    os.environ["E2E_DISABLE_GNEWS"] = "0"
    os.environ["E2E_SKIP_PUBLIC_API"] = "0"
    if not (os.getenv("PUBLIC_API_FEEDS") or "").strip():
        os.environ["PUBLIC_API_FEEDS"] = "hackernews"
    _log(
        "full crawl env: DIGEST_FAST_MODE=0 SITE_CRAWLERS_ENABLED=1 "
        "E2E_DISABLE_GNEWS=0 E2E_SKIP_PUBLIC_API=0 "
        f"PUBLIC_API_FEEDS={os.environ.get('PUBLIC_API_FEEDS', '')!r}"
    )


def main() -> int:
    _log(f"cwd={os.getcwd()} ROOT={ROOT}")
    load_env()
    apply_full_crawl_env()

    _log("importing langgraph_agent.run_with_graph …")
    t_import = time.perf_counter()
    from ai_news_skill.orchestration.langgraph_agent import run_with_graph

    _log(f"import done in {time.perf_counter() - t_import:.2f}s")

    gnews_key = (os.getenv("GNEWS_API_KEY") or "").strip()
    cfg = {
        "sources": str(ROOT / "sources.json"),
        "out": str(ROOT / "daily_docs"),
        "runs_dir": str(ROOT / "runs"),
        "limit": int(os.getenv("E2E_LIMIT", "8")),
        "window_hours": WINDOW_HOURS,
        "official_window_hours": WINDOW_HOURS,
        "max_paper_ratio": 0.35,
        "min_official_items": int(os.getenv("E2E_MIN_OFFICIAL", "0")),
        "site_crawlers_enabled": True,
        "intent_text": "codex",
        "enable_openclaw": False,
        "use_llm": True,
        "llm_provider": os.getenv("LLM_PROVIDER", "auto"),
        "llm_base_url": os.getenv("OPENAI_BASE_URL", "") or os.getenv("LLM_BASE_URL", ""),
        "allow_custom_llm_endpoint": (os.getenv("ALLOW_CUSTOM_LLM_ENDPOINT", "").strip().lower() in ("1", "true", "yes")),
        "llm_intent_analysis": True,
        "ark_model": os.getenv("ARK_MODEL") or os.getenv("ARK_Model") or "",
        "ark_endpoint_id": os.getenv("ARK_ENDPOINT_ID", ""),
        "ark_api_key": os.getenv("ARK_API_KEY", ""),
        "allow_insecure_ssl": True,
        "pipeline_log": True,
        "gnews_enabled": bool(gnews_key),
        "gnews_api_key": gnews_key,
        "gnews_lang": "en",
        "gnews_max": 12,
        "gnews_category": "行业资讯",
        "gnews_per_keyword": True,
        "public_api_feeds": os.getenv("PUBLIC_API_FEEDS", ""),
        "allow_os_public_api_feeds": True,
        "public_api_feed_max": int(os.getenv("E2E_PUBLIC_API_MAX", "4")),
        "items_per_category_max": int(os.getenv("E2E_ITEMS_CAP", "12")),
        "collect_news_max_workers": int(os.getenv("COLLECT_NEWS_MAX_WORKERS", "12")),
        "excerpt_fetch_max_workers": int(os.getenv("EXCERPT_FETCH_MAX_WORKERS", "12")),
        "site_crawl_max_workers": int(os.getenv("SITE_CRAWL_MAX_WORKERS", "4")),
        "gnews_max_workers": int(os.getenv("GNEWS_MAX_WORKERS", "6")),
    }
    if not gnews_key:
        _log("GNEWS_API_KEY 未配置：GNews 阶段将跳过（其余抓取仍执行）")

    def _progress_cb(step: dict) -> None:
        st = step.get("status", "")
        name = step.get("name", "")
        detail = (step.get("detail") or "").strip()
        if len(detail) > 100:
            detail = detail[:97] + "..."
        _log(f"pipeline | {st:8} | {name} | {detail}")

    cfg["_progress_cb"] = _progress_cb

    _log(
        f"config: window_hours={WINDOW_HOURS} site_crawl_workers={cfg['site_crawl_max_workers']} "
        f"gnews={cfg['gnews_enabled']} public_api_feeds={cfg.get('public_api_feeds')!r}"
    )
    _log("calling run_with_graph …")

    t0 = time.perf_counter()
    try:
        st = run_with_graph(config=cfg, max_retries=2)
    except Exception as ex:  # noqa: BLE001
        _log(f"run_with_graph raised: {type(ex).__name__}: {ex}")
        raise
    elapsed = time.perf_counter() - t0
    _log(f"run_with_graph returned in {elapsed:.2f}s")

    print("ELAPSED_SEC", round(elapsed, 2), flush=True)

    if st.get("result"):
        r = st["result"]
        print("STATUS", "OK", flush=True)
        print("ITEMS", r.get("items"), flush=True)
        print("DOC", r.get("doc_path"), flush=True)
        tf = st.get("trace_file")
        if tf:
            _log(f"trace_file={tf}")
        errs = r.get("errors") or []
        print("ERRORS_COUNT", len(errs), flush=True)
        for i, e in enumerate(errs[:40]):
            s = str(e)
            if len(s) > 220:
                s = s[:217] + "..."
            print(f"ERR[{i}]", s, flush=True)
        return 0
    print("STATUS", "FAIL", flush=True)
    print("error:", st.get("error"), flush=True)
    print("keys:", list(st.keys()), flush=True)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
