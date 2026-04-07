#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import pathlib
import sys
from datetime import datetime

# `project/` on sys.path: works for `streamlit run project/app.py` (exec 本文件) 与直接 run ui/app.py
def _project_dir_path() -> pathlib.Path:
    start = pathlib.Path(__file__).resolve()
    if start.name == "app.py" and (start.parent / "ai_news_skill").is_dir():
        return start.parent
    return start.parents[2]


_project_dir = _project_dir_path()
if str(_project_dir) not in sys.path:
    sys.path.insert(0, str(_project_dir))

import streamlit as st

from ai_news_skill.orchestration.agent_tool_runner import run_with_tool_agent
from ai_news_skill.orchestration.langgraph_agent import run_with_graph
from ai_news_skill.user_news_memory import attach_memory_to_config, maybe_update_memory_with_llm, memory_enabled
from mcp_bridge import append_jsonl_record, discover_mcp_tools


def _repo_root() -> pathlib.Path:
    """仓库根（含 sources.json、.env）。通过 project/app.py exec 加载时 __file__ 在 project/ 下，不能用固定 parents[3]。"""
    start = pathlib.Path(__file__).resolve()
    for d in [start.parent, *start.parents]:
        if (d / "sources.json").is_file():
            return d
    try:
        return start.parents[3]
    except IndexError:
        return start.parent


def _use_tool_agent_mcp_route(use_llm: bool, repo_root: pathlib.Path) -> bool:
    return (
        (os.getenv("USE_TOOL_AGENT_MCP", "").strip().lower() in ("1", "true", "yes"))
        and use_llm
        and (repo_root / "mcp_servers.json").is_file()
    )


def _load_dotenv_file(path: pathlib.Path) -> None:
    if not path.exists():
        return
    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError:
        return
    for raw in raw_text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
    # 常见笔误：与代码里 os.getenv("ARK_MODEL") 对齐
    if os.getenv("ARK_MODEL", "").strip() == "" and (os.getenv("ARK_Model") or "").strip():
        os.environ["ARK_MODEL"] = os.environ["ARK_Model"].strip()


def _load_repo_dotenv() -> None:
    root = _repo_root()
    for name in (".env", ".ENV"):
        _load_dotenv_file(root / name)


def _declared_env_keys() -> set[str]:
    """.env / .ENV 里出现过的变量名（大小写均收录），用于侧栏只展示你列过的配置。"""
    keys: set[str] = set()
    root = _repo_root()
    for name in (".env", ".ENV"):
        p = root / name
        if not p.exists():
            continue
        try:
            lines = p.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for raw in lines:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k = line.split("=", 1)[0].strip()
            if k:
                keys.add(k)
                keys.add(k.upper())
    return keys


_load_repo_dotenv()

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
        tasks.insert(
            2,
            "检测到榜单需求：抓取 OpenClaw 热榜（输出在正文条目之后，作社区热度辅助；开源事实以 GitHub/官方条目为主）",
        )
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
    """与 agent_runtime.emit_step 的 name 对齐；未映射的步骤不展示内部英文名。"""
    mapping = {
        "intent_plan": "识别用户意图并规划抓取任务",
        "load_sources": "解析用户意图并抽取关注主题",
        "collect_plan": "按规划筛选信源与抓取范围",
        "site_crawl": "分信源抓取网页资讯",
        "rss_fetch": "RSS 信源拉取完成",
        "rss_source": "RSS 单信源拉取",
        "collect_news": "按系统内置信源抓取最近窗口内资讯",
        "gnews_search": "LLM 抽取搜索词并调用 GNews",
        "gnews_intent_gap": "意图未命中时补充 GNews 关键词检索",
        "public_api_feeds": "合并 Public API 扩展信源（NYT/Guardian/HN 等）",
        "fetch_article_body": "抓取原文链接正文（trafilatura）",
        "collect_filter": "按意图过滤抓取结果",
        "intent_rank": "按用户意图匹配并重排资讯（泛化浏览时可用用户记忆加权）",
        "official_backfill": "补充官方发布资讯",
        "balance_items": "去重、分组并做重要性排序",
        "openclaw": "抓取 OpenClaw 热榜",
        "llm_enrich": "按偏好生成中文摘要与解读",
        "render_and_write": "输出报告并保留可追踪历史",
        "cap_per_category": "按板块截断为固定条数",
    }
    return mapping.get(name, "执行步骤")


if "chat_messages" not in st.session_state:
    st.session_state["chat_messages"] = []
if "api_ready" not in st.session_state:
    # 根目录 .env 已填 LLM Key 时，无需每次再点「确认保存」
    st.session_state["api_ready"] = bool(
        (os.getenv("ARK_API_KEY") or "").strip() or (os.getenv("OPENAI_API_KEY") or "").strip()
    )
if "ARK_API_KEY" not in st.session_state and os.getenv("ARK_API_KEY"):
    st.session_state["ARK_API_KEY"] = os.getenv("ARK_API_KEY", "")
if "OPENAI_API_KEY" not in st.session_state and os.getenv("OPENAI_API_KEY"):
    st.session_state["OPENAI_API_KEY"] = os.getenv("OPENAI_API_KEY", "")
if "GNEWS_API_KEY" not in st.session_state and os.getenv("GNEWS_API_KEY"):
    st.session_state["GNEWS_API_KEY"] = os.getenv("GNEWS_API_KEY", "")

def _env_int(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


_declared = _declared_env_keys()
_show_ark = any(k in _declared for k in ("ARK_API_KEY", "ARK_ENDPOINT_ID", "ARK_MODEL", "ARK_Model"))
_show_openai = any(
    k in _declared for k in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL")
)
_show_gnews = "GNEWS_API_KEY" in _declared
_show_public_feeds = "PUBLIC_API_FEEDS" in _declared or "PUBLIC_API_FEED_MAX" in _declared
_show_digest_items_cap = "DIGEST_UI_ITEMS_CAP" in _declared
_show_digest_gnews_max = "DIGEST_UI_GNEWS_MAX" in _declared

# 下面聊天区会用到；若某块未在 .env 列出，则用安全默认且不在侧栏展示
llm_provider = "ark"
llm_base_url = ""
ark_model = os.getenv("ARK_MODEL") or os.getenv("ARK_Model") or "Doubao-Seed-1.6-lite"
ark_endpoint_id = os.getenv("ARK_ENDPOINT_ID", "")
openai_model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
ark_api_key = ""
openai_api_key = ""
gnews_enabled = False
gnews_api_key = ""
gnews_lang = "en"
public_api_feeds = (os.getenv("PUBLIC_API_FEEDS", "") or "").strip()
try:
    public_api_feed_max = max(1, min(30, int(os.getenv("PUBLIC_API_FEED_MAX", "8"))))
except ValueError:
    public_api_feed_max = 8
items_cap = _env_int("DIGEST_UI_ITEMS_CAP", 100)
gnews_cap = _env_int("DIGEST_UI_GNEWS_MAX", items_cap)

with st.sidebar:
    st.header("运行设置")
    days = st.slider("时间窗口（天）", min_value=1, max_value=30, value=7)
    if _show_digest_items_cap:
        items_cap = st.number_input(
            "每信源 / RSS / 每板块最多条数",
            min_value=1,
            max_value=500,
            value=_env_int("DIGEST_UI_ITEMS_CAP", 100),
            step=1,
            help="同步写入 limit、items_per_category_max。",
        )
    else:
        st.caption("每板块条数使用默认（可在 .env 增加 `DIGEST_UI_ITEMS_CAP` 后在侧栏调节）。")
    if _show_gnews and _show_digest_gnews_max:
        gnews_cap = st.number_input(
            "GNews 最多条数",
            min_value=1,
            max_value=500,
            value=_env_int("DIGEST_UI_GNEWS_MAX", items_cap),
            step=1,
        )
    elif _show_gnews:
        gnews_cap = _env_int("DIGEST_UI_GNEWS_MAX", items_cap)
    st.caption(
        "时间窗口 = 天数×24 小时；**改窗口或条数后请再发一条消息**才会重跑。"
    )
    st.caption(
        "抓取结果默认**仅保留与意图关键词匹配的条目**（与原先侧栏「严格」一致），由后台 "
        "`STRICT_INTENT_MATCH` 控制，默认开启；需在 .env 写 `STRICT_INTENT_MATCH=false` 可关闭。"
    )
    use_llm = st.checkbox("启用 LLM 解读", value=True)

    if not _show_ark and not _show_openai:
        st.warning("请在仓库根目录 `.env` / `.ENV` 中至少写明 `ARK_*` 或 `OPENAI_*`，侧栏才会出现对应 LLM 配置。")
    else:
        st.header("LLM（与 .env 中列出的项一致）")
        if _show_ark and _show_openai:
            llm_provider = st.selectbox("LLM Provider", ["ark", "openai-compatible"], index=0)
        elif _show_openai:
            llm_provider = "openai-compatible"
        else:
            llm_provider = "ark"

        if llm_provider == "ark" and _show_ark:
            ark_endpoint_id = st.text_input(
                "ARK_ENDPOINT_ID",
                value=os.getenv("ARK_ENDPOINT_ID", ""),
                help="来自 .env；可在此覆盖当前会话",
            )
            ark_model = st.text_input(
                "ARK_MODEL",
                value=os.getenv("ARK_MODEL") or os.getenv("ARK_Model") or "Doubao-Seed-1.6-lite",
            )
            ark_api_key = st.text_input(
                "ARK_API_KEY",
                value="",
                type="password",
                help="留空则使用 .env 中已加载的值；仅在需临时覆盖时填写",
            )
        if llm_provider == "openai-compatible" and _show_openai:
            llm_base_url = st.text_input(
                "OPENAI_BASE_URL",
                value=os.getenv("OPENAI_BASE_URL", ""),
            )
            openai_model = st.text_input(
                "OPENAI_MODEL",
                value=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            )
            openai_api_key = st.text_input(
                "OPENAI_API_KEY",
                value="",
                type="password",
                help="留空则使用 .env 中已加载的值",
            )

    if _show_gnews:
        st.header("GNews")
        _gnews_default = bool((st.session_state.get("GNEWS_API_KEY") or os.getenv("GNEWS_API_KEY", "")).strip())
        gnews_enabled = st.checkbox(
            "启用 GNews",
            value=_gnews_default,
            help="搜索词由 LLM 从聊天中抽取；Key 来自 .env 的 GNEWS_API_KEY",
        )
        gnews_api_key = st.text_input(
            "GNEWS_API_KEY（留空则用 .env）",
            value="",
            type="password",
        )
        gnews_lang = st.selectbox("GNews 语言", ["en", "zh"], index=0)

    if _show_public_feeds:
        st.header("扩展 API 信源")
        public_api_feeds = st.text_input(
            "PUBLIC_API_FEEDS",
            value=os.getenv("PUBLIC_API_FEEDS", ""),
            help="逗号分隔 id；与 public_api_feeds.py 中一致",
        )
        try:
            _paf_max_default = int(os.getenv("PUBLIC_API_FEED_MAX", "8"))
        except ValueError:
            _paf_max_default = 8
        public_api_feed_max = st.number_input(
            "PUBLIC_API_FEED_MAX",
            min_value=1,
            max_value=30,
            value=max(1, min(30, _paf_max_default)),
        )

    if st.button("确认保存 API 配置", use_container_width=True):
        if ark_api_key.strip():
            st.session_state["ARK_API_KEY"] = ark_api_key.strip()
            os.environ["ARK_API_KEY"] = ark_api_key.strip()
        if openai_api_key.strip():
            st.session_state["OPENAI_API_KEY"] = openai_api_key.strip()
            os.environ["OPENAI_API_KEY"] = openai_api_key.strip()
        if gnews_api_key.strip():
            st.session_state["GNEWS_API_KEY"] = gnews_api_key.strip()
            os.environ["GNEWS_API_KEY"] = gnews_api_key.strip()
        if ark_endpoint_id.strip():
            os.environ["ARK_ENDPOINT_ID"] = ark_endpoint_id.strip()
        if ark_model.strip():
            os.environ["ARK_MODEL"] = ark_model.strip()
        st.session_state["api_ready"] = True
    if st.session_state.get("api_ready"):
        st.success("已就绪：可直接在下方输入意图执行（或再点保存以覆盖会话中的 Key）")
    else:
        st.warning("请在 .env 中配置 LLM Key，或填写上方密码框后点「确认保存 API 配置」")

    st.header("历史记录")
    history_path = _repo_root() / "runs" / "history.jsonl"
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

mcp_tools = discover_mcp_tools(str(_repo_root() / "mcp_servers.json"))

user_text = st.chat_input("输入你的新闻意图，发送后将自动执行（不需要额外按钮）")
if user_text:
    st.session_state["chat_messages"].append({"role": "user", "content": user_text})
    st.chat_message("user").write(user_text)

    if not st.session_state.get("api_ready"):
        err = "请先在左侧 API 配置中输入 key 并点击“确认保存 API 配置”。"
        st.session_state["chat_messages"].append({"role": "assistant", "content": err})
        st.chat_message("assistant").write(err)
        st.stop()

    _eff_ark_key = (st.session_state.get("ARK_API_KEY") or os.getenv("ARK_API_KEY", "")).strip()
    _eff_openai_key = (st.session_state.get("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY", "")).strip()
    if _eff_ark_key:
        os.environ["ARK_API_KEY"] = _eff_ark_key
    if _eff_openai_key:
        os.environ["OPENAI_API_KEY"] = _eff_openai_key
    _eff_gnews = (gnews_api_key or st.session_state.get("GNEWS_API_KEY", "") or os.getenv("GNEWS_API_KEY", "")).strip()
    if _eff_gnews:
        st.session_state["GNEWS_API_KEY"] = _eff_gnews
        os.environ["GNEWS_API_KEY"] = _eff_gnews

    if use_llm:
        if llm_provider == "ark":
            if not _eff_ark_key:
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
            if not _eff_openai_key:
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

    _root = _repo_root()
    runs_dir = str(_root / "runs")
    out_dir = str(_root / "daily_docs")
    window_hours = int(days) * 24
    config = {
        "sources": str(_root / "sources.json"),
        "out": out_dir,
        "runs_dir": runs_dir,
        "limit": int(items_cap),
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
        "ark_api_key": _eff_ark_key,
        "allow_custom_llm_endpoint": False,
        "allow_insecure_ssl": True,
        "llm_prompt_variant": "auto",
        "pipeline_log": True,
        "gnews_enabled": gnews_enabled and bool(_eff_gnews),
        "gnews_api_key": _eff_gnews,
        "gnews_lang": gnews_lang,
        "gnews_max": int(gnews_cap),
        "gnews_category": "行业资讯",
        "llm_intent_analysis": True,
        "gnews_per_keyword": True,
        "public_api_feeds": (public_api_feeds or "").strip() if _show_public_feeds else "",
        "allow_os_public_api_feeds": bool(_show_public_feeds),
        "public_api_feed_max": int(public_api_feed_max),
        "items_per_category_max": int(items_cap),
        "final_items_total": 10,
        # I/O 并行（见 run_daily_digest / agent_runtime）；也可用环境变量覆盖
        "collect_news_max_workers": int(os.getenv("COLLECT_NEWS_MAX_WORKERS", "12")),
        "excerpt_fetch_max_workers": int(os.getenv("EXCERPT_FETCH_MAX_WORKERS", "12")),
        "site_crawl_max_workers": int(os.getenv("SITE_CRAWL_MAX_WORKERS", "4")),
        "gnews_max_workers": int(os.getenv("GNEWS_MAX_WORKERS", "6")),
    }
    if _use_tool_agent_mcp_route(use_llm, _root):
        config["mcp_servers_json"] = str(_root / "mcp_servers.json")
    attach_memory_to_config(config, _root)
    if llm_provider == "openai-compatible" and openai_model.strip():
        os.environ["OPENAI_MODEL"] = openai_model.strip()

    try:
        _mr = int(os.getenv("DIGEST_MAX_RETRIES", "2"))
    except ValueError:
        _mr = 2
    max_retries = max(0, min(5, _mr))

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
            # 不展示 detail：避免本地路径、信源名称、内部参数泄露到界面
            if step_status == "warn" and detail:
                safe = detail
                if len(safe) > 220:
                    safe = safe[:217] + "..."
                status.write(f"{icon} {name} — {safe}")
            else:
                status.write(f"{icon} {name}")

        config["_progress_cb"] = _progress_cb
        if _use_tool_agent_mcp_route(use_llm, _root):
            state = run_with_tool_agent(config=config, user_intent=user_text, max_retries=max_retries)
        else:
            state = run_with_graph(config=config, max_retries=max_retries)
        status.update(label="执行完成", state="complete")

    if state.get("result"):
        result = state["result"]
        doc_path = pathlib.Path(result["doc_path"])
        reply = (
            f"本次时间窗口：**{days} 天**（{window_hours} 小时）；"
            f"条数上限：每信源/板块 **{items_cap}**，GNews **{gnews_cap}**。\n"
            f"最终报告固定输出上限：**10 条**。\n"
            f"已完成：共抓取 {result.get('items', 0)} 条。\n报告路径：`{doc_path}`"
        )
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
            "execution_mode": "tool_agent_mcp" if _use_tool_agent_mcp_route(use_llm, _root) else "langgraph",
            "execution_route": state.get("route", "langgraph"),
            "plan": plan,
            "api_keys": {
                "ark": _mask_key(st.session_state.get("ARK_API_KEY", "")),
                "openai": _mask_key(st.session_state.get("OPENAI_API_KEY", "")),
            },
        }
        append_jsonl_record(str(_repo_root() / "runs" / "history.jsonl"), audit)
        if use_llm and memory_enabled():
            summ = reply[:1200] if reply else ""
            try:
                maybe_update_memory_with_llm(
                    repo_root=_root,
                    user_message=user_text,
                    assistant_summary=summ,
                    config=config,
                )
            except Exception:  # noqa: BLE001
                pass
    else:
        err = f"运行失败：{state.get('error', 'unknown error')}"
        st.session_state["chat_messages"].append({"role": "assistant", "content": err})
        st.chat_message("assistant").write(err)

