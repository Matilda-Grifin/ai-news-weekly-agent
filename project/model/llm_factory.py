"""Build LangChain ChatOpenAI instances for Ark / OpenAI-compatible endpoints."""

from __future__ import annotations

import os

import httpx
from langchain_openai import ChatOpenAI


def openai_base_url_from_chat_completions_url(url: str) -> str:
    """Strip /chat/completions so the OpenAI client can append it."""
    u = (url or "").strip().rstrip("/")
    if u.endswith("/chat/completions"):
        return u[: -len("/chat/completions")]
    return u


def build_chat_openai(
    *,
    api_key: str,
    model: str,
    chat_completions_url: str,
    temperature: float = 0.2,
    timeout_s: int = 120,
    allow_insecure_fallback: bool = False,
) -> ChatOpenAI:
    """
    chat_completions_url: full HTTPS URL ending with .../chat/completions (as returned by resolve_llm_runtime).
    """
    base = openai_base_url_from_chat_completions_url(chat_completions_url)
    kwargs: dict = {
        "model": model,
        "api_key": api_key,
        "base_url": base,
        "temperature": temperature,
        "timeout": timeout_s,
        "max_retries": int(os.getenv("LLM_MAX_RETRIES", "2")),
    }
    if allow_insecure_fallback:
        kwargs["http_client"] = httpx.Client(verify=False)
    return ChatOpenAI(**kwargs)
