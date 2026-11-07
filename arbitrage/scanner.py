"""Scan orchestration: Keepa deals -> product details -> profit -> DB."""
from __future__ import annotations

import csv
import io
import logging
import threading
import time

from . import config
from .db import DB
from .demo import DemoKeepa
from .keepa import AMAZON, NEW, KeepaClient
from .profit import evaluate, parse_product

log = logging.getLogger("arbitrage")


def make_client():
    if config.DEMO_MODE:
        return DemoKeepa(config.KEEPA_DOMAIN)
    return KeepaClient(config.KEEPA_API_KEY, config.KEEPA_DOMAIN)


def deal_selection(s, page: int = 0) -> dict:
    """Keepa /deal query: items whose price dropped vs. their 90-day average."""
    sel = {
        "page": page,
        "domainId": config.KEEPA_DOMAIN,
        "priceTypes": s.deal_price_types[:1] or [AMAZON],
        "dateRange": 3,                         # compare against 90-day average
        "deltaPercentRange": [s.min_drop_percent, 95],
        "currentRange": [int(s.min_buy_price * 100), int(s.max_buy_price * 100)],
        "salesRankRange": [1, s.max_sales_rank],
        "isFilterEnabled": True,
        "isRangeEnabled": True,
        "filterErotic": True,
        "hasReviews": True,
        "sortType": 4,                          # sort by percentage delta
    }
    if s.include_categories:
        sel["includeCategories"] = s.include_categories
    if s.exclude_categories:
        sel["excludeCategories"] = s.exclude_categories
    return sel


def buy_url(asin: str) -> str:
    return f"https://www.{config.AMAZON_HOSTS.get(config.KEEPA_DOMAIN, 'amazon.com')}/dp/{asin}"


class Scanner:
    def __init__(self, db: DB):
        self.db = db
        self.lock = threading.Lock()
        self.running = False
        self.last_tokens: int | None = None
        self.next_run_at: float | None = None

    def tokens(self) -> dict | None:
        client = make_client()
        try:
            data = client.token_status()
            self.last_tokens = data.get("tokensLeft")
            return data
        except Exception as e:  # noqa: BLE001
            return {"error": str(e)}
        finally:
            client.close()

    # --- automatic deal scan -------------------------------------------------
    def run_deal_scan(self) -> dict:
        if not self.lock.acquire(blocking=False):
            return {"status": "busy"}
        self.running = True
        scan_id = self.db.start_scan("deals")
        client = make_client()
        checked = found = 0
        try:
            s = self.db.get_settings()
            asins: list[str] = []
            page = 0
            while len(asins) < s.max_products_per_scan and page < 5:
                deals = client.deals(deal_selection(s, page))
                if not deals:
                    break
                for d in deals:
                    if d.get("asin") and d["asin"] not in asins:
                        asins.append(d["asin"])
                page += 1
            asins = asins[: s.max_products_per_scan]

            price_idx = (s.deal_price_types or [AMAZON])[0]
            for product in client.products(asins=asins, buybox=s.use_buybox):
                info = parse_product(product)
                buy = info["amazon_price"] if price_idx == AMAZON else info["new_price"]
                if price_idx not in (AMAZON, NEW):
                    buy = info["new_price"]
                play = evaluate(info, buy, s)
                checked += 1
                found += int(play["passes"])
                self.db.upsert_play(play, "deals", scan_id, buy_url=buy_url(info["asin"]),
                                    store="Amazon" if price_idx == AMAZON else "Amazon 3P")
            self.last_tokens = client.tokens_left
            msg = f"{found} plays from {checked} products"
            self.db.finish_scan(scan_id, "ok", checked, found, client.tokens_left, msg)
            return {"status": "ok", "checked": checked, "found": found, "scan_id": scan_id}
        except Exception as e:  # noqa: BLE001
            log.exception("deal scan failed")
            self.db.finish_scan(scan_id, "error", checked, found, client.tokens_left, str(e))
            return {"status": "error", "message": str(e)}
        finally:
            client.close()
            self.running = False
            self.lock.release()

    # --- manual retail leads ------------------------------------------------
    def check_leads(self, leads: list[dict]) -> list[dict]:
        """leads: [{code: UPC/EAN/ASIN, cost: float, store?: str, url?: str}]"""
        scan_id = self.db.start_scan("leads")
        client = make_client()
        results: list[dict] = []
        try:
            s = self.db.get_settings()
            for lead in leads:
                code = str(lead.get("code", "")).strip()
                try:
                    cost = float(str(lead.get("cost", "")).replace("$", ""))
                except ValueError:
                    continue
                if not code:
                    continue
                is_asin = len(code) == 10 and code[0].isalpha()
                products = client.products(asins=[code] if is_asin else [],
                                           codes=[] if is_asin else [code], buybox=s.use_buybox)
                if not products:
                    results.append({"code": code, "error": "Not found on Amazon"})
                    continue
                # A UPC can map to several ASINs; keep the best one.
                plays = [evaluate(parse_product(p), cost, s) for p in products]
                best = max(plays, key=lambda p: (p["passes"], p.get("profit", -1e9)))
                best["lead_code"] = code
                self.db.upsert_play(best, "leads", scan_id, buy_url=lead.get("url") or None,
                                    store=lead.get("store") or None)
                results.append(best)
            found = sum(1 for r in results if r.get("passes"))
            self.last_tokens = client.tokens_left
            self.db.finish_scan(scan_id, "ok", len(results), found, client.tokens_left,
                                f"{found} of {len(results)} leads profitable")
            return results
        except Exception as e:  # noqa: BLE001
            log.exception("lead check failed")
            self.db.finish_scan(scan_id, "error", len(results), 0, client.tokens_left, str(e))
            raise
        finally:
            client.close()


def parse_leads_csv(text: str) -> list[dict]:
    """Accepts CSV with headers like code/upc/asin, cost/price, store, url."""
    reader = csv.DictReader(io.StringIO(text.strip()))
    out = []
    for row in reader:
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
        code = row.get("code") or row.get("upc") or row.get("asin") or row.get("ean")
        cost = row.get("cost") or row.get("price") or row.get("buy")
        if code and cost:
            out.append({"code": code, "cost": cost, "store": row.get("store", ""), "url": row.get("url", "")})
    return out


def scheduler_loop(scanner: Scanner, stop: threading.Event) -> None:
    """Background thread: run deal scans on the configured interval."""
    time.sleep(3)
    while not stop.is_set():
        s = scanner.db.get_settings()
        interval = max(10, s.scan_interval_minutes) * 60
        last = next((x for x in scanner.db.scans(10) if x["source"] == "deals"), None)
        due = (last["started_at"] + interval) if last else time.time()
        scanner.next_run_at = due if s.auto_scan else None
        if s.auto_scan and time.time() >= due:
            scanner.run_deal_scan()
        stop.wait(30)
