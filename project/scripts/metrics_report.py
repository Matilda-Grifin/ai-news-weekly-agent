#!/usr/bin/env python3
"""Print daily metrics from runs/history.jsonl and trace files."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any


@dataclass
class RunMetrics:
    run_id: str
    timestamp: str
    status: str
    items: int
    errors: int
    latency_s: float | None
    source_coverage: float | None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Show AI news pipeline metrics for a given day."
    )
    parser.add_argument(
        "--date",
        default=date.today().isoformat(),
        help="Target date in YYYY-MM-DD (default: today).",
    )
    parser.add_argument(
        "--runs-dir",
        default="runs",
        help="Runs directory that contains history.jsonl and trace folders.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output metrics in JSON format.",
    )
    parser.add_argument(
        "--show-runs",
        action="store_true",
        help="Also print per-run details.",
    )
    return parser.parse_args()


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    value = value.strip()
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * (p / 100.0)
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    fraction = rank - low
    return ordered[low] * (1 - fraction) + ordered[high] * fraction


def load_trace(trace_path: Path) -> dict[str, Any] | None:
    if not trace_path.exists():
        return None
    try:
        return json.loads(trace_path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def compute_source_coverage(trace: dict[str, Any] | None) -> float | None:
    if not trace:
        return None
    steps = trace.get("steps") or []
    planned: int | None = None
    attempted_sources: set[str] = set()
    ok_sources: set[str] = set()

    for step in steps:
        if not isinstance(step, dict):
            continue
        name = str(step.get("name") or "")
        status = str(step.get("status") or "")
        detail = str(step.get("detail") or "").strip()
        extra = step.get("extra") if isinstance(step.get("extra"), dict) else {}

        if name == "collect_plan":
            maybe = extra.get("planned_sources")
            if isinstance(maybe, (int, float)):
                planned = int(maybe)

        if name == "site_crawl":
            source = str(extra.get("source") or detail).strip()
            if source:
                attempted_sources.add(source)
                if status == "ok":
                    ok_sources.add(source)
            continue

        if name == "rss_source":
            source = detail
            if source:
                attempted_sources.add(source)
                if status == "ok":
                    ok_sources.add(source)
            continue

        if name == "source_result":
            source = str(extra.get("source") or detail).strip()
            if source:
                attempted_sources.add(source)
                ok_flag = extra.get("ok")
                if status == "ok" and (ok_flag is None or bool(ok_flag)):
                    ok_sources.add(source)

    denom = planned if planned and planned > 0 else len(attempted_sources)
    if denom <= 0:
        return None
    return len(ok_sources) / denom


def classify_status(items: int, trace: dict[str, Any] | None) -> str:
    if trace and str(trace.get("status") or "").lower() not in {"", "ok"}:
        return "FAILED"
    if items > 0:
        return "SUCCESS_WITH_NEWS"
    if items == 0:
        return "SUCCESS_NO_NEWS"
    return "FAILED"


def build_run_metrics(day: str, runs_dir: Path) -> list[RunMetrics]:
    history_path = runs_dir / "history.jsonl"
    if not history_path.exists():
        raise FileNotFoundError(f"history file not found: {history_path}")

    metrics: list[RunMetrics] = []
    with history_path.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            record = json.loads(line)
            ts = str(record.get("time") or "")
            if not ts.startswith(day):
                continue

            run_id = str(record.get("run_id") or "")
            items = _safe_int(record.get("items"), default=0)
            errors = len(record.get("errors") or [])

            trace_path = runs_dir / run_id / "trace.json"
            trace = load_trace(trace_path)
            latency_s: float | None = None
            if trace:
                raw_elapsed = trace.get("elapsed_seconds")
                if isinstance(raw_elapsed, (int, float)):
                    latency_s = float(raw_elapsed)
                else:
                    started = parse_iso(str(trace.get("started_at") or ""))
                    ended = parse_iso(str(trace.get("ended_at") or ""))
                    if started and ended:
                        latency_s = (ended - started).total_seconds()

            metrics.append(
                RunMetrics(
                    run_id=run_id,
                    timestamp=ts,
                    status=classify_status(items=items, trace=trace),
                    items=items,
                    errors=errors,
                    latency_s=latency_s,
                    source_coverage=compute_source_coverage(trace),
                )
            )
    return metrics


def summarize(day: str, runs: list[RunMetrics]) -> dict[str, Any]:
    total = len(runs)
    with_news = sum(1 for r in runs if r.status == "SUCCESS_WITH_NEWS")
    no_news = sum(1 for r in runs if r.status == "SUCCESS_NO_NEWS")
    failed = sum(1 for r in runs if r.status == "FAILED")

    latencies = [r.latency_s for r in runs if r.latency_s is not None]
    coverage = [r.source_coverage for r in runs if r.source_coverage is not None]

    def ratio(v: int) -> float:
        return (v / total) if total else 0.0

    return {
        "date": day,
        "total_runs": total,
        "status_counts": {
            "SUCCESS_WITH_NEWS": with_news,
            "SUCCESS_NO_NEWS": no_news,
            "FAILED": failed,
        },
        "rates": {
            "delivery_rate": ratio(with_news),
            "no_news_rate": ratio(no_news),
            "failure_rate": ratio(failed),
            "pipeline_success_rate": ratio(with_news + no_news),
            "edr_proxy": ratio(with_news),
        },
        "latency_seconds": {
            "p50": percentile(latencies, 50),
            "p95": percentile(latencies, 95),
            "avg": (sum(latencies) / len(latencies)) if latencies else None,
            "count": len(latencies),
        },
        "source_coverage": {
            "avg": (sum(coverage) / len(coverage)) if coverage else None,
            "count": len(coverage),
        },
    }


def fmt_pct(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value * 100:.1f}%"


def fmt_sec(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:.1f}s"


def print_text_dashboard(summary: dict[str, Any], runs: list[RunMetrics], show_runs: bool) -> None:
    print(f"Date: {summary['date']}")
    print(f"Total runs: {summary['total_runs']}")
    print("")
    print("Status")
    status_counts = summary["status_counts"]
    rates = summary["rates"]
    print(
        f"- SUCCESS_WITH_NEWS: {status_counts['SUCCESS_WITH_NEWS']} ({fmt_pct(rates['delivery_rate'])})"
    )
    print(f"- SUCCESS_NO_NEWS:   {status_counts['SUCCESS_NO_NEWS']} ({fmt_pct(rates['no_news_rate'])})")
    print(f"- FAILED:            {status_counts['FAILED']} ({fmt_pct(rates['failure_rate'])})")
    print(f"- Pipeline Success:  {fmt_pct(rates['pipeline_success_rate'])}")
    print(f"- EDR (proxy):       {fmt_pct(rates['edr_proxy'])}")
    print("")
    latency = summary["latency_seconds"]
    print("Latency")
    print(f"- P50: {fmt_sec(latency['p50'])}")
    print(f"- P95: {fmt_sec(latency['p95'])}")
    print(f"- Avg: {fmt_sec(latency['avg'])}")
    print("")
    source_coverage = summary["source_coverage"]
    print("Source Coverage")
    print(f"- Avg Source Coverage: {fmt_pct(source_coverage['avg'])}")
    print("")
    if show_runs:
        print("Per-run")
        for run in runs:
            print(
                f"- {run.run_id} | {run.status} | items={run.items} "
                f"| errors={run.errors} | latency={fmt_sec(run.latency_s)} "
                f"| coverage={fmt_pct(run.source_coverage)}"
            )


def main() -> None:
    args = parse_args()
    runs_dir = Path(args.runs_dir).resolve()
    runs = build_run_metrics(day=args.date, runs_dir=runs_dir)
    summary = summarize(day=args.date, runs=runs)

    if args.json:
        payload = {
            "summary": summary,
            "runs": [r.__dict__ for r in runs] if args.show_runs else None,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    print_text_dashboard(summary=summary, runs=runs, show_runs=args.show_runs)


if __name__ == "__main__":
    main()
