"""
单文件：新闻 Agent 用户记忆（长期 JSON + 可选 LLM 合并；短期对话在 Streamlit session_state）。

默认存储：{repo_root}/runs/user_news_memory.json
关闭：环境变量 USER_NEWS_MEMORY_ENABLED=false
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import uuid
from datetime import datetime, timezone
from typing import Any

VALID_CATEGORIES = frozenset(
    {
        "官方发布",
        "开源与工具",
        "行业资讯",
        "论文研究",
        "国内厂商动态",
        "垂直与趣味",
    }
)

_SCHEMA_VERSION = "1.0"


def _empty_memory() -> dict[str, Any]:
    return {
        "version": _SCHEMA_VERSION,
        "lastUpdated": "",
        "profile": "",
        "prefer_categories": [],
        "extra_keywords": [],
        "excluded_source_substrings": [],
    }


def memory_file_path(repo_root: pathlib.Path) -> pathlib.Path:
    return (repo_root / "runs" / "user_news_memory.json").resolve()


def load_user_memory(repo_root: pathlib.Path) -> dict[str, Any]:
    if not memory_enabled():
        return _empty_memory()
    path = memory_file_path(repo_root)
    if not path.is_file():
        return _empty_memory()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty_memory()
    if not isinstance(data, dict):
        return _empty_memory()
    out = _empty_memory()
    out["profile"] = str(data.get("profile") or "").strip()[:2000]
    cats = data.get("prefer_categories") or []
    if isinstance(cats, list):
        out["prefer_categories"] = [str(c).strip() for c in cats if str(c).strip() in VALID_CATEGORIES][:12]
    ek = data.get("extra_keywords") or []
    if isinstance(ek, list):
        out["extra_keywords"] = [str(x).strip() for x in ek if str(x).strip()][:24]
    ex = data.get("excluded_source_substrings") or []
    if isinstance(ex, list):
        out["excluded_source_substrings"] = [str(x).strip() for x in ex if str(x).strip()][:24]
    return out


def save_user_memory(repo_root: pathlib.Path, data: dict[str, Any]) -> None:
    path = memory_file_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _empty_memory()
    payload.update({k: data.get(k, payload[k]) for k in payload})
    payload["version"] = _SCHEMA_VERSION
    payload["lastUpdated"] = datetime.now(timezone.utc).isoformat()
    tmp = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def memory_enabled() -> bool:
    return (os.getenv("USER_NEWS_MEMORY_ENABLED", "true") or "true").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


_GENERIC_BROWSE_PHRASES = (
    "随便看看",
    "随便看下",
    "随便查下",
    "随便查查",
    "随便刷刷",
    "看看最近",
    "看看本周",
    "看看新闻",
    "看看资讯",
    "来点新闻",
    "来点资讯",
    "发我新闻",
    "发我资讯",
    "新闻汇总",
    "资讯汇总",
    "今日新闻",
    "本周新闻",
    "这周新闻",
)

_GENERIC_TIME_HINTS = ("最近", "本周", "这周", "今日", "今天", "近7天", "近一周", "近期")
_GENERIC_NEWS_HINTS = ("新闻", "资讯", "动态", "消息", "周报")

_EXPLICIT_ACTION_HINTS = (
    "查",
    "搜索",
    "检索",
    "搜一下",
    "查找",
    "找一下",
    "了解下",
    "了解一下",
    "给我找",
    "帮我找",
    "find",
    "search",
    "lookup",
)

_EXPLICIT_TOPIC_HINTS = (
    "agent",
    "agents",
    "multi-agent",
    "multi agent",
    "智能体",
    "多智能体",
    "harness",
    "engineering",
    "arxiv",
    "openai",
    "anthropic",
    "huggingface",
    "llm",
    "api",
    "sdk",
    "github",
    "多模态",
    "视频模型",
    "推理",
    "论文",
    "paper",
)


def _contains_any(text: str, kws: tuple[str, ...]) -> bool:
    t = text.lower()
    return any(k.lower() in t for k in kws)


def classify_query_mode(intent_text: str) -> str:
    """
    区分本轮检索模式（决定 User 记忆如何参与）：
    - generic：泛化浏览（如「看看最近新闻」）→ 可合并记忆关键词、板块偏好、重排加权。
    - explicit：显式主题（如「查 harness engineering」「视频模型」）→ 仅用当前句做检索，
      不把历史 User 记忆里的关键词/板块拼进检索；仍保留排除信源等「硬规则」。
    """
    t = (intent_text or "").strip()
    if not t:
        return "generic"
    # 先处理泛化短句：把「随便查下/看下」视为泛化浏览而非显式检索。
    if _contains_any(t, _GENERIC_BROWSE_PHRASES):
        return "generic"
    # 显式检索 / 指向具体主题（动作词 + 主题词联合命中时优先 explicit）。
    if _contains_any(t, _EXPLICIT_ACTION_HINTS) and _contains_any(t, _EXPLICIT_TOPIC_HINTS):
        return "explicit"
    if re.search(r"(github\.com|/|api\b|sdk\b|arxiv|paper|论文)", t, re.I) and _contains_any(t, _EXPLICIT_ACTION_HINTS):
        return "explicit"
    # 泛化浏览：最近/本周 + 新闻资讯，或极短「随便看看」类
    if re.match(
        r"^[\s]*("
        r"(我)?想?(要)?看看(.{0,8})?(最近|本周|今日)?(的)?(新闻|资讯|动态|消息)|"
        r"看看(.{0,6})?(最近|本周)?(的)?(新闻|资讯)|"
        r"最近(.{0,8})?(有)?什么(.{0,6})?(新闻|资讯|消息|动态)|"
        r"随便看看|随便看下|随便查下|随便查查|"
        r"(给|来|发)(我)?(.{0,6})?(点|一些)?(新闻|资讯)|"
        r"(今日|本周|这周)(的)?(新闻|资讯)(汇总)?|"
        r"来(点|一份)?(新闻|资讯|周报)?"
        r")[\s。！!？?…]*$",
        t,
        re.I,
    ):
        return "generic"
    if len(t) <= 36 and re.search(r"(新闻|资讯|动态|消息|周报)", t) and not re.search(
        r"(模型|论文|开源|api|github|harness|视频|生成|推理)", t, re.I
    ):
        return "generic"
    if _contains_any(t, _GENERIC_TIME_HINTS) and _contains_any(t, _GENERIC_NEWS_HINTS):
        return "generic"
    if _contains_any(t, _EXPLICIT_ACTION_HINTS) and _contains_any(t, _EXPLICIT_TOPIC_HINTS):
        return "explicit"
    return "explicit"


def _llm_classify_query_mode(intent_text: str, config: dict[str, Any]) -> str | None:
    """Use LLM to classify query mode. Returns 'generic'/'explicit' or None."""
    if not (intent_text or "").strip():
        return "generic"
    try:
        from run_daily_digest import call_chat_completion, resolve_llm_runtime
    except Exception:
        return None

    class _NS:
        pass

    ns = _NS()
    ns.llm_provider = str(config.get("llm_provider", "auto"))
    ns.llm_base_url = str(config.get("llm_base_url", ""))
    ns.allow_custom_llm_endpoint = bool(config.get("allow_custom_llm_endpoint", False))
    ns.ark_api_key = str(config.get("ark_api_key", "")).strip()
    ns.ark_endpoint_id = str(config.get("ark_endpoint_id", "")).strip()
    ns.ark_model = str(config.get("ark_model", "")).strip()
    try:
        _provider, base_url, api_key = resolve_llm_runtime(ns)
    except Exception:
        return None
    if not (api_key or "").strip():
        return None

    provider = str(config.get("llm_provider", "auto")).strip().lower()
    if provider == "openai-compatible":
        model = str(config.get("openai_model", "")).strip() or os.getenv("OPENAI_MODEL", "").strip() or "gpt-4o-mini"
    else:
        model = (
            str(config.get("ark_endpoint_id", "")).strip()
            or str(config.get("ark_model", "")).strip()
            or str(config.get("openai_model", "")).strip()
            or os.getenv("OPENAI_MODEL", "").strip()
            or "Doubao-Seed-1.6-lite"
        )
    sys_prompt = (
        "你是一个意图分类器。只做二分类并返回 JSON。\n"
        "分类定义：\n"
        "1) generic：泛化浏览，用户想“随便看看最近新闻/资讯/动态”，不限定具体主题词。\n"
        "2) explicit：显式主题检索，用户明确指定了主题/对象/术语并希望按该主题查找。\n"
        "判定规则可以参考：\n"
        "- “随便查下 / 随便看下 / 看看最近新闻”这类没有明确主题词和查询关键词的属于 generic。\n"
        "- 仅有动作词而无主题词（如“查”“搜一下”“看下”）属于 generic。\n"
        "- “查下 harness engineering / 搜 openai api 更新”这种有明确主题词和查询关键词的（比如“harness engineering”但不局限于这个词）属于 explicit。\n"
        "输出必须是 JSON：{\"mode\":\"generic|explicit\",\"confidence\":0-1,\"reason\":\"简短中文\"}"
    )
    user_prompt = f"用户输入：{intent_text}"
    try:
        raw = call_chat_completion(
            api_key=api_key.strip(),
            model=model,
            messages=[
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": user_prompt},
            ],
            base_url=base_url,
            timeout=30,
            allow_insecure_fallback=bool(config.get("allow_insecure_ssl", False)),
        )
    except Exception:
        return None
    txt = (raw or "").strip()
    txt = re.sub(r"^```(?:json)?\s*", "", txt, flags=re.I | re.M)
    txt = re.sub(r"\s*```\s*$", "", txt, flags=re.M).strip()
    try:
        obj = json.loads(txt)
    except Exception:
        return None
    mode = str((obj or {}).get("mode", "")).strip().lower()
    if mode in ("generic", "explicit"):
        return mode
    return None


def resolve_query_mode(intent_text: str, config: dict[str, Any]) -> str:
    """
    Query mode resolution strategy:
    1) LLM-first when enabled + key available
    2) lexical/regex baseline fallback (deterministic)
    """
    base = classify_query_mode(intent_text)
    if not (intent_text or "").strip():
        return base
    # Hard explicit: action + explicit topic should not be relaxed to generic by LLM.
    if _contains_any(intent_text, _EXPLICIT_ACTION_HINTS) and _contains_any(intent_text, _EXPLICIT_TOPIC_HINTS):
        return "explicit"
    use_llm = (os.getenv("QUERY_MODE_USE_LLM", "true") or "true").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )
    if use_llm:
        llm_mode = _llm_classify_query_mode(intent_text, config)
        if llm_mode in ("generic", "explicit"):
            return llm_mode
    return base


def exclusions_only_system_block(mem: dict[str, Any]) -> str:
    """显式主题轮次：仅注入排除信源，避免旧偏好关键词干扰。"""
    ex = mem.get("excluded_source_substrings") or []
    if not ex:
        return ""
    return "用户曾排除的信源名称关键词（仍生效）：" + "、".join(str(x) for x in ex[:12])


def memory_system_block_for_prompt(mem: dict[str, Any]) -> str:
    """注入工具 Agent system 提示的短块。"""
    lines: list[str] = []
    if mem.get("profile"):
        lines.append("用户长期偏好：" + str(mem["profile"])[:1200])
    if mem.get("prefer_categories"):
        lines.append("偏好板块：" + "、".join(str(x) for x in mem["prefer_categories"]))
    if mem.get("extra_keywords"):
        lines.append("常关注检索词：" + "、".join(str(x) for x in mem["extra_keywords"][:12]))
    if mem.get("excluded_source_substrings"):
        lines.append("不希望出现的信源关键词：" + "、".join(str(x) for x in mem["excluded_source_substrings"][:10]))
    return "\n".join(lines).strip()


def attach_memory_to_config(config: dict[str, Any], repo_root: pathlib.Path) -> None:
    """把长期记忆注入 config；按 classify_query_mode 决定全量注入或仅排除信源。"""
    if not memory_enabled():
        config["_query_mode"] = resolve_query_mode(str(config.get("intent_text", "") or ""), config)
        config["_user_memory_boost_keywords"] = []
        config["_user_memory_boost_categories"] = []
        return
    mem = load_user_memory(repo_root)
    intent_text = str(config.get("intent_text", "") or "")
    mode = resolve_query_mode(intent_text, config)
    config["_query_mode"] = mode

    excl = list(mem.get("excluded_source_substrings") or [])
    config["_user_memory_excluded_source_substrings"] = excl
    # 用于 generic 模式下的重排加分（与文件一致；explicit 时置空不加权）
    config["_user_memory_boost_keywords"] = [str(x).strip() for x in (mem.get("extra_keywords") or []) if str(x).strip()][
        :24
    ]
    config["_user_memory_boost_categories"] = [
        str(x).strip() for x in (mem.get("prefer_categories") or []) if str(x).strip() in VALID_CATEGORIES
    ][:12]

    if mode == "explicit":
        config["_user_memory_prefer_categories"] = []
        config["_user_memory_extra_keywords"] = []
        config["_user_memory_intent_augment"] = ""
        eb = exclusions_only_system_block(mem)
        config["_user_memory_system_block"] = eb
        return

    config["_user_memory_prefer_categories"] = list(config["_user_memory_boost_categories"])
    config["_user_memory_extra_keywords"] = list(config["_user_memory_boost_keywords"])
    blk = memory_system_block_for_prompt(mem)
    if blk:
        config["_user_memory_system_block"] = blk
    aug_parts: list[str] = []
    if mem.get("profile"):
        aug_parts.append(str(mem["profile"]))
    if mem.get("extra_keywords"):
        aug_parts.append("偏好检索词：" + "、".join(str(x) for x in mem["extra_keywords"][:16]))
    config["_user_memory_intent_augment"] = "\n".join(aug_parts).strip()


def rerank_items_by_user_memory_boost(
    items: list[dict[str, Any]],
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    """仅 generic 模式：按记忆关键词/偏好板块对条目加分后稳定重排（不删条目）。"""
    if config.get("_query_mode") != "generic":
        return items
    kws = [k.lower() for k in (config.get("_user_memory_boost_keywords") or []) if k]
    cats = set(config.get("_user_memory_boost_categories") or [])
    if not kws and not cats:
        return items

    def _hay(it: dict[str, Any]) -> str:
        return " ".join(
            [
                str(it.get("title", "")),
                str(it.get("summary", "")),
                str(it.get("source", "")),
                str(it.get("category", "")),
            ]
        ).lower()

    def _score(it: dict[str, Any]) -> float:
        h = _hay(it)
        s = 0.0
        for k in kws:
            if k in h:
                s += 2.0
        if it.get("category") in cats:
            s += 1.0
        return s

    indexed = list(enumerate(items))
    indexed.sort(key=lambda iv: (-_score(iv[1]), iv[0]))
    return [it for _, it in indexed]


def merge_memory_into_intent_plan(plan: dict[str, Any], config: dict[str, Any]) -> None:
    cats = list(config.get("_user_memory_prefer_categories") or [])
    if not cats:
        return
    cur = list(plan.get("prefer_categories") or [])
    seen = {str(x) for x in cur}
    for c in cats:
        if c in VALID_CATEGORIES and c not in seen:
            cur.append(c)
            seen.add(c)
    if cur:
        plan["prefer_categories"] = cur


def merge_memory_keywords(
    keywords: list[str],
    lower_kw: list[str],
    config: dict[str, Any],
) -> tuple[list[str], list[str]]:
    for kw in config.get("_user_memory_extra_keywords") or []:
        k = str(kw).strip()
        if not k:
            continue
        low = k.lower()
        if low not in lower_kw:
            keywords.append(k)
            lower_kw.append(low)
    return keywords, lower_kw


def filter_sources_by_memory_exclusions(
    sources: list[dict[str, Any]],
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    excl = [str(x).lower() for x in (config.get("_user_memory_excluded_source_substrings") or []) if str(x).strip()]
    if not excl:
        return sources
    out: list[dict[str, Any]] = []
    for s in sources:
        name = str(s.get("name", "")).lower()
        if any(sub in name for sub in excl):
            continue
        out.append(s)
    return out if out else sources


_MERGE_SYSTEM = """你是新闻阅读偏好记忆模块。根据「当前记忆 JSON」与「本轮对话片段」，输出更新后的完整 JSON（不要 markdown）。
字段说明：
- profile: 字符串，1~6 句中文，概括用户关心的技术方向、行业、读新闻习惯（无新信息则保持原 profile 或略改）。
- prefer_categories: 数组，元素只能是：官方发布、开源与工具、行业资讯、论文研究、国内厂商动态、垂直与趣味 之一；只填用户明确或强烈表现出的偏好，不确定则 []。
- extra_keywords: 数组，最多 20 条短检索词（中英文均可），用于 GNews/意图检索；去重。
- excluded_source_substrings: 数组，用户明确不想看的信源名称子串（小写匹配），如某媒体名的一部分；最多 20 条。

必须返回合法 JSON 对象，键为 profile, prefer_categories, extra_keywords, excluded_source_substrings。"""


def maybe_update_memory_with_llm(
    *,
    repo_root: pathlib.Path,
    user_message: str,
    assistant_summary: str,
    config: dict[str, Any],
) -> dict[str, Any] | None:
    """
    用 LLM 合并记忆；失败返回 None。需在 config 中具备与 pipeline 一致的 LLM 字段。
    """
    if not memory_enabled():
        return None
    um = (os.getenv("USER_NEWS_MEMORY_UPDATE", "true") or "true").strip().lower()
    if um in ("0", "false", "no", "off"):
        return None
    user_message = (user_message or "").strip()
    assistant_summary = (assistant_summary or "").strip()
    if not user_message and not assistant_summary:
        return None

    try:
        from run_daily_digest import call_chat_completion, resolve_llm_runtime
    except Exception:
        return None

    class _NS:
        pass

    ns = _NS()
    ns.llm_provider = str(config.get("llm_provider", "auto"))
    ns.llm_base_url = str(config.get("llm_base_url", ""))
    ns.allow_custom_llm_endpoint = bool(config.get("allow_custom_llm_endpoint", False))
    ns.ark_api_key = str(config.get("ark_api_key", "")).strip()
    ns.ark_endpoint_id = str(config.get("ark_endpoint_id", "")).strip()
    ns.ark_model = str(config.get("ark_model", "")).strip()

    try:
        _p, base_url, ak = resolve_llm_runtime(ns)
    except Exception:
        return None
    if not (ak or "").strip():
        return None
    provider = str(config.get("llm_provider", "auto")).strip().lower()
    if provider == "openai-compatible":
        model = str(config.get("openai_model", "")).strip() or os.getenv("OPENAI_MODEL", "").strip() or "gpt-4o-mini"
    else:
        model = (
            str(config.get("ark_endpoint_id", "")).strip()
            or str(config.get("ark_model", "")).strip()
            or str(config.get("openai_model", "")).strip()
            or "Doubao-Seed-1.6-lite"
        )
    current = load_user_memory(repo_root)
    conv = f"用户说：{user_message[:4000]}\n\n助手摘要：{assistant_summary[:4000]}"
    user_payload = (
        "当前记忆：\n"
        + json.dumps(current, ensure_ascii=False)
        + "\n\n本轮对话：\n"
        + conv
        + "\n\n请输出更新后的 JSON。"
    )
    try:
        raw = call_chat_completion(
            api_key=ak.strip(),
            model=model,
            messages=[
                {"role": "system", "content": _MERGE_SYSTEM},
                {"role": "user", "content": user_payload},
            ],
            base_url=base_url,
            timeout=90,
            allow_insecure_fallback=bool(config.get("allow_insecure_ssl", False)),
        )
    except Exception:
        return None
    parsed = _parse_memory_json(raw)
    if not parsed:
        return None
    save_user_memory(repo_root, parsed)
    return parsed


def _parse_memory_json(raw: str) -> dict[str, Any] | None:
    s = (raw or "").strip()
    s = re.sub(r"^```(?:json)?\s*", "", s, flags=re.IGNORECASE | re.MULTILINE)
    s = re.sub(r"\s*```\s*$", "", s, flags=re.MULTILINE).strip()
    try:
        obj = json.loads(s)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    fixed = _empty_memory()
    fixed["profile"] = str(obj.get("profile") or "")[:2000]
    pc = obj.get("prefer_categories") or []
    if isinstance(pc, list):
        fixed["prefer_categories"] = [str(x).strip() for x in pc if str(x).strip() in VALID_CATEGORIES][:12]
    ek = obj.get("extra_keywords") or []
    if isinstance(ek, list):
        fixed["extra_keywords"] = list(dict.fromkeys(str(x).strip() for x in ek if str(x).strip()))[:20]
    ex = obj.get("excluded_source_substrings") or []
    if isinstance(ex, list):
        fixed["excluded_source_substrings"] = list(dict.fromkeys(str(x).strip().lower() for x in ex if str(x).strip()))[
            :20
        ]
    return fixed
