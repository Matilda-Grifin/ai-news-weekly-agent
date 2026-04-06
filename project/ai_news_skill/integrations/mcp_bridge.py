from __future__ import annotations

import asyncio
import concurrent.futures
import json
import pathlib
import re
from datetime import timedelta
from typing import Any

from langchain_core.tools import StructuredTool

from mcp import types
from mcp.client.session_group import (
    ClientSessionGroup,
    ClientSessionParameters,
    SseServerParameters,
    ServerParameters,
    StreamableHttpParameters,
)
from mcp.client.stdio import StdioServerParameters

_TOOL_META_CACHE: dict[str, Any] = {"key": None, "entries": []}
_SYNC_POOL = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="mcp-async")


def load_mcp_config(path: str | pathlib.Path = "mcp_servers.json") -> list[dict[str, Any]]:
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


def resolve_mcp_servers_path(repo_root: str | pathlib.Path) -> pathlib.Path:
    return pathlib.Path(repo_root).resolve() / "mcp_servers.json"


def _sanitize_lc_tool_name(name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9_]+", "_", name).strip("_")
    if not s:
        s = "mcp_tool"
    if s[0].isdigit():
        s = "mcp_" + s
    return s[:64]


def server_params_from_dict(entry: dict[str, Any]) -> ServerParameters:
    transport = str(entry.get("transport") or "stdio").lower().strip()
    if transport == "stdio":
        cmd = (entry.get("command") or "").strip()
        if not cmd:
            raise ValueError(f"MCP server '{entry.get('name')}' (stdio) requires 'command'")
        args = entry.get("args") or []
        if not isinstance(args, list):
            args = []
        args = [str(a) for a in args]
        env = entry.get("env")
        env_dict: dict[str, str] | None = None
        if isinstance(env, dict):
            env_dict = {str(k): str(v) for k, v in env.items()}
        return StdioServerParameters(command=cmd, args=args, env=env_dict)
    if transport == "sse":
        url = (entry.get("url") or "").strip()
        if not url:
            raise ValueError(f"MCP server '{entry.get('name')}' (sse) requires 'url'")
        headers = entry.get("headers")
        hdr = dict(headers) if isinstance(headers, dict) else None
        return SseServerParameters(url=url, headers=hdr)
    if transport in ("http", "streamable_http"):
        url = (entry.get("url") or "").strip()
        if not url:
            raise ValueError(f"MCP server '{entry.get('name')}' (http) requires 'url'")
        headers = entry.get("headers")
        hdr = dict(headers) if isinstance(headers, dict) else None
        return StreamableHttpParameters(url=url, headers=hdr)
    raise ValueError(f"MCP server '{entry.get('name')}' has unsupported transport: {transport}")


def enabled_mcp_servers(path: str | pathlib.Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for entry in load_mcp_config(path):
        if entry.get("enabled", True) is False:
            continue
        out.append(entry)
    return out


def mcp_component_name_hook(name: str, server_info: types.Implementation) -> str:
    prefix = (server_info.name or "srv").replace(" ", "_")
    return f"{prefix}__{name}"


def _cache_key(path: pathlib.Path) -> str | None:
    try:
        st = path.stat()
        return f"{path.resolve()}:{st.st_mtime_ns}"
    except OSError:
        return None


async def _list_tools_for_server(server_params: ServerParameters) -> list[types.Tool]:
    async with ClientSessionGroup() as group:
        await group.connect_to_server(server_params, ClientSessionParameters())
        return list(group.tools.values())


async def _collect_tool_entries(path: pathlib.Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for raw in enabled_mcp_servers(path):
        try:
            sp = server_params_from_dict(raw)
        except Exception as ex:  # noqa: BLE001
            entries.append(
                {
                    "server_name": str(raw.get("name", "")),
                    "error": str(ex),
                    "tools": [],
                }
            )
            continue
        server_name = str(raw.get("name", "server"))
        try:
            tools = await _list_tools_for_server(sp)
        except Exception as ex:  # noqa: BLE001
            entries.append({"server_name": server_name, "error": str(ex), "tools": []})
            continue
        entries.append(
            {
                "server_name": server_name,
                "server_params": sp,
                "tools": tools,
            }
        )
    return entries


def _run_coro_sync(coro: Any) -> Any:
    """Run async MCP calls from sync code (e.g. discover_mcp_tools); not used on hot tool path."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    fut = _SYNC_POOL.submit(asyncio.run, coro)
    return fut.result()


def _format_tool_result(result: types.CallToolResult) -> str:
    if getattr(result, "structuredContent", None):
        try:
            return json.dumps(result.structuredContent, ensure_ascii=False)
        except Exception:  # noqa: BLE001
            pass
    chunks: list[str] = []
    for block in result.content:
        if isinstance(block, types.TextContent):
            chunks.append(block.text)
        elif isinstance(block, types.ImageContent):
            chunks.append(f"[image {block.mimeType}]")
        elif isinstance(block, types.EmbeddedResource):
            chunks.append(f"[embedded resource {block.resource.uri}]")
        else:
            chunks.append(str(block))
    text = "\n".join(chunks).strip()
    if result.isError:
        return json.dumps({"isError": True, "text": text}, ensure_ascii=False)
    return text or json.dumps({"ok": True, "empty": True}, ensure_ascii=False)


def build_mcp_tools_for_group(
    group: ClientSessionGroup,
    loop: asyncio.AbstractEventLoop,
    *,
    read_timeout_seconds: timedelta | None = None,
) -> list[Any]:
    """
    LangChain StructuredTools that call into an already-connected ClientSessionGroup.
    Tool bodies run on the agent executor thread and marshal to ``loop`` via run_coroutine_threadsafe.
    """
    rt = read_timeout_seconds or timedelta(seconds=120)
    tools: list[Any] = []
    seen: set[str] = set()
    for tool_key, tool_def in group.tools.items():
        base = _sanitize_lc_tool_name(f"mcp_{tool_key}")
        lc_name = base
        n = 1
        while lc_name in seen:
            n += 1
            lc_name = f"{base}_{n}"
        seen.add(lc_name)
        desc = (tool_def.description or f"MCP `{tool_key}`").strip()
        full_desc = (
            f"{desc}\n"
            "参数 `arguments_json` 为 JSON 字符串，对应 MCP 工具的输入字段；无参数时用 \"{{}}\"。"
        )

        def _make_sync(mcp_key: str, description: str, tool_lc_name: str) -> Any:
            def _sync(arguments_json: str = "{}") -> str:
                try:
                    parsed = json.loads(arguments_json or "{}")
                except json.JSONDecodeError:
                    parsed = {}
                if not isinstance(parsed, dict):
                    parsed = {}
                coro = group.call_tool(mcp_key, parsed, read_timeout_seconds=rt)
                fut = asyncio.run_coroutine_threadsafe(coro, loop)
                return _format_tool_result(fut.result(timeout=300))

            return StructuredTool.from_function(name=tool_lc_name, description=description, func=_sync)

        tools.append(_make_sync(tool_key, full_desc, lc_name))
    return tools


def discover_mcp_tools(path: str | pathlib.Path = "mcp_servers.json") -> list[str]:
    """Human-readable MCP tool names for audit; uses cached discovery when possible."""
    p = pathlib.Path(path).resolve()
    if not p.exists():
        return []
    key = _cache_key(p)
    if key and _TOOL_META_CACHE["key"] == key and _TOOL_META_CACHE["entries"]:
        return _format_discovered_lines(_TOOL_META_CACHE["entries"])

    try:
        entries = _run_coro_sync(_collect_tool_entries(p))
    except Exception:  # noqa: BLE001
        servers = enabled_mcp_servers(p)
        return [f"{s.get('name')} ({s.get('transport', 'stdio')})" for s in servers]

    if key:
        _TOOL_META_CACHE["key"] = key
        _TOOL_META_CACHE["entries"] = entries
    return _format_discovered_lines(entries)


def _format_discovered_lines(entries: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for block in entries:
        sname = block.get("server_name", "")
        if block.get("error"):
            lines.append(f"{sname} (error: {block['error']})")
            continue
        for t in block.get("tools") or []:
            if isinstance(t, types.Tool):
                lines.append(f"{sname}::{t.name}")
            else:
                lines.append(f"{sname}::(invalid tool entry)")
    return lines


def append_jsonl_record(path: str, record: dict[str, Any]) -> None:
    p = pathlib.Path(path).resolve()
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
