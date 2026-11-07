"""FastAPI app: JSON API + dashboard. Run: uvicorn arbitrage.app:app --port 8000"""
from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config
from .config import Settings
from .db import DB
from .scanner import Scanner, parse_leads_csv, scheduler_loop

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
STATIC = Path(__file__).parent / "static"

db = DB(config.DB_PATH)
scanner = Scanner(db)
_stop = threading.Event()


@asynccontextmanager
async def lifespan(_: FastAPI):
    t = threading.Thread(target=scheduler_loop, args=(scanner, _stop), daemon=True)
    t.start()
    yield
    _stop.set()


app = FastAPI(title="Amazon Flip Finder", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/status")
def status():
    scans = db.scans(1)
    return {
        "demo": config.DEMO_MODE,
        "domain": config.KEEPA_DOMAIN,
        "scanning": scanner.running,
        "tokens_left": scanner.last_tokens,
        "last_scan": scans[0] if scans else None,
        "next_scan_at": scanner.next_run_at,
    }


@app.get("/api/tokens")
def tokens():
    return scanner.tokens()


@app.get("/api/summary")
def summary():
    return db.summary()


@app.get("/api/plays")
def plays(all: bool = False, status: str | None = None, source: str | None = None,
          sort: str = "score", limit: int = 200):
    return db.plays(passes_only=not all, status=status or None, source=source or None,
                    sort=sort, limit=min(limit, 1000))


class PlayUpdate(BaseModel):
    status: str | None = None
    notes: str | None = None


@app.patch("/api/plays/{source}/{asin}")
def update_play(source: str, asin: str, body: PlayUpdate):
    if body.status and body.status not in ("new", "watching", "bought", "dismissed"):
        raise HTTPException(400, "bad status")
    if not db.update_play(asin, source, body.status, body.notes):
        raise HTTPException(404, "not found")
    return {"ok": True}


@app.post("/api/scan")
def scan(background: BackgroundTasks):
    if scanner.running:
        return {"status": "busy"}
    background.add_task(scanner.run_deal_scan)
    return {"status": "started"}


@app.get("/api/scans")
def scans(limit: int = 30):
    return db.scans(limit)


@app.get("/api/settings")
def get_settings():
    return db.get_settings().to_dict()


@app.put("/api/settings")
def put_settings(body: dict):
    merged = {**db.get_settings().to_dict(), **body}
    s = Settings.from_dict(merged)
    db.save_settings(s)
    return s.to_dict()


class LeadsIn(BaseModel):
    leads: list[dict] | None = None
    csv: str | None = None


@app.post("/api/leads")
def leads(body: LeadsIn):
    items = list(body.leads or [])
    if body.csv:
        items += parse_leads_csv(body.csv)
    if not items:
        raise HTTPException(400, "No leads. Send [{code, cost}] or CSV with code,cost columns.")
    if len(items) > 200:
        raise HTTPException(400, "Max 200 leads per batch")
    return scanner.check_leads(items)
