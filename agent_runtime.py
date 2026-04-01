#!/usr/bin/env python3
import json
import os
import pathlib
from datetime import datetime
from typing import Any

from run_daily_digest import (
    attach_content_excerpts_to_items,
    balance_items,
    cap_papers_by_ratio,
    collect_news,
    dedupe_items,
    extract_intent_keywords,
    enrich_items_with_llm,
    fetch_gnews_for_pipeline,
    fetch_openclaw_stars_top,
    infer_gnews_search_query,
    load_sources,
    now_local,
    render_markdown,
    resolve_llm_runtime,
    write_doc,
)


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
    emit_step(config, trace, "intent_plan", "started", intent_text[:80])
    keywords = extract_intent_keywords(intent_text)
    lower_kw = [k.lower() for k in keywords]
    plan: dict[str, Any] = {
        "keywords": keywords,
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
    emit_step(config, trace, "intent_plan", "ok", extra=plan)
    return plan


def collect_stage(config: dict[str, Any], trace: dict[str, Any], intent_plan: dict[str, Any] | None = None) -> tuple[list[dict], list[dict], list[str], int]:
    sources_path = pathlib.Path(config.get("sources", "sources.json")).resolve()
    emit_step(config, trace, "load_sources", "started", f"reading {sources_path}")
    sources = load_sources(sources_path)
    emit_step(config, trace, "load_sources", "ok", extra={"source_count": len(sources)})
    planned_sources = list(sources)
    plan = intent_plan or {}
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

    emit_step(config, trace, "collect_news", "started")
    intent_text = str(config.get("intent_text", ""))
    rss_items, errors = collect_news(
        sources=planned_sources,
        per_source=max(1, int(config.get("limit", 5))),
        allow_insecure_fallback=bool(config.get("allow_insecure_ssl", False)),
        window_hours=max(1, int(config.get("window_hours", 168))),
    )
    items = list(rss_items)
    gnews_key = (
        str(config.get("gnews_api_key", "")).strip() or os.getenv("GNEWS_API_KEY", "").strip()
    )
    if bool(config.get("gnews_enabled", False)) and gnews_key:
        allow_cb = bool(config.get("allow_insecure_ssl", False))
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
        kw = plan.get("keywords") or extract_intent_keywords(intent_text)
        gitems, gerr = fetch_gnews_for_pipeline(
            intent_text=intent_text,
            plan_keywords=kw,
            api_key=gnews_key,
            gnews_query=resolved_q,
            window_hours=max(1, int(config.get("window_hours", 168))),
            max_articles=max(1, int(config.get("gnews_max", config.get("limit", 10)))),
            lang=str(config.get("gnews_lang", "en")),
            category=str(config.get("gnews_category", "行业资讯")),
            allow_insecure_fallback=allow_cb,
        )
        if gerr:
            errors = list(errors)
            errors.append(gerr)
            emit_step(config, trace, "gnews_search", "warn", gerr[:200])
        else:
            # GNews 在前：去重时优先保留 GNews 条目顺序
            items = dedupe_items(gitems + items)
            emit_step(
                config,
                trace,
                "gnews_search",
                "ok",
                extra={"gnews_items": len(gitems), "gnews_q": (resolved_q or "")[:160]},
            )
    elif bool(config.get("gnews_enabled", False)) and not gnews_key:
        errors = list(errors)
        errors.append("GNews: 已启用但未配置 gnews_api_key 或环境变量 GNEWS_API_KEY")

    keywords = [str(k).lower() for k in (plan.get("keywords", []) or [])]

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

    if keywords and items:
        matched = [it for it in items if any(k in _intent_hay(it) for k in keywords)]
        if not matched and gnews_key and bool(config.get("gnews_enabled", False)):
            emit_step(
                config,
                trace,
                "gnews_intent_gap",
                "started",
                "OR keywords: " + ",".join(keywords[:6]),
            )
            q_gap = " OR ".join(keywords[:8])
            gitems2, gerr2 = fetch_gnews_for_pipeline(
                intent_text=intent_text,
                plan_keywords=plan.get("keywords") or extract_intent_keywords(intent_text),
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
                emit_step(config, trace, "gnews_intent_gap", "warn", gerr2[:200])
            elif gitems2:
                items = dedupe_items(gitems2 + items)
                emit_step(
                    config,
                    trace,
                    "gnews_intent_gap",
                    "ok",
                    extra={"added": len(gitems2)},
                )
            matched = [it for it in items if any(k in _intent_hay(it) for k in keywords)]

        strict = bool(config.get("strict_intent_match", True))
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
    emit_step(config, trace, "collect_news", "ok", extra={"items": len(items), "errors": len(errors)})

    min_official_items = max(0, int(config.get("min_official_items", 3)))
    official_count = len([x for x in items if x.get("category") == "官方发布"])
    keywords_for_skip = [str(k).lower() for k in (plan.get("keywords", []) or [])]
    strict_mode = bool(config.get("strict_intent_match", True)) and bool(keywords_for_skip)
    if official_count < min_official_items and not strict_mode:
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
            )
            items = dedupe_items(items + more_items)
            for err in more_errors:
                if err not in errors:
                    errors.append(err)
        emit_step(config, trace, "official_backfill", "ok", extra={"items_after": len(items)})
    return sources, items, errors, min_official_items


def intent_stage(config: dict[str, Any], trace: dict[str, Any], items: list[dict], min_official_items: int) -> tuple[list[dict], list[str]]:
    items = balance_items(
        items,
        max_paper_ratio=float(config.get("max_paper_ratio", 0.2)),
        min_official_items=min_official_items,
    )
    items = cap_papers_by_ratio(items, max_paper_ratio=float(config.get("max_paper_ratio", 0.2)))
    emit_step(config, trace, "balance_items", "ok", extra={"items_after": len(items)})
    return items, []


def openclaw_stage(config: dict[str, Any], trace: dict[str, Any], errors: list[str]) -> tuple[list[dict], dict | None, str, list[str]]:
    openclaw_top: list[dict] = []
    openclaw_focus: dict | None = None
    openclaw_asof = ""
    if not (bool(config.get("enable_openclaw", False)) or str(config.get("focus_skill", "")).strip()):
        return openclaw_top, openclaw_focus, openclaw_asof, errors
    try:
        emit_step(config, trace, "openclaw", "started", "检测到用户请求，调用 openclaw 排行 skill")
        openclaw_top, openclaw_focus, openclaw_asof = fetch_openclaw_stars_top(
            top_n=3,
            focus_skill=str(config.get("focus_skill", "")),
            allow_insecure_fallback=bool(config.get("allow_insecure_ssl", False)),
        )
        emit_step(config, trace, "openclaw", "ok", extra={"top_count": len(openclaw_top)})
    except Exception as ex:  # noqa: BLE001
        errors = list(errors)
        errors.append(f"OpenClaw热榜: {type(ex).__name__} {ex}")
        emit_step(config, trace, "openclaw", "warn", str(ex))
    return openclaw_top, openclaw_focus, openclaw_asof, errors


def enrich_stage(config: dict[str, Any], trace: dict[str, Any], items: list[dict], errors: list[str]) -> tuple[str, list[str], list[str], list[str], list[str]]:
    llm_overview = ""
    llm_core_points: list[str] = []
    llm_values: list[str] = []
    llm_titles_cn: list[str] = []
    allow_cb = bool(config.get("allow_insecure_ssl", False))
    if items:
        emit_step(config, trace, "fetch_article_body", "started", "trafilatura / 正文兜底")
        attach_content_excerpts_to_items(items, allow_cb)
        emit_step(config, trace, "fetch_article_body", "ok", extra={"items": len(items)})

    if not bool(config.get("use_llm", True)):
        return llm_overview, llm_core_points, llm_values, llm_titles_cn, errors

    emit_step(config, trace, "llm_enrich", "started")
    try:
        runtime_config = _build_runtime_namespace(config)
        provider, base_url, api_key = resolve_llm_runtime(runtime_config)
        if not api_key:
            errors = list(errors)
            errors.append("LLM: 未检测到可用 API Key（ARK_API_KEY 或 OPENAI_API_KEY）")
            emit_step(config, trace, "llm_enrich", "warn", "missing api key")
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
            emit_step(config, trace, "llm_enrich", "ok", extra={"provider": provider, "model": model})
    except Exception as ex:  # noqa: BLE001
        errors = list(errors)
        msg = f"{type(ex).__name__} {ex}"
        hint = ""
        if "404" in msg:
            hint = "（Ark 通常需使用 ARK_ENDPOINT_ID=ep-xxx 而非模型展示名）"
        errors.append(f"LLM: {msg}{hint}")
        emit_step(config, trace, "llm_enrich", "warn", f"{type(ex).__name__}: {ex}")
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
    emit_step(config, trace, "render_and_write", "ok", str(doc_path))
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
