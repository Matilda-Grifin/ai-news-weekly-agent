from __future__ import annotations

import pathlib
import time
from typing import Any, TypedDict

from ai_news_skill.user_news_memory import attach_memory_to_config

from langchain_core.tools import tool
from langgraph.graph import END, StateGraph

from ai_news_skill.runtime.agent_runtime import (
    collect_stage,
    enrich_stage,
    finalize_failed,
    finalize_ok,
    init_run_context,
    intent_plan_stage,
    intent_stage,
    openclaw_stage,
    write_stage,
)

# Tool registry for ReAct-style routing or future `create_agent` wiring (see digest_tools.py).
from digest_tools import DIGEST_AGENT_TOOLS


class DigestState(TypedDict, total=False):
    config: dict[str, Any]
    attempt: int
    max_retries: int
    result: dict[str, Any]
    error: str
    started_iso: str
    started_obj: Any
    trace: dict[str, Any]
    trace_file: str
    output_dir: str
    run_id: str
    run_dir: str
    sources: list[dict]
    items: list[dict]
    errors: list[str]
    min_official_items: int
    openclaw_top: list[dict]
    openclaw_focus: dict[str, Any] | None
    openclaw_asof: str
    llm_overview: str
    llm_core_points: list[str]
    llm_values: list[str]
    llm_titles_cn: list[str]
    intent_plan: dict[str, Any]


@tool
def run_digest_tool(config: dict[str, Any]) -> dict[str, Any]:
    """Run digest pipeline and return doc path + metrics."""
    # Keep tool compatibility for callers that still need one-shot execution.
    from ai_news_skill.runtime.agent_runtime import run_digest_pipeline

    return run_digest_pipeline(config)


@tool
def save_run_record_tool(record: dict[str, Any]) -> dict[str, Any]:
    """Save run metadata record; pipeline already stores trace, tool echoes summary."""
    return {"saved": True, "record": record}


def _bootstrap_node(state: DigestState) -> DigestState:
    if state.get("trace"):
        return state
    config = state.get("config", {})
    try:
        src_p = pathlib.Path(config.get("sources", "sources.json")).resolve()
        attach_memory_to_config(config, src_p.parent)
    except Exception:
        pass
    ctx = init_run_context(config)
    state["started_obj"] = ctx["started"]
    state["started_iso"] = ctx["started"].isoformat()
    state["trace"] = ctx["trace"]
    state["trace_file"] = str(ctx["trace_file"])
    state["output_dir"] = str(ctx["output_dir"])
    state["run_id"] = str(ctx["run_id"])
    state["run_dir"] = str(ctx["run_dir"])
    state["items"] = []
    state["errors"] = []
    state["openclaw_top"] = []
    state["openclaw_focus"] = None
    state["openclaw_asof"] = ""
    state["llm_overview"] = ""
    state["llm_core_points"] = []
    state["llm_values"] = []
    state["llm_titles_cn"] = []
    return state


def _collect_node(state: DigestState) -> DigestState:
    config = state.get("config", {})
    trace = state.get("trace", {})
    attempt = int(state.get("attempt", 1))
    state.pop("error", None)
    try:
        sources, items, errors, min_official_items = collect_stage(
            config,
            trace,
            intent_plan=state.get("intent_plan"),
        )
        items, _ = intent_stage(
            config=config,
            trace=trace,
            items=items,
            min_official_items=min_official_items,
        )
        state["sources"] = sources
        state["items"] = items
        state["errors"] = errors
        state["min_official_items"] = min_official_items
        top, focus, asof, errors_oc = openclaw_stage(
            config=config,
            trace=trace,
            errors=state.get("errors", []),
        )
        state["openclaw_top"] = top
        state["openclaw_focus"] = focus
        state["openclaw_asof"] = asof
        state["errors"] = errors_oc
        return state
    except Exception as ex:  # noqa: BLE001
        state["error"] = f"{type(ex).__name__}: {ex}"
        state["attempt"] = attempt + 1
        return state


def _intent_node(state: DigestState) -> DigestState:
    config = state.get("config", {})
    trace = state.get("trace", {})
    attempt = int(state.get("attempt", 1))
    try:
        state["intent_plan"] = intent_plan_stage(config, trace)
        state.pop("error", None)
        return state
    except Exception as ex:  # noqa: BLE001
        state["error"] = f"{type(ex).__name__}: {ex}"
        state["attempt"] = attempt + 1
        return state


def _enrich_node(state: DigestState) -> DigestState:
    config = state.get("config", {})
    trace = state.get("trace", {})
    attempt = int(state.get("attempt", 1))
    try:
        overview, core_points, values, titles, errors = enrich_stage(
            config=config,
            trace=trace,
            items=state.get("items", []),
            errors=state.get("errors", []),
        )
        state["llm_overview"] = overview
        state["llm_core_points"] = core_points
        state["llm_values"] = values
        state["llm_titles_cn"] = titles
        state["errors"] = errors
        state.pop("error", None)
        return state
    except Exception as ex:  # noqa: BLE001
        state["error"] = f"{type(ex).__name__}: {ex}"
        state["attempt"] = attempt + 1
        return state


def _write_node(state: DigestState) -> DigestState:
    config = state.get("config", {})
    trace = state.get("trace", {})
    attempt = int(state.get("attempt", 1))
    try:
        import pathlib

        doc_path = write_stage(
            config=config,
            trace=trace,
            output_dir=pathlib.Path(state.get("output_dir", "")),
            items=state.get("items", []),
            errors=state.get("errors", []),
            llm_overview=state.get("llm_overview", ""),
            llm_core_points=state.get("llm_core_points", []),
            llm_values=state.get("llm_values", []),
            llm_titles_cn=state.get("llm_titles_cn", []),
            openclaw_top=state.get("openclaw_top", []),
            openclaw_focus=state.get("openclaw_focus"),
            openclaw_asof=state.get("openclaw_asof", ""),
        )
        state["result"] = {
            "items": len(state.get("items", [])),
            "errors": state.get("errors", []),
            "doc_path": str(doc_path),
            "run_dir": state.get("run_dir", ""),
        }
        state.pop("error", None)
        return state
    except Exception as ex:  # noqa: BLE001
        state["error"] = f"{type(ex).__name__}: {ex}"
        state["attempt"] = attempt + 1
        return state


def _retry_or_next(state: DigestState, next_node: str) -> str:
    max_retries = int(state.get("max_retries", 2))
    if state.get("error") and int(state.get("attempt", 1)) <= max_retries:
        time.sleep(1.2)
        return "collect"
    if state.get("error"):
        return "persist"
    return next_node


def _after_collect(state: DigestState) -> str:
    return _retry_or_next(state, "enrich")


def _after_intent(state: DigestState) -> str:
    return _retry_or_next(state, "collect")


def _after_enrich(state: DigestState) -> str:
    return _retry_or_next(state, "write")


def _after_write(state: DigestState) -> str:
    if state.get("error"):
        return "persist"
    return "persist"


def _persist_node(state: DigestState) -> DigestState:
    import pathlib
    from datetime import datetime

    trace = state.get("trace", {})
    trace_file = pathlib.Path(state.get("trace_file", ""))
    started = state.get("started_obj") or datetime.now().astimezone()
    if state.get("result"):
        result = dict(state["result"])
        result["trace_file"] = str(trace_file)
        result["run_id"] = state.get("run_id", "")
        finalize_ok(trace, trace_file, started, result)
        state["result"] = result
    elif state.get("error"):
        finalize_failed(trace, trace_file, started, str(state.get("error")))
    if state.get("result"):
        save_run_record_tool.func(state["result"])
    return state


def build_workflow():
    graph = StateGraph(DigestState)
    graph.add_node("bootstrap", _bootstrap_node)
    graph.add_node("collect", _collect_node)
    graph.add_node("intent", _intent_node)
    graph.add_node("enrich", _enrich_node)
    graph.add_node("write", _write_node)
    graph.add_node("persist", _persist_node)
    graph.set_entry_point("bootstrap")
    graph.add_edge("bootstrap", "intent")
    graph.add_conditional_edges("intent", _after_intent, {"collect": "collect", "persist": "persist"})
    graph.add_conditional_edges("collect", _after_collect, {"collect": "collect", "enrich": "enrich", "persist": "persist"})
    graph.add_conditional_edges("enrich", _after_enrich, {"collect": "collect", "write": "write", "persist": "persist"})
    graph.add_conditional_edges("write", _after_write, {"persist": "persist"})
    graph.add_edge("persist", END)
    return graph.compile()


def run_with_graph(config: dict[str, Any], max_retries: int = 2) -> DigestState:
    app = build_workflow()
    initial: DigestState = {"config": config, "attempt": 1, "max_retries": max_retries}
    return app.invoke(initial)

