"""Tool-calling agent: stage tools + optional MCP (same stack as project tool agent)."""
from __future__ import annotations

import asyncio
import os
import pathlib
from typing import Any

from langchain.agents import AgentExecutor, create_tool_calling_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from agent_platform.paths import repo_root


def _setup_project_path() -> None:
    import sys

    root = pathlib.Path(__file__).resolve().parents[3]
    pr = root / "project"
    if str(pr) not in sys.path:
        sys.path.insert(0, str(pr))


def _system_prompt(*, mcp_tool_count: int) -> str:
    mcp_hint = ""
    if mcp_tool_count > 0:
        mcp_hint = (
            "\nMCP 工具名以 mcp_ 开头；需要外部能力时再调用；"
            "参数使用 arguments_json（JSON 字符串）。\n"
        )
    return (
        "你是 AI 新闻周报 Harness Agent。用户 API 已由服务器从 .env 注入，无需向用户索要 Key。\n"
        "你可以：\n"
        "A) 一键：skill_run_full_pipeline(intent_text=用户主题) —— 最稳妥，等价旧版整条流水线。\n"
        "B) 分步：harness_bootstrap → skill_set_intent → skill_intent_plan → skill_collect_and_filter "
        "→ skill_openclaw_leaderboard（可选）→ skill_enrich → skill_write_report → skill_finalize_run。\n"
        "不确定时优先用 A。回答应引用工具返回的 JSON，勿编造路径。\n"
        + mcp_hint
    )


def _build_executor(tools: list[Any], sys_prompt: str) -> AgentExecutor:
    _setup_project_path()
    from model.llm_factory import build_chat_openai
    from run_daily_digest import resolve_llm_runtime

    # resolve_llm needs config-shaped namespace
    class NS:
        llm_provider = "auto"
        llm_base_url = ""
        allow_custom_llm_endpoint = False
        ark_api_key = (os.getenv("ARK_API_KEY", "") or "").strip()
        ark_endpoint_id = (os.getenv("ARK_ENDPOINT_ID", "") or "").strip()
        ark_model = (os.getenv("ARK_MODEL", "Doubao-Seed-1.6-lite") or "").strip()
        openai_model = (os.getenv("OPENAI_MODEL", "") or "").strip()

    ns = NS()
    provider, base_url, api_key = resolve_llm_runtime(ns)
    if provider == "openai-compatible":
        model = ns.openai_model or ns.ark_model or "gpt-4o-mini"
    else:
        model = ns.ark_endpoint_id or ns.ark_model or "Doubao-Seed-1.6-lite"
    allow_insecure = (os.getenv("ALLOW_INSECURE_SSL", "").lower() in ("1", "true", "yes"))
    llm = build_chat_openai(
        api_key=api_key or "",
        model=model,
        chat_completions_url=base_url,
        allow_insecure_fallback=allow_insecure,
    )
    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", sys_prompt),
            ("human", "{input}"),
            MessagesPlaceholder("agent_scratchpad"),
        ]
    )
    agent = create_tool_calling_agent(llm=llm, tools=tools, prompt=prompt)
    return AgentExecutor(agent=agent, tools=tools, verbose=False, max_iterations=24)


def run_harness_turn(session_id: str, user_text: str) -> str:
    from agent_platform.backend.harness.tools import build_stage_tools

    stage_tools = build_stage_tools(session_id)
    mcp_path = repo_root() / "mcp_servers.json"
    use_mcp = mcp_path.is_file() and os.getenv("HARNESS_ENABLE_MCP", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )

    if not use_mcp:
        ex = _build_executor(stage_tools, _system_prompt(mcp_tool_count=0))
        out = ex.invoke({"input": user_text})
        return str(out.get("output") or "")

    _setup_project_path()
    from ai_news_skill.integrations.mcp_bridge import (
        build_mcp_tools_for_group,
        enabled_mcp_servers,
        mcp_component_name_hook,
        server_params_from_dict,
    )
    from mcp.client.session_group import ClientSessionGroup, ClientSessionParameters

    servers = enabled_mcp_servers(str(mcp_path))
    if not servers:
        ex = _build_executor(stage_tools, _system_prompt(mcp_tool_count=0))
        out = ex.invoke({"input": user_text})
        return str(out.get("output") or "")

    async def _run_with_mcp() -> str:
        async with ClientSessionGroup(component_name_hook=mcp_component_name_hook) as group:
            for raw in servers:
                sp = server_params_from_dict(raw)
                await group.connect_to_server(sp, ClientSessionParameters())
            loop = asyncio.get_running_loop()
            mcp_tools = build_mcp_tools_for_group(group, loop)
            tools = [*stage_tools, *mcp_tools]
            ex = _build_executor(tools, _system_prompt(mcp_tool_count=len(mcp_tools)))
            out = await asyncio.to_thread(ex.invoke, {"input": user_text})
            return str(out.get("output") or "")

    try:
        return asyncio.run(_run_with_mcp())
    except Exception:
        ex = _build_executor(stage_tools, _system_prompt(mcp_tool_count=0))
        out = ex.invoke({"input": user_text})
        return str(out.get("output") or "")
