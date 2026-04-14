"""Load repo `.env` and build digest config dict (same keys as Streamlit / pipeline)."""
from __future__ import annotations

import os
import pathlib
from typing import Any


def load_dotenv_file(path: pathlib.Path) -> None:
    if not path.is_file():
        return
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


def bootstrap_env(repo_root: pathlib.Path) -> None:
    load_dotenv_file(repo_root / ".env")
    load_dotenv_file(repo_root / ".ENV")


def build_base_config(repo_root: pathlib.Path) -> dict[str, Any]:
    bootstrap_env(repo_root)
    if (os.getenv("ARK_MODEL", "").strip() == "") and (os.getenv("ARK_Model") or "").strip():
        os.environ["ARK_MODEL"] = os.environ["ARK_Model"].strip()

    def _i(name: str, default: int) -> int:
        try:
            return max(1, int(os.getenv(name, str(default))))
        except ValueError:
            return default

    sources = repo_root / "sources.json"
    return {
        "sources": str(sources),
        "runs_dir": str(repo_root / "runs"),
        "out": str(repo_root / "daily_docs"),
        "intent_text": "",
        "use_llm": True,
        "llm_provider": (os.getenv("LLM_PROVIDER", "auto") or "auto").strip(),
        "llm_base_url": (os.getenv("OPENAI_BASE_URL", "") or "").strip(),
        "allow_custom_llm_endpoint": (os.getenv("ALLOW_CUSTOM_LLM_ENDPOINT", "").lower() in ("1", "true", "yes")),
        "allow_insecure_ssl": (os.getenv("ALLOW_INSECURE_SSL", "").lower() in ("1", "true", "yes")),
        "ark_api_key": (os.getenv("ARK_API_KEY", "") or "").strip(),
        "ark_endpoint_id": (os.getenv("ARK_ENDPOINT_ID", "") or "").strip(),
        "ark_model": (os.getenv("ARK_MODEL", "Doubao-Seed-1.6-lite") or "Doubao-Seed-1.6-lite").strip(),
        "openai_api_key": (os.getenv("OPENAI_API_KEY", "") or "").strip(),
        "openai_model": (os.getenv("OPENAI_MODEL", "") or "").strip(),
        "window_hours": _i("DIGEST_WINDOW_HOURS", 168),
        "max_paper_ratio": float(os.getenv("MAX_PAPER_RATIO", "0.2") or 0.2),
        "items_per_source_max": _i("DIGEST_UI_ITEMS_CAP", 100),
        "site_crawlers_enabled": (os.getenv("SITE_CRAWLERS_ENABLED", "true") or "true").lower()
        not in ("0", "false", "no", "off"),
        "enable_openclaw": False,
        "focus_skill": "",
        "pipeline_log": True,
        "llm_intent_analysis": True,
        "excerpt_fetch_max_workers": _i("EXCERPT_FETCH_MAX_WORKERS", 12),
        "site_crawl_max_workers": _i("SITE_CRAWL_MAX_WORKERS", 4),
        "gnews_max_workers": _i("GNEWS_MAX_WORKERS", 6),
    }
