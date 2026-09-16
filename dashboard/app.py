# dashboard/app.py
import sqlite3
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from config import load_config
from db.migrate import init_db

app = FastAPI()
# Absolute path, not the relative string "dashboard/templates": a relative
# searchpath is resolved against the process's CURRENT working directory at
# template-load time (not at this line's execution time), which breaks the
# moment anything changes cwd -- a test using monkeypatch.chdir, or Plan 6's
# systemd unit running this from an unrelated WorkingDirectory=.
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


def get_conn() -> sqlite3.Connection:
    """Read-only usage from every dashboard route -- never insert/update/delete.
    A fresh connection per request is simplest and cheap for a low-traffic
    LAN dashboard; WAL mode (already set by init_db) supports concurrent
    readers alongside the live engine's own writer connection."""
    cfg = load_config()
    return init_db(cfg.db_path)


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "base.html", {})
