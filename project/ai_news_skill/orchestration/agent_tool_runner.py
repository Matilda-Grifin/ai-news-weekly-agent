from __future__ import annotations

import asyncio
import copy
import json
import pathlib
from typing import Any

from langchain.agents import AgentExecutor, create_tool_calling_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.tools import tool

from ai_news_skill.integrations.mcp_bridge import (
    build_mcp_tools_for_group,
    enabled_mcp_servers,
    mcp_component_name_hook,
    server_params_from_dict,
)
from ai_news_skill.orchestration.langgraph_agent import run_with_graph
from mcp.client.session_group import ClientSessionGroup, ClientSessionParameters
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
        openai_model = str(config.get("openai_model", "")).strip()

    return NS()


def _system_prompt(mem_blk: str, *, mcp_tool_count: int) -> str:
    mcp_hint = ""
    if mcp_tool_count > 0:
        mcp_hint = (
            "\n4) MCP 工具名称以 `mcp_` 前缀开头；"
            "仅在用户问题确实需要外部 MCP 能力时调用；"
            "参数一律使用字符串参数 `arguments_json`，内容为 JSON 对象（无参数用 \"{}\"）。\n"
        )
    sys0 = (
        "你是 AI 新闻周报代理。请根据用户需求调用工具。\n"
        "规则：\n"
        "1) 默认必须调用 run_digest_with_context 生成正式报告结果。\n"
        "2) 仅当用户明确提到 openclaw/topclaw/技能榜/榜单 时，再调用 fetch_openclaw_with_context。\n"
        "3) 回答必须基于工具结果，禁止臆造。"
        + mcp_hint
    )
    if mem_blk:
        sys0 += "\n\n" + mem_blk
    return sys0


def _build_executor(
    *,
    base_config: dict[str, Any],
    tools: list[Any],
    sys0: str,
    user_intent: str,
) -> AgentExecutor:
    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", sys0),
            ("human", "{input}"),
            MessagesPlaceholder("agent_scratchpad"),
        ]
    )
    runtime = _build_runtime_namespace(base_config)
    _provider, base_url, api_key = resolve_llm_runtime(runtime)
    if _provider == "openai-compatible":
        model = runtime.openai_model or runtime.ark_model or "gpt-4o-mini"
    else:
        model = runtime.ark_endpoint_id or runtime.ark_model or "Doubao-Seed-1.6-lite"
    llm = build_chat_openai(
        api_key=api_key,
        model=model,
        chat_completions_url=base_url,
        allow_insecure_fallback=bool(base_config.get("allow_insecure_ssl", False)),
    )
    agent = create_tool_calling_agent(llm=llm, tools=tools, prompt=prompt)
    return AgentExecutor(agent=agent, tools=tools, verbose=False, max_iterations=6)


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
    mem_blk = str(base_config.get("_user_memory_system_block") or "").strip()

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

    core_tools: list[Any] = [run_digest_with_context, fetch_openclaw_with_context]
    mcp_path = str(base_config.get("mcp_servers_json") or "").strip()
    mcp_file_ok = bool(mcp_path) and pathlib.Path(mcp_path).is_file()
    servers = enabled_mcp_servers(mcp_path) if mcp_file_ok else []

    async def _run_with_pooled_mcp() -> None:
        async with ClientSessionGroup(component_name_hook=mcp_component_name_hook) as group:
            for raw in servers:
                sp = server_params_from_dict(raw)
                await group.connect_to_server(sp, ClientSessionParameters())
            loop = asyncio.get_running_loop()
            mcp_tools = build_mcp_tools_for_group(group, loop)
            tools = [*core_tools, *mcp_tools]
            sys0 = _system_prompt(mem_blk, mcp_tool_count=len(mcp_tools))
            executor = _build_executor(base_config=base_config, tools=tools, sys0=sys0, user_intent=user_intent)
            await asyncio.to_thread(executor.invoke, {"input": user_intent})

    try:
        if mcp_file_ok and servers:
            log_pipeline_event("tool_agent", "started", "create_tool_calling_agent route (mcp pooled)")
            asyncio.run(_run_with_pooled_mcp())
            if isinstance(holder.get("result"), dict):
                return {"result": holder["result"], "route": "tool_agent"}
        else:
            tools = core_tools
            sys0 = _system_prompt(mem_blk, mcp_tool_count=0)
            executor = _build_executor(base_config=base_config, tools=tools, sys0=sys0, user_intent=user_intent)
            log_pipeline_event("tool_agent", "started", "create_tool_calling_agent route")
            executor.invoke({"input": user_intent})
            if isinstance(holder.get("result"), dict):
                return {"result": holder["result"], "route": "tool_agent"}
    except Exception as ex:  # noqa: BLE001
        log_pipeline_event("tool_agent", "warn", str(ex))

    fallback_state = run_with_graph(config=base_config, max_retries=max_retries)
    fallback_state["route"] = "langgraph_fallback"
    return fallback_state
