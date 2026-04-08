#!/usr/bin/env python3
import json
import os
import pathlib
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any

from ai_news_skill.user_news_memory import (
    attach_memory_to_config,
    filter_sources_by_memory_exclusions,
    merge_memory_into_intent_plan,
    merge_memory_keywords,
    rerank_items_by_user_memory_boost,
)
from run_daily_digest import (
    attach_content_excerpts_to_items,
    balance_items,
    cap_items_per_category,
    cap_items_per_source,
    cap_papers_by_ratio,
    collect_news,
    dedupe_items,
    extract_intent_keywords,
    enrich_items_with_llm,
    fetch_gnews_articles,
    fetch_gnews_for_pipeline,
    fetch_openclaw_stars_top,
    infer_gnews_search_query,
    llm_extract_intent_search_queries,
    load_sources,
    now_local,
    render_markdown,
    resolve_llm_runtime,
    write_doc,
)


def resolve_strict_intent_match(config: dict[str, Any]) -> bool:
    """
    与 Streamlit 原「仅展示与搜索关键词相关的条目（严格）」一致，默认开启。
    不在前端暴露开关：未在 config 中指定时读环境变量 STRICT_INTENT_MATCH（默认 true）；
    需要放宽时在 .env 写 STRICT_INTENT_MATCH=false。
    """
    if "strict_intent_match" in config:
        return bool(config["strict_intent_match"])
    raw = (os.getenv("STRICT_INTENT_MATCH", "true") or "true").strip().lower()
    return raw not in ("0", "false", "no", "off")


def resolve_final_items_total(config: dict[str, Any]) -> int:
    """
    Final report item cap (all modes share the same limit).
    Priority: config.final_items_total -> env DIGEST_FINAL_ITEMS_TOTAL -> default 10.
    """
    raw = config.get("final_items_total", os.getenv("DIGEST_FINAL_ITEMS_TOTAL", 10))
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return 10


def _env_truthy(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in ("1", "true", "yes", "on")


def site_crawlers_effective(config: dict[str, Any]) -> bool:
    """
    是否执行 Playwright/站点列表爬取。关则只走 RSS（显著提速）。
    优先级：SITE_CRAWLERS_ENABLED=false → 关；DIGEST_FAST_MODE / DIGEST_FAST → 关；否则看 config。
    """
    raw_site = (os.getenv("SITE_CRAWLERS_ENABLED") or "").strip().lower()
    if raw_site in ("0", "false", "no", "off"):
        return False
    if _env_truthy("DIGEST_FAST_MODE") or _env_truthy("DIGEST_FAST"):
        return False
    return bool(config.get("site_crawlers_enabled", True))


def _safe_name(ts: datetime) -> str:
    return ts.strftime("%Y%m%d_%H%M%S")


def ensure_dir(path: pathlib.Path) -> pathlib.Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def init_run_context(config: dict[str, Any]) -> dict[str, Any]:
    started = datetime.now().astimezone()
    runs_dir = ensure_dir(pathlib.Path(config.get("runs_dir", "runs")).resolve())
    output_dir = ensure_dir(pathlib.Path(config.get("out", "daily_docs")).resolve())
    run_id = _safe_name(started)
    run_dir = ensure_dir(runs_dir / run_id)
    trace_file = run_dir / "trace.json"
    trace: dict[str, Any] = {
        "run_id": run_id,
        "started_at": started.isoformat(),
        "steps": [],
        "status": "running",
    }
    return {
        "started": started,
        "output_dir": output_dir,
        "run_id": run_id,
        "run_dir": run_dir,
        "trace_file": trace_file,
        "trace": trace,
    }


def _llm_model_from_config(config: dict[str, Any]) -> str:
    return (
        str(config.get("ark_endpoint_id", "")).strip()
        or os.getenv("ARK_ENDPOINT_ID", "").strip()
        or str(config.get("ark_model", "")).strip()
        or os.getenv("ARK_MODEL", "").strip()
        or os.getenv("OPENAI_MODEL", "").strip()
        or os.getenv("MODEL", "").strip()
        or "Doubao-Seed-1.6-lite"
    )


def _parse_query_list_json(raw: str) -> list[str]:
    txt = (raw or "").strip()
    if not txt:
        return []
    txt = re.sub(r"^```(?:json)?\s*", "", txt, flags=re.I | re.M)
    txt = re.sub(r"\s*```\s*$", "", txt, flags=re.M).strip()
    if not txt:
        return []
    try:
        data = json.loads(txt)
    except Exception:
        m = re.search(r"(\[[\s\S]*\]|\{[\s\S]*\})", txt)
        if not m:
            return []
        try:
            data = json.loads(m.group(1))
        except Exception:
            return []
    out: list[str] = []
    if isinstance(data, list):
        out = [str(x).strip() for x in data if str(x).strip()]
    elif isinstance(data, dict):
        arr = data.get("queries") or data.get("keywords") or []
        if isinstance(arr, list):
            out = [str(x).strip() for x in arr if str(x).strip()]
    dedup: list[str] = []
    seen: set[str] = set()
    for q in out:
        low = q.lower()
        if low in seen:
            continue
        seen.add(low)
        dedup.append(q)
    return dedup[:16]


def _expand_keywords_with_english_aliases(
    keywords: list[str],
    intent_text: str,
    config: dict[str, Any],
) -> list[str]:
    """
    Expand CN / mixed-language intent keywords to English search aliases.
    Fallback-safe: on any failure, return original keywords unchanged.
    """
    if not keywords:
        return keywords
    use_expand = (os.getenv("INTENT_KEYWORDS_EN_EXPAND", "true") or "true").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )
    if not use_expand:
        return keywords
    has_non_ascii = any(any(ord(ch) > 127 for ch in str(k)) for k in keywords) or any(
        ord(ch) > 127 for ch in (intent_text or "")
    )
    if not has_non_ascii:
        return keywords
    try:
        rt = _build_runtime_namespace(config)
        _provider, base_url, ak = resolve_llm_runtime(rt)
    except Exception:
        return keywords
    if not (ak or "").strip():
        return keywords
    sys_prompt = (
        "你是新闻检索词扩展助手。"
        "给定用户意图与已有关键词，补充可用于英文新闻搜索的英文/同义词短语。"
        "要求：保留原词含义，不要编造事实；输出 JSON，格式为"
        "{\"queries\":[\"...\"]}，仅数组，不要解释。"
    )
    user_prompt = (
        f"用户意图：{(intent_text or '').strip()}\n"
        f"已有关键词：{json.dumps(keywords, ensure_ascii=False)}\n"
        "请返回英文搜索扩展词（可含 multi-agent / agent framework 这类短语）。"
    )
    try:
        from run_daily_digest import call_chat_completion

        raw = call_chat_completion(
            api_key=ak.strip(),
            model=_llm_model_from_config(config),
            messages=[
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": user_prompt},
            ],
            base_url=base_url,
            timeout=30,
            allow_insecure_fallback=bool(config.get("allow_insecure_ssl", False)),
        )
    except Exception:
        return keywords
    extra = _parse_query_list_json(raw)
    if not extra:
        return keywords
    out = list(keywords)
    seen = {str(k).strip().lower() for k in out if str(k).strip()}
    for q in extra:
        t = str(q).strip()
        if not t:
            continue
        low = t.lower()
        if low in seen:
            continue
        out.append(t)
        seen.add(low)
    return out[:24]


def _worker_cap(config: dict[str, Any], key: str, default: int, env_key: str) -> int:
    """Parallel worker count from config or env (minimum 1)."""
    v = config.get(key)
    if v is not None:
        try:
            return max(1, int(v))
        except (TypeError, ValueError):
            pass
    try:
        return max(1, int(os.environ.get(env_key, str(default))))
    except ValueError:
        return default


def emit_step(config: dict[str, Any], trace: dict[str, Any], name: str, status: str, detail: str = "", extra: dict[str, Any] | None = None) -> None:
    step = {"name": name, "status": status, "detail": detail, "time": datetime.now().astimezone().isoformat()}
    if extra:
        step["extra"] = extra
    trace.setdefault("steps", []).append(step)
    if bool(config.get("pipeline_log", True)):
        from middleware.pipeline_middleware import log_pipeline_event

        log_pipeline_event(name, status, detail, extra)
    progress_cb = config.get("_progress_cb")
    if callable(progress_cb):
        try:
            progress_cb(step)
        except Exception:
            pass


def intent_plan_stage(config: dict[str, Any], trace: dict[str, Any]) -> dict[str, Any]:
    intent_text = str(config.get("intent_text", "")).strip()
    aug = str(config.get("_user_memory_intent_augment", "")).strip()
    intent_for_llm = intent_text if not aug else f"{intent_text}\n\n[用户长期偏好参考]\n{aug}"
    _t_plan = time.perf_counter()
    emit_step(config, trace, "intent_plan", "started", intent_text[:80])
    keywords: list[str] = []
    intent_llm_used = False
    if bool(config.get("llm_intent_analysis", True)) and intent_text.strip():
        try:
            rt = _build_runtime_namespace(config)
            _p, base_url, ak = resolve_llm_runtime(rt)
            if (ak or "").strip():
                model = _llm_model_from_config(config)
                kw = llm_extract_intent_search_queries(
                    intent_for_llm,
                    api_key=ak.strip(),
                    model=model,
                    base_url=base_url,
                    allow_insecure_fallback=bool(config.get("allow_insecure_ssl", False)),
                )
                if kw:
                    keywords = kw
                    intent_llm_used = True
        except Exception:
            pass
    if not keywords:
        keywords = extract_intent_keywords(intent_for_llm if aug else intent_text)
    keywords = _expand_keywords_with_english_aliases(keywords, intent_text, config)
    lower_kw = [k.lower() for k in keywords]
    plan: dict[str, Any] = {
        "keywords": keywords,
        "intent_llm_used": intent_llm_used,
        "prefer_categories": [],
        "prefer_source_name_keywords": [],
        "need_official_first": True,
    }
    if any(k in lower_kw for k in ["视频", "video", "multimodal", "多模态", "生成"]):
        plan["prefer_categories"] = ["官方发布", "开源与工具", "行业资讯"]
        plan["prefer_source_name_keywords"] = ["openai", "hugging", "anthropic", "venturebeat", "techcrunch"]
    elif any(k in lower_kw for k in ["论文", "paper", "arxiv"]):
        plan["prefer_categories"] = ["论文研究", "官方发布"]
    elif any(k in lower_kw for k in ["国内", "国产", "腾讯", "阿里", "字节"]):
        plan["prefer_categories"] = ["国内厂商动态", "官方发布"]
    merge_memory_into_intent_plan(plan, config)
    keywords, lower_kw = merge_memory_keywords(keywords, lower_kw, config)
    plan["keywords"] = keywords
    _plan_extra = dict(plan)
    _plan_extra["duration_ms"] = int((time.perf_counter() - _t_plan) * 1000)
    emit_step(config, trace, "intent_plan", "ok", extra=_plan_extra)
    return plan


def collect_stage(config: dict[str, Any], trace: dict[str, Any], intent_plan: dict[str, Any] | None = None) -> tuple[list[dict], list[dict], list[str], int]:
    sources_path = pathlib.Path(config.get("sources", "sources.json")).resolve()
    _t_ls = time.perf_counter()
    emit_step(config, trace, "load_sources", "started", f"reading {sources_path}")
    sources = load_sources(sources_path)
    sources = filter_sources_by_memory_exclusions(sources, config)
    emit_step(
        config,
        trace,
        "load_sources",
        "ok",
        extra={"source_count": len(sources), "duration_ms": int((time.perf_counter() - _t_ls) * 1000)},
    )
    planned_sources = list(sources)
    plan = intent_plan or {}
    intent_text = str(config.get("intent_text", ""))
    plan_kw = [str(x).strip() for x in (plan.get("keywords") or []) if str(x).strip()]
    if not plan_kw:
        plan_kw = extract_intent_keywords(intent_text)
    query_mode = str(config.get("_query_mode", "explicit") or "explicit").strip().lower()
    mem_kw = [str(x).strip() for x in (config.get("_user_memory_extra_keywords") or []) if str(x).strip()]
    prefer_categories = set(plan.get("prefer_categories", []) or [])
    prefer_source_kw = [str(x).lower() for x in (plan.get("prefer_source_name_keywords", []) or [])]
    if prefer_categories:
        filtered = [s for s in planned_sources if s.get("category", "") in prefer_categories]
        if filtered:
            planned_sources = filtered
    if prefer_source_kw:
        filtered = [
            s
            for s in planned_sources
            if any(k in str(s.get("name", "")).lower() for k in prefer_source_kw)
        ]
        if filtered:
            planned_sources = filtered
    emit_step(config, trace, "collect_plan", "ok", extra={"planned_sources": len(planned_sources)})

    errors: list[str] = []
    site_items: list[dict] = []
    site_crawled_source_names: set[str] = set()
    if not site_crawlers_effective(config):
        reason = "SITE_CRAWLERS_ENABLED=false" if (os.getenv("SITE_CRAWLERS_ENABLED") or "").strip().lower() in (
            "0",
            "false",
            "no",
            "off",
        ) else ("DIGEST_FAST" if (_env_truthy("DIGEST_FAST_MODE") or _env_truthy("DIGEST_FAST")) else "config")
        emit_step(config, trace, "site_crawl", "ok", extra={"skipped": True, "reason": reason})
    else:
        try:
            from ai_news_skill.crawlers.site.registry import crawler_by_source_name

            per_source = max(1, int(config.get("limit", 5)))
            window_h = max(1, int(config.get("window_hours", 168)))
            allow_cb = bool(config.get("allow_insecure_ssl", False))
            site_detail_cap = max(1, min(10, int(config.get("site_max_detail_items", 10))))
            crawl_jobs: list[tuple[str, Any]] = []
            for s in planned_sources:
                src_name = str(s.get("name", ""))
                c = crawler_by_source_name(src_name)
                if c:
                    crawl_jobs.append((src_name, c))

            sc_workers = _worker_cap(config, "site_crawl_max_workers", 4, "SITE_CRAWL_MAX_WORKERS")
            sc_workers = max(1, min(sc_workers, max(1, len(crawl_jobs))))

            def _run_site_crawl(job: tuple[str, Any]) -> tuple[list[dict], str | None, str]:
                """Returns (items_chunk, error_message_or_none, source_name_for_rss_skip)."""
                src_name, c = job
                emit_step(config, trace, "site_crawl", "started", src_name[:120])
                _t_sc = time.perf_counter()
                try:
                    # Prefer contextual crawl for crawlers that support site-search / memory-aware candidate narrowing.
                    if hasattr(c, "crawl_with_context"):
                        try:
                            got = c.crawl_with_context(
                                per_source=per_source,
                                window_hours=window_h,
                                allow_insecure_fallback=allow_cb,
                                intent_keywords=plan_kw,
                                memory_keywords=mem_kw,
                                query_mode=query_mode,
                                max_detail_items=site_detail_cap,
                            )
                        except TypeError:
                            got = c.crawl(
                                per_source=per_source,
                                window_hours=window_h,
                                allow_insecure_fallback=allow_cb,
                            )
                    else:
                        got = c.crawl(
                            per_source=per_source,
                            window_hours=window_h,
                            allow_insecure_fallback=allow_cb,
                        )
                except Exception as ex:
                    emit_step(
                        config,
                        trace,
                        "site_crawl",
                        "warn",
                        f"{c.name}: {type(ex).__name__}"[:200],
                        extra={"duration_ms": int((time.perf_counter() - _t_sc) * 1000)},
                    )
                    return [], f"SiteCrawler({c.name}): {type(ex).__name__} {str(ex)[:120]}", src_name
                chunk: list[dict] = []
                for it in got or []:
                    chunk.append(
                        {
                            "title": it.title,
                            "link": it.link,
                            "published": it.published,
                            "summary": it.summary,
                            "source": it.source,
                            "category": it.category,
                        }
                    )
                emit_step(
                    config,
                    trace,
                    "site_crawl",
                    "ok",
                    extra={
                        "source": c.name,
                        "items": len(got or []),
                        "duration_ms": int((time.perf_counter() - _t_sc) * 1000),
                    },
                )
                return chunk, None, src_name

            if len(crawl_jobs) <= 1 or sc_workers <= 1:
                for job in crawl_jobs:
                    chunk, err_msg, src_name = _run_site_crawl(job)
                    if err_msg:
                        errors.append(err_msg)
                    else:
                        site_items.extend(chunk)
                        site_crawled_source_names.add(src_name)
            else:
                with ThreadPoolExecutor(max_workers=sc_workers) as pool:
                    futs = [pool.submit(_run_site_crawl, job) for job in crawl_jobs]
                    for fut in as_completed(futs):
                        chunk, err_msg, src_name = fut.result()
                        if err_msg:
                            errors.append(err_msg)
                        else:
                            site_items.extend(chunk)
                            site_crawled_source_names.add(src_name)
        except Exception:
            pass

    emit_step(config, trace, "collect_news", "started")
    _t_collect_start = time.perf_counter()
    rss_sources = [s for s in planned_sources if str(s.get("name", "")) not in site_crawled_source_names]

    def _on_rss_source(name: str, elapsed: float, n_items: int, src_errs: list[str]) -> None:
        emit_step(
            config,
            trace,
            "rss_source",
            "ok",
            name[:200],
            extra={
                "duration_ms": int(round(elapsed * 1000)),
                "seconds": round(elapsed, 3),
                "items": n_items,
                "ok": not src_errs,
                "error_preview": (src_errs[0][:400] if src_errs else ""),
            },
        )

    _t_rss_wall = time.perf_counter()
    rss_items, rss_errors = collect_news(
        sources=rss_sources,
        per_source=max(1, int(config.get("limit", 5))),
        allow_insecure_fallback=bool(config.get("allow_insecure_ssl", False)),
        window_hours=max(1, int(config.get("window_hours", 168))),
        max_parallel=_worker_cap(config, "collect_news_max_workers", 12, "COLLECT_NEWS_MAX_WORKERS"),
        rss_source_hook=_on_rss_source,
    )
    errors.extend(rss_errors)
    emit_step(
        config,
        trace,
        "rss_fetch",
        "ok",
        extra={
            "sources": len(rss_sources),
            "rss_items": len(rss_items),
            "rss_errs": len(rss_errors),
            "wall_ms": int((time.perf_counter() - _t_rss_wall) * 1000),
        },
    )
    items = dedupe_items(list(site_items) + list(rss_items))
    gnews_key = (
        str(config.get("gnews_api_key", "")).strip() or os.getenv("GNEWS_API_KEY", "").strip()
    )
    window_h = max(1, int(config.get("window_hours", 168)))
    gnews_max = max(1, int(config.get("gnews_max", config.get("limit", 10))))
    glang = str(config.get("gnews_lang", "en"))
    gcat = str(config.get("gnews_category", "行业资讯"))

    if bool(config.get("gnews_enabled", False)) and gnews_key:
        allow_cb = bool(config.get("allow_insecure_ssl", False))
        gnews_per_keyword = bool(config.get("gnews_per_keyword", True))
        max_kw = max(1, int(config.get("gnews_per_keyword_max", 8)))
        qlist = plan_kw[:max_kw]

        if gnews_per_keyword and qlist:
            emit_step(
                config,
                trace,
                "gnews_search",
                "started",
                f"per-keyword ×{len(qlist)}: " + " | ".join(qlist[:5]),
            )
            _t_gnews = time.perf_counter()
            per_q = max(1, min(15, gnews_max // max(1, len(qlist)) + 1))
            merged_g: list[dict] = []
            gerr_last: str | None = None
            gn_workers = _worker_cap(config, "gnews_max_workers", 6, "GNEWS_MAX_WORKERS")
            gn_workers = max(1, min(gn_workers, len(qlist)))

            def _one_gnews(kw: str) -> tuple[list[dict], str | None]:
                return fetch_gnews_articles(
                    query=kw[:500],
                    api_key=gnews_key,
                    window_hours=window_h,
                    max_articles=per_q,
                    lang=glang,
                    category=gcat,
                    allow_insecure_fallback=allow_cb,
                )

            if gn_workers <= 1:
                for kw in qlist:
                    chunk, err = _one_gnews(kw)
                    merged_g.extend(chunk)
                    if err:
                        gerr_last = err
            else:
                with ThreadPoolExecutor(max_workers=gn_workers) as pool:
                    futs = [pool.submit(_one_gnews, kw) for kw in qlist]
                    for fut in as_completed(futs):
                        chunk, err = fut.result()
                        merged_g.extend(chunk)
                        if err:
                            gerr_last = err
            items = dedupe_items(merged_g + items)
            if gerr_last:
                errors = list(errors)
                errors.append(gerr_last)
                emit_step(
                    config,
                    trace,
                    "gnews_search",
                    "warn",
                    gerr_last[:200],
                    extra={"duration_ms": int((time.perf_counter() - _t_gnews) * 1000)},
                )
            else:
                emit_step(
                    config,
                    trace,
                    "gnews_search",
                    "ok",
                    extra={
                        "gnews_items": len(merged_g),
                        "gnews_queries": qlist,
                        "duration_ms": int((time.perf_counter() - _t_gnews) * 1000),
                    },
                )
        else:
            resolved_q = ""
            try:
                rt = _build_runtime_namespace(config)
                _provider, base_url, ak = resolve_llm_runtime(rt)
                resolved_q = infer_gnews_search_query(
                    intent_text,
                    api_key=ak,
                    model=_llm_model_from_config(config),
                    base_url=base_url,
                    allow_insecure_fallback=allow_cb,
                )
            except Exception:
                resolved_q = infer_gnews_search_query(
                    intent_text,
                    api_key=None,
                    model="",
                    base_url="",
                    allow_insecure_fallback=allow_cb,
                )
            emit_step(
                config,
                trace,
                "gnews_search",
                "started",
                (resolved_q[:120] if resolved_q else "GNews API search"),
            )
            _t_gnews_single = time.perf_counter()
            kw = plan.get("keywords") or extract_intent_keywords(intent_text)
            gitems, gerr = fetch_gnews_for_pipeline(
                intent_text=intent_text,
                plan_keywords=kw,
                api_key=gnews_key,
                gnews_query=resolved_q,
                window_hours=window_h,
                max_articles=gnews_max,
                lang=glang,
                category=gcat,
                allow_insecure_fallback=allow_cb,
            )
            if gerr:
                errors = list(errors)
                errors.append(gerr)
                emit_step(
                    config,
                    trace,
                    "gnews_search",
                    "warn",
                    gerr[:200],
                    extra={"duration_ms": int((time.perf_counter() - _t_gnews_single) * 1000)},
                )
            else:
                items = dedupe_items(gitems + items)
                emit_step(
                    config,
                    trace,
                    "gnews_search",
                    "ok",
                    extra={
                        "gnews_items": len(gitems),
                        "gnews_q": (resolved_q or "")[:160],
                        "duration_ms": int((time.perf_counter() - _t_gnews_single) * 1000),
                    },
                )
    elif bool(config.get("gnews_enabled", False)) and not gnews_key:
        errors = list(errors)
        errors.append("GNews: 已启用但未配置 gnews_api_key 或环境变量 GNEWS_API_KEY")

    _paf_raw = str(config.get("public_api_feeds", "")).strip()
    if bool(config.get("allow_os_public_api_feeds", True)) and not _paf_raw:
        _paf_raw = os.getenv("PUBLIC_API_FEEDS", "").strip()
    if _paf_raw:
        try:
            from ai_news_skill.integrations.public_api_feeds import collect_public_api_feed_items

            pub_max = config.get("public_api_feed_max")
            try:
                pub_max_i = int(pub_max) if pub_max is not None else None
            except (TypeError, ValueError):
                pub_max_i = None
            api_extra, api_errs = collect_public_api_feed_items(
                window_hours=window_h,
                allow_insecure_fallback=bool(config.get("allow_insecure_ssl", False)),
                config=config,
                max_per_feed=pub_max_i,
            )
            if api_errs:
                errors = list(errors)
                errors.extend(api_errs)
                emit_step(config, trace, "public_api_feeds", "warn", "; ".join(api_errs)[:200])
            if api_extra:
                items = dedupe_items(api_extra + items)
                emit_step(
                    config,
                    trace,
                    "public_api_feeds",
                    "ok",
                    extra={"items": len(api_extra), "feeds": "see PUBLIC_API_FEEDS"},
                )
        except Exception as ex:  # noqa: BLE001
            emit_step(config, trace, "public_api_feeds", "warn", f"{type(ex).__name__}: {str(ex)[:120]}")

    keywords = [str(k).lower() for k in plan_kw]

    def _intent_hay(it: dict) -> str:
        return " ".join(
            [
                str(it.get("title", "")),
                str(it.get("summary", "")),
                str(it.get("source", "")),
                str(it.get("category", "")),
                str(it.get("link", "")),
            ]
        ).lower()

    def _ai_context_ok(hay: str) -> bool:
        ai_terms = (
            " ai ",
            "artificial intelligence",
            "llm",
            "gpt",
            "chatgpt",
            "claude",
            "anthropic",
            "openai",
            "ai model",
            "language model",
            "foundation model",
            "agentic",
            "multi-agent",
            "multi agent",
            "智能体",
            "多智能体",
            "大模型",
            "模型",
            "推理",
            "机器学习",
        )
        h = f" {hay} "
        return any(t in h for t in ai_terms)

    def _is_agent_like_keyword(ks: list[str]) -> bool:
        for k in ks:
            kk = (k or "").strip().lower()
            if not kk:
                continue
            if kk in ("agent", "agents", "智能体", "多智能体"):
                return True
            if "multi-agent" in kk or "multi agent" in kk:
                return True
        return False

    if keywords and items and query_mode == "explicit":
        agent_guard = _is_agent_like_keyword(keywords)
        matched = []
        for it in items:
            hay = _intent_hay(it)
            if not any(k in hay for k in keywords):
                continue
            if agent_guard and not _ai_context_ok(hay):
                continue
            matched.append(it)
        if not matched and gnews_key and bool(config.get("gnews_enabled", False)):
            emit_step(
                config,
                trace,
                "gnews_intent_gap",
                "started",
                "OR keywords: " + ",".join(keywords[:6]),
            )
            _t_gnews_gap = time.perf_counter()
            q_gap = " OR ".join(keywords[:8])
            gitems2, gerr2 = fetch_gnews_for_pipeline(
                intent_text=intent_text,
                plan_keywords=plan_kw if plan_kw else extract_intent_keywords(intent_text),
                api_key=gnews_key,
                gnews_query=q_gap,
                window_hours=max(1, int(config.get("window_hours", 168))),
                max_articles=max(1, int(config.get("gnews_max", config.get("limit", 10)))),
                lang=str(config.get("gnews_lang", "en")),
                category=str(config.get("gnews_category", "行业资讯")),
                allow_insecure_fallback=bool(config.get("allow_insecure_ssl", False)),
            )
            if gerr2:
                errors = list(errors)
                errors.append(gerr2)
                emit_step(
                    config,
                    trace,
                    "gnews_intent_gap",
                    "warn",
                    gerr2[:200],
                    extra={"duration_ms": int((time.perf_counter() - _t_gnews_gap) * 1000)},
                )
            elif gitems2:
                items = dedupe_items(gitems2 + items)
                emit_step(
                    config,
                    trace,
                    "gnews_intent_gap",
                    "ok",
                    extra={
                        "added": len(gitems2),
                        "duration_ms": int((time.perf_counter() - _t_gnews_gap) * 1000),
                    },
                )
            matched = [it for it in items if any(k in _intent_hay(it) for k in keywords)]

        strict = resolve_strict_intent_match(config)
        if matched:
            items = matched
            emit_step(config, trace, "collect_filter", "ok", extra={"keywords": keywords, "items_after": len(items)})
        elif strict:
            items = []
            errors = list(errors)
            errors.append(
                f"严格过滤：未找到与关键词 {', '.join(keywords[:8])} 相关的条目（已丢弃不相关内容）。"
                "可放宽时间窗口、开启 GNews，或调整搜索词。"
            )
            emit_step(
                config,
                trace,
                "collect_filter",
                "warn",
                extra={"keywords": keywords, "items_after": 0},
            )
        else:
            errors = list(errors)
            errors.append(
                f"意图提示：未匹配到关键词 {', '.join(keywords[:6])}，以下为未严格过滤的资讯。"
            )
            emit_step(
                config,
                trace,
                "collect_filter",
                "warn",
                extra={"keywords": keywords, "items_after": len(items)},
            )
    elif keywords and items:
        # Generic browse mode should preserve broad recall and avoid hard intent filtering.
        emit_step(
            config,
            trace,
            "collect_filter",
            "ok",
            extra={"mode": "generic_skip_strict_filter", "keywords": keywords[:8], "items_after": len(items)},
        )
    emit_step(
        config,
        trace,
        "collect_news",
        "ok",
        extra={
            "items": len(items),
            "errors": len(errors),
            "duration_ms": int((time.perf_counter() - _t_collect_start) * 1000),
        },
    )

    min_official_items = max(0, int(config.get("min_official_items", 3)))
    official_count = len([x for x in items if x.get("category") == "官方发布"])
    keywords_for_skip = [str(k).lower() for k in (plan.get("keywords", []) or [])]
    strict_mode = resolve_strict_intent_match(config) and bool(keywords_for_skip)
    if official_count < min_official_items and not strict_mode:
        _t_ob = time.perf_counter()
        emit_step(config, trace, "official_backfill", "started", f"official={official_count}, min={min_official_items}")
        official_sources = [s for s in sources if s.get("category") == "官方发布"]
        if official_sources:
            more_items, more_errors = collect_news(
                sources=official_sources,
                per_source=max(1, int(config.get("limit", 5))),
                allow_insecure_fallback=bool(config.get("allow_insecure_ssl", False)),
                window_hours=max(
                    int(config.get("window_hours", 168)),
                    int(config.get("official_window_hours", 168)),
                ),
                rss_source_hook=lambda n, el, ni, er: _on_rss_source(f"[official_backfill] {n}", el, ni, er),
            )
            items = dedupe_items(items + more_items)
            for err in more_errors:
                if err not in errors:
                    errors.append(err)
        emit_step(
            config,
            trace,
            "official_backfill",
            "ok",
            extra={"items_after": len(items), "duration_ms": int((time.perf_counter() - _t_ob) * 1000)},
        )

    cap_max = max(1, int(config.get("items_per_category_max", 5)))
    items = cap_items_per_category(items, max_per=cap_max)
    emit_step(config, trace, "cap_per_category", "ok", extra={"max_per": cap_max, "items_after": len(items)})

    return sources, items, errors, min_official_items


def intent_stage(config: dict[str, Any], trace: dict[str, Any], items: list[dict], min_official_items: int) -> tuple[list[dict], list[str]]:
    items = balance_items(
        items,
        max_paper_ratio=float(config.get("max_paper_ratio", 0.2)),
        min_official_items=min_official_items,
    )
    items = cap_papers_by_ratio(items, max_paper_ratio=float(config.get("max_paper_ratio", 0.2)))
    emit_step(config, trace, "balance_items", "ok", extra={"items_after": len(items)})
    before_r = len(items)
    items = rerank_items_by_user_memory_boost(items, config)
    if config.get("_query_mode") == "generic" and (config.get("_user_memory_boost_keywords") or config.get("_user_memory_boost_categories")):
        emit_step(
            config,
            trace,
            "intent_rank",
            "ok",
            extra={"mode": "user_memory_boost", "items": before_r},
        )
    source_cap = max(1, int(config.get("items_per_source_max", 1)))
    if source_cap > 0 and items:
        items = cap_items_per_source(items, max_per_source=source_cap)
        emit_step(config, trace, "source_cap", "ok", extra={"max_per_source": source_cap, "items_after": len(items)})
    final_cap = resolve_final_items_total(config)
    if len(items) > final_cap:
        items = items[:final_cap]
    emit_step(config, trace, "final_cap", "ok", extra={"max_items": final_cap, "items_after": len(items)})
    return items, []


def openclaw_stage(config: dict[str, Any], trace: dict[str, Any], errors: list[str]) -> tuple[list[dict], dict | None, str, list[str]]:
    openclaw_top: list[dict] = []
    openclaw_focus: dict | None = None
    openclaw_asof = ""
    if not (bool(config.get("enable_openclaw", False)) or str(config.get("focus_skill", "")).strip()):
        return openclaw_top, openclaw_focus, openclaw_asof, errors
    try:
        emit_step(config, trace, "openclaw", "started", "检测到用户请求，调用 openclaw 排行 skill")
        _t_oc = time.perf_counter()
        openclaw_top, openclaw_focus, openclaw_asof = fetch_openclaw_stars_top(
            top_n=3,
            focus_skill=str(config.get("focus_skill", "")),
            allow_insecure_fallback=bool(config.get("allow_insecure_ssl", False)),
        )
        emit_step(
            config,
            trace,
            "openclaw",
            "ok",
            extra={"top_count": len(openclaw_top), "duration_ms": int((time.perf_counter() - _t_oc) * 1000)},
        )
    except Exception as ex:  # noqa: BLE001
        errors = list(errors)
        errors.append(f"OpenClaw热榜: {type(ex).__name__} {ex}")
        emit_step(
            config,
            trace,
            "openclaw",
            "warn",
            str(ex),
            extra={"duration_ms": int((time.perf_counter() - _t_oc) * 1000)},
        )
    return openclaw_top, openclaw_focus, openclaw_asof, errors


def enrich_stage(config: dict[str, Any], trace: dict[str, Any], items: list[dict], errors: list[str]) -> tuple[str, list[str], list[str], list[str], list[str]]:
    llm_overview = ""
    llm_core_points: list[str] = []
    llm_values: list[str] = []
    llm_titles_cn: list[str] = []
    allow_cb = bool(config.get("allow_insecure_ssl", False))
    if items:
        emit_step(config, trace, "fetch_article_body", "started", "trafilatura / 正文兜底")
        _t_ex = time.perf_counter()
        attach_content_excerpts_to_items(
            items,
            allow_cb,
            max_parallel=_worker_cap(config, "excerpt_fetch_max_workers", 12, "EXCERPT_FETCH_MAX_WORKERS"),
        )
        emit_step(
            config,
            trace,
            "fetch_article_body",
            "ok",
            extra={"items": len(items), "duration_ms": int((time.perf_counter() - _t_ex) * 1000)},
        )

    if not bool(config.get("use_llm", True)):
        return llm_overview, llm_core_points, llm_values, llm_titles_cn, errors

    emit_step(config, trace, "llm_enrich", "started")
    _t_llm = time.perf_counter()
    try:
        runtime_config = _build_runtime_namespace(config)
        provider, base_url, api_key = resolve_llm_runtime(runtime_config)
        if not api_key:
            errors = list(errors)
            errors.append("LLM: 未检测到可用 API Key（ARK_API_KEY 或 OPENAI_API_KEY）")
            emit_step(
                config,
                trace,
                "llm_enrich",
                "warn",
                "missing api key",
                extra={"duration_ms": int((time.perf_counter() - _t_llm) * 1000)},
            )
        else:
            model = runtime_config.ark_endpoint_id or runtime_config.ark_model or "Doubao-Seed-1.6-lite"

            def _do_enrich() -> tuple[str, list[str], list[str], list[str]]:
                return enrich_items_with_llm(
                    items=items,
                    api_key=api_key,
                    model=model,
                    base_url=base_url,
                    allow_insecure_fallback=allow_cb,
                    prompt_variant=str(config.get("llm_prompt_variant", "auto")),
                    intent_text=str(config.get("intent_text", "")),
                    enable_openclaw=bool(config.get("enable_openclaw", False)),
                )

            if bool(config.get("pipeline_log", True)):
                from middleware.pipeline_middleware import timed_call

                llm_overview, llm_core_points, llm_values, llm_titles_cn = timed_call("llm_enrich", _do_enrich)
            else:
                llm_overview, llm_core_points, llm_values, llm_titles_cn = _do_enrich()
            emit_step(
                config,
                trace,
                "llm_enrich",
                "ok",
                extra={
                    "provider": provider,
                    "model": model,
                    "duration_ms": int((time.perf_counter() - _t_llm) * 1000),
                },
            )
    except Exception as ex:  # noqa: BLE001
        errors = list(errors)
        msg = f"{type(ex).__name__} {ex}"
        hint = ""
        if "404" in msg:
            hint = "（Ark 通常需使用 ARK_ENDPOINT_ID=ep-xxx 而非模型展示名）"
        errors.append(f"LLM: {msg}{hint}")
        emit_step(
            config,
            trace,
            "llm_enrich",
            "warn",
            f"{type(ex).__name__}: {ex}",
            extra={"duration_ms": int((time.perf_counter() - _t_llm) * 1000)},
        )
    return llm_overview, llm_core_points, llm_values, llm_titles_cn, errors


def write_stage(
    config: dict[str, Any],
    trace: dict[str, Any],
    output_dir: pathlib.Path,
    items: list[dict],
    errors: list[str],
    llm_overview: str,
    llm_core_points: list[str],
    llm_values: list[str],
    llm_titles_cn: list[str],
    openclaw_top: list[dict],
    openclaw_focus: dict | None,
    openclaw_asof: str,
) -> pathlib.Path:
    _t_rw = time.perf_counter()
    md = render_markdown(
        date_label=now_local().strftime("%Y-%m-%d"),
        items=items,
        errors=errors,
        llm_overview=llm_overview,
        llm_core_points=llm_core_points,
        llm_values=llm_values,
        llm_titles_cn=llm_titles_cn,
        openclaw_top=openclaw_top,
        openclaw_focus=openclaw_focus,
        openclaw_asof=openclaw_asof,
    )
    doc_path = write_doc(output_dir, md)
    emit_step(
        config,
        trace,
        "render_and_write",
        "ok",
        str(doc_path),
        extra={"duration_ms": int((time.perf_counter() - _t_rw) * 1000)},
    )
    return doc_path


def finalize_ok(trace: dict[str, Any], trace_file: pathlib.Path, started: datetime, result: dict[str, Any]) -> None:
    ended = datetime.now().astimezone()
    trace.update(
        {
            "status": "ok",
            "ended_at": ended.isoformat(),
            "elapsed_seconds": round((ended - started).total_seconds(), 3),
            "result": result,
        }
    )
    trace_file.write_text(json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8")


def finalize_failed(trace: dict[str, Any], trace_file: pathlib.Path, started: datetime, error: str) -> None:
    ended = datetime.now().astimezone()
    trace.update(
        {
            "status": "failed",
            "ended_at": ended.isoformat(),
            "elapsed_seconds": round((ended - started).total_seconds(), 3),
            "error": error,
        }
    )
    trace_file.write_text(json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8")


def run_digest_pipeline(config: dict[str, Any]) -> dict[str, Any]:
    try:
        src_p = pathlib.Path(config.get("sources", "sources.json")).resolve()
        attach_memory_to_config(config, src_p.parent)
    except Exception:
        pass
    ctx = init_run_context(config)
    started = ctx["started"]
    trace = ctx["trace"]
    trace_file = ctx["trace_file"]
    output_dir = ctx["output_dir"]
    run_id = str(ctx["run_id"])
    run_dir = str(ctx["run_dir"])
    try:
        plan = intent_plan_stage(config, trace)
        _, items, errors, min_official_items = collect_stage(config, trace, intent_plan=plan)
        items, _ = intent_stage(config, trace, items, min_official_items)
        openclaw_top, openclaw_focus, openclaw_asof, errors = openclaw_stage(config, trace, errors)
        llm_overview, llm_core_points, llm_values, llm_titles_cn, errors = enrich_stage(config, trace, items, errors)
        doc_path = write_stage(
            config=config,
            trace=trace,
            output_dir=output_dir,
            items=items,
            errors=errors,
            llm_overview=llm_overview,
            llm_core_points=llm_core_points,
            llm_values=llm_values,
            llm_titles_cn=llm_titles_cn,
            openclaw_top=openclaw_top,
            openclaw_focus=openclaw_focus,
            openclaw_asof=openclaw_asof,
        )
        result = {
            "items": len(items),
            "errors": errors,
            "doc_path": str(doc_path),
            "run_dir": run_dir,
            "trace_file": str(trace_file),
            "run_id": run_id,
        }
        finalize_ok(trace, trace_file, started, result)
        return result
    except Exception as ex:  # noqa: BLE001
        finalize_failed(trace, trace_file, started, f"{type(ex).__name__}: {ex}")
        raise


def _build_runtime_namespace(config: dict[str, Any]) -> Any:
    class NS:
        llm_provider = str(config.get("llm_provider", "auto"))
        llm_base_url = str(config.get("llm_base_url", ""))
        allow_custom_llm_endpoint = bool(config.get("allow_custom_llm_endpoint", False))
        ark_api_key = str(config.get("ark_api_key", "")).strip()
        ark_endpoint_id = str(config.get("ark_endpoint_id", "")).strip()
        ark_model = str(config.get("ark_model", "Doubao-Seed-1.6-lite")).strip()

    return NS()

