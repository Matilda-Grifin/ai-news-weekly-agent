"""Webhook notification helpers."""

from __future__ import annotations

import json
import urllib.request
from typing import Any

from ai_news_skill.core.http_client import USER_AGENT
from ai_news_skill.pipeline.llm_client import validate_https_url

ALLOWED_WEBHOOK_SUFFIXES = (
    ".feishu.cn",
    ".larksuite.com",
    ".dingtalk.com",
)


def post_webhook(url: str, text: str) -> tuple[bool, str]:
    try:
        parsed = validate_https_url(url, "webhook URL")
    except ValueError as ex:
        return False, str(ex)
    host = (parsed.hostname or "").lower()
    if not (host.endswith(ALLOWED_WEBHOOK_SUFFIXES) or host in {"feishu.cn", "larksuite.com", "dingtalk.com"}):
        return False, "Unsupported webhook host (expect Feishu/Lark or DingTalk official domains)"

    if host.endswith(".feishu.cn") or host.endswith(".larksuite.com") or host == "feishu.cn" or host == "larksuite.com":
        payload = {"msg_type": "text", "content": {"text": text}}
    elif host.endswith(".dingtalk.com") or host == "dingtalk.com":
        payload = {"msgtype": "text", "text": {"content": text}}
    else:
        return False, "Unsupported webhook host (expect feishu/lark or dingtalk)"

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode("utf-8", errors="ignore")
            return True, body[:500]
    except Exception as ex:
        return False, f"{type(ex).__name__}: {ex}"
