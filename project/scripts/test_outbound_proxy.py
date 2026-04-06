#!/usr/bin/env python3
"""Load repo .env/.ENV and test outbound proxy per scope (no secrets printed)."""
from __future__ import annotations

import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT / "project"))


def load_env() -> None:
    for name in (".env", ".ENV"):
        p = ROOT / name
        if not p.exists():
            continue
        for raw in p.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def main() -> int:
    load_env()
    from urllib.parse import urlparse

    from run_daily_digest import fetch_text, outbound_http_proxy_url_for_scope

    def _target(raw: str) -> str:
        if not raw:
            return "(none)"
        pr = urlparse(raw)
        port = pr.port or (80 if (pr.scheme or "http") == "http" else 443)
        return f"{pr.hostname}:{port}"

    pw = outbound_http_proxy_url_for_scope("playwright")
    dg = outbound_http_proxy_url_for_scope("digest")
    pf = outbound_http_proxy_url_for_scope("public_feeds")
    sc = outbound_http_proxy_url_for_scope("site_crawler")
    print("proxy_by_scope playwright:", _target(pw), flush=True)
    print("proxy_by_scope digest:", _target(dg), flush=True)
    print("proxy_by_scope public_feeds:", _target(pf), flush=True)
    print("proxy_by_scope site_crawler:", _target(sc), flush=True)

    probe = pw or dg
    if not probe:
        print(
            "proxy_set: False — set WEBSHARE_API_KEY (or WEBSHARE_PROXY_URL / HTTPS_PROXY)",
            flush=True,
        )
        return 2
    fetch_scope = "playwright" if pw else "digest"
    pr = urlparse(probe)
    port = pr.port or (80 if (pr.scheme or "http") == "http" else 443)
    print("probe_uses_scope:", fetch_scope, "target:", f"{pr.hostname}:{port}", flush=True)

    for url in (
        "https://api.ipify.org?format=json",
        "https://httpbin.org/ip",
    ):
        print("fetching:", url, "...", flush=True)
        try:
            body = fetch_text(
                url, timeout=25, allow_insecure_fallback=True, proxy_scope=fetch_scope
            )
            print("fetch_ok:", url, "bytes:", len(body), flush=True)
        except Exception as e:  # noqa: BLE001
            print("fetch_fail:", url, type(e).__name__, str(e)[:200], flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
