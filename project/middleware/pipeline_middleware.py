"""Lightweight logging around digest pipeline steps (similar spirit to LangChain agent middleware)."""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

logger = logging.getLogger("digest.pipeline")


def log_pipeline_event(name: str, status: str, detail: str = "", extra: dict[str, Any] | None = None) -> None:
    parts = [f"{name}", status]
    if detail:
        parts.append(detail)
    if extra:
        parts.append(str(extra))
    logger.info(" | ".join(parts))


def timed_call(label: str, fn: Callable[[], Any]) -> Any:
    t0 = time.perf_counter()
    try:
        out = fn()
        logger.info("[timing] %s ok %.3fs", label, time.perf_counter() - t0)
        return out
    except Exception:
        logger.exception("[timing] %s failed after %.3fs", label, time.perf_counter() - t0)
        raise
