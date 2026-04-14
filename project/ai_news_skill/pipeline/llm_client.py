"""LLM API client: chat completion, URL validation, runtime resolution."""

from __future__ import annotations

import argparse
import json
import os
import re
import ssl
import urllib.parse
import urllib.request
from typing import Any

from ai_news_skill.core.exceptions import ConfigError, LLMClientError
from ai_news_skill.core.http_client import USER_AGENT

ARK_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3/chat/completions"
DEFAULT_ALLOWED_LLM_HOSTS = {
    "ark.cn-beijing.volces.com",
    "api.openai.com",
    "openrouter.ai",
}


def call_chat_completion(
    api_key: str,
    model: str,
    messages: list[dict],
    base_url: str,
    timeout: int = 90,
    allow_insecure_fallback: bool = False,
) -> str:
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.2,
    }
    req = urllib.request.Request(
        base_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ssl.create_default_context()) as resp:
            body = resp.read().decode("utf-8", errors="ignore")
    except ssl.SSLCertVerificationError:
        if not allow_insecure_fallback:
            raise
        with urllib.request.urlopen(
            req, timeout=timeout, context=ssl._create_unverified_context()
        ) as resp:
            body = resp.read().decode("utf-8", errors="ignore")
    except urllib.error.URLError as ex:
        if not allow_insecure_fallback:
            raise
        if isinstance(ex.reason, ssl.SSLCertVerificationError):
            with urllib.request.urlopen(
                req, timeout=timeout, context=ssl._create_unverified_context()
            ) as resp:
                body = resp.read().decode("utf-8", errors="ignore")
        else:
            raise
    data = json.loads(body)
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as ex:
        raise LLMClientError(f"invalid chat completion response: {ex}") from ex


def normalize_chat_completions_url(raw_url: str) -> str:
    url = (raw_url or "").strip()
    if not url:
        return ""
    if url.endswith("/v1"):
        return url + "/chat/completions"
    if url.endswith("/chat/completions"):
        return url
    return url.rstrip("/") + "/chat/completions"


def validate_https_url(url: str, field_name: str) -> urllib.parse.ParseResult:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme.lower() != "https" or not parsed.netloc:
        raise ConfigError(f"{field_name} must be a valid https URL")
    return parsed


def allowed_llm_hosts_from_env() -> set[str]:
    raw = os.getenv("LLM_ALLOWED_HOSTS", "")
    extra = {x.strip().lower() for x in raw.split(",") if x.strip()}
    return {h.lower() for h in DEFAULT_ALLOWED_LLM_HOSTS} | extra


def resolve_llm_runtime(args: argparse.Namespace | Any) -> tuple[str, str, str]:
    """Resolve (provider, base_url, api_key) from argparse namespace or NS-like object."""
    api_key = (
        getattr(args, "ark_api_key", "")
        or os.getenv("ARK_API_KEY", "")
        or os.getenv("OPENAI_API_KEY", "")
        or os.getenv("api_key", "")
        or os.getenv("API_KEY", "")
        or ""
    ).strip()

    ark_model_or_ep = (
        getattr(args, "ark_endpoint_id", "")
        or os.getenv("ARK_ENDPOINT_ID", "")
        or getattr(args, "ark_model", "")
        or os.getenv("ARK_MODEL", "")
        or ""
    ).strip()
    openai_model = (os.getenv("OPENAI_MODEL", "") or os.getenv("MODEL", "")).strip()
    model = ark_model_or_ep or openai_model or "Doubao-Seed-1.6-lite"

    provider = (getattr(args, "llm_provider", "auto") or "auto").strip().lower()
    if provider not in {"auto", "ark", "openai-compatible"}:
        provider = "auto"

    has_ark_hint = bool(
        getattr(args, "ark_endpoint_id", "") or os.getenv("ARK_ENDPOINT_ID") or os.getenv("ARK_MODEL")
    )
    if provider == "auto":
        provider = "ark" if has_ark_hint else "openai-compatible"

    if provider == "ark":
        base_url = (os.getenv("ARK_BASE_URL", "") or ARK_BASE_URL).strip()
    else:
        base_url = (
            getattr(args, "llm_base_url", "")
            or os.getenv("OPENAI_BASE_URL", "")
            or os.getenv("LLM_BASE_URL", "")
            or ""
        ).strip()
        if not base_url:
            raise ConfigError(
                "openai-compatible mode requires OPENAI_BASE_URL (or --llm-base-url)"
            )
        base_url = normalize_chat_completions_url(base_url)

    base_url = normalize_chat_completions_url(base_url)
    parsed = validate_https_url(base_url, "LLM base URL")
    host = (parsed.hostname or "").lower()
    if not getattr(args, "allow_custom_llm_endpoint", False) and host not in allowed_llm_hosts_from_env():
        raise ConfigError(
            f"LLM endpoint host '{host}' is not in allowlist. "
            "Use --allow-custom-llm-endpoint or set LLM_ALLOWED_HOSTS."
        )

    return provider, base_url, api_key


# ---------------------------------------------------------------------------
# Intent analysis prompts & helpers
# ---------------------------------------------------------------------------

GNEWS_LLM_SYSTEM = (
    "你是新闻搜索词抽取助手。用户用自然语言描述想看的资讯。"
    "请只输出一行：适合新闻搜索 API 的查询字符串（3–10 个关键词，用空格分隔；中英文均可）。"
    "不要引号、不要解释、不要前缀、不要换行。"
)

INTENT_ANALYSIS_SYSTEM = (
    "你是资讯检索意图分析助手。用户用自然语言描述想同时追踪的几类主题。\n"
    "请从用户话里抽出 **彼此独立** 的检索短语（每个短语用于单独搜索新闻/网页），"
    "保留原文中的专有名词（如 agent harness、Claude Code）。\n"
    "只输出一个 JSON：可以是字符串数组，或形如 {\"queries\":[\"...\",\"...\"]} 的对象。"
    "短语数量 1～8 个；不要重复；不要解释；不要 markdown 代码块标记。"
)


def _parse_intent_queries_json(raw: str) -> list[str]:
    s = (raw or "").strip()
    s = re.sub(r"^```(?:json)?\s*", "", s, flags=re.IGNORECASE | re.MULTILINE)
    s = re.sub(r"\s*```\s*$", "", s, flags=re.MULTILINE).strip()
    if not s:
        return []
    try:
        data = json.loads(s)
    except Exception:
        m = re.search(r"(\[[\s\S]*\]|\{[\s\S]*\})", s)
        if not m:
            return []
        try:
            data = json.loads(m.group(1))
        except Exception:
            return []
    out: list[str] = []
    if isinstance(data, list):
        out = [str(x).strip() for x in data if str(x).strip()]
    elif isinstance(data, dict):
        q = data.get("queries") or data.get("search_queries") or data.get("keywords")
        if isinstance(q, list):
            out = [str(x).strip() for x in q if str(x).strip()]
    seen: set[str] = set()
    uniq: list[str] = []
    for x in out:
        low = x.lower()
        if low in seen:
            continue
        seen.add(low)
        uniq.append(x)
    return uniq[:8]


def llm_extract_intent_search_queries(
    intent_text: str,
    *,
    api_key: str,
    model: str,
    base_url: str,
    allow_insecure_fallback: bool,
) -> list[str]:
    text = (intent_text or "").strip()
    if not text:
        return []
    try:
        raw = call_chat_completion(
            api_key=api_key,
            model=model,
            messages=[
                {"role": "system", "content": INTENT_ANALYSIS_SYSTEM},
                {"role": "user", "content": text},
            ],
            base_url=base_url,
            timeout=60,
            allow_insecure_fallback=allow_insecure_fallback,
        )
    except Exception:
        return []
    return _parse_intent_queries_json(raw)


def infer_gnews_search_query_llm(
    intent_text: str,
    *,
    api_key: str,
    model: str,
    base_url: str,
    allow_insecure_fallback: bool,
) -> str:
    text = (intent_text or "").strip()
    if not text:
        return ""
    try:
        content = call_chat_completion(
            api_key=api_key,
            model=model,
            base_url=base_url,
            allow_insecure_fallback=allow_insecure_fallback,
            messages=[
                {"role": "system", "content": GNEWS_LLM_SYSTEM},
                {"role": "user", "content": text},
            ],
        )
        q = (content or "").strip().splitlines()[0].strip()
        q = re.sub(r'^["\']|["\']$', "", q)
        q = re.sub(r"\s+", " ", q).strip()
        if not q:
            return ""
        return q[:500]
    except Exception:
        return ""
