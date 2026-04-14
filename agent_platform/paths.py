"""Repository layout: agent_platform/ lives under repo root (ai_news_skill/)."""
from __future__ import annotations

import pathlib

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
PROJECT_ROOT = _REPO_ROOT / "project"


def repo_root() -> pathlib.Path:
    return _REPO_ROOT


def project_root() -> pathlib.Path:
    return PROJECT_ROOT
