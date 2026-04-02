#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import pathlib
from datetime import datetime

import streamlit as st

from ai_news_skill.orchestration.langgraph_agent import run_with_graph
from mcp_bridge import append_jsonl_record, discover_mcp_tools


st.set_page_config(page_title="AI News Agent Console", page_icon="📰", layout="wide")
st.title("AI 新闻 Agent")
st.caption("聊天式意图输入 + 任务规划 + 新闻执行")


def _mask_key(value: str) -> str:
    text = (value or "").strip()
    if len(text) < 8:
        return "*" * len(text)
    return f"{text[:4]}***{text[-4:]}"


def _load_sources(path: str = "sources.json") -> list[dict]:
    p = pathlib.Path(path).resolve()
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
    if not isinstance(data, list):
        return []
    return [x for x in data if isinstance(x, dict) and x.get("name") and x.get("rss_url")]


def _infer_plan(user_intent: str) -> tuple[dict, list[str]]:
    text = (user_intent or "").strip().lower()
    plan = {
        "topic_hint": "",
        "need_official_first": True,
        "need_industry": True,
        "need_papers": False,
        "need_cn_vendor": False,
        "detail_level": "normal",
    }
    tasks = [
        "解析用户意图并抽取关注主题",
        "按系统内置信源抓取最近窗口内资讯",
        "去重、分组并做重要性排序",
        "按偏好生成中文摘要与解读",
        "输出报告并保留可追踪历史",
    ]
    if not text:
        return plan, tasks

    if "论文" in text or "paper" in text or "arxiv" in text:
        plan["need_papers"] = True
    if "国内" in text or "国产" in text or "腾讯" in text or "阿里" in text:
        plan["need_cn_vendor"] = True
    if "官方" in text or "发布" in text:
        plan["need_official_first"] = True
    if "深度" in text or "详细" in text or "深入" in text:
        plan["detail_level"] = "deep"
    if "快讯" in text or "简报" in text:
        plan["detail_level"] = "brief"
    if any(k in text for k in ["openclaw", "topclaw", "技能榜", "榜单", "skill"]):
        tasks.insert(2, "检测到榜单需求，调用 OpenClaw 榜单 skill")
    plan["topic_hint"] = user_intent[:80]
    return plan, tasks


def _extract_focus_skill(user_text: str) -> str:
    text = (user_text or "").strip()
    if not text:
        return ""
    if not any(k in text.lower() for k in ["openclaw", "topclaw", "技能榜", "榜单", "skill"]):
        return ""
    cleaned = text
    for token in ["openclaw", "OpenClaw", "topclaw", "TopClaw", "技能榜", "榜单", "skill", "排名", "排行", "查询", "查看"]:
        cleaned = cleaned.replace(token, " ")
    cleaned = " ".join(cleaned.split())
    return cleaned[:60]


def _step_label(name: str) -> str:
    mapping = {
        "intent_plan": "识别用户意图并规划抓取任务",
        "load_sources": "解析用户意图并抽取关注主题",
        "collect_plan": "按规划筛选信源与抓取范围",
        "collect_news": "按系统内置信源抓取最近窗口内资讯",
        "gnews_search": "LLM 抽取搜索词并调用 GNews",
        "gnews_intent_gap": "意图未命中时补充 GNews 关键词检索",
        "fetch_article_body": "抓取原文链接正文（trafilatura）",
        "collect_filter": "按意图过滤抓取结果",
        "intent_rank": "按用户意图匹配并重排资讯",
        "official_backfill": "补充官方发布资讯",
        "balance_items": "去重、分组并做重要性排序",
        "openclaw": "抓取 OpenClaw 热榜",
        "llm_enrich": "按偏好生成中文摘要与解读",
        "render_and_write": "输出报告并保留可追踪历史",
        "google_hk_search": "Google HK 网页检索（Playwright）",
        "cap_per_category": "按板块截断为固定条数",
    }
    return mapping.get(name, name)


if "chat_messages" not in st.session_state:
    st.session_state["chat_messages"] = []
if "api_ready" not in st.session_state:
    st.session_state["api_ready"] = False
if "ARK_API_KEY" not in st.session_state and os.getenv("ARK_API_KEY"):
    st.session_state["ARK_API_KEY"] = os.getenv("ARK_API_KEY", "")
if "OPENAI_API_KEY" not in st.session_state and os.getenv("OPENAI_API_KEY"):
    st.session_state["OPENAI_API_KEY"] = os.getenv("OPENAI_API_KEY", "")
if "GNEWS_API_KEY" not in st.session_state and os.getenv("GNEWS_API_KEY"):
    st.session_state["GNEWS_API_KEY"] = os.getenv("GNEWS_API_KEY", "")

with st.sidebar:
    st.header("运行设置")
    days = st.slider("时间窗口（天）", min_value=1, max_value=30, value=7)
    st.caption("每个信源、GNews、各板块展示条数固定为 **5**（后端统一控制，不在此选择）。")
    use_llm = st.checkbox("启用 LLM 解读", value=True)
    max_retries = st.slider("最大重试次数", min_value=0, max_value=5, value=2)

    st.header("LLM 与 API 配置（仅当前会话）")
    llm_provider = st.selectbox("LLM Provider", ["ark", "openai-compatible"], index=0)
    llm_base_url = ""
    ark_model = ""
    ark_endpoint_id = ""
    openai_model = ""
    if llm_provider == "ark":
        ark_endpoint_id = st.text_input(
            "ARK Endpoint ID（推荐，形如 ep-xxx）",
            value=os.getenv("ARK_ENDPOINT_ID", ""),
        )
        ark_model = st.text_input("ARK Model", value="Doubao-Seed-1.6-lite")
        ark_api_key = st.text_input("ARK_API_KEY", value="", type="password")
        openai_api_key = ""
    else:
        llm_base_url = st.text_input("OpenAI-compatible Base URL", value="")
        openai_model = st.text_input("OpenAI Model", value="gpt-4o-mini")
        openai_api_key = st.text_input("OPENAI_API_KEY", value="", type="password")
        ark_api_key = ""

    st.header("GNews（建议开启）")
    _gnews_default = bool((st.session_state.get("GNEWS_API_KEY") or os.getenv("GNEWS_API_KEY", "")).strip())
    gnews_enabled = st.checkbox(
        "启用 GNews（优先与 RSS 合并展示，GNews 在前）",
        value=_gnews_default,
        help="需填写 GNEWS_API_KEY；搜索词由 LLM 从聊天中抽取",
    )
    gnews_api_key = st.text_input(
        "GNEWS_API_KEY",
        value=st.session_state.get("GNEWS_API_KEY", ""),
        type="password",
        help="https://gnews.io ；可与下方「确认保存」一并写入会话",
    )
    gnews_lang = st.selectbox("GNews 语言", ["en", "zh"], index=0)
    strict_intent_match = st.checkbox(
        "仅展示与搜索关键词相关的条目（严格）",
        value=True,
        help="关闭后：关键词未命中时仍会展示时间序资讯（不推荐）",
    )

    if st.button("确认保存 API 配置", use_container_width=True):
        if ark_api_key:
            st.session_state["ARK_API_KEY"] = ark_api_key
        if openai_api_key:
            st.session_state["OPENAI_API_KEY"] = openai_api_key
        if gnews_api_key.strip():
            st.session_state["GNEWS_API_KEY"] = gnews_api_key.strip()
            os.environ["GNEWS_API_KEY"] = gnews_api_key.strip()
        st.session_state["api_ready"] = True
    if st.session_state.get("api_ready"):
        st.success("API 配置已确认，可开始对话执行")
    else:
        st.warning("请先输入 key 并点“确认保存 API 配置”")

    mcp_tools = discover_mcp_tools()
    if mcp_tools:
        st.info(f"MCP: {', '.join(mcp_tools)}")
    else:
        st.caption("MCP 未启用")

    st.header("历史记录")
    history_path = pathlib.Path("runs/history.jsonl").resolve()
    if history_path.exists():
        rows = []
        for line in history_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:  # noqa: BLE001
                continue
        rows = list(reversed(rows[-10:]))
        if rows:
            for r in rows:
                st.caption(f"- {r.get('time', '')[:16]} | {r.get('intent', '')[:18]} | {r.get('items', 0)}条")
        else:
            st.caption("暂无历史")
    else:
        st.caption("暂无历史")

for msg in st.session_state["chat_messages"]:
    st.chat_message(msg["role"]).write(msg["content"])

user_text = st.chat_input("输入你的新闻意图，发送后将自动执行（不需要额外按钮）")
if user_text:
    st.session_state["chat_messages"].append({"role": "user", "content": user_text})
    st.chat_message("user").write(user_text)

    if not st.session_state.get("api_ready"):
        err = "请先在左侧 API 配置中输入 key 并点击“确认保存 API 配置”。"
        st.session_state["chat_messages"].append({"role": "assistant", "content": err})
        st.chat_message("assistant").write(err)
        st.stop()

    if st.session_state.get("ARK_API_KEY"):
        os.environ["ARK_API_KEY"] = st.session_state["ARK_API_KEY"]
    if st.session_state.get("OPENAI_API_KEY"):
        os.environ["OPENAI_API_KEY"] = st.session_state["OPENAI_API_KEY"]
    if gnews_api_key.strip():
        st.session_state["GNEWS_API_KEY"] = gnews_api_key.strip()
        os.environ["GNEWS_API_KEY"] = gnews_api_key.strip()

    if use_llm:
        if llm_provider == "ark":
            if not (st.session_state.get("ARK_API_KEY") or os.getenv("ARK_API_KEY")):
                err = "当前选择 ark，请先在左侧填写 ARK_API_KEY 并点击“确认保存 API 配置”。"
                st.session_state["chat_messages"].append({"role": "assistant", "content": err})
                st.chat_message("assistant").write(err)
                st.stop()
            if not ark_endpoint_id.strip():
                err = "当前选择 ark，请先在左侧填写 ARK Endpoint ID（形如 ep-xxx）并保存后再发送。"
                st.session_state["chat_messages"].append({"role": "assistant", "content": err})
                st.chat_message("assistant").write(err)
                st.stop()
            if not ark_model.strip():
                err = "当前选择 ark，请先在左侧填写 ARK Model 并保存后再发送。"
                st.session_state["chat_messages"].append({"role": "assistant", "content": err})
                st.chat_message("assistant").write(err)
                st.stop()
        elif llm_provider == "openai-compatible":
            if not (st.session_state.get("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY")):
                err = "当前选择 openai-compatible，请先在左侧填写 OPENAI_API_KEY 并点击“确认保存 API 配置”。"
                st.session_state["chat_messages"].append({"role": "assistant", "content": err})
                st.chat_message("assistant").write(err)
                st.stop()
            if not llm_base_url.strip():
                err = "当前选择 openai-compatible，请先在左侧填写 OpenAI-compatible Base URL 并保存后再发送。"
                st.session_state["chat_messages"].append({"role": "assistant", "content": err})
                st.chat_message("assistant").write(err)
                st.stop()

    plan, plan_tasks = _infer_plan(user_text)

    runs_dir = "runs"
    out_dir = "daily_docs"
    window_hours = int(days) * 24
    config = {
        "sources": "sources.json",
        "out": out_dir,
        "runs_dir": runs_dir,
        "limit": 5,
        "window_hours": window_hours,
        "official_window_hours": window_hours,
        "max_paper_ratio": 0.2,
        "min_official_items": 3,
        "focus_skill": _extract_focus_skill(user_text),
        "intent_text": user_text,
        "enable_openclaw": any(key in user_text.lower() for key in ["openclaw", "topclaw", "技能榜", "榜单", "skill"]),
        "use_llm": use_llm,
        "llm_provider": llm_provider,
        "llm_base_url": llm_base_url.strip(),
        "ark_model": ark_model.strip(),
        "ark_endpoint_id": ark_endpoint_id.strip(),
        "ark_api_key": st.session_state.get("ARK_API_KEY", ""),
        "allow_custom_llm_endpoint": False,
        "allow_insecure_ssl": True,
        "llm_prompt_variant": "auto",
        "pipeline_log": True,
        "gnews_enabled": gnews_enabled,
        "gnews_api_key": (gnews_api_key or st.session_state.get("GNEWS_API_KEY", "") or "").strip(),
        "gnews_lang": gnews_lang,
        "gnews_max": 5,
        "gnews_category": "行业资讯",
        "strict_intent_match": bool(strict_intent_match),
        "llm_intent_analysis": True,
        "gnews_per_keyword": True,
        "google_hk_search_enabled": True,
        "items_per_category_max": 5,
        "google_hk_serp_pool": 20,
        "google_hk_max_queries": 6,
        "google_playwright_headless": False,
        "google_playwright_user_data_dir": str(pathlib.Path(runs_dir).resolve() / "google_profile"),
    }
    if llm_provider == "openai-compatible" and openai_model.strip():
        os.environ["OPENAI_MODEL"] = openai_model.strip()

    with st.status("Agent 正在执行...", expanded=True) as status:
        def _progress_cb(step: dict) -> None:
            icon_map = {"started": "⏳", "ok": "✅", "warn": "⚠️"}
            step_status = step.get("status", "")
            icon = icon_map.get(step_status, "•")
            name = _step_label(step.get("name", "step"))
            detail = (step.get("detail") or "").strip()
            if step_status == "started":
                status.update(label=f"进行中：{name}", state="running")
            elif step_status == "ok":
                status.update(label=f"已完成：{name}", state="running")
            elif step_status == "warn":
                status.update(label=f"告警：{name}", state="running")
            status.write(f"{icon} {name}" + (f" - {detail}" if detail else ""))

        config["_progress_cb"] = _progress_cb
        state = run_with_graph(config=config, max_retries=max_retries)
        status.update(label="执行完成", state="complete")

    if state.get("result"):
        result = state["result"]
        doc_path = pathlib.Path(result["doc_path"])
        reply = f"已完成：共抓取 {result.get('items', 0)} 条。\n报告路径：`{doc_path}`"
        if doc_path.exists():
            md = doc_path.read_text(encoding="utf-8")
            preview = md
            reply += f"\n\n报告预览：\n\n{preview}"
        st.session_state["chat_messages"].append({"role": "assistant", "content": reply})
        st.chat_message("assistant").write(reply)

        audit = {
            "time": datetime.now().astimezone().isoformat(),
            "run_id": result.get("run_id"),
            "doc_path": result.get("doc_path"),
            "trace_file": result.get("trace_file"),
            "items": result.get("items"),
            "errors": result.get("errors"),
            "llm_provider": llm_provider,
            "mcp_tools": mcp_tools,
            "intent": user_text,
            "execution_mode": "langgraph",
            "execution_route": "langgraph",
            "plan": plan,
            "api_keys": {
                "ark": _mask_key(st.session_state.get("ARK_API_KEY", "")),
                "openai": _mask_key(st.session_state.get("OPENAI_API_KEY", "")),
            },
        }
        append_jsonl_record("runs/history.jsonl", audit)
    else:
        err = f"运行失败：{state.get('error', 'unknown error')}"
        st.session_state["chat_messages"].append({"role": "assistant", "content": err})
        st.chat_message("assistant").write(err)

