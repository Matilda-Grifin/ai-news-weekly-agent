#!/usr/bin/env python3
"""仅跑注册的 Playwright 站点爬虫，统计条数与耗时（不走 RSS/GNews/管线）。

默认只跑一个站点（默认 OpenAI Blog），避免五个串起来很慢：
  python project/scripts/benchmark_playwright_site_crawlers.py
  python project/scripts/benchmark_playwright_site_crawlers.py --only "Hugging Face Blog"
跑全部：
  python project/scripts/benchmark_playwright_site_crawlers.py --all
"""
from __future__ import annotations

import argparse
import os
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT / "project"))


def _load_dotenv() -> None:
    for name in (".env", ".ENV"):
        p = ROOT / name
        if not p.exists():
            continue
        for raw in p.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "--only",
        default="OpenAI Blog",
        metavar="SUBSTRING",
        help="只跑名称匹配的爬虫（子串，不区分大小写）。默认: OpenAI Blog",
    )
    ap.add_argument("--all", action="store_true", help="跑全部注册爬虫（顺序执行，总耗时可很长）")
    args = ap.parse_args()

    _load_dotenv()
    try:
        per_source = max(1, int(os.getenv("BENCH_PER_SOURCE", "8")))
    except ValueError:
        per_source = 8
    try:
        window_h = max(1, int(os.getenv("BENCH_WINDOW_HOURS", "24")))
    except ValueError:
        window_h = 24
    allow_cb = os.getenv("BENCH_ALLOW_INSECURE_SSL", "1").strip().lower() in ("1", "true", "yes", "on")

    from ai_news_skill.crawlers.site.registry import all_crawlers

    all_c = all_crawlers()
    if args.all:
        crawlers = all_c
    else:
        needle = (args.only or "").strip().lower()
        crawlers = [c for c in all_c if needle in c.name.lower()]
        if not crawlers:
            print(f"No crawler matched {args.only!r}. Known: {[c.name for c in all_c]}", flush=True)
            return 1

    print(
        f"ROOT={ROOT}\n"
        f"per_source={per_source} window_hours={window_h} allow_insecure_ssl={allow_cb}\n"
        f"running {len(crawlers)} crawler(s): {[c.name for c in crawlers]}\n",
        flush=True,
    )

    total_items = 0
    t_all = time.perf_counter()

    for c in crawlers:
        t0 = time.perf_counter()
        err = ""
        n = 0
        try:
            got = c.crawl(
                per_source=per_source,
                window_hours=window_h,
                allow_insecure_fallback=allow_cb,
            )
            n = len(got or [])
        except Exception as ex:  # noqa: BLE001
            err = f"{type(ex).__name__}: {ex}"
        elapsed = time.perf_counter() - t0
        total_items += n
        line = f"  {c.name}: {n} items in {elapsed:.2f}s"
        if err:
            line += f"  ERROR {err[:200]}"
        print(line, flush=True)

    wall = time.perf_counter() - t_all
    print(f"\nTOTAL: {total_items} items, sequential wall {wall:.2f}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
