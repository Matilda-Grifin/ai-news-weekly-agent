#!/usr/bin/env python3
"""One-shot: LangGraph digest, 3d window, intent codex; load repo .env; verbose logs to stdout."""
from __future__ import annotations

import os
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT / "project"))

# 冒烟默认 1 天窗口 + 较少条数；完整压测可设 E2E_WINDOW_DAYS=3
try:
    _E2E_DAYS = max(1, min(14, int(os.getenv("E2E_WINDOW_DAYS", "1") or "1")))
except ValueError:
    _E2E_DAYS = 1
WINDOW_HOURS = max(6, min(24 * 14, _E2E_DAYS * 24))


def _log(msg: str) -> None:
    print(f"[e2e {time.strftime('%H:%M:%S')}] {msg}", flush=True)


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
    # 默认开启快速模式：跳过 Playwright 站点爬取（可在 .env 写 DIGEST_FAST_MODE=false 覆盖）
    os.environ.setdefault("DIGEST_FAST_MODE", "1")
    # RSS 单源超时（秒）：慢源早失败，避免长时间挂住
    os.environ.setdefault("RSS_HTTP_TIMEOUT", "12")
    _log(
        "env loaded; DIGEST_FAST_MODE="
        + os.environ.get("DIGEST_FAST_MODE", "")
        + " RSS_HTTP_TIMEOUT="
        + os.environ.get("RSS_HTTP_TIMEOUT", "")
    )


def main() -> int:
    _log(f"cwd={os.getcwd()} ROOT={ROOT}")
    load_env()
    _log("importing langgraph_agent.run_with_graph …")
    t_import = time.perf_counter()
    from ai_news_skill.orchestration.langgraph_agent import run_with_graph

    _log(f"import done in {time.perf_counter() - t_import:.2f}s")

    cfg = {
        "sources": str(ROOT / "sources.json"),
        "out": str(ROOT / "daily_docs"),
        "runs_dir": str(ROOT / "runs"),
        "limit": int(os.getenv("E2E_LIMIT", "8")),
        "window_hours": WINDOW_HOURS,
        "official_window_hours": WINDOW_HOURS,
        "max_paper_ratio": 0.35,
        # 0：避免再走一轮 official_backfill 的 collect_news
        "min_official_items": int(os.getenv("E2E_MIN_OFFICIAL", "0")),
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
        "gnews_enabled": bool((os.getenv("GNEWS_API_KEY") or "").strip()),
        "gnews_api_key": os.getenv("GNEWS_API_KEY", ""),
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
    # 本脚本默认跳过 GNews（易超时）；完整测 GNews 时：E2E_DISABLE_GNEWS=0
    if os.getenv("E2E_DISABLE_GNEWS", "1").strip().lower() in ("1", "true", "yes", "on"):
        cfg["gnews_enabled"] = False
        _log("E2E_DISABLE_GNEWS: GNews skipped for smoke speed")
    # 默认不拉 PUBLIC_API_FEEDS（多 HTTP，慢）；需要时 E2E_SKIP_PUBLIC_API=0
    if os.getenv("E2E_SKIP_PUBLIC_API", "1").strip().lower() in ("1", "true", "yes", "on"):
        cfg["allow_os_public_api_feeds"] = False
        cfg["public_api_feeds"] = ""
        _log("E2E_SKIP_PUBLIC_API: extra API feeds skipped")

    def _progress_cb(step: dict) -> None:
        st = step.get("status", "")
        name = step.get("name", "")
        detail = (step.get("detail") or "").strip()
        if len(detail) > 100:
            detail = detail[:97] + "..."
        _log(f"pipeline | {st:8} | {name} | {detail}")

    cfg["_progress_cb"] = _progress_cb

    _log(
        f"config: window_hours={WINDOW_HOURS} (E2E_WINDOW_DAYS={_E2E_DAYS}) limit={cfg['limit']} "
        f"intent=codex gnews={cfg.get('gnews_enabled')} public_api={cfg.get('allow_os_public_api_feeds')} "
        f"llm={cfg['use_llm']} provider={cfg['llm_provider']}"
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
        for i, e in enumerate(errs[:25]):
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
