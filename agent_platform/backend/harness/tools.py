"""LangChain tools: one stage per tool + full-pipeline escape hatch. Built per session_id."""
from __future__ import annotations

import json
import pathlib
from typing import Any

from langchain_core.tools import tool

from agent_platform.backend.harness import gates
from agent_platform.backend.harness.session import get_session


def build_stage_tools(session_id: str) -> list[Any]:
    def _sess():
        s = get_session(session_id)
        if not s:
            raise ValueError("invalid session")
        return s

    @tool
    def harness_bootstrap(intent_text: str = "") -> str:
        """初始化一次周报运行：创建 run_id、trace、输出目录，并加载用户记忆。应先调用。"""
        from ai_news_skill.user_news_memory import attach_memory_to_config
        from ai_news_skill.runtime.agent_runtime import init_run_context

        s = _sess()
        if intent_text.strip():
            s.config["intent_text"] = intent_text.strip()
        src = pathlib.Path(s.config.get("sources", "sources.json")).resolve()
        try:
            attach_memory_to_config(s.config, src.parent)
        except Exception:
            pass
        s.ctx = init_run_context(s.config)
        s.phases.add("bootstrap")
        return json.dumps(
            {
                "ok": True,
                "run_id": s.ctx["run_id"],
                "trace_file": str(s.ctx["trace_file"]),
                "output_dir": str(s.ctx["output_dir"]),
            },
            ensure_ascii=False,
        )

    @tool
    def skill_set_intent(intent_text: str) -> str:
        """设置本周周报的用户意图/主题（自然语言）。"""
        s = _sess()
        s.config["intent_text"] = (intent_text or "").strip()
        return json.dumps({"ok": True, "intent_text": s.config["intent_text"]}, ensure_ascii=False)

    @tool
    def skill_intent_plan() -> str:
        """阶段：意图拆解与检索计划（关键词、偏好类别等）。需已完成 bootstrap。"""
        from ai_news_skill.runtime.agent_runtime import intent_plan_stage

        s = _sess()
        g = gates.require_bootstrap(s)
        if g:
            return json.dumps({"ok": False, "error": g}, ensure_ascii=False)
        trace = s.ctx["trace"]  # type: ignore[index]
        s.intent_plan = intent_plan_stage(s.config, trace)
        s.phases.add("intent_plan")
        return json.dumps({"ok": True, "plan": s.intent_plan}, ensure_ascii=False)

    @tool
    def skill_collect_and_filter() -> str:
        """阶段：加载信源、抓取、平衡与意图过滤。需已完成 intent_plan。"""
        from ai_news_skill.runtime.agent_runtime import collect_stage, intent_stage

        s = _sess()
        g = gates.require_intent_plan(s)
        if g:
            return json.dumps({"ok": False, "error": g}, ensure_ascii=False)
        trace = s.ctx["trace"]  # type: ignore[index]
        sources, items, errors, min_official = collect_stage(s.config, trace, intent_plan=s.intent_plan)
        items, _ = intent_stage(s.config, trace, items, min_official_items=min_official)
        s.sources = sources
        s.items = items
        s.errors = errors
        s.min_official_items = min_official
        s.phases.add("collect")
        return json.dumps(
            {
                "ok": True,
                "sources": len(sources),
                "items": len(items),
                "errors_preview": errors[:5],
            },
            ensure_ascii=False,
        )

    @tool
    def skill_openclaw_leaderboard() -> str:
        """阶段：若配置启用 openclaw 或 focus_skill，则拉取热榜。否则快速跳过。"""
        from ai_news_skill.runtime.agent_runtime import openclaw_stage

        s = _sess()
        g = gates.require_collect(s)
        if g:
            return json.dumps({"ok": False, "error": g}, ensure_ascii=False)
        trace = s.ctx["trace"]  # type: ignore[index]
        top, focus, asof, errs = openclaw_stage(s.config, trace, s.errors)
        s.openclaw_top = top
        s.openclaw_focus = focus
        s.openclaw_asof = asof
        s.errors = errs
        s.phases.add("openclaw")
        return json.dumps(
            {"ok": True, "top_n": len(top), "asof": asof},
            ensure_ascii=False,
        )

    @tool
    def skill_enrich() -> str:
        """阶段：正文抽取与 LLM 增强综述。需已完成 collect。"""
        from ai_news_skill.runtime.agent_runtime import enrich_stage, pre_enrich_reflect_stage

        s = _sess()
        g = gates.require_collect(s)
        if g:
            return json.dumps({"ok": False, "error": g}, ensure_ascii=False)
        trace = s.ctx["trace"]  # type: ignore[index]
        s.items = pre_enrich_reflect_stage(
            s.config,
            trace,
            s.items,
            sources=s.sources,
            intent_plan=s.intent_plan,
            min_official_items=s.min_official_items,
        )
        ov, core, vals, titles, errs = enrich_stage(s.config, trace, s.items, s.errors)
        s.llm_overview = ov
        s.llm_core_points = core
        s.llm_values = vals
        s.llm_titles_cn = titles
        s.errors = errs
        s.phases.add("enrich")
        return json.dumps(
            {"ok": True, "overview_len": len(ov), "errors_preview": errs[:3]},
            ensure_ascii=False,
        )

    @tool
    def skill_write_report() -> str:
        """阶段：渲染 Markdown 并写入 daily_docs。需已完成 enrich。"""
        from ai_news_skill.runtime.agent_runtime import write_stage

        s = _sess()
        g = gates.require_enrich(s)
        if g:
            return json.dumps({"ok": False, "error": g}, ensure_ascii=False)
        trace = s.ctx["trace"]  # type: ignore[index]
        out = pathlib.Path(s.ctx["output_dir"])  # type: ignore[index]
        doc = write_stage(
            config=s.config,
            trace=trace,
            output_dir=out,
            items=s.items,
            errors=s.errors,
            llm_overview=s.llm_overview,
            llm_core_points=s.llm_core_points,
            llm_values=s.llm_values,
            llm_titles_cn=s.llm_titles_cn,
            openclaw_top=s.openclaw_top,
            openclaw_focus=s.openclaw_focus,
            openclaw_asof=s.openclaw_asof,
        )
        s.doc_path = str(doc)
        s.phases.add("write")
        return json.dumps({"ok": True, "doc_path": s.doc_path}, ensure_ascii=False)

    @tool
    def skill_finalize_run() -> str:
        """结束：将 trace 落盘并返回与 run_digest_pipeline 一致的结果摘要。"""
        from ai_news_skill.runtime.agent_runtime import finalize_ok
        from datetime import datetime

        s = _sess()
        if s.ctx is None or not s.doc_path:
            return json.dumps(
                {"ok": False, "error": "gate: 请先完成 skill_write_report。"},
                ensure_ascii=False,
            )
        trace = s.ctx["trace"]
        trace_file = pathlib.Path(s.ctx["trace_file"])
        started = s.ctx["started"]
        result = {
            "items": len(s.items),
            "errors": s.errors,
            "doc_path": s.doc_path,
            "run_dir": str(s.ctx["run_dir"]),
            "trace_file": str(trace_file),
            "run_id": str(s.ctx["run_id"]),
        }
        finalize_ok(trace, trace_file, started, result)
        s.result = result
        s.phases.add("finalize")
        s.append_memory("assistant", f"finalize ok {s.doc_path}")
        return json.dumps({"ok": True, "result": result}, ensure_ascii=False)

    @tool
    def skill_run_full_pipeline(intent_text: str = "") -> str:
        """一键执行与旧版相同的完整流水线（run_digest_pipeline），无需逐步调用。"""
        from ai_news_skill.runtime.agent_runtime import run_digest_pipeline

        s = _sess()
        if intent_text.strip():
            s.config["intent_text"] = intent_text.strip()
        try:
            r = run_digest_pipeline(s.config)
            s.result = r
            s.doc_path = r.get("doc_path")
            s.phases.update({"bootstrap", "intent_plan", "collect", "openclaw", "enrich", "write", "finalize"})
            return json.dumps({"ok": True, "result": r}, ensure_ascii=False)
        except Exception as ex:  # noqa: BLE001
            return json.dumps({"ok": False, "error": f"{type(ex).__name__}: {ex}"}, ensure_ascii=False)

    @tool
    def harness_plan_hint(user_goal: str = "") -> str:
        """仅输出推荐工具调用顺序（不执行）。用于向用户展示计划。"""
        hint = (
            "推荐顺序（与旧版 LangGraph 一致）：\n"
            "1) harness_bootstrap\n"
            "2) skill_set_intent（若未在 bootstrap 传入意图）\n"
            "3) skill_intent_plan\n"
            "4) skill_collect_and_filter\n"
            "5) skill_openclaw_leaderboard（可选，需 enable_openclaw/focus_skill）\n"
            "6) skill_enrich\n"
            "7) skill_write_report\n"
            "8) skill_finalize_run\n"
            "或一条 skill_run_full_pipeline 覆盖全部。\n"
            f"当前用户目标摘要：{(user_goal or '')[:500]}"
        )
        return hint

    return [
        harness_bootstrap,
        skill_set_intent,
        skill_intent_plan,
        skill_collect_and_filter,
        skill_openclaw_leaderboard,
        skill_enrich,
        skill_write_report,
        skill_finalize_run,
        skill_run_full_pipeline,
        harness_plan_hint,
    ]
