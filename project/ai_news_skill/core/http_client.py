"""HTTP client: proxy resolution, fetch_text, fetch_json_url, Playwright proxy helper.

Consolidates all outbound HTTP / proxy logic that was originally in run_daily_digest.py.
"""

from __future__ import annotations

import json
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

USER_AGENT = "ai-digest-agent/0.1 (+local-script)"


# ---------------------------------------------------------------------------
# Webshare proxy helpers (encapsulated)
# ---------------------------------------------------------------------------

def _webshare_apply_backbone_port_override(proxy_url: str) -> str:
    port_raw = (os.getenv("WEBSHARE_BACKBONE_PORT") or os.getenv("WEBSHARE_PROXY_PORT") or "").strip()
    if not port_raw.isdigit():
        return proxy_url
    p = urllib.parse.urlparse(proxy_url)
    if not p.hostname or "webshare.io" not in p.hostname.lower():
        return proxy_url
    new_port = str(int(port_raw))
    if p.username is not None:
        uq = urllib.parse.quote(p.username, safe="")
        pq = urllib.parse.quote(p.password or "", safe="")
        netloc = f"{uq}:{pq}@{p.hostname}:{new_port}"
    else:
        netloc = f"{p.hostname}:{new_port}"
    return urllib.parse.urlunparse(
        (p.scheme or "http", netloc, p.path or "", p.params, p.query, p.fragment)
    )


def _urlopen_webshare_control_api(req: urllib.request.Request, *, timeout: int = 25):
    ctx = ssl.create_default_context()
    opener = urllib.request.build_opener(
        urllib.request.HTTPHandler(),
        urllib.request.HTTPSHandler(context=ctx),
    )
    return opener.open(req, timeout=timeout)


def _webshare_api_token() -> str:
    return (os.getenv("WEBSHARE_API_TOKEN") or os.getenv("WEBSHARE_API_KEY") or "").strip()


def _webshare_list_api_enabled() -> bool:
    if not _webshare_api_token():
        return False
    flag = (os.getenv("WEBSHARE_USE_PROXY_LIST_API") or "").strip().lower()
    if flag in ("0", "false", "no", "off"):
        return False
    return True


def _webshare_fetch_proxy_list_payload() -> dict[str, Any] | None:
    token = _webshare_api_token()
    if not token or not _webshare_list_api_enabled():
        return None
    list_url = (os.getenv("WEBSHARE_PROXY_LIST_URL") or "").strip()
    try:
        page_size = int((os.getenv("WEBSHARE_PROXY_LIST_PAGE_SIZE") or "10").strip() or "10")
    except ValueError:
        page_size = 10
    try:
        page = int((os.getenv("WEBSHARE_PROXY_LIST_PAGE") or "1").strip() or "1")
    except ValueError:
        page = 1
    mode = (os.getenv("WEBSHARE_PROXY_LIST_MODE") or "backbone").strip().lower()
    if mode not in ("direct", "backbone"):
        mode = "backbone"
    plan_id = (os.getenv("WEBSHARE_PLAN_ID") or "").strip()

    if not list_url:
        params: dict[str, Any] = {"page_size": page_size, "page": page, "mode": mode}
        if plan_id:
            params["plan_id"] = plan_id
        try:
            import requests

            r = requests.get(
                "https://proxy.webshare.io/api/v2/proxy/list/",
                headers={"Authorization": f"Token {token}", "User-Agent": USER_AGENT},
                params=params,
                timeout=10,
            )
            if r.ok:
                data = r.json()
                return data if isinstance(data, dict) else None
        except Exception:
            pass
        q = [f"page_size={page_size}", f"page={page}", f"mode={urllib.parse.quote(mode)}"]
        if plan_id:
            q.append(f"plan_id={urllib.parse.quote(plan_id)}")
        list_url = "https://proxy.webshare.io/api/v2/proxy/list/?" + "&".join(q)

    req = urllib.request.Request(
        list_url,
        headers={"Authorization": f"Token {token}", "User-Agent": USER_AGENT},
    )
    try:
        with _urlopen_webshare_control_api(req, timeout=25) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="ignore"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _webshare_rows_to_candidate_urls(data: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for item in data.get("results") or []:
        if not isinstance(item, dict) or not item.get("valid", True):
            continue
        addr = item.get("proxy_address") or "p.webshare.io"
        if isinstance(addr, str):
            addr = addr.strip() or "p.webshare.io"
        else:
            addr = "p.webshare.io"
        port = item.get("port")
        username = (item.get("username") or "").strip()
        password = (item.get("password") or "").strip()
        if port is None or not username or not password:
            continue
        try:
            port_i = int(port)
        except (TypeError, ValueError):
            continue
        uq = urllib.parse.quote(username, safe="")
        pq = urllib.parse.quote(password, safe="")
        out.append(f"http://{uq}:{pq}@{addr}:{port_i}")
    return out


def _webshare_rotating_gateway_url() -> str:
    u = (os.getenv("WEBSHARE_PROXY_USERNAME") or os.getenv("WEBSHARE_USERNAME") or "").strip()
    p = (os.getenv("WEBSHARE_PROXY_PASSWORD") or os.getenv("WEBSHARE_PASSWORD") or "").strip()
    if not u or not p:
        return ""
    port_raw = (os.getenv("WEBSHARE_BACKBONE_PORT") or os.getenv("WEBSHARE_PROXY_PORT") or "").strip()
    port = int(port_raw) if port_raw.isdigit() else 80
    uq = urllib.parse.quote(u, safe="")
    pq = urllib.parse.quote(p, safe="")
    return f"http://{uq}:{pq}@p.webshare.io:{port}/"


def _webshare_only_proxy_urls_ordered() -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()

    def add(u: str) -> None:
        u = u.strip().strip('"').strip("'")
        if not u or u in seen:
            return
        seen.add(u)
        urls.append(u)

    manual = (os.getenv("WEBSHARE_PROXY_URL") or "").strip().strip('"').strip("'")
    if manual:
        add(manual)
    data = _webshare_fetch_proxy_list_payload()
    if data:
        for u in _webshare_rows_to_candidate_urls(data):
            add(u)
    gw = _webshare_rotating_gateway_url()
    if gw:
        add(gw.rstrip("/"))
    return urls


def _webshare_only_proxy_url() -> str:
    lst = _webshare_only_proxy_urls_ordered()
    return lst[0] if lst else ""


# ---------------------------------------------------------------------------
# Generic proxy resolution
# ---------------------------------------------------------------------------

def _generic_outbound_proxy_url() -> str:
    for key in (
        "OUTBOUND_HTTP_PROXY",
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "YOUTUBE_PROXY_HTTPS",
        "YOUTUBE_PROXY_HTTP",
    ):
        v = (os.getenv(key) or "").strip().strip('"').strip("'")
        if v:
            return v
    return ""


def _outbound_webshare_scope_allowed(scope: str) -> bool:
    raw = (os.getenv("OUTBOUND_WEBSHARE_SCOPES") or os.getenv("WEBSHARE_SCOPES") or "").strip().lower()
    s = (scope or "digest").strip().lower()
    if not raw:
        return True
    if raw in ("*", "all", "1", "true", "yes", "on"):
        return True
    allowed = {p.strip().lower() for p in raw.split(",") if p.strip()}
    return s in allowed


def outbound_http_proxy_url_for_scope(scope: str) -> str:
    """Resolve outbound proxy URL for a given scope (digest, site_crawler, playwright, public_feeds)."""
    g = _generic_outbound_proxy_url()
    if g:
        return _webshare_apply_backbone_port_override(g)
    if _outbound_webshare_scope_allowed(scope):
        w = _webshare_only_proxy_url()
        if w:
            return _webshare_apply_backbone_port_override(w)
    return ""


def outbound_http_proxy_url() -> str:
    """Default proxy URL (scope='digest')."""
    return outbound_http_proxy_url_for_scope("digest")


# ---------------------------------------------------------------------------
# Core HTTP helpers
# ---------------------------------------------------------------------------

def urlopen_with_outbound_proxy(
    req: urllib.request.Request,
    *,
    timeout: int,
    context: ssl.SSLContext,
    proxy_scope: str = "digest",
):
    """urllib open with proxy support; tries Webshare candidate list sequentially."""
    g = _generic_outbound_proxy_url()
    if g:
        proxy = _webshare_apply_backbone_port_override(g)
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy}),
            urllib.request.HTTPHandler(),
            urllib.request.HTTPSHandler(context=context),
        )
        return opener.open(req, timeout=timeout)

    if not _outbound_webshare_scope_allowed(proxy_scope):
        return urllib.request.urlopen(req, timeout=timeout, context=context)

    candidates = [
        _webshare_apply_backbone_port_override(u) for u in _webshare_only_proxy_urls_ordered()
    ]
    if not candidates:
        return urllib.request.urlopen(req, timeout=timeout, context=context)

    last_err: BaseException | None = None
    for proxy in candidates:
        try:
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({"http": proxy, "https": proxy}),
                urllib.request.HTTPHandler(),
                urllib.request.HTTPSHandler(context=context),
            )
            return opener.open(req, timeout=timeout)
        except (urllib.error.URLError, ssl.SSLError, TimeoutError, OSError) as exc:
            last_err = exc
            continue
    if last_err is not None:
        raise last_err
    return urllib.request.urlopen(req, timeout=timeout, context=context)


def fetch_text(
    url: str,
    timeout: int = 15,
    allow_insecure_fallback: bool = False,
    *,
    proxy_scope: str = "digest",
) -> str:
    """GET text from URL with proxy and optional insecure SSL fallback."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})

    def _read_with_context(context: ssl.SSLContext) -> str:
        with urlopen_with_outbound_proxy(
            req, timeout=timeout, context=context, proxy_scope=proxy_scope
        ) as resp:
            return resp.read().decode("utf-8", errors="ignore")

    try:
        return _read_with_context(ssl.create_default_context())
    except ssl.SSLCertVerificationError:
        if not allow_insecure_fallback:
            raise
        return _read_with_context(ssl._create_unverified_context())
    except urllib.error.URLError as ex:
        if not allow_insecure_fallback:
            raise
        if isinstance(ex.reason, ssl.SSLCertVerificationError):
            return _read_with_context(ssl._create_unverified_context())
        raise


def fetch_json_url(
    url: str,
    timeout: int = 20,
    allow_insecure_fallback: bool = False,
    *,
    proxy_scope: str = "digest",
) -> dict[str, Any]:
    """GET JSON from URL with proxy and optional insecure SSL fallback."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})

    def _read(context: ssl.SSLContext) -> dict[str, Any]:
        with urlopen_with_outbound_proxy(
            req, timeout=timeout, context=context, proxy_scope=proxy_scope
        ) as resp:
            body = resp.read().decode("utf-8", errors="ignore")
        return json.loads(body)

    try:
        return _read(ssl.create_default_context())
    except ssl.SSLCertVerificationError:
        if not allow_insecure_fallback:
            raise
        return _read(ssl._create_unverified_context())
    except urllib.error.URLError as ex:
        if not allow_insecure_fallback:
            raise
        if isinstance(ex.reason, ssl.SSLCertVerificationError):
            return _read(ssl._create_unverified_context())
        raise


def playwright_proxy_for_browser() -> dict[str, str] | None:
    """Proxy config dict for ``chromium.launch(proxy=...)``."""
    raw = outbound_http_proxy_url_for_scope("playwright")
    if not raw:
        return None
    parsed = urllib.parse.urlparse(raw)
    if not parsed.hostname:
        return None
    scheme = (parsed.scheme or "http").lower()
    port = parsed.port
    if port is None:
        port = 443 if scheme == "https" else 80
    server = f"{scheme}://{parsed.hostname}:{port}"
    inner: dict[str, str] = {"server": server}
    if parsed.username is not None:
        inner["username"] = urllib.parse.unquote(parsed.username)
    if parsed.password is not None:
        inner["password"] = urllib.parse.unquote(parsed.password)
    return inner
