#!/usr/bin/env python3
"""Print pipeline step durations from a run's trace.json (extra.duration_ms / wall_ms)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _ms(step: dict) -> int | None:
    ex = step.get("extra") or {}
    if "duration_ms" in ex:
        return int(ex["duration_ms"])
    if "wall_ms" in ex:
        return int(ex["wall_ms"])
    return None


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "trace_json",
        nargs="?",
        type=Path,
        help="Path to trace.json (default: latest under runs/ if exists)",
    )
    args = p.parse_args()
    path = args.trace_json
    if path is None:
        root = Path(__file__).resolve().parents[1]
        runs = root / "runs"
        if not runs.is_dir():
            print("No trace path and no runs/ directory.", file=sys.stderr)
            return 1
        traces = sorted(runs.glob("*/trace.json"), key=lambda x: x.stat().st_mtime, reverse=True)
        if not traces:
            print("No runs/*/trace.json found.", file=sys.stderr)
            return 1
        path = traces[0]
        print(f"Using {path}\n", file=sys.stderr)

    data = json.loads(path.read_text(encoding="utf-8"))
    steps = data.get("steps") or []
    print(f"status={data.get('status')} elapsed_seconds={data.get('elapsed_seconds')}")
    for s in steps:
        name = s.get("name", "")
        st = s.get("status", "")
        detail = (s.get("detail") or "")[:80]
        ms = _ms(s)
        ex = s.get("extra") or {}
        line = f"  {name} [{st}]"
        if ms is not None:
            line += f" {ms}ms"
        if detail:
            line += f"  {detail!r}"
        print(line)
        if name == "rss_source" and ex:
            src = detail or ex.get("error_preview", "")
            print(f"      items={ex.get('items')} ok={ex.get('ok')} seconds={ex.get('seconds')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
