from __future__ import annotations

import copy
import json
from typing import Any

from langchain.agents import AgentExecutor, create_tool_calling_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.tools import tool

from ai_news_skill.orchestration.langgraph_agent import run_with_graph
from middleware.pipeline_middleware import log_pipeline_event
from model.llm_factory import build_chat_openai
from run_daily_digest import resolve_llm_runtime


def _build_runtime_namespace(config: dict[str, Any]) -> Any:
    class NS:
        llm_provider = str(config.get("llm_provider", "auto"))
        llm_base_url = str(config.get("llm_base_url", ""))
        allow_custom_llm_endpoint = bool(config.get("allow_custom_llm_endpoint", False))
        ark_api_key = str(config.get("ark_api_key", "")).strip()
        ark_endpoint_id = str(config.get("ark_endpoint_id", "")).strip()
        ark_model = str(config.get("ark_model", "Doubao-Seed-1.6-lite")).strip()

    return NS()


def run_with_tool_agent(
    *,
    config: dict[str, Any],
    user_intent: str,
    max_retries: int = 2,
) -> dict[str, Any]:
    """
    Reference-style tool-calling orchestrator.
    Falls back to LangGraph flow if tool agent path cannot produce a valid pipeline result.
    """
    holder: dict[str, Any] = {"result": None}
    base_config = copy.deepcopy(config)

    @tool
    def run_digest_with_context(config_patch_json: str = "{}") -> str:
        """Run weekly digest pipeline using current session config; optional JSON patch can override a subset of fields."""
        from ai_news_skill.runtime.agent_runtime import run_digest_pipeline

        merged = copy.deepcopy(base_config)
        try:
            patch = json.loads(config_patch_json or "{}")
            if isinstance(patch, dict):
                merged.update(patch)
        except Exception:
            pass
        result = run_digest_pipeline(merged)
        holder["result"] = result
        return json.dumps(result, ensure_ascii=False)

    @tool
    def fetch_openclaw_with_context(focus_skill: str = "") -> str:
        """Fetch OpenClaw leaderboard. Use only when user explicitly asks for OpenClaw/top skills ranking."""
        from run_daily_digest import fetch_openclaw_stars_top

        top, focus, asof = fetch_openclaw_stars_top(
            top_n=3,
            focus_skill=focus_skill or str(base_config.get("focus_skill", "")),
            allow_insecure_fallback=bool(base_config.get("allow_insecure_ssl", False)),
        )
        payload = {"asof": asof, "top": top, "focus": focus}
        return json.dumps(payload, ensure_ascii=False)

    tools = [run_digest_with_context, fetch_openclaw_with_context]
    prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "你是 AI 新闻周报代理。请根据用户需求调用工具。\n"
                "规则：\n"
                "1) 默认必须调用 run_digest_with_context 生成正式报告结果。\n"
                "2) 仅当用户明确提到 openclaw/topclaw/技能榜/榜单 时，再调用 fetch_openclaw_with_context。\n"
                "3) 回答必须基于工具结果，禁止臆造。",
            ),
            ("human", "{input}"),
            MessagesPlaceholder("agent_scratchpad"),
        ]
    )
    try:
        runtime = _build_runtime_namespace(base_config)
        provider, base_url, api_key = resolve_llm_runtime(runtime)
        model = runtime.ark_endpoint_id or runtime.ark_model or "Doubao-Seed-1.6-lite"
        llm = build_chat_openai(
            api_key=api_key,
            model=model,
            chat_completions_url=base_url,
            allow_insecure_fallback=bool(base_config.get("allow_insecure_ssl", False)),
        )
        agent = create_tool_calling_agent(llm=llm, tools=tools, prompt=prompt)
        executor = AgentExecutor(agent=agent, tools=tools, verbose=False, max_iterations=6)
        log_pipeline_event("tool_agent", "started", "create_tool_calling_agent route")
        _ = executor.invoke({"input": user_intent})
        if isinstance(holder.get("result"), dict):
            return {"result": holder["result"], "route": "tool_agent"}
    except Exception as ex:  # noqa: BLE001
        log_pipeline_event("tool_agent", "warn", str(ex))

    # Safe fallback keeps production path stable.
    fallback_state = run_with_graph(config=base_config, max_retries=max_retries)
    fallback_state["route"] = "langgraph_fallback"
    return fallback_state

