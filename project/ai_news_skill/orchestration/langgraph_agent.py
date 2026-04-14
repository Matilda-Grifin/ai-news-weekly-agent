from __future__ import annotations

# 这个文件是项目里 LangGraph 编排的核心：
# - 定义状态结构 DigestState（图里每个节点都读写这份状态）
# - 定义各阶段节点（collect/enrich/write 等）
# - 定义条件边（成功走下一步，失败走重试或持久化）
# - 暴露 run_with_graph 作为统一执行入口

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
    pre_enrich_reflect_stage,
    write_stage,
)

# Tool registry for ReAct-style routing or future `create_agent` wiring (see digest_tools.py).
from digest_tools import DIGEST_AGENT_TOOLS


class DigestState(TypedDict, total=False):
    # 运行配置与重试控制
    config: dict[str, Any]
    attempt: int
    max_retries: int
    # 输出结果与错误信息
    result: dict[str, Any]
    error: str
    # 运行时元信息（用于 trace、输出目录、run_id）
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
    # 意图规划阶段产物：用于后续采集和筛选的策略
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
    # 幂等初始化：如果已有 trace，说明已初始化过，直接返回
    if state.get("trace"):
        return state
    config = state.get("config", {})
    try:
        src_p = pathlib.Path(config.get("sources", "sources.json")).resolve()
        attach_memory_to_config(config, src_p.parent)
    except Exception:
        pass
    # 初始化一次运行上下文（trace 文件、输出目录、run_id 等）
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
    # 采集节点：做数据抓取 + 意图筛选 + OpenClaw 补充 + 反思预处理
    config = state.get("config", {})
    trace = state.get("trace", {})
    attempt = int(state.get("attempt", 1))
    state.pop("error", None)
    try:
        # 1) 采集多源新闻
        sources, items, errors, min_official_items = collect_stage(
            config,
            trace,
            intent_plan=state.get("intent_plan"),
        )
        # 2) 基于 intent 进行内容筛选/排序
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
        # 3) 拉取 OpenClaw 结果（热点与聚焦）
        top, focus, asof, errors_oc = openclaw_stage(
            config=config,
            trace=trace,
            errors=state.get("errors", []),
        )
        state["openclaw_top"] = top
        state["openclaw_focus"] = focus
        state["openclaw_asof"] = asof
        state["errors"] = errors_oc
        # 4) enrich 前反思，进一步修正待摘要内容
        state["items"] = pre_enrich_reflect_stage(
            config,
            trace,
            state["items"],
            sources=state["sources"],
            intent_plan=state.get("intent_plan"),
            min_official_items=state["min_official_items"],
        )
        return state
    except Exception as ex:  # noqa: BLE001
        # 节点失败：记录错误并增加 attempt，交给条件边决定是否重试
        state["error"] = f"{type(ex).__name__}: {ex}"
        state["attempt"] = attempt + 1
        return state


def _intent_node(state: DigestState) -> DigestState:
    # 先做“意图规划”，产出后续采集的方向和优先级策略
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
    # 调用 LLM 做摘要增强：总览、要点、价值、中文标题
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
    # 将整理后的结果写入 markdown 文档，并回填 result
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
    # 通用路由规则：
    # - 若当前有 error 且未超过重试次数 -> 回 collect 重试
    # - 若当前有 error 且超过重试次数 -> 去 persist 落盘失败信息
    # - 无 error -> 进入指定下一节点
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
    # 统一收尾节点：无论成功失败都在这里写 trace，并保存运行记录
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
    # LangGraph 拓扑（主流程）：
    # bootstrap -> intent -> collect -> enrich -> write -> persist -> END
    # 其中 intent/collect/enrich 可能因错误回 collect 重试
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
    # 对外执行入口：构建图并 invoke 初始状态
    app = build_workflow()
    initial: DigestState = {"config": config, "attempt": 1, "max_retries": max_retries}
    return app.invoke(initial)

