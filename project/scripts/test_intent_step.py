#!/usr/bin/env python3
"""仅测试：用户一句话 → intent_plan（LLM 拆检索短语）。需 .env 中 ARK_API_KEY 等。"""

from __future__ import annotations

import json
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from run_daily_digest import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from ai_news_skill.runtime.agent_runtime import init_run_context, intent_plan_stage  # noqa: E402


def main() -> int:
    text = (
        (sys.argv[1] if len(sys.argv) > 1 else "").strip()
        or "我要查 agent harness 的新闻，还想看看视频模型的进展"
    )
    cfg = {
        "intent_text": text,
        "runs_dir": str(ROOT / "runs"),
        "out": str(ROOT / "daily_docs"),
        "llm_intent_analysis": True,
        "llm_provider": "ark",
        "ark_api_key": os.getenv("ARK_API_KEY", ""),
        "ark_endpoint_id": os.getenv("ARK_ENDPOINT_ID", ""),
        "ark_model": os.getenv("ARK_MODEL", "Doubao-Seed-1.6-lite"),
        "llm_base_url": "",
        "allow_insecure_ssl": True,
        "allow_custom_llm_endpoint": False,
        "pipeline_log": False,
    }
    ctx = init_run_context(cfg)
    trace = ctx["trace"]
    plan = intent_plan_stage(cfg, trace)
    print(json.dumps({"intent_text": text, "plan": plan, "trace_steps": trace.get("steps", [])}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
