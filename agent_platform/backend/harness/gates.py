"""Lightweight preconditions for stage tools (avoid silent skips)."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_platform.backend.harness.session import HarnessSession


def require_bootstrap(s: "HarnessSession") -> str | None:
    if s.ctx is None:
        return "gate: 请先调用 harness_bootstrap 初始化本次运行上下文。"
    return None


def require_intent_plan(s: "HarnessSession") -> str | None:
    err = require_bootstrap(s)
    if err:
        return err
    if not s.intent_plan:
        return "gate: 请先调用 skill_intent_plan 生成检索计划。"
    return None


def require_collect(s: "HarnessSession") -> str | None:
    err = require_intent_plan(s)
    if err:
        return err
    if "collect" not in s.phases:
        return "gate: 请先调用 skill_collect_and_filter 完成采集与意图过滤。"
    return None


def require_enrich(s: "HarnessSession") -> str | None:
    err = require_collect(s)
    if err:
        return err
    if "enrich" not in s.phases:
        return "gate: 请先调用 skill_enrich 完成正文与 LLM 增强。"
    return None
