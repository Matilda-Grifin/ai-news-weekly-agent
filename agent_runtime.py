#!/usr/bin/env python3
import json
import pathlib
import traceback
from datetime import datetime
from typing import Any

from run_daily_digest import (
    balance_items,
    cap_papers_by_ratio,
    collect_news,
    dedupe_items,
    enrich_items_with_llm,
    fetch_openclaw_stars_top,
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


def run_digest_pipeline(config: dict[str, Any]) -> dict[str, Any]:
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
    progress_cb = config.get("_progress_cb")

    def add_step(name: str, status: str, detail: str = "", extra: dict[str, Any] | None = None) -> None:
        step = {"name": name, "status": status, "detail": detail, "time": datetime.now().astimezone().isoformat()}
        if extra:
            step["extra"] = extra
        trace["steps"].append(step)
        if callable(progress_cb):
            try:
                progress_cb(step)
            except Exception:
                pass

    try:
        sources_path = pathlib.Path(config.get("sources", "sources.json")).resolve()
        add_step("load_sources", "started", f"reading {sources_path}")
        sources = load_sources(sources_path)
        add_step("load_sources", "ok", extra={"source_count": len(sources)})

        add_step("collect_news", "started")
        items, errors = collect_news(
            sources=sources,
            per_source=max(1, int(config.get("limit", 5))),
            allow_insecure_fallback=bool(config.get("allow_insecure_ssl", False)),
            window_hours=max(1, int(config.get("window_hours", 168))),
        )
        add_step("collect_news", "ok", extra={"items": len(items), "errors": len(errors)})

        min_official_items = max(0, int(config.get("min_official_items", 3)))
        official_count = len([x for x in items if x.get("category") == "官方发布"])
        if official_count < min_official_items:
            add_step("official_backfill", "started", f"official={official_count}, min={min_official_items}")
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
            add_step("official_backfill", "ok", extra={"items_after": len(items)})

        items = balance_items(
            items,
            max_paper_ratio=float(config.get("max_paper_ratio", 0.2)),
            min_official_items=min_official_items,
        )
        items = cap_papers_by_ratio(items, max_paper_ratio=float(config.get("max_paper_ratio", 0.2)))
        add_step("balance_items", "ok", extra={"items_after": len(items)})

        openclaw_top = []
        openclaw_focus = None
        openclaw_asof = ""
        try:
            add_step("openclaw", "started")
            openclaw_top, openclaw_focus, openclaw_asof = fetch_openclaw_stars_top(
                top_n=3,
                focus_skill=str(config.get("focus_skill", "")),
                allow_insecure_fallback=bool(config.get("allow_insecure_ssl", False)),
            )
            add_step("openclaw", "ok", extra={"top_count": len(openclaw_top)})
        except Exception as ex:  # noqa: BLE001
            errors.append(f"OpenClaw热榜: {type(ex).__name__} {ex}")
            add_step("openclaw", "warn", str(ex))

        llm_overview = ""
        llm_details: list[str] = []
        llm_titles_cn: list[str] = []

        if bool(config.get("use_llm", True)):
            add_step("llm_enrich", "started")
            try:
                runtime_config = _build_runtime_namespace(config)
                provider, base_url, api_key = resolve_llm_runtime(runtime_config)
                if not api_key:
                    errors.append("LLM: 未检测到可用 API Key（ARK_API_KEY 或 OPENAI_API_KEY）")
                    add_step("llm_enrich", "warn", "missing api key")
                else:
                    model = (
                        runtime_config.ark_endpoint_id
                        or runtime_config.ark_model
                        or "Doubao-Seed-1.6-lite"
                    )
                    llm_overview, llm_details, llm_titles_cn = enrich_items_with_llm(
                        items=items,
                        api_key=api_key,
                        model=model,
                        base_url=base_url,
                        allow_insecure_fallback=bool(config.get("allow_insecure_ssl", False)),
                    )
                    add_step("llm_enrich", "ok", extra={"provider": provider, "model": model})
            except Exception as ex:  # noqa: BLE001
                errors.append(f"LLM: {type(ex).__name__} {ex}")
                add_step("llm_enrich", "warn", f"{type(ex).__name__}: {ex}")

        md = render_markdown(
            date_label=now_local().strftime("%Y-%m-%d"),
            items=items,
            errors=errors,
            llm_overview=llm_overview,
            llm_details=llm_details,
            llm_titles_cn=llm_titles_cn,
            openclaw_top=openclaw_top,
            openclaw_focus=openclaw_focus,
            openclaw_asof=openclaw_asof,
        )
        doc_path = write_doc(output_dir, md)
        add_step("render_and_write", "ok", str(doc_path))

        ended = datetime.now().astimezone()
        trace.update(
            {
                "status": "ok",
                "ended_at": ended.isoformat(),
                "elapsed_seconds": round((ended - started).total_seconds(), 3),
                "result": {
                    "items": len(items),
                    "errors": errors,
                    "doc_path": str(doc_path),
                    "run_dir": str(run_dir),
                },
            }
        )
        trace_file.write_text(json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8")
        return trace["result"] | {"trace_file": str(trace_file), "run_id": run_id}
    except Exception as ex:  # noqa: BLE001
        ended = datetime.now().astimezone()
        trace.update(
            {
                "status": "failed",
                "ended_at": ended.isoformat(),
                "elapsed_seconds": round((ended - started).total_seconds(), 3),
                "error": f"{type(ex).__name__}: {ex}",
                "traceback": traceback.format_exc(),
            }
        )
        trace_file.write_text(json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8")
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
