from __future__ import annotations

import json
import pathlib
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from agent_platform.paths import repo_root


@dataclass
class HarnessSession:
    """Mutable harness state for split tools (intent → collect → … → write)."""

    session_id: str
    config: dict[str, Any]
    ctx: dict[str, Any] | None = None
    intent_plan: dict[str, Any] | None = None
    sources: list[dict] = field(default_factory=list)
    items: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    min_official_items: int = 0
    openclaw_top: list[dict] = field(default_factory=list)
    openclaw_focus: dict[str, Any] | None = None
    openclaw_asof: str = ""
    llm_overview: str = ""
    llm_core_points: list[str] = field(default_factory=list)
    llm_values: list[str] = field(default_factory=list)
    llm_titles_cn: list[str] = field(default_factory=list)
    doc_path: str | None = None
    result: dict[str, Any] | None = None
    phases: set[str] = field(default_factory=set)

    def touch_memory_file(self) -> pathlib.Path:
        p = repo_root() / "runs" / "agent_platform_memory.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def append_memory(self, role: str, text: str) -> None:
        line = {
            "ts": datetime.now().astimezone().isoformat(),
            "session_id": self.session_id,
            "role": role,
            "text": text[:8000],
        }
        path = self.touch_memory_file()
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")


_sessions: dict[str, HarnessSession] = {}
_lock = threading.Lock()


def get_session(sid: str) -> HarnessSession | None:
    with _lock:
        return _sessions.get(sid)


def create_session(base_config: dict[str, Any]) -> HarnessSession:
    cfg = json.loads(json.dumps(base_config))
    sid = uuid.uuid4().hex[:16]
    hs = HarnessSession(session_id=sid, config=cfg)
    with _lock:
        _sessions[sid] = hs
    return hs


def delete_session(sid: str) -> None:
    with _lock:
        _sessions.pop(sid, None)
