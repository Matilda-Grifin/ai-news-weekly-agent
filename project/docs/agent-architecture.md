# AI News Digest Agent — Architecture

> **说明**：本仓库是「资讯周报 / 日报」编排型 Agent：**主路径**为 LangGraph 状态机 + `agent_runtime` 流水线；**可选路径**为 LangChain Tool-Calling，失败时回退 LangGraph。

路径可以记成：

用户一句话 → `intent_text`（以及 focus_skill / enable_openclaw 等）→ `run_with_graph` → **intent**：`intent_plan_stage`（**可选 LLM** 将意图拆成多条检索短语；失败时按逗号分句规则回退）→ **collect**：**站点定制爬虫（优先）→ RSS（兜底）** → **按短语分别 GNews**（可关）→ **按短语 Playwright 搜 Google HK**（可选，见 `ai_news_skill/crawlers/google_hk_playwright.py`）→ 内联 `openclaw_stage` → enrich → write → persist。

**语言**：中文

---

## Table of Contents

- [Architecture Overview](#architecture-overview)
- [The Core Pipeline（主路径）](#the-core-pipeline主路径)
- [Directory Reference](#directory-reference)
- [Data Flow: A Single Run Lifecycle](#data-flow-a-single-run-lifecycle)
- [LangGraph State Machine](#langgraph-state-machine)
- [Tool System](#tool-system)
- [State & Persistence](#state--persistence)
- [Stage Details（分步说明）](#stage-details分步说明)
- [Intent → 多关键词与 Google HK](#intent--多关键词与-google-hk)（含 Playwright 镜像安装）
- [Optional: Tool-Calling Route](#optional-tool-calling-route)

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         ENTRY / UI LAYER                                 │
│  app.py (Streamlit) ──> 组装 config(intent_text, keys, gnews, openclaw) │
│       └── _progress_cb ──> emit_step 映射到界面进度条                    │
└────────────────────────────────┬────────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                      ORCHESTRATION LAYER                                 │
│                                                                          │
│   run_with_graph(config)  ◄────────────────────────────────────┐       │
│         │                                                         │       │
│         │  StateGraph(DigestState)  nodes:                        │       │
│         │  bootstrap → intent → collect → enrich → write          │       │
│         │              → persist → END                            │       │
│         │  （collect 节点末尾内联 openclaw_stage，与独立图节点等价）│       │
│         │                                                         │       │
│         └── 失败重试: 回跳 collect（attempt++，sleep ~1.2s）       │       │
│                                                                          │
│   run_with_tool_agent (可选) ──> AgentExecutor + 工具 ───────────┘       │
│         └── 未拿到 result 或异常 ──> 回退 run_with_graph                   │
└────────────────────────────────┬────────────────────────────────────────┘
                                 │
              ┌──────────────────┼──────────────────┐
              ▼                  ▼                  ▼
┌──────────────────┐ ┌─────────────────┐ ┌──────────────────┐
│  agent_runtime   │ │ run_daily_digest │ │ model / middleware│
│  intent_plan_*   │ │ collect_news     │ │ llm_factory       │
│  collect_stage   │ │ enrich (LLM)     │ │ pipeline_middleware│
│  openclaw_stage  │ │ render_markdown  │ │ timed_call, logs  │
│  enrich_stage    │ │ dedupe, balance  │ │                   │
│  write_stage     │ │ trafilatura body │ │                   │
└────────┬─────────┘ └────────┬─────────┘ └────────┬───────────┘
         │                    │                     │
         └────────────────────┼─────────────────────┘
                              ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         EXTERNAL / IO                                    │
│  sources.json + 站点爬虫    GNews API    （可选）Google HK / Playwright  │
│  OpenClaw 榜单         Ark/OpenAI LLM（意图拆词 + enrich）                 │
│  runs/<run_id>/trace.json    daily_docs/*.md    runs/history.jsonl       │
└─────────────────────────────────────────────────────────────────────────┘
```

**与「最小 Agent 循环」的对照**：通用 Coding Agent 是 `User → LLM → tool_use? → execute → loop`（见 [learn-coding-agent · The Agent Pattern](https://github.com/sanbuphy/learn-coding-agent)）。本项目中 **生产默认** 是 **确定性图编排**（LangGraph），LLM 出现在 **GNews 搜词推断**、**条目解读 enrich** 等子步骤中；**Tool Agent** 路径才是「模型选工具」的完整循环。

---

## The Core Pipeline（主路径）

```
                    DIGEST PIPELINE (agent_runtime 语义顺序)
                    =====================================

    config
       │
       ▼
    init_run_context ──> run_id, run_dir, trace.json 骨架
       │
       ▼
    intent_plan_stage ──> keywords[]（LLM JSON 或逗号分句回退）, prefer_categories…
       │
       ▼
    collect_stage ──────> 站点爬虫优先 → RSS 兜底 → GNews 按短语分别请求（可配置）→ 可选 Google HK
       │                    → 关键词过滤 / 官方回填
       │
       ▼
    intent_stage ───────> balance_items, cap_papers_by_ratio
       │
       ▼
    openclaw_stage ─────> 可选：热榜 / focus_skill（LangGraph：写在 collect 节点末尾）
       │
       ▼
    enrich_stage ───────> 正文摘录 + 可选 LLM 生成 overview / 要点…
       │
       ▼
    write_stage ────────> render_markdown + write_doc
       │
       ▼
    finalize_ok / finalize_failed ──> 写全量 trace.json


    LangGraph 将上述阶段拆成节点：collect 节点内顺序为 collect_stage →
    intent_stage → openclaw_stage；「意图规划」单独占 intent 节点。
    run_digest_pipeline（无 LangGraph）仍为同一函数顺序，行为一致。
```

---

## Directory Reference

```
ai_news_skill/
├── app.py                      # Streamlit：意图输入、config、run_with_graph、历史 JSONL
├── langgraph_agent.py          # StateGraph：DigestState、节点、条件边、build_workflow
├── agent_runtime.py            # 流水线阶段：collect / openclaw / enrich / write / finalize
├── agent_tool_runner.py        # Tool-Calling 编排 + 回退 LangGraph
├── run_daily_digest.py         # RSS、GNews、LLM enrich、Markdown 渲染等具体实现
├── digest_tools.py             # LangChain @tool 注册（DIGEST_AGENT_TOOLS）
├── ai_news_skill/crawlers/google_hk_playwright.py  # 可选：Playwright 抓取 google.com.hk SERP（根目录保留 wrapper）
├── scripts/test_intent_step.py # 仅测 intent_plan（拆检索短语）
├── mcp_bridge.py               # MCP 配置发现、history.jsonl 追加
├── model/
│   └── llm_factory.py          # build_chat_openai 等
├── middleware/
│   └── pipeline_middleware.py  # 日志、timed_call
├── chains/
│   └── news_enrich_chain.py    # 富化链（若被引用）
├── prompts/
│   └── digest_llm_prompts.py
├── sources.json                # 信源列表（默认）
├── daily_docs/                 # 输出 Markdown（默认 out）
└── runs/                       # trace.json、history.jsonl
```

---

## Data Flow: A Single Run Lifecycle

```
 USER INTENT + SIDEBAR CONFIG
     │
     ▼
 app.py: config = { intent_text, window_hours, gnews_*, enable_openclaw,
                    focus_skill, use_llm, keys, ... }
     │
     ▼
 run_with_graph(config, max_retries)
     │
     ▼
 ┌─→ bootstrap: init_run_context，DigestState 填 trace 路径、空列表
 │   │
 │   ▼
 │   intent: intent_plan_stage → intent_plan
 │   │
 │   ▼
 │   collect: collect_stage + intent_stage + openclaw_stage
 │              → sources, items, errors, openclaw_*（可空）
 │   │
 │   ▼
 │   enrich: 正文 + llm_enrich → llm_overview, core_points, ...
 │   │
 │   ▼
 │   write: render_markdown + write_doc → result{ doc_path, items, ... }
 │   │
 │   └── 任一步异常? ──> state.error, attempt++
 │           │
 │           ├── attempt ≤ max_retries ──> sleep ──> 回到 collect
 │           └── 否则 ──> persist（失败 trace）
 │
 ▼
 persist: finalize_ok 或 finalize_failed；成功时 save_run_record_tool
     │
     ▼
 UI 展示报告路径、预览；append_jsonl_record(history)
```

---

## LangGraph State Machine

```
                         [ START ]
                             │
                             ▼
                      ┌─────────────┐
                      │  bootstrap  │
                      └──────┬──────┘
                             │
                             ▼
                      ┌─────────────┐
         ┌───────────│   intent    │───────────┐
         │           └──────┬──────┘           │
         │  error & retry    │ ok               │ error & exhausted
         │                   ▼                  ▼
         │           ┌─────────────┐     ┌───────────┐
         │           │  collect    │     │  persist  │──┐
         │           │ +openclaw │     └───────────┘  │
         │           └──────┬──────┘                   │
         │                  │                          │
         └──────────────────┤ error & retry            │
                            │ ok                       │
                            ▼                          │
                     ┌─────────────┐                   │
                     │   enrich    │                   │
                     └──────┬──────┘                   │
                            │                          │
                            ▼                          │
                     ┌─────────────┐                   │
                     │   write     │                   │
                     └──────┬──────┘                   │
                            │                          │
                            └──────────┬───────────────┘
                                       ▼
                                 ┌───────────┐
                                 │  persist  │
                                 └─────┬─────┘
                                       │
                                       ▼
                                    [ END ]
```

**`DigestState`（主要字段）**

- 输入：`config`、`attempt`、`max_retries`
- 过程：`trace`、`trace_file`、`output_dir`、`run_id`、`run_dir`、`sources`、`items`、`errors`、`intent_plan`、`openclaw_*`、`llm_*`
- 输出：`result` 或 `error`

---

## Tool System

```
                    TOOLS（本仓库）
                    ==============

    digest_tools.py (注册表，供未来 create_agent / ReAct)
    ├── run_weekly_digest_pipeline(config_json)  → run_digest_pipeline
    └── fetch_openclaw_stars_tool(focus_skill)   → OpenClaw JSON

    langgraph_agent.py (@tool，与图并存)
    ├── run_digest_tool(config)                  → run_digest_pipeline
    └── save_run_record_tool(record)             → 占位回显

    agent_tool_runner.py（AgentExecutor 专用）
    ├── run_digest_with_context(config_patch_json)
    └── fetch_openclaw_with_context(focus_skill)

    规则（Tool Agent 的 system prompt）：
    1) 默认必须跑 run_digest_with_context 生成正式报告
    2) 仅当用户明确提 openclaw/榜单等时再 fetch_openclaw
    3) 回答须基于工具结果
```

---

## State & Persistence

```
    PERSISTENCE
    ===========

    runs/<run_id>/
    └── trace.json              ← steps[]、status、result / error、耗时

    daily_docs/
    └── <generated>.md          ← 周报正文

    runs/history.jsonl            ← app 成功运行后的审计行（意图、路径、条数）

    mcp_servers.json              ← MCP 仅 discover 展示名，不阻塞本地运行
```

---

## Stage Details（分步说明）

### `collect_stage`（顺序）

1. **load_sources**：读 `sources.json`（或 `config["sources"]`）。
2. **collect_plan**：按 `intent_plan` 裁剪类目 / 信源名关键词。
3. **collect_news**：RSS 抓取，`limit` × `window_hours`。
4. **GNews（可选）**：`gnews_enabled` + Key → `infer_gnews_search_query` + `fetch_gnews_for_pipeline`，与 RSS **去重合并**（GNews 在前）。
5. **collect_filter / gnews_intent_gap**：关键词匹配；严格模式无命中时可补一轮 OR 关键词检索。
6. **official_backfill（可选）**：官方条数不足且非「严格无匹配清空」时，仅官方源加大窗口再抓。

### `intent_stage`（在 `_collect_node` 内接在 collect 后）

1. **balance_items**、**cap_papers_by_ratio**：配比与论文占比上限。

### `openclaw_stage`（与 collect 同节点）

仍实现于 `agent_runtime.openclaw_stage`；**LangGraph** 在 `_collect_node` 中于 `intent_stage` 之后调用，trace 中仍有独立 `openclaw` 步骤。未开启 `enable_openclaw` 且无 `focus_skill` 则立即返回空；否则 **fetch_openclaw_stars_top**（`top_n=3`）。`run_digest_pipeline` 仍单独调用该函数，顺序不变。

### `enrich_stage`

1. **attach_content_excerpts_to_items**（有 items 时）。
2. `use_llm` 为 False 则跳过 LLM。
3. 否则 **resolve_llm_runtime** → **enrich_items_with_llm**（可 **timed_call**）。

### `write_stage`

**render_markdown** → **write_doc** → emit **render_and_write**。

---

## Intent → 多关键词与 Google HK

| 配置项（`app.py` / `config`） | 含义 |
|------------------------------|------|
| `llm_intent_analysis` | 为 True 且 LLM 可用时，调用 `llm_extract_intent_search_queries`，输出 JSON 数组或 `{"queries":[...]}`。 |
| `gnews_per_keyword` | 为 True 时，对 `plan["keywords"]` **每个短语各调一次** `fetch_gnews_articles`；为 False 时沿用「单条 infer_gnews + 合并 query」旧逻辑。 |
| `google_hk_search_enabled` | **默认 True**。`build_google_web_items_valid_only`：按 SERP 顺序尝试抓取正文，**仅保留** `is_valid_article_excerpt` 通过的条目，凑满 `items_per_category_max`（默认 5）条「网页检索」或候选耗尽；失败则试下一条 URL。 |
| `items_per_category_max` | 默认 **5**：`cap_items_per_category` 对每个 **板块**（category）最多保留 5 条。 |
| `limit` / `gnews_max` | 后端固定 **5**（Streamlit 不再提供条数滑块）。 |
| **LLM 解读** | `enrich_items_with_llm` 要求每条 `core_cn` 约 **200–300 汉字**，基于 `content_excerpt` 压缩，英文译中文。 |

**Playwright 浏览器下载（卡点与镜像）**

- 默认从微软 CDN 拉取 Chromium（约 160MB+），国内常卡在 **30%～70%**，属于 **下载未完成或极慢**，不是 Python 死锁。
- **可用镜像**（官方支持 [PLAYWRIGHT_DOWNLOAD_HOST](https://playwright.dev/python/docs/browsers#download-from-artifact-repository)）：  
  `export PLAYWRIGHT_DOWNLOAD_HOST=https://registry.npmmirror.com/-/binary/playwright`  
  然后再执行：`python3 -m playwright install chromium`  
  仓库提供脚本：`bash scripts/install_playwright_chromium_mirror.sh`（已设上述镜像，可改环境变量覆盖）。
- **是否下完**：在本机执行 `python3 -m playwright install --list`，若列出 `chromium` 且路径存在即已就绪；缓存目录一般为 macOS `~/Library/Caches/ms-playwright`。
- 仍慢时可设：`export PLAYWRIGHT_DOWNLOAD_CONNECTION_TIMEOUT=120000`（毫秒）延长超时。

**回退**：LLM 失败或无 Key 时，使用 `extract_intent_keywords`；若用户输入含中英文逗号，优先用 `_clause_core_phrases` 按分句得到短语（例如「…新闻，还想…进展」→ 两条独立检索）。

**合规**：自动化访问搜索引擎可能违反服务条款；见 `ai_news_skill/crawlers/google_hk_playwright.py` 顶部注释。若需关闭，在调用 `run_with_graph` / `run_digest_pipeline` 的 `config` 中设 `google_hk_search_enabled: False`。

**单步调试**：`python3 scripts/test_intent_step.py "你的句子"` 只跑 `intent_plan_stage` 并打印 `keywords`。

---

## Optional: Tool-Calling Route

```
 User intent
     │
     ▼
 AgentExecutor(create_tool_calling_agent)
     │
     ├── LLM 多轮 ──> run_digest_with_context ──> run_digest_pipeline
     │                    └── holder["result"] = result
     │
     └── （可选）fetch_openclaw_with_context

     if holder["result"] is dict:
            return { result, route: "tool_agent" }
     else:
            return run_with_graph(...)  # route: "langgraph_fallback"
```

**说明**：Streamlit **默认**只走 `run_with_graph`，不经 Tool Agent，路径更稳定、trace 更直。

---

## Reference

- 外部「CLI Agent 解剖」式写法参考：[sanbuphy/learn-coding-agent](https://github.com/sanbuphy/learn-coding-agent)（README 中的 *Architecture Overview*、*The Agent Pattern*、*Data Flow* 等）。
- 本仓库实现入口：`langgraph_agent.py`、`agent_runtime.py`、`agent_tool_runner.py`、`app.py`。

---

*文档与仓库 `main` 行为对齐，便于 onboarding。*
