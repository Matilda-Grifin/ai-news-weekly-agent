#!/usr/bin/env python3
"""
One-shot repo restructure helper.

Goal:
- Move root-level python entrypoints/modules into a single folder (default: project/)
- Keep functionality by updating common commands in README (manual), and preserving importability

NOTE:
- This script performs FILE MOVES. Review before running.
- It is intentionally conservative: it only moves a known allowlist of paths.
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path


MOVE_ALLOWLIST = [
    "app.py",
    "agent_runtime.py",
    "langgraph_agent.py",
    "run_daily_digest.py",
    "mcp_bridge.py",
    "agent_tool_runner.py",
    "digest_tools.py",
    # folders
    "ai_news_skill",
    "site_crawlers",
    "scripts",
    "docs",
    "test_harness_crawl",
    "middleware",
    "model",
    "chains",
    "prompts",
]


def _mv(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dest", default="project", help="destination folder name under repo root")
    ap.add_argument("--dry-run", action="store_true", help="print operations without moving")
    args = ap.parse_args()

    repo = Path(__file__).resolve().parents[1]
    dest_root = (repo / args.dest).resolve()
    dest_root.mkdir(parents=True, exist_ok=True)

    planned: list[tuple[Path, Path]] = []
    for rel in MOVE_ALLOWLIST:
        src = repo / rel
        if not src.exists():
            continue
        if src.resolve() == dest_root:
            continue
        dst = dest_root / rel
        planned.append((src, dst))

    if not planned:
        print("[INFO] nothing to move (allowlist items not found).")
        return 0

    print("[PLAN] move the following paths:")
    for s, d in planned:
        print(f"  - {s.relative_to(repo)} -> {d.relative_to(repo)}")

    if args.dry_run:
        print("[DRY RUN] no files moved.")
        return 0

    for s, d in planned:
        _mv(s, d)

    print("[OK] move completed.")
    print()
    print("[NEXT] update your commands to run from the new folder, e.g.:")
    print(f"  streamlit run {args.dest}/app.py")
    print(f"  python {args.dest}/run_daily_digest.py --help")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

