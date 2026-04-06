#!/usr/bin/env python3
import argparse
import datetime as dt
import html
import json
import os
import pathlib
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import timedelta
from email.utils import parsedate_to_datetime
from typing import Any


DEFAULT_SOURCES_FILE = "sources.json"
DEFAULT_OUTPUT_DIR = "daily_docs"
USER_AGENT = "ai-digest-agent/0.1 (+local-script)"
ARK_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3/chat/completions"


def _webshare_apply_backbone_port_override(proxy_url: str) -> str:
    """
    Webshare Backbone：官方文档允许在 p.webshare.io 上使用 80、1080、3128、9999–19999 等端口。
    若本机 :80 被墙/超时，可在 .env 设 WEBSHARE_BACKBONE_PORT=1080（或 WEBSHARE_PROXY_PORT）。
    """
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
    """
    调用 proxy.webshare.io 的 REST API 时不应走 OUTBOUND_HTTP_PROXY（避免自指环路与 Token 请求异常）。
    见 https://apidocs.webshare.io/proxy-list/list
    """
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
    """
    拉取 Webshare proxy/list JSON（与 YouTube 子项目一致：优先 requests，params mode=backbone&page_size=10）。
    """
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
    """
    与参考实现一致：addr = proxy_address or \"p.webshare.io\"；每条 http://user:pass@addr:port
    """
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
    """
    无 API、无 WEBSHARE_PROXY_URL 时：用控制台 Proxy List 的用户名+密码走旋转网关（与另一项目 WEBSHARE_PROXY_USERNAME/PASSWORD 一致）。
    """
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
    """
    与 YouTube 子项目候选顺序对齐：手写 URL（若有）→ API 多条 → rotating 网关。
    urllib 会依次尝试列表直至成功。
    """
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


def _generic_outbound_proxy_url() -> str:
    """通用环境代理（对所有出站场景生效，优先于 Webshare）。含 YOUTUBE_PROXY_* 以兼容另一项目 .env。"""
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
    """
    Webshare 仅用于需要换出口 IP 的路径（默认：站点 urllib + Playwright），避免 RSS/GNews/公开 API 全走住宅代理。
    OUTBOUND_WEBSHARE_SCOPES（或 WEBSHARE_SCOPES）：逗号分隔
      digest — run_daily_digest 里 RSS、GNews、文章摘录等 fetch_text/fetch_json
      public_feeds — integrations/public_api_feeds
      site_crawler — TechCrunch / VentureBeat 等站点 urllib
      playwright — Chromium 站点爬取
    未设置时默认全场景使用 Webshare（只配 WEBSHARE_API_KEY 即可跑通整站抓取）。
    若只想给浏览器/站点 urllib 用代理：OUTBOUND_WEBSHARE_SCOPES=site_crawler,playwright
    """
    raw = (os.getenv("OUTBOUND_WEBSHARE_SCOPES") or os.getenv("WEBSHARE_SCOPES") or "").strip().lower()
    s = (scope or "digest").strip().lower()
    if not raw:
        return True
    if raw in ("*", "all", "1", "true", "yes", "on"):
        return True
    allowed = {p.strip().lower() for p in raw.split(",") if p.strip()}
    return s in allowed


def outbound_http_proxy_url_for_scope(scope: str) -> str:
    """
    按场景解析出站代理：先通用 HTTPS_PROXY 等；否则仅在 scope 允许时使用 Webshare（含 API / 网关用户名密码）。
    """
    g = _generic_outbound_proxy_url()
    if g:
        return _webshare_apply_backbone_port_override(g)
    if _outbound_webshare_scope_allowed(scope):
        w = _webshare_only_proxy_url()
        if w:
            return _webshare_apply_backbone_port_override(w)
    return ""


def outbound_http_proxy_url() -> str:
    """
    兼容旧调用：等同 outbound_http_proxy_url_for_scope("digest")。
    诊断「Webshare 是否用于浏览器爬取」请用 outbound_http_proxy_url_for_scope("playwright")。
    """
    return outbound_http_proxy_url_for_scope("digest")


def urlopen_with_outbound_proxy(
    req: urllib.request.Request,
    *,
    timeout: int,
    context: ssl.SSLContext,
    proxy_scope: str = "digest",
):
    """
    urllib.request.urlopen 的代理版。
    通用代理单条；Webshare 侧与参考项目一致：API 返回多条时依次尝试直至成功。
    """
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


def playwright_proxy_for_browser() -> dict[str, str] | None:
    """
    供 Playwright chromium.launch(proxy=...) 使用；Webshare 仅当 OUTBOUND_WEBSHARE_SCOPES 含 playwright（默认含）时生效。
    """
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
OPENCLAW_LEADERBOARD_URL = "https://topclawhubskills.com/"
GNEWS_SEARCH_URL = "https://gnews.io/api/v4/search"
DEFAULT_ALLOWED_LLM_HOSTS = {
    "ark.cn-beijing.volces.com",
    "api.openai.com",
    "openrouter.ai",
}
ALLOWED_WEBHOOK_SUFFIXES = (
    ".feishu.cn",
    ".larksuite.com",
    ".dingtalk.com",
)


def now_local() -> dt.datetime:
    return dt.datetime.now().astimezone()


def strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def load_dotenv(path: pathlib.Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def fetch_text(
    url: str,
    timeout: int = 15,
    allow_insecure_fallback: bool = False,
    *,
    proxy_scope: str = "digest",
) -> str:
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
    """GET JSON from HTTPS URL (e.g. GNews API)."""
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


def parse_human_number(text: str) -> float:
    value = (text or "").strip().upper().replace(",", "")
    if not value:
        return 0.0
    m = re.match(r"^([0-9]+(?:\.[0-9]+)?)([KM]?)$", value)
    if not m:
        return 0.0
    num = float(m.group(1))
    unit = m.group(2)
    if unit == "K":
        return num * 1000
    if unit == "M":
        return num * 1000000
    return num


def strip_tags(raw: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", raw or "")).strip()


def fetch_openclaw_stars_top(
    top_n: int = 3, focus_skill: str = "", allow_insecure_fallback: bool = False
) -> tuple[list[dict], dict | None, str]:
    html_text = fetch_text(
        OPENCLAW_LEADERBOARD_URL,
        timeout=25,
        allow_insecure_fallback=allow_insecure_fallback,
    )
    panel_match = re.search(
        r'<div class="panel" id="panel-stars".*?<tbody>(.*?)</tbody>',
        html_text,
        flags=re.S,
    )
    if not panel_match:
        raise ValueError("cannot find OpenClaw stars panel")
    tbody = panel_match.group(1)
    rows = re.findall(r"<tr.*?>(.*?)</tr>", tbody, flags=re.S)
    items = []
    for row in rows:
        tds = re.findall(r"<td.*?>(.*?)</td>", row, flags=re.S)
        if len(tds) < 5:
            continue
        rank_text = strip_tags(tds[0])
        rank = int(rank_text) if rank_text.isdigit() else 0
        skill_anchor_match = re.search(r'(<a[^>]*class="skill-name"[^>]*>.*?</a>)', tds[1], flags=re.S)
        author_match = re.search(r'href="([^"]+)"[^>]*>(.*?)</a>', tds[2], flags=re.S)
        summary_match = re.search(r'<div class="skill-summary">(.*?)</div>', tds[1], flags=re.S)
        skill_anchor = skill_anchor_match.group(1) if skill_anchor_match else ""
        skill_name = strip_tags(skill_anchor)
        skill_url_match = re.search(r'href="([^"]+)"', skill_anchor)
        skill_url = (skill_url_match.group(1) if skill_url_match else "").strip()
        author = strip_tags(author_match.group(2) if author_match else "")
        author_url = (author_match.group(1) if author_match else "").strip()
        summary = strip_tags(summary_match.group(1) if summary_match else "")
        stars_text = strip_tags(tds[3])
        downloads_text = strip_tags(tds[4])
        items.append(
            {
                "rank": rank,
                "skill_name": skill_name,
                "skill_url": skill_url,
                "author": author,
                "author_url": author_url,
                "stars_text": stars_text,
                "stars_num": parse_human_number(stars_text),
                "downloads_text": downloads_text,
                "summary": summary,
            }
        )
    items = [x for x in items if x["rank"] > 0 and x["skill_name"]]
    items.sort(key=lambda x: (-x["stars_num"], x["rank"]))

    focus_result = None
    if focus_skill:
        q = focus_skill.lower().strip()
        for row in items:
            if q in row["skill_name"].lower():
                focus_result = row
                break

    asof_match = re.search(r"As of ([^<]+)</div>", html_text, flags=re.S)
    asof_text = strip_tags(asof_match.group(1)) if asof_match else "最新可用快照"
    return items[: max(1, top_n)], focus_result, asof_text


def text_of(elem: ET.Element, tag: str) -> str:
    child = elem.find(tag)
    return child.text.strip() if child is not None and child.text else ""


def parse_rss(xml_text: str) -> list[dict]:
    items = []
    root = ET.fromstring(xml_text)

    channel = root.find("channel")
    if channel is not None:
        for node in channel.findall("item"):
            title = strip_html(text_of(node, "title"))
            link = text_of(node, "link")
            desc = strip_html(text_of(node, "description"))
            pub_date = text_of(node, "pubDate")
            if title and link:
                items.append(
                    {"title": title, "link": link, "summary": desc, "published": pub_date}
                )

    if items:
        return items

    ns = {
        "atom": "http://www.w3.org/2005/Atom",
    }
    for node in root.findall("atom:entry", ns):
        title = strip_html(text_of(node, "{http://www.w3.org/2005/Atom}title"))
        link = ""
        link_elem = node.find("{http://www.w3.org/2005/Atom}link")
        if link_elem is not None:
            link = link_elem.attrib.get("href", "")
        summary = text_of(node, "{http://www.w3.org/2005/Atom}summary")
        if not summary:
            summary = text_of(node, "{http://www.w3.org/2005/Atom}content")
        published = text_of(node, "{http://www.w3.org/2005/Atom}updated")
        if title and link:
            items.append(
                {
                    "title": strip_html(title),
                    "link": link.strip(),
                    "summary": strip_html(summary),
                    "published": published,
                }
            )
    return items


def dedupe_items(items: list[dict]) -> list[dict]:
    seen = set()
    out = []
    for it in items:
        key = (it.get("link") or "").strip() or (it.get("title") or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


def _clause_core_phrases(intent_text: str) -> list[str]:
    """按中英文逗号拆句，去掉「我要查/还想看看…」等套话，得到独立检索短语（LLM 失败时的回退）。"""
    raw = (intent_text or "").strip()
    if not raw:
        return []
    if not re.search(r"[，,;；]", raw):
        return []
    out: list[str] = []
    for clause in re.split(r"[，,;；]+", raw):
        s = clause.strip()
        if not s:
            continue
        s = re.sub(
            r"^(我要查|我想查|查一下|还想看看|想看看|看看)+",
            "",
            s,
            flags=re.IGNORECASE,
        ).strip()
        s = re.sub(r"(的新闻|的进展|相关资讯|资讯|新闻)$", "", s).strip()
        s = re.sub(r"\s+", " ", s)
        if len(s) >= 2 and s.lower() not in {x.lower() for x in out}:
            out.append(s)
    return out[:8]


def extract_intent_keywords(intent_text: str) -> list[str]:
    text = (intent_text or "").strip().lower()
    if not text:
        return []
    clause_kw = _clause_core_phrases(intent_text)
    if clause_kw:
        return clause_kw
    raw_tokens = re.findall(r"[a-z0-9][a-z0-9\-\+\.]*|[\u4e00-\u9fff]{2,}", text)
    stopwords = {
        "新闻",
        "资讯",
        "相关",
        "看看",
        "查询",
        "我想",
        "一下",
        "最近",
        "关于",
        "生成",
        "ai",
        "我要查",
        "的新闻",
        "还想看看",
        "的进展",
        "还想",
        "我要",
        "一下的",
        "进展",
    }
    out: list[str] = []
    for tok in raw_tokens:
        if tok in stopwords or len(tok) < 2:
            continue
        if tok not in out:
            out.append(tok)
    return out[:12]


def rank_items_by_intent(items: list[dict], intent_text: str) -> list[dict]:
    keywords = extract_intent_keywords(intent_text)
    if not items or not keywords:
        return items

    def _score(it: dict) -> int:
        hay = " ".join(
            [
                str(it.get("title", "")).lower(),
                str(it.get("summary", "")).lower(),
                str(it.get("category", "")).lower(),
                str(it.get("source", "")).lower(),
            ]
        )
        score = 0
        for kw in keywords:
            if kw in hay:
                score += 3
        return score

    scored = [(it, _score(it)) for it in items]
    matched = [x for x in scored if x[1] > 0]
    if not matched:
        return items
    unmatched = [x for x in scored if x[1] == 0]
    matched.sort(
        key=lambda x: (x[1], parse_published_dt_for_sort(x[0].get("published", ""))),
        reverse=True,
    )
    unmatched.sort(key=lambda x: parse_published_dt_for_sort(x[0].get("published", "")), reverse=True)
    # Keep matched items first, but never drop unmatched news.
    return [x[0] for x in matched] + [x[0] for x in unmatched]


def load_sources(path: pathlib.Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError("sources.json must be an array")
    return data


def _parse_published_dt_collect(raw: str, now_dt: dt.datetime) -> dt.datetime | None:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        d = parsedate_to_datetime(text)
        if d is None:
            return None
        if d.tzinfo is None:
            d = d.replace(tzinfo=now_dt.tzinfo)
        return d.astimezone(now_dt.tzinfo)
    except Exception:
        pass
    try:
        norm = text.replace("Z", "+00:00")
        d2 = dt.datetime.fromisoformat(norm)
        if d2.tzinfo is None:
            d2 = d2.replace(tzinfo=now_dt.tzinfo)
        return d2.astimezone(now_dt.tzinfo)
    except Exception:
        return None


def _collect_one_rss_source(
    src: dict,
    *,
    per_source: int,
    allow_insecure_fallback: bool,
    now_dt: dt.datetime,
    cutoff: dt.datetime,
) -> tuple[list[dict], list[str]]:
    out: list[dict] = []
    errs: list[str] = []
    name = src.get("name", "unknown")
    rss = src.get("rss_url", "").strip()
    category = src.get("category", "其他")
    if not rss:
        return out, [f"{name}: rss_url empty"]
    try:
        try:
            rss_to = int(os.environ.get("RSS_HTTP_TIMEOUT", "15"))
        except ValueError:
            rss_to = 15
        rss_to = max(5, min(90, rss_to))
        xml_text = fetch_text(
            rss,
            timeout=rss_to,
            allow_insecure_fallback=allow_insecure_fallback,
        )
        entries = parse_rss(xml_text)[:per_source]
        for e in entries:
            pub_dt = _parse_published_dt_collect(e.get("published", ""), now_dt)
            if pub_dt is None or pub_dt < cutoff or pub_dt > now_dt + timedelta(minutes=10):
                continue
            out.append(
                {
                    "source": name,
                    "category": category,
                    "title": e["title"],
                    "link": e["link"],
                    "summary": e.get("summary", ""),
                    "published": e.get("published", ""),
                }
            )
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ET.ParseError) as ex:
        errs.append(f"{name}: {type(ex).__name__} {ex}")
    except Exception as ex:  # noqa: BLE001
        errs.append(f"{name}: {type(ex).__name__} {ex}")
    return out, errs


def collect_news(
    sources: list[dict],
    per_source: int,
    allow_insecure_fallback: bool,
    window_hours: int,
    *,
    max_parallel: int | None = None,
    rss_source_hook: Callable[[str, float, int, list[str]], None] | None = None,
) -> tuple[list[dict], list[str]]:
    all_items: list[dict] = []
    errors: list[str] = []
    now_dt = now_local()
    cutoff = now_dt - timedelta(hours=max(1, window_hours))
    if not sources:
        return dedupe_items(all_items), errors

    try:
        env_w = int(os.environ.get("COLLECT_NEWS_MAX_WORKERS", "8"))
    except ValueError:
        env_w = 8
    workers = max_parallel if max_parallel is not None else env_w
    workers = max(1, min(workers, len(sources)))

    def _one(src: dict) -> tuple[list[dict], list[str]]:
        t0 = time.perf_counter()
        chunk, errs = _collect_one_rss_source(
            src,
            per_source=per_source,
            allow_insecure_fallback=allow_insecure_fallback,
            now_dt=now_dt,
            cutoff=cutoff,
        )
        if rss_source_hook is not None:
            elapsed = time.perf_counter() - t0
            rss_source_hook(str(src.get("name", "")), elapsed, len(chunk), errs)
        return chunk, errs

    if workers <= 1:
        for src in sources:
            chunk, errs = _one(src)
            all_items.extend(chunk)
            errors.extend(errs)
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futs = [pool.submit(_one, src) for src in sources]
            for fut in as_completed(futs):
                chunk, errs = fut.result()
                all_items.extend(chunk)
                errors.extend(errs)
    return dedupe_items(all_items), errors


def build_gnews_query(
    intent_text: str, plan_keywords: list[str], override: str = ""
) -> str:
    if (override or "").strip():
        return (override or "").strip()[:500]
    q = (intent_text or "").strip()
    if q:
        return q[:500]
    clean_kw = [str(k).strip() for k in (plan_keywords or []) if str(k).strip()]
    if clean_kw:
        return " OR ".join(clean_kw[:8])[:500]
    return "artificial intelligence"


GNEWS_LLM_SYSTEM = (
    "你是新闻搜索词抽取助手。用户用自然语言描述想看的资讯。"
    "请只输出一行：适合新闻搜索 API 的查询字符串（3–10 个关键词，用空格分隔；中英文均可）。"
    "不要引号、不要解释、不要前缀、不要换行。"
)


INTENT_ANALYSIS_SYSTEM = (
    "你是资讯检索意图分析助手。用户用自然语言描述想同时追踪的几类主题。\n"
    "请从用户话里抽出 **彼此独立** 的检索短语（每个短语用于单独搜索新闻/网页），"
    "保留原文中的专有名词（如 agent harness、Claude Code）。\n"
    "只输出一个 JSON：可以是字符串数组，或形如 {\"queries\":[\"...\",\"...\"]} 的对象。"
    "短语数量 1～8 个；不要重复；不要解释；不要 markdown 代码块标记。"
)


def llm_extract_intent_search_queries(
    intent_text: str,
    *,
    api_key: str,
    model: str,
    base_url: str,
    allow_insecure_fallback: bool,
) -> list[str]:
    """LLM 将自然语言意图拆成多个独立检索短语；失败返回 []."""
    text = (intent_text or "").strip()
    if not text:
        return []
    try:
        raw = call_chat_completion(
            api_key=api_key,
            model=model,
            messages=[
                {"role": "system", "content": INTENT_ANALYSIS_SYSTEM},
                {"role": "user", "content": text},
            ],
            base_url=base_url,
            timeout=60,
            allow_insecure_fallback=allow_insecure_fallback,
        )
    except Exception:
        return []
    return _parse_intent_queries_json(raw)


def _parse_intent_queries_json(raw: str) -> list[str]:
    s = (raw or "").strip()
    s = re.sub(r"^```(?:json)?\s*", "", s, flags=re.IGNORECASE | re.MULTILINE)
    s = re.sub(r"\s*```\s*$", "", s, flags=re.MULTILINE).strip()
    if not s:
        return []
    try:
        data = json.loads(s)
    except Exception:
        m = re.search(r"(\[[\s\S]*\]|\{[\s\S]*\})", s)
        if not m:
            return []
        try:
            data = json.loads(m.group(1))
        except Exception:
            return []
    out: list[str] = []
    if isinstance(data, list):
        out = [str(x).strip() for x in data if str(x).strip()]
    elif isinstance(data, dict):
        q = data.get("queries") or data.get("search_queries") or data.get("keywords")
        if isinstance(q, list):
            out = [str(x).strip() for x in q if str(x).strip()]
    seen: set[str] = set()
    uniq: list[str] = []
    for x in out:
        low = x.lower()
        if low in seen:
            continue
        seen.add(low)
        uniq.append(x)
    return uniq[:8]


def infer_gnews_search_query_llm(
    intent_text: str,
    *,
    api_key: str,
    model: str,
    base_url: str,
    allow_insecure_fallback: bool,
) -> str:
    text = (intent_text or "").strip()
    if not text:
        return ""
    try:
        content = call_chat_completion(
            api_key=api_key,
            model=model,
            base_url=base_url,
            allow_insecure_fallback=allow_insecure_fallback,
            messages=[
                {"role": "system", "content": GNEWS_LLM_SYSTEM},
                {"role": "user", "content": text},
            ],
        )
        q = (content or "").strip().splitlines()[0].strip()
        q = re.sub(r'^["\']|["\']$', "", q)
        q = re.sub(r"\s+", " ", q).strip()
        if not q:
            return ""
        return q[:500]
    except Exception:
        return ""


def infer_gnews_search_query(
    intent_text: str,
    *,
    api_key: str | None,
    model: str,
    base_url: str,
    allow_insecure_fallback: bool,
) -> str:
    """LLM 抽取 GNews 搜索词；无 key 或失败时回退为规则分词 OR 拼接。"""
    text = (intent_text or "").strip()
    if not text:
        return "artificial intelligence"
    if (api_key or "").strip():
        q = infer_gnews_search_query_llm(
            text,
            api_key=api_key.strip(),
            model=model,
            base_url=base_url,
            allow_insecure_fallback=allow_insecure_fallback,
        )
        if q.strip():
            return q.strip()
    kws = extract_intent_keywords(text)
    if kws:
        return build_gnews_query("", kws, "")
    return text[:500] if text else "artificial intelligence"


def attach_content_excerpts_to_items(
    items: list[dict],
    allow_insecure_fallback: bool,
    *,
    max_parallel: int | None = None,
) -> None:
    """Fill content_excerpt by fetching article HTML when RSS summary is thin."""
    to_fetch: list[dict] = []
    for it in items:
        ce = (it.get("content_excerpt") or "").strip()
        if len(ce) >= 120:
            continue
        link = (it.get("link") or "").strip()
        if not link:
            continue
        to_fetch.append(it)

    if not to_fetch:
        return

    try:
        env_w = int(os.environ.get("EXCERPT_FETCH_MAX_WORKERS", "8"))
    except ValueError:
        env_w = 8
    workers = max_parallel if max_parallel is not None else env_w
    workers = max(1, min(workers, len(to_fetch)))

    def _fetch_one(it: dict) -> tuple[dict, str]:
        link = (it.get("link") or "").strip()
        ex = fetch_article_excerpt(link, allow_insecure_fallback=allow_insecure_fallback)
        return it, ex

    if workers <= 1:
        for it in to_fetch:
            _, excerpt = _fetch_one(it)
            if excerpt:
                it["content_excerpt"] = excerpt
        return

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = [pool.submit(_fetch_one, it) for it in to_fetch]
        for fut in as_completed(futs):
            it, excerpt = fut.result()
            if excerpt:
                it["content_excerpt"] = excerpt


def is_valid_article_excerpt(text: str) -> bool:
    """正文可用：足够长且不像接口/反爬错误 JSON。"""
    t = (text or "").strip()
    if len(t) < 120:
        return False
    head = t[:900]
    if '{"error"' in head or '"error"' in head[:400] and '"message"' in head[:600]:
        return False
    if "暂时限制本次访问" in t or ("存在异常" in t and "知乎" in t):
        return False
    return True


def cap_items_per_category(items: list[dict], max_per: int = 5) -> list[dict]:
    """每个 category 最多保留 max_per 条（按发布时间新到旧）。"""
    if not items or max_per <= 0:
        return items
    grouped: dict[str, list[dict]] = defaultdict(list)
    for it in items:
        cat = str(it.get("category", "其他") or "其他")
        grouped[cat].append(it)
    out: list[dict] = []
    for cat in sorted(grouped.keys(), key=category_order_key):
        rows = grouped[cat]
        rows.sort(key=lambda x: parse_published_dt_for_sort(x.get("published", "")), reverse=True)
        out.extend(rows[:max_per])
    return dedupe_items(out)


def fetch_gnews_articles(
    *,
    query: str,
    api_key: str,
    window_hours: int,
    max_articles: int,
    lang: str,
    category: str,
    allow_insecure_fallback: bool,
) -> tuple[list[dict], str | None]:
    """Call GNews API v4 search; returns items compatible with collect_news."""
    if not query.strip() or not api_key.strip():
        return [], "GNews: empty query or api key"

    now_dt = now_local()
    cutoff = now_dt - timedelta(hours=max(1, window_hours))

    def _parse_iso_dt(raw: str) -> dt.datetime | None:
        text = (raw or "").strip()
        if not text:
            return None
        try:
            norm = text.replace("Z", "+00:00")
            d2 = dt.datetime.fromisoformat(norm)
            if d2.tzinfo is None:
                d2 = d2.replace(tzinfo=dt.timezone.utc)
            return d2.astimezone(now_dt.tzinfo)
        except Exception:
            return None

    params = {
        "q": query,
        "lang": lang,
        "max": str(min(max(1, max_articles), 100)),
        "apikey": api_key.strip(),
    }
    full_url = f"{GNEWS_SEARCH_URL}?{urllib.parse.urlencode(params)}"

    try:
        data = fetch_json_url(full_url, allow_insecure_fallback=allow_insecure_fallback)
    except urllib.error.HTTPError as ex:
        body = ex.read().decode("utf-8", errors="ignore")
        msg = body[:400]
        try:
            ej = json.loads(body)
            err = ej.get("errors", ej.get("message"))
            if isinstance(err, list):
                msg = "; ".join(str(x) for x in err)
            elif err:
                msg = str(err)
        except Exception:  # noqa: BLE001
            pass
        return [], f"GNews: HTTP {ex.code} {msg}"
    except Exception as ex:  # noqa: BLE001
        return [], f"GNews: {type(ex).__name__} {ex}"

    if not isinstance(data, dict):
        return [], "GNews: invalid JSON response"

    errs = data.get("errors")
    if errs:
        if isinstance(errs, list):
            return [], f"GNews: {'; '.join(str(x) for x in errs)}"
        return [], f"GNews: {errs}"

    articles = data.get("articles") or []
    out: list[dict] = []
    for art in articles:
        if not isinstance(art, dict):
            continue
        link = (art.get("url") or "").strip()
        title = (art.get("title") or "").strip()
        if not link or not title:
            continue
        src = art.get("source") if isinstance(art.get("source"), dict) else {}
        src_name = (src.get("name") if isinstance(src, dict) else "") or "GNews"
        summary = (art.get("description") or art.get("content") or "").strip()
        published = (art.get("publishedAt") or "").strip()
        pub_dt = _parse_iso_dt(published)
        if pub_dt is None or pub_dt < cutoff or pub_dt > now_dt + timedelta(minutes=10):
            continue
        out.append(
            {
                "source": f"{src_name} (GNews)",
                "category": category,
                "title": title,
                "link": link,
                "summary": summary,
                "published": published,
            }
        )
    return dedupe_items(out), None


def fetch_gnews_for_pipeline(
    *,
    intent_text: str,
    plan_keywords: list[str],
    api_key: str,
    gnews_query: str = "",
    window_hours: int,
    max_articles: int,
    lang: str,
    category: str,
    allow_insecure_fallback: bool,
) -> tuple[list[dict], str | None]:
    q = build_gnews_query(intent_text, plan_keywords, override=gnews_query)
    return fetch_gnews_articles(
        query=q,
        api_key=api_key,
        window_hours=window_hours,
        max_articles=max_articles,
        lang=lang,
        category=category,
        allow_insecure_fallback=allow_insecure_fallback,
    )


def parse_published_dt_for_sort(raw: str) -> dt.datetime:
    text = (raw or "").strip()
    if not text:
        return dt.datetime.fromtimestamp(0, tz=now_local().tzinfo)
    try:
        d = parsedate_to_datetime(text)
        if d is None:
            raise ValueError("empty datetime")
        if d.tzinfo is None:
            d = d.replace(tzinfo=now_local().tzinfo)
        return d.astimezone(now_local().tzinfo)
    except Exception:
        pass
    try:
        norm = text.replace("Z", "+00:00")
        d2 = dt.datetime.fromisoformat(norm)
        if d2.tzinfo is None:
            d2 = d2.replace(tzinfo=now_local().tzinfo)
        return d2.astimezone(now_local().tzinfo)
    except Exception:
        return dt.datetime.fromtimestamp(0, tz=now_local().tzinfo)


def balance_items(
    items: list[dict],
    max_paper_ratio: float,
    min_official_items: int,
) -> list[dict]:
    if not items:
        return items
    safe_ratio = min(1.0, max(0.0, max_paper_ratio))
    papers = [x for x in items if x.get("category") == "论文研究"]
    non_papers = [x for x in items if x.get("category") != "论文研究"]
    papers.sort(key=lambda x: parse_published_dt_for_sort(x.get("published", "")), reverse=True)
    non_papers.sort(key=lambda x: parse_published_dt_for_sort(x.get("published", "")), reverse=True)

    # Ensure official releases are prioritized in non-paper set.
    official = [x for x in non_papers if x.get("category") == "官方发布"]
    others = [x for x in non_papers if x.get("category") != "官方发布"]
    non_papers_sorted = official + others

    if not papers or safe_ratio >= 1.0:
        return non_papers_sorted + papers

    total_target = len(items)
    max_papers = int(total_target * safe_ratio)
    max_papers = max(0, max_papers)
    keep_papers = papers[:max_papers]
    kept = non_papers_sorted + keep_papers

    # Keep at least some official news if available.
    if min_official_items > 0:
        official_kept = [x for x in kept if x.get("category") == "官方发布"]
        if len(official_kept) < min_official_items and official:
            need = min_official_items - len(official_kept)
            add = official[:need]
            for x in add:
                if x not in kept:
                    kept.insert(0, x)

    return dedupe_items(kept)


def cap_papers_by_ratio(items: list[dict], max_paper_ratio: float) -> list[dict]:
    if not items:
        return items
    safe_ratio = min(1.0, max(0.0, max_paper_ratio))
    papers = [x for x in items if x.get("category") == "论文研究"]
    news = [x for x in items if x.get("category") != "论文研究"]
    if not papers:
        return items
    if not news and safe_ratio < 1.0:
        # If only papers are available, keep a few to avoid empty report.
        return papers[: max(1, min(5, len(papers)))]

    # papers <= ratio * total => papers <= ratio/(1-ratio) * news
    max_papers = int((safe_ratio / (1.0 - safe_ratio)) * len(news)) if safe_ratio < 1.0 else len(papers)
    max_papers = max(0, min(len(papers), max_papers))
    papers_sorted = sorted(
        papers,
        key=lambda x: parse_published_dt_for_sort(x.get("published", "")),
        reverse=True,
    )
    return dedupe_items(news + papers_sorted[:max_papers])


def category_order_key(name: str) -> tuple[int, str]:
    preferred = {
        "官方发布": 0,
        "国内厂商动态": 1,
        "开源与工具": 2,
        "行业资讯": 3,
        "垂直与趣味": 4,
        "网页检索": 5,
        "社区讨论": 6,
        # Paper feeds are often less time-sensitive in daily digest; keep at the bottom.
        "论文研究": 98,
        "其他": 99,
    }
    return (preferred.get(name, 90), name)


def call_chat_completion(
    api_key: str,
    model: str,
    messages: list[dict],
    base_url: str,
    timeout: int = 90,
    allow_insecure_fallback: bool = False,
) -> str:
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.2,
    }
    req = urllib.request.Request(
        base_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ssl.create_default_context()) as resp:
            body = resp.read().decode("utf-8", errors="ignore")
    except ssl.SSLCertVerificationError:
        if not allow_insecure_fallback:
            raise
        with urllib.request.urlopen(
            req, timeout=timeout, context=ssl._create_unverified_context()
        ) as resp:
            body = resp.read().decode("utf-8", errors="ignore")
    except urllib.error.URLError as ex:
        if not allow_insecure_fallback:
            raise
        if isinstance(ex.reason, ssl.SSLCertVerificationError):
            with urllib.request.urlopen(
                req, timeout=timeout, context=ssl._create_unverified_context()
            ) as resp:
                body = resp.read().decode("utf-8", errors="ignore")
        else:
            raise
    data = json.loads(body)
    try:
        return data["choices"][0]["message"]["content"]
    except Exception as ex:  # noqa: BLE001
        raise ValueError(f"invalid chat completion response: {ex}") from ex


def normalize_chat_completions_url(raw_url: str) -> str:
    url = (raw_url or "").strip()
    if not url:
        return ""
    if url.endswith("/v1"):
        return url + "/chat/completions"
    if url.endswith("/chat/completions"):
        return url
    return url.rstrip("/") + "/chat/completions"


def validate_https_url(url: str, field_name: str) -> urllib.parse.ParseResult:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme.lower() != "https" or not parsed.netloc:
        raise ValueError(f"{field_name} must be a valid https URL")
    return parsed


def allowed_llm_hosts_from_env() -> set[str]:
    raw = os.getenv("LLM_ALLOWED_HOSTS", "")
    extra = {x.strip().lower() for x in raw.split(",") if x.strip()}
    return {h.lower() for h in DEFAULT_ALLOWED_LLM_HOSTS} | extra


def resolve_llm_runtime(args: argparse.Namespace) -> tuple[str, str, str]:
    # Backward-compatible API key resolution: Ark first, then common OpenAI-style env names.
    api_key = (
        args.ark_api_key
        or os.getenv("ARK_API_KEY", "")
        or os.getenv("OPENAI_API_KEY", "")
        or os.getenv("api_key", "")
        or os.getenv("API_KEY", "")
        or ""
    ).strip()

    ark_model_or_ep = (
        args.ark_endpoint_id
        or os.getenv("ARK_ENDPOINT_ID", "")
        or args.ark_model
        or os.getenv("ARK_MODEL", "")
        or ""
    ).strip()
    openai_model = (os.getenv("OPENAI_MODEL", "") or os.getenv("MODEL", "")).strip()
    model = ark_model_or_ep or openai_model or "Doubao-Seed-1.6-lite"

    provider = (args.llm_provider or "auto").strip().lower()
    if provider not in {"auto", "ark", "openai-compatible"}:
        provider = "auto"

    # Auto mode: if Ark-specific vars exist, keep Ark behavior; otherwise use OpenAI-compatible.
    has_ark_hint = bool(
        args.ark_endpoint_id or os.getenv("ARK_ENDPOINT_ID") or os.getenv("ARK_MODEL")
    )
    if provider == "auto":
        provider = "ark" if has_ark_hint else "openai-compatible"

    if provider == "ark":
        base_url = (os.getenv("ARK_BASE_URL", "") or ARK_BASE_URL).strip()
    else:
        base_url = (
            args.llm_base_url
            or os.getenv("OPENAI_BASE_URL", "")
            or os.getenv("LLM_BASE_URL", "")
            or ""
        ).strip()
        if not base_url:
            raise ValueError(
                "openai-compatible mode requires OPENAI_BASE_URL (or --llm-base-url)"
            )
        base_url = normalize_chat_completions_url(base_url)

    base_url = normalize_chat_completions_url(base_url)
    parsed = validate_https_url(base_url, "LLM base URL")
    host = (parsed.hostname or "").lower()
    if not args.allow_custom_llm_endpoint and host not in allowed_llm_hosts_from_env():
        raise ValueError(
            f"LLM endpoint host '{host}' is not in allowlist. "
            "Use --allow-custom-llm-endpoint or set LLM_ALLOWED_HOSTS."
        )

    return provider, base_url, api_key


def _trafilatura_extract(html: str, url: str) -> str:
    try:
        import trafilatura

        extracted = trafilatura.extract(
            html,
            url=url,
            include_comments=False,
            include_tables=False,
            favor_recall=True,
        )
        if extracted and len(extracted.strip()) >= 80:
            return re.sub(r"\s+", " ", extracted.strip())
    except Exception:
        pass
    return ""


def fetch_article_excerpt(url: str, allow_insecure_fallback: bool = False) -> str:
    if not url:
        return ""
    # Avoid non-HTML pages such as PDF downloads.
    if url.lower().endswith(".pdf"):
        return ""
    try:
        page = fetch_text(url, timeout=22, allow_insecure_fallback=allow_insecure_fallback)
    except Exception:
        return ""
    tf = _trafilatura_extract(page, url)
    if len(tf) >= 120:
        return tf[:8000]
    page = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", page)
    page = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", page)
    # Prefer main/article section when possible, then fallback to full page text.
    main_match = re.search(r"(?is)<main[^>]*>(.*?)</main>", page)
    article_match = re.search(r"(?is)<article[^>]*>(.*?)</article>", page)
    best_block = (main_match.group(1) if main_match else "") or (article_match.group(1) if article_match else "")
    # Meta description is often the only stable summary block on modern JS-heavy pages.
    meta_desc = ""
    meta_match = re.search(
        r'(?is)<meta[^>]+(?:name|property)=["\'](?:description|og:description)["\'][^>]+content=["\'](.*?)["\']',
        page,
    )
    if meta_match:
        meta_desc = strip_tags(meta_match.group(1))
    text = strip_tags(best_block) if best_block else strip_tags(page)
    if len(text) < 120 and meta_desc:
        text = meta_desc
    if len(text) < 120:
        return ""
    return text[:8000]


def to_beijing_time_label(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return "未知"
    dt_obj = None
    try:
        dt_obj = parsedate_to_datetime(text)
    except Exception:
        dt_obj = None
    if dt_obj is None:
        try:
            dt_obj = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
        except Exception:
            return text
    if dt_obj.tzinfo is None:
        dt_obj = dt_obj.replace(tzinfo=dt.timezone.utc)
    bj = dt_obj.astimezone(dt.timezone(dt.timedelta(hours=8)))
    return bj.strftime("%a, %d %b %Y %H:%M:%S CST")


def format_source_desc(item: dict) -> str:
    source = item.get("source", "未知信源")
    category = item.get("category", "行业动态")
    return f"{source}的{category}动态"


def looks_mostly_english(text: str) -> bool:
    s = (text or "").strip()
    if not s:
        return False
    letters = re.findall(r"[A-Za-z]", s)
    cjk = re.findall(r"[\u4e00-\u9fff]", s)
    return len(letters) > 0 and len(cjk) == 0


def fallback_title_cn(item: dict, idx: int) -> str:
    title = (item.get("title") or "").strip()
    summary = strip_html((item.get("summary") or "").strip())
    category = item.get("category", "资讯")
    if title and not looks_mostly_english(title):
        return title
    if summary and not looks_mostly_english(summary):
        head = summary[:28].rstrip("，。,:;；")
        if head:
            return f"{head}（{category}）"
    if title:
        link = (item.get("link") or "").strip()
        slug = ""
        if link:
            path = urllib.parse.urlparse(link).path.strip("/")
            if path:
                slug = path.split("/")[-1].replace("-", " ").strip()
        if slug:
            return f"英文资讯：{slug}（{category}）"
        return f"英文资讯：{title[:40]}（{category}）"
    return f"第{idx}条资讯"


def enrich_items_with_llm(
    items: list[dict],
    api_key: str,
    model: str,
    base_url: str,
    allow_insecure_fallback: bool,
    *,
    prompt_variant: str = "auto",
    intent_text: str = "",
    enable_openclaw: bool = False,
) -> tuple[str, list[str], list[str], list[str]]:
    if not items:
        return "今日暂无可分析资讯。", [], [], []

    rows = []
    for idx, it in enumerate(items, 1):
        cached = (it.get("content_excerpt") or "").strip()
        excerpt = cached if len(cached) >= 120 else fetch_article_excerpt(
            it.get("link", ""), allow_insecure_fallback=allow_insecure_fallback
        )
        rows.append(
            {
                "idx": idx,
                "category": it.get("category", "其他"),
                "source": it.get("source", ""),
                "title": it.get("title", ""),
                "summary": (it.get("summary", "") or "")[:220],
                "link": it.get("link", ""),
                "published": it.get("published", ""),
                "content_excerpt": excerpt[:2200],
            }
        )

    from prompts.digest_llm_prompts import pick_enrich_system_prompt

    pv = (prompt_variant or "auto").strip().lower()
    if pv == "auto":
        pv = "intent" if (intent_text or "").strip() else "news"
    system_prompt = pick_enrich_system_prompt(
        prompt_variant=pv,
        intent_text=intent_text,
        enable_openclaw=enable_openclaw,
    )
    user_prompt = {
        "task": "生成周报顶部小结和逐条中文解读",
        "rules": {
            "overview": "3-4句，概括本周动态、主线与重要变化",
            "per_item": (
                "每条生成 title_cn、core_cn、value_cn，全部必须为中文。"
                "title_cn：中文标题，原文英文须译成中文。"
                "core_cn：仅依据 news 中每条 content_excerpt（全文摘录）撰写，"
                "将信息压缩为约 200–300 个汉字（约 200–300 字）的核心内容；"
                "英文内容必须译为中文后再组织语言；不得少于约 200 字、不要明显超过 300 字。"
                "value_cn：该动态对业务/产品/研发的应用价值，不超过 80 字。"
                "禁止空话、禁止复述标题不写实质信息。"
            ),
            "style": "中文、自然、信息密度高，不要空话",
        },
        "format": {
            "overview": "string",
            "items": [{"idx": 1, "title_cn": "string", "core_cn": "string", "value_cn": "string"}],
        },
        "news": rows,
    }

    from chains.news_enrich_chain import invoke_enrich_chain

    overview, llm_core_points, llm_values, llm_titles_cn = invoke_enrich_chain(
        system_prompt,
        user_prompt,
        api_key=api_key,
        model=model,
        base_url=base_url,
        allow_insecure_fallback=allow_insecure_fallback,
        item_count=len(items),
    )

    # Post-process translation/normalization:
    # If LLM output is empty or still mostly English (common when structured parsing fails),
    # re-translate title/core/value into Chinese. This ensures "英文资讯：..." and English core
    # don't leak into the report.
    def _safe_strip_json(text: str) -> str:
        s = (text or "").strip()
        s = re.sub(r"^```(?:json)?\s*", "", s, flags=re.IGNORECASE)
        s = re.sub(r"\s*```$", "", s)
        return s.strip()

    def _safe_extract_first_json_object(text: str) -> dict[str, Any] | None:
        raw = _safe_strip_json(text)
        try:
            obj = json.loads(raw)
            return obj if isinstance(obj, dict) else None
        except Exception:
            pass
        # Fallback: find first {...} block
        m = re.search(r"(\{[\s\S]*\})", raw)
        if not m:
            return None
        try:
            obj = json.loads(m.group(1))
            return obj if isinstance(obj, dict) else None
        except Exception:
            return None

    # Determine which indices need a translation rerun.
    need_idxs: list[int] = []
    for i in range(len(items)):
        t = (llm_titles_cn[i] if i < len(llm_titles_cn) else "") or ""
        c = (llm_core_points[i] if i < len(llm_core_points) else "") or ""
        v = (llm_values[i] if i < len(llm_values) else "") or ""
        if not t.strip() or looks_mostly_english(t):
            need_idxs.append(i)
            continue
        if not c.strip() or looks_mostly_english(c):
            need_idxs.append(i)
            continue
        # value_cn is shorter; translate only when empty/English.
        if v and looks_mostly_english(v):
            need_idxs.append(i)

    if need_idxs and api_key and (api_key or "").strip():
        # Batch translate only the needed items to reduce token usage.
        payload_items: list[dict[str, Any]] = []
        for i in need_idxs:
            core_src = (rows[i].get("content_excerpt") or "").strip()
            # Keep translation prompt size reasonable.
            if len(core_src) > 6000:
                core_src = core_src[:6000]
            payload_items.append(
                {
                    "idx": i + 1,
                    "title": (rows[i].get("title") or "").strip(),
                    "core_src": core_src,
                    "value_src": (rows[i].get("summary") or "").strip(),
                }
            )

        translate_system_prompt = (
            "你是中文新闻编辑。任务：把输入中每条的英文标题/正文摘要翻译成中文，并压缩成报告可用的核心内容。"
            "输出严格 JSON，不要解释，不要 markdown。"
        )
        translate_user_prompt = {
            "items": payload_items,
            "constraints": {
                "title_cn": "中文标题，不能为空，尽量不超过 60 字。",
                "core_cn": "核心内容：压缩为约 200-300 个汉字；若核心输入为英文，必须翻译后再压缩；不得少于约 200 字，不要明显超过 300 字。",
                "value_cn": "应用价值：<=80 字中文。",
            },
        }
        try:
            translate_raw = call_chat_completion(
                api_key=api_key,
                model=model,
                messages=[
                    {"role": "system", "content": translate_system_prompt},
                    {"role": "user", "content": json.dumps(translate_user_prompt, ensure_ascii=False)},
                ],
                base_url=base_url,
                timeout=120,
                allow_insecure_fallback=allow_insecure_fallback,
            )
            obj = _safe_extract_first_json_object(translate_raw)
            if obj:
                arr = obj.get("items") or obj.get("results") or []
                if isinstance(arr, list):
                    for row in arr:
                        if not isinstance(row, dict):
                            continue
                        idx = row.get("idx")
                        try:
                            idx_i = int(idx) - 1
                        except Exception:
                            continue
                        if 0 <= idx_i < len(items):
                            if isinstance(row.get("title_cn"), str) and row["title_cn"].strip():
                                llm_titles_cn[idx_i] = row["title_cn"].strip()
                            if isinstance(row.get("core_cn"), str) and row["core_cn"].strip():
                                llm_core_points[idx_i] = row["core_cn"].strip()
                            if isinstance(row.get("value_cn"), str) and row["value_cn"].strip():
                                llm_values[idx_i] = row["value_cn"].strip()
        except Exception:
            # If translation fails, we keep original outputs and fallback_title_cn/core_cn
            # will still be used later.
            pass

    return overview, llm_core_points, llm_values, llm_titles_cn


def build_fallback_detail(item: dict) -> str:
    summary = (item.get("summary") or "").strip()
    if summary:
        base = summary if len(summary) <= 220 else summary[:217] + "..."
    else:
        base = "原文摘要信息较少，建议打开原文链接查看完整上下文。"
    return f"核心内容：{base}\n应用价值：可作为相关产品路线和竞品动态的输入信号。"


def build_openclaw_purpose_text(skill_name: str, summary: str) -> str:
    text = (summary or "").strip()
    lower = text.lower()
    if "continuous improvement" in lower or "self-improving" in lower:
        return (
            "这个技能用于把代理在执行任务时的失败案例、修正过程和成功经验沉淀成可复用记忆，"
            "让后续同类任务少走弯路。它适合长期使用的个人工作流，价值在于持续降低重复错误率，"
            "并逐步提升任务完成质量与稳定性。"
        )
    if "google workspace" in lower or "gmail" in lower or "calendar" in lower:
        return (
            "这个技能把邮件、日历、文档、表格、网盘等 Google Workspace 操作统一成可调用能力，"
            "适合做跨工具的自动化处理。它的价值是减少手动切换和重复操作，"
            "把日程整理、信息检索、文档协作串成一条完整流程。"
        )
    if "web search" in lower or "tavily" in lower or "search" in lower:
        return (
            "这个技能用于执行面向 AI 代理的网页检索，重点是返回结构化、相关性更高的结果，"
            "方便后续摘要、比对和事实校验。它适合资讯追踪与研究场景，"
            "价值在于提高检索效率并降低无效信息噪声。"
        )
    return (
        f"这个技能主要用于 {skill_name} 相关能力扩展，帮助代理在特定场景下执行更稳定、可复用的操作。"
        "在日常使用中，建议重点评估它对你的任务链路是否能带来效率提升、错误率下降和更强的自动化闭环。"
    )


def render_markdown(
    date_label: str,
    items: list[dict],
    errors: list[str],
    llm_overview: str = "",
    llm_core_points: list[str] | None = None,
    llm_values: list[str] | None = None,
    llm_titles_cn: list[str] | None = None,
    openclaw_top: list[dict] | None = None,
    openclaw_focus: dict | None = None,
    openclaw_asof: str = "",
) -> str:
    if llm_core_points is None:
        llm_core_points = []
    if llm_values is None:
        llm_values = []
    if llm_titles_cn is None:
        llm_titles_cn = []
    categories = Counter(it.get("category", "其他") for it in items)
    top_categories = sorted(categories.items(), key=lambda x: (-x[1], category_order_key(x[0])))

    lines = [
        f"# AI 资讯周报 - {date_label}",
        "",
        "## 小结",
    ]
    if items:
        overview_text = llm_overview.strip()
        lines.extend(
            [
                f"- 研判小结：{overview_text or '今日主要增量集中在模型发布、工具链演进与论文更新。'}",
                "",
            ]
        )
    else:
        lines.extend(
            [
                "- 今天没有抓取到有效资讯，建议检查网络或信源地址可用性。",
                "",
            ]
        )

    lines.extend(["## 正文", ""])

    if not items:
        lines.extend(["- 今日无可用条目（可检查网络或信源地址）", ""])
    else:
        grouped = defaultdict(list)
        for it in items:
            grouped[it.get("category", "其他")].append(it)

        idx = 1
        for cat_name in sorted(grouped.keys(), key=category_order_key):
            lines.append(f"### {cat_name}")
            lines.append("")
            for it in grouped[cat_name]:
                core_cn = ""
                if idx - 1 < len(llm_core_points):
                    core_cn = llm_core_points[idx - 1].strip()
                value_cn = ""
                if idx - 1 < len(llm_values):
                    value_cn = llm_values[idx - 1].strip()
                title_cn = ""
                if idx - 1 < len(llm_titles_cn):
                    title_cn = llm_titles_cn[idx - 1].strip()
                if not title_cn:
                    title_cn = fallback_title_cn(it, idx)
                beijing_time = to_beijing_time_label(it.get("published", "未知"))
                source_desc = format_source_desc(it)
                fallback_core = strip_html((it.get("summary") or "")[:400])
                if not fallback_core:
                    fallback_core = strip_html((it.get("content_excerpt") or "")[:900])
                if not fallback_core:
                    fallback_core = "原文摘要不足，建议查看原文链接。"
                lines.extend(
                    [
                        f"#### {idx}. {title_cn}",
                        f"- 发布日期：{beijing_time}",
                        f"- 发布来源：{source_desc}",
                        f"- 核心内容：{core_cn or fallback_core}",
                        f"- 应用价值：{value_cn or '可用于评估对业务流程、产品能力和技术选型的实际影响。'}",
                        f"- 原文链接：{it.get('link', '')}",
                        "",
                    ]
                )
                idx += 1

    # OpenClaw 置于资讯条目之后：GitHub Release/仓库动态等信源优先阅读，热榜为社区热度辅助。
    if openclaw_top:
        lines.extend(
            [
                "### OpenClaw 技能热榜（社区热度 · 辅助参考）",
                "",
                "以下榜单按 Star 排序，用于对照工具生态关注度；**技术事实与版本动态请以正文中 GitHub/官方条目为准。**",
                "",
            ]
        )
        lines.append(
            f"快照更新时间：{openclaw_asof or '未知'}（最近一周区间内可获取的最新榜单）。"
        )
        lines.append("")
        for i, row in enumerate(openclaw_top, 1):
            purpose = build_openclaw_purpose_text(row["skill_name"], row.get("summary", ""))
            lines.extend(
                [
                    f"#### 热榜第{i}名：{row['skill_name']}",
                    f"该技能由 {row['author']} 发布，当前 Stars 约为 {row['stars_text']}。"
                    f"主要用途：{purpose}",
                    "",
                ]
            )
        if openclaw_focus:
            lines.append(
                f"你关注的技能「{openclaw_focus['skill_name']}」当前排名第 {openclaw_focus['rank']}，"
                f"发布者为 {openclaw_focus['author']}，Stars 约 {openclaw_focus['stars_text']}。"
            )
            lines.append("")

        lines.extend(["## OpenClaw 链接", ""])
        lines.append(f"- 榜单来源页：{OPENCLAW_LEADERBOARD_URL}")
        for i, row in enumerate(openclaw_top, 1):
            lines.append(f"- 热榜第{i}名 {row['skill_name']}：{row['skill_url']}")
            if row.get("author_url"):
                lines.append(f"- 热榜第{i}名发布者 {row['author']}：{row['author_url']}")
        if openclaw_focus:
            lines.append(
                f"- 关注技能 {openclaw_focus['skill_name']}：{openclaw_focus['skill_url']}"
            )
        lines.append("")

    lines.extend(["## 信源与原文链接", ""])
    for i, it in enumerate(items, 1):
        lines.append(
            f"- [{i}] {it.get('source', '未知信源')}（发布日期：{it.get('published', '未知')}）：{it.get('link', '')}"
        )
    lines.append("")

    lines.extend(
        [
            "## 生成信息",
            f"- 生成时间：{now_local().strftime('%Y-%m-%d %H:%M:%S %z')}",
            f"- 总条目：{len(items)}",
            f"- 信源数：{len(set(i['source'] for i in items)) if items else 0}",
            f"- 分类数：{len(categories)}",
            "",
        ]
    )

    lines.extend(["## 抓取异常", ""])
    if errors:
        lines.extend([f"- {err}" for err in errors])
    else:
        lines.append("- 无")
    lines.append("")
    return "\n".join(lines)


def write_doc(output_dir: pathlib.Path, content: str) -> pathlib.Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    name = f"ai_weekly_{now_local().strftime('%Y%m%d')}.md"
    out_path = output_dir / name
    out_path.write_text(content, encoding="utf-8")
    return out_path


def post_webhook(url: str, text: str) -> tuple[bool, str]:
    try:
        parsed = validate_https_url(url, "webhook URL")
    except ValueError as ex:
        return False, str(ex)
    host = (parsed.hostname or "").lower()
    if not (host.endswith(ALLOWED_WEBHOOK_SUFFIXES) or host in {"feishu.cn", "larksuite.com", "dingtalk.com"}):
        return False, "Unsupported webhook host (expect Feishu/Lark or DingTalk official domains)"

    if host.endswith(".feishu.cn") or host.endswith(".larksuite.com") or host == "feishu.cn" or host == "larksuite.com":
        payload = {"msg_type": "text", "content": {"text": text}}
    elif host.endswith(".dingtalk.com") or host == "dingtalk.com":
        payload = {"msgtype": "text", "text": {"content": text}}
    else:
        return False, "Unsupported webhook host (expect feishu/lark or dingtalk)"

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode("utf-8", errors="ignore")
            return True, body[:500]
    except Exception as ex:  # noqa: BLE001
        return False, f"{type(ex).__name__}: {ex}"


def main() -> int:
    load_dotenv(pathlib.Path(".env").resolve())

    parser = argparse.ArgumentParser(description="Generate AI weekly digest markdown")
    parser.add_argument("--sources", default=DEFAULT_SOURCES_FILE, help="Path to sources json")
    parser.add_argument("--out", default=DEFAULT_OUTPUT_DIR, help="Output docs directory")
    parser.add_argument("--limit", type=int, default=5, help="Max items per source")
    parser.add_argument(
        "--webhook-url",
        default=os.getenv("DIGEST_WEBHOOK_URL", ""),
        help="Optional Feishu/DingTalk webhook URL",
    )
    parser.add_argument(
        "--send",
        action="store_true",
        help="Send message to webhook after generating markdown",
    )
    parser.add_argument(
        "--allow-insecure-ssl",
        action="store_true",
        help="Allow insecure SSL fallback (not recommended)",
    )
    parser.add_argument(
        "--use-llm",
        action="store_true",
        help="Use LLM to generate Chinese overview/details",
    )
    parser.add_argument(
        "--llm-provider",
        default="auto",
        choices=["auto", "ark", "openai-compatible"],
        help="LLM provider mode",
    )
    parser.add_argument(
        "--llm-base-url",
        default=os.getenv("OPENAI_BASE_URL", ""),
        help="OpenAI-compatible chat completions URL",
    )
    parser.add_argument(
        "--allow-custom-llm-endpoint",
        action="store_true",
        help="Allow non-allowlisted LLM endpoint host (use with caution)",
    )
    parser.add_argument(
        "--ark-model",
        default=os.getenv("ARK_MODEL", "Doubao-Seed-1.6-lite"),
        help="Ark model name (legacy arg, still supported)",
    )
    parser.add_argument(
        "--ark-endpoint-id",
        default=os.getenv("ARK_ENDPOINT_ID", ""),
        help="Ark endpoint ID (ep-xxx), higher priority than --ark-model",
    )
    parser.add_argument(
        "--ark-api-key",
        default="",
        help="Ark API key (legacy arg, higher priority than env)",
    )
    parser.add_argument(
        "--window-hours",
        type=int,
        default=168,
        help="Only keep news published in the last N hours (default: 168h = 1 week)",
    )
    parser.add_argument(
        "--focus-skill",
        default="",
        help="Optional: skill name keyword to show its ranking",
    )
    parser.add_argument(
        "--official-window-hours",
        type=int,
        default=168,
        help="Fallback hours window for official-release sources",
    )
    parser.add_argument(
        "--max-paper-ratio",
        type=float,
        default=0.2,
        help="Maximum ratio for paper news in final report",
    )
    parser.add_argument(
        "--min-official-items",
        type=int,
        default=3,
        help="Try to keep at least this many official-release items",
    )
    parser.add_argument(
        "--intent-text",
        default="",
        help="User intent text used for in-pipeline ranking/filtering",
    )
    parser.add_argument(
        "--enable-gnews",
        action="store_true",
        help="Supplement with GNews API keyword search (needs GNEWS_API_KEY)",
    )
    parser.add_argument(
        "--gnews-api-key",
        default=os.getenv("GNEWS_API_KEY", ""),
        help="GNews API key (or set GNEWS_API_KEY)",
    )
    parser.add_argument(
        "--gnews-lang",
        default=os.getenv("GNEWS_LANG", "en"),
        help="GNews lang parameter (e.g. en, zh)",
    )
    parser.add_argument(
        "--gnews-max",
        type=int,
        default=10,
        help="Max GNews articles to fetch",
    )
    parser.add_argument(
        "--enable-openclaw",
        action="store_true",
        help="Fetch and render OpenClaw leaderboard section",
    )
    parser.add_argument(
        "--llm-prompt-variant",
        choices=["auto", "news", "intent"],
        default="auto",
        help="LLM enrichment system prompt variant (LCEL chain)",
    )
    args = parser.parse_args()

    sources_path = pathlib.Path(args.sources).resolve()
    out_dir = pathlib.Path(args.out).resolve()

    if not sources_path.exists():
        print(f"[ERROR] sources file not found: {sources_path}")
        return 1

    try:
        sources = load_sources(sources_path)
    except Exception as ex:  # noqa: BLE001
        print(f"[ERROR] load sources failed: {ex}")
        return 1

    items, errors = collect_news(
        sources,
        max(1, args.limit),
        allow_insecure_fallback=args.allow_insecure_ssl,
        window_hours=args.window_hours,
    )

    if args.enable_gnews and (args.gnews_api_key or "").strip():
        g_kw = extract_intent_keywords(args.intent_text)
        resolved_q = ""
        try:
            provider, base_url, ak = resolve_llm_runtime(args)
            model = (
                str(args.ark_endpoint_id or os.getenv("ARK_ENDPOINT_ID", "") or args.ark_model or "").strip()
                or os.getenv("OPENAI_MODEL", "").strip()
                or os.getenv("MODEL", "").strip()
                or "Doubao-Seed-1.6-lite"
            )
            resolved_q = infer_gnews_search_query(
                args.intent_text,
                api_key=ak,
                model=model,
                base_url=base_url,
                allow_insecure_fallback=args.allow_insecure_ssl,
            )
        except Exception:
            resolved_q = infer_gnews_search_query(
                args.intent_text,
                api_key=None,
                model="",
                base_url="",
                allow_insecure_fallback=args.allow_insecure_ssl,
            )
        g_items, g_err = fetch_gnews_for_pipeline(
            intent_text=args.intent_text,
            plan_keywords=g_kw,
            api_key=args.gnews_api_key.strip(),
            gnews_query=resolved_q,
            window_hours=args.window_hours,
            max_articles=max(1, args.gnews_max),
            lang=(args.gnews_lang or "en").strip(),
            category="行业资讯",
            allow_insecure_fallback=args.allow_insecure_ssl,
        )
        if g_err:
            errors.append(g_err)
        else:
            items = dedupe_items(items + g_items)
    elif args.enable_gnews and not (args.gnews_api_key or "").strip():
        errors.append("GNews: --enable-gnews set but no --gnews-api-key or GNEWS_API_KEY")

    if (os.getenv("PUBLIC_API_FEEDS", "") or "").strip():
        try:
            from ai_news_skill.integrations.public_api_feeds import collect_public_api_feed_items

            api_extra, api_errs = collect_public_api_feed_items(
                window_hours=args.window_hours,
                allow_insecure_fallback=args.allow_insecure_ssl,
                config={},
                max_per_feed=None,
            )
            errors.extend(api_errs)
            if api_extra:
                items = dedupe_items(api_extra + items)
        except Exception as ex:  # noqa: BLE001
            errors.append(f"public_api_feeds: {type(ex).__name__} {ex}")

    # If official releases are too few in base window, fetch official sources with a wider window.
    official_count = len([x for x in items if x.get("category") == "官方发布"])
    if official_count < max(0, args.min_official_items):
        official_sources = [s for s in sources if s.get("category") == "官方发布"]
        if official_sources:
            more_items, more_errors = collect_news(
                official_sources,
                max(1, args.limit),
                allow_insecure_fallback=args.allow_insecure_ssl,
                window_hours=max(args.window_hours, args.official_window_hours),
            )
            items = dedupe_items(items + more_items)
            # Keep only unique extra errors to avoid noisy duplicates.
            for err in more_errors:
                if err not in errors:
                    errors.append(err)

    items = balance_items(
        items,
        max_paper_ratio=args.max_paper_ratio,
        min_official_items=max(0, args.min_official_items),
    )
    items = cap_papers_by_ratio(items, max_paper_ratio=args.max_paper_ratio)
    items = rank_items_by_intent(items, getattr(args, "intent_text", ""))
    attach_content_excerpts_to_items(items, args.allow_insecure_ssl)

    llm_overview = ""
    llm_core_points: list[str] = []
    llm_values: list[str] = []
    llm_titles_cn: list[str] = []
    openclaw_top: list[dict] = []
    openclaw_focus: dict | None = None
    openclaw_asof = ""

    if bool(getattr(args, "enable_openclaw", False)) or bool((args.focus_skill or "").strip()):
        try:
            openclaw_top, openclaw_focus, openclaw_asof = fetch_openclaw_stars_top(
                top_n=3,
                focus_skill=args.focus_skill,
                allow_insecure_fallback=args.allow_insecure_ssl,
            )
        except Exception as ex:  # noqa: BLE001
            errors.append(f"OpenClaw热榜: {type(ex).__name__} {ex}")

    if args.use_llm:
        try:
            provider, base_url, api_key = resolve_llm_runtime(args)
            if not api_key:
                errors.append("LLM: 未检测到可用 API Key（ARK_API_KEY 或 OPENAI_API_KEY）")
            else:
                model = (
                    args.ark_endpoint_id
                    or os.getenv("ARK_ENDPOINT_ID", "")
                    or args.ark_model
                    or os.getenv("ARK_MODEL", "")
                    or os.getenv("OPENAI_MODEL", "")
                    or os.getenv("MODEL", "")
                    or "Doubao-Seed-1.6-lite"
                )
                llm_overview, llm_core_points, llm_values, llm_titles_cn = enrich_items_with_llm(
                    items,
                    api_key,
                    model,
                    base_url=base_url,
                    allow_insecure_fallback=args.allow_insecure_ssl,
                    prompt_variant=args.llm_prompt_variant,
                    intent_text=getattr(args, "intent_text", "") or "",
                    enable_openclaw=bool(getattr(args, "enable_openclaw", False)),
                )
                print(f"[INFO] llm_provider={provider}, llm_model={model}")
        except Exception as ex:  # noqa: BLE001
            hint = ""
            msg = f"{type(ex).__name__} {ex}"
            if "404" in msg:
                hint = "（Ark 模式可能需要 ARK_ENDPOINT_ID=ep-xxx，而不是模型展示名）"
            errors.append(f"LLM: {msg}{hint}")

    date_label = now_local().strftime("%Y-%m-%d")
    md = render_markdown(
        date_label,
        items,
        errors,
        llm_overview=llm_overview,
        llm_core_points=llm_core_points,
        llm_values=llm_values,
        llm_titles_cn=llm_titles_cn,
        openclaw_top=openclaw_top,
        openclaw_focus=openclaw_focus,
        openclaw_asof=openclaw_asof,
    )
    doc_path = write_doc(out_dir, md)

    print(f"[OK] markdown generated: {doc_path}")
    print(f"[INFO] items={len(items)}, errors={len(errors)}")

    if args.send:
        if not args.webhook_url:
            print("[WARN] --send is set but webhook url is empty, skip sending")
            return 0
        msg = f"AI 日报已生成：{doc_path.name}\n路径：{doc_path}\n条目数：{len(items)}"
        success, resp = post_webhook(args.webhook_url, msg)
        if success:
            print(f"[OK] webhook sent: {resp}")
        else:
            print(f"[WARN] webhook failed: {resp}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
