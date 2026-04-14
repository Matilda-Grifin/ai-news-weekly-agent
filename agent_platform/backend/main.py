"""Harness Web UI — default port 8787 (Streamlit 通常为 8501)."""
from __future__ import annotations

import pathlib
import sys

_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "project"))

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agent_platform.backend.harness.agent_runner import run_harness_turn
from agent_platform.backend.harness.env_config import build_base_config
from agent_platform.backend.harness.session import create_session, delete_session, get_session
from agent_platform.paths import repo_root

app = FastAPI(title="AI News Harness", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_STATIC = pathlib.Path(__file__).resolve().parent / "static"
if _STATIC.is_dir():
    app.mount("/static", StaticFiles(directory=str(_STATIC)), name="static")


class ChatIn(BaseModel):
    session_id: str
    message: str


@app.on_event("startup")
def _startup() -> None:
    build_base_config(repo_root())  # preload .env into os.environ


@app.get("/")
def index() -> FileResponse:
    idx = _STATIC / "index.html"
    if not idx.is_file():
        raise HTTPException(500, "static/index.html missing")
    return FileResponse(idx)


@app.post("/api/session")
def new_session() -> dict:
    cfg = build_base_config(repo_root())
    s = create_session(cfg)
    return {"session_id": s.session_id}


@app.delete("/api/session/{session_id}")
def remove_session(session_id: str) -> dict:
    delete_session(session_id)
    return {"ok": True}


@app.post("/api/chat")
def chat(body: ChatIn) -> dict:
    s = get_session(body.session_id)
    if not s:
        raise HTTPException(400, "invalid session_id")
    s.append_memory("user", body.message)
    try:
        out = run_harness_turn(body.session_id, body.message)
    except Exception as ex:  # noqa: BLE001
        s.append_memory("assistant", f"error: {ex}")
        raise HTTPException(500, str(ex)) from ex
    s.append_memory("assistant", out[:8000])
    return {"reply": out}


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "repo_root": str(repo_root())}
