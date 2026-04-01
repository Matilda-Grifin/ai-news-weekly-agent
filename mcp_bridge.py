from __future__ import annotations

import json
import pathlib
from typing import Any


def load_mcp_config(path: str = "mcp_servers.json") -> list[dict[str, Any]]:
    cfg = pathlib.Path(path).resolve()
    if not cfg.exists():
        return []
    try:
        data = json.loads(cfg.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
    if not isinstance(data, list):
        return []
    return [x for x in data if isinstance(x, dict) and x.get("name")]


def discover_mcp_tools(path: str = "mcp_servers.json") -> list[str]:
    servers = load_mcp_config(path)
    if not servers:
        return []
    # Real MCP tool binding can be attached here when servers are available.
    # Kept lightweight to avoid blocking local usage when MCP servers are absent.
    return [f"{s.get('name')} ({s.get('transport', 'stdio')})" for s in servers]


def append_jsonl_record(path: str, record: dict[str, Any]) -> None:
    p = pathlib.Path(path).resolve()
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
