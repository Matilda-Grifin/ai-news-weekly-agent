from __future__ import annotations

import time
from typing import Any, TypedDict

from langchain_core.tools import tool
from langgraph.graph import END, StateGraph

from agent_runtime import run_digest_pipeline


class DigestState(TypedDict, total=False):
    config: dict[str, Any]
    attempt: int
    max_retries: int
    result: dict[str, Any]
    error: str


@tool
def run_digest_tool(config: dict[str, Any]) -> dict[str, Any]:
    """Run digest pipeline and return doc path + metrics."""
    return run_digest_pipeline(config)


@tool
def save_run_record_tool(record: dict[str, Any]) -> dict[str, Any]:
    """Save run metadata record; pipeline already stores trace, tool echoes summary."""
    return {"saved": True, "record": record}


def _run_node(state: DigestState) -> DigestState:
    config = state.get("config", {})
    attempt = int(state.get("attempt", 1))
    try:
        # Call tool function directly inside graph node to avoid schema mismatch.
        result = run_digest_tool.func(config)
        state["result"] = result
        state.pop("error", None)
        return state
    except Exception as ex:  # noqa: BLE001
        state["error"] = f"{type(ex).__name__}: {ex}"
        state["attempt"] = attempt + 1
        return state


def _retry_or_end(state: DigestState) -> str:
    max_retries = int(state.get("max_retries", 2))
    if state.get("error") and int(state.get("attempt", 1)) <= max_retries:
        time.sleep(1.2)
        return "run"
    return "persist"


def _persist_node(state: DigestState) -> DigestState:
    if state.get("result"):
        save_run_record_tool.func(state["result"])
    return state


def build_workflow():
    graph = StateGraph(DigestState)
    graph.add_node("run", _run_node)
    graph.add_node("persist", _persist_node)
    graph.set_entry_point("run")
    graph.add_conditional_edges("run", _retry_or_end, {"run": "run", "persist": "persist"})
    graph.add_edge("persist", END)
    return graph.compile()


def run_with_graph(config: dict[str, Any], max_retries: int = 2) -> DigestState:
    app = build_workflow()
    initial: DigestState = {"config": config, "attempt": 1, "max_retries": max_retries}
    return app.invoke(initial)
