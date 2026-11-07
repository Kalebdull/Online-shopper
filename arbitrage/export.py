"""Export plays + latest scan as flat JSON rows (for the phone dashboard)."""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from .db import DB


def _iso(ts: float | None) -> str | None:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds") if ts else None


def export(db: DB, out_dir: str) -> dict:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for p in db.plays(passes_only=False, limit=1000):
        d = p["data"]
        rows.append({
            "asin": p["asin"], "source": p["source"], "status": p["status"],
            "passes": bool(p["passes"]), "title": p["title"], "brand": p["brand"],
            "category": p["category"] or "Unknown", "store": p["store"], "buy_url": p["buy_url"],
            "buy_price": p["buy_price"], "sell_price": p["sell_price"], "profit": p["profit"],
            "roi": p["roi"], "score": p["score"], "est_monthly_profit": p["est_monthly_profit"],
            "sales_rank": p["sales_rank"], "monthly_sold": p["monthly_sold"],
            "offer_count": p["offer_count"], "drop_pct": d.get("drop_pct"),
            "amazon_bb_share": d.get("amazon_bb_share"),
            "fee_referral": (d.get("fees") or {}).get("referral"),
            "fee_fba": (d.get("fees") or {}).get("fba"),
            "flags": " | ".join(d.get("flags") or []),
            "rejects": " | ".join(d.get("rejects") or []),
            "last_seen": _iso(p["last_seen"]),
        })
    scans = [{"id": s["id"], "source": s["source"], "started_at": _iso(s["started_at"]),
              "status": s["status"], "checked": s["checked"], "found": s["found"],
              "tokens_left": s["tokens_left"], "message": s["message"]} for s in db.scans(50)]
    (out / "plays.json").write_text(json.dumps(rows))
    (out / "scans.json").write_text(json.dumps(scans))
    return {"plays": len(rows), "passing": sum(r["passes"] for r in rows), "scans": len(scans),
            "out": str(out), "exported_at": _iso(time.time())}
