"""SQLite persistence: settings, scans, plays, leads."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

from .config import Settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS scans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    started_at REAL NOT NULL,
    finished_at REAL,
    status TEXT NOT NULL DEFAULT 'running',
    checked INTEGER DEFAULT 0,
    found INTEGER DEFAULT 0,
    tokens_left INTEGER,
    message TEXT
);
CREATE TABLE IF NOT EXISTS plays (
    asin TEXT NOT NULL,
    source TEXT NOT NULL,
    title TEXT, brand TEXT, category TEXT, image TEXT,
    buy_price REAL, sell_price REAL, profit REAL, roi REAL, margin REAL, score REAL,
    est_monthly_profit REAL,
    sales_rank INTEGER, monthly_sold INTEGER, offer_count INTEGER,
    passes INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'new',
    notes TEXT DEFAULT '',
    buy_url TEXT, store TEXT,
    data TEXT NOT NULL,
    first_seen REAL NOT NULL,
    last_seen REAL NOT NULL,
    scan_id INTEGER,
    PRIMARY KEY (asin, source)
);
CREATE INDEX IF NOT EXISTS plays_score ON plays(passes, score DESC);
"""

PLAY_COLUMNS = ("title", "brand", "category", "image", "buy_price", "sell_price", "profit",
                "roi", "margin", "score", "est_monthly_profit", "sales_rank",
                "monthly_sold", "offer_count")


class DB:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def _exec(self, sql: str, params=()) -> sqlite3.Cursor:
        with self._lock:
            cur = self.conn.execute(sql, params)
            self.conn.commit()
            return cur

    def _all(self, sql: str, params=()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    # -- settings --
    def get_settings(self) -> Settings:
        row = self._all("SELECT value FROM settings WHERE key='settings'")
        return Settings.from_dict(json.loads(row[0]["value"]) if row else {})

    def save_settings(self, s: Settings) -> None:
        self._exec("INSERT INTO settings(key,value) VALUES('settings',?) "
                   "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (json.dumps(s.to_dict()),))

    # -- scans --
    def start_scan(self, source: str) -> int:
        return self._exec("INSERT INTO scans(source, started_at) VALUES(?,?)",
                          (source, time.time())).lastrowid

    def finish_scan(self, scan_id: int, status: str, checked: int, found: int,
                    tokens_left: int | None, message: str = "") -> None:
        self._exec("UPDATE scans SET finished_at=?, status=?, checked=?, found=?, tokens_left=?, message=? "
                   "WHERE id=?", (time.time(), status, checked, found, tokens_left, message, scan_id))

    def scans(self, limit: int = 30) -> list[dict]:
        return self._all("SELECT * FROM scans ORDER BY id DESC LIMIT ?", (limit,))

    # -- plays --
    def upsert_play(self, play: dict, source: str, scan_id: int | None,
                    buy_url: str | None = None, store: str | None = None) -> None:
        now = time.time()
        values = [play.get(c) for c in PLAY_COLUMNS]
        self._exec(
            f"""INSERT INTO plays(asin, source, {', '.join(PLAY_COLUMNS)}, passes, buy_url, store,
                                  data, first_seen, last_seen, scan_id)
                VALUES (?, ?, {', '.join('?' * len(PLAY_COLUMNS))}, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(asin, source) DO UPDATE SET
                  {', '.join(f'{c}=excluded.{c}' for c in PLAY_COLUMNS)},
                  passes=excluded.passes, data=excluded.data, last_seen=excluded.last_seen,
                  scan_id=excluded.scan_id,
                  buy_url=COALESCE(excluded.buy_url, plays.buy_url),
                  store=COALESCE(excluded.store, plays.store)""",
            (play["asin"], source, *values, int(bool(play.get("passes"))), buy_url, store,
             json.dumps(play), now, now, scan_id))

    def plays(self, *, passes_only: bool = True, status: str | None = None,
              source: str | None = None, sort: str = "score", limit: int = 200) -> list[dict]:
        sort_col = {"score": "score", "profit": "profit", "roi": "roi",
                    "monthly": "est_monthly_profit", "recent": "last_seen"}.get(sort, "score")
        where, params = [], []
        if passes_only:
            where.append("passes=1")
        if status:
            where.append("status=?"); params.append(status)
        else:
            where.append("status!='dismissed'")
        if source:
            where.append("source=?"); params.append(source)
        sql = (f"SELECT * FROM plays WHERE {' AND '.join(where) or '1=1'} "
               f"ORDER BY {sort_col} DESC LIMIT ?")
        rows = self._all(sql, (*params, limit))
        for r in rows:
            r["data"] = json.loads(r["data"])
        return rows

    def update_play(self, asin: str, source: str, status: str | None, notes: str | None) -> bool:
        sets, params = [], []
        if status is not None:
            sets.append("status=?"); params.append(status)
        if notes is not None:
            sets.append("notes=?"); params.append(notes)
        if not sets:
            return False
        return self._exec(f"UPDATE plays SET {', '.join(sets)} WHERE asin=? AND source=?",
                          (*params, asin, source)).rowcount > 0

    def summary(self) -> dict:
        row = self._all("""SELECT COUNT(*) n, AVG(roi) avg_roi, SUM(profit) total_profit,
                                  SUM(est_monthly_profit) monthly, MAX(score) best
                           FROM plays WHERE passes=1 AND status!='dismissed'""")[0]
        by_cat = self._all("""SELECT COALESCE(category,'Unknown') category, COUNT(*) n
                              FROM plays WHERE passes=1 AND status!='dismissed'
                              GROUP BY 1 ORDER BY n DESC LIMIT 8""")
        by_status = self._all("SELECT status, COUNT(*) n FROM plays WHERE passes=1 GROUP BY status")
        checked = self._all("SELECT COUNT(*) n FROM plays")[0]["n"]
        return {**row, "checked_total": checked, "by_category": by_cat,
                "by_status": {r["status"]: r["n"] for r in by_status}}
