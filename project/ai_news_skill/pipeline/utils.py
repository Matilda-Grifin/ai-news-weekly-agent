"""Shared utility functions for the digest pipeline."""

from __future__ import annotations

import datetime as dt
import html
import json
import os
import pathlib
import re
from typing import Any


def now_local() -> dt.datetime:
    return dt.datetime.now().astimezone()


def strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def strip_tags(raw: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", raw or "")).strip()


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


def load_sources(path: pathlib.Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError("sources.json must be an array")
    return data


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


def looks_mostly_english(text: str) -> bool:
    s = (text or "").strip()
    if not s:
        return False
    letters = re.findall(r"[A-Za-z]", s)
    cjk = re.findall(r"[\u4e00-\u9fff]", s)
    return len(letters) > 0 and len(cjk) == 0
