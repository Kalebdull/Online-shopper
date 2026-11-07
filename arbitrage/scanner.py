"""Scan orchestration: Keepa deals -> product details -> profit -> DB."""
from __future__ import annotations

import csv
import io
import logging
import threading
import time
from collections import Counter

from . import config
from .db import DB
from .demo import DemoKeepa
from .keepa import AMAZON, NEW, KeepaClient
from .planner import (DEAL_PAGE_COST, FULL_COST, HARD_REJECTS, QUICK_COST, plan_quick_checks,
                      prefilter)
from .profit import evaluate, parse_product

log = logging.getLogger("arbitrage")


def make_client():
    if config.DEMO_MODE:
        return DemoKeepa(config.KEEPA_DOMAIN)
    return KeepaClient(config.KEEPA_API_KEY, config.KEEPA_DOMAIN)


def deal_selection(s, page: int = 0, price_type: int = AMAZON, sort: int | None = None,
                   window: int | None = None) -> dict:
    """Keepa /deal query: items whose price dropped vs. their 90-day average."""
    sel = {
        "page": page,
        "domainId": config.KEEPA_DOMAIN,
        "priceTypes": [price_type],
        "dateRange": s.deal_window if window is None else window,             # 0=vs last day, 1=week, 2=month, 3=90 days
        "deltaPercentRange": [s.min_drop_percent, 95],
        "currentRange": [int(s.min_buy_price * 100), int(s.max_buy_price * 100)],
        "salesRankRange": [1, s.max_sales_rank],
        "isFilterEnabled": True,
        "isRangeEnabled": True,
        "filterErotic": True,
        "hasReviews": True,
        "sortType": s.deal_sort if sort is None else sort,               # 3 = best sellers first, 4 = biggest drop %, 1 = newest
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
        """Spend the available Keepa tokens where they're most likely to find a real flip."""
        if not self.lock.acquire(blocking=False):
            return {"status": "busy"}
        self.running = True
        scan_id = self.db.start_scan("deals")
        client = make_client()
        checked = found = 0
        try:
            s = self.db.get_settings()
            tokens = (client.token_status() or {}).get("tokensLeft") or 0      # free call
            budget = min(tokens, s.max_tokens_per_scan) if s.max_tokens_per_scan > 0 else tokens
            if budget < DEAL_PAGE_COST + 10:
                msg = f"Skipped: only {tokens} Keepa tokens available"
                self.db.finish_scan(scan_id, "ok", 0, 0, tokens, msg)
                return {"status": "skipped", "message": msg}
            spent = 0
            types = [t for t in (s.deal_price_types or [AMAZON]) if t in (AMAZON, NEW)] or [AMAZON]

            # 1) many cheap deal pages, 2) free pre-filter + ranking
            candidates: dict[str, tuple[float, int]] = {}
            skipped: Counter = Counter()
            deals_seen = 0
            def can_buy_page() -> bool:
                # Keep paging while the rest of the budget still covers checking every candidate.
                need = len(candidates) * (QUICK_COST + 0.15 * FULL_COST) + 10
                return spent + DEAL_PAGE_COST + need <= budget
            # Several deal searches, so leftover tokens find more candidates instead of expiring.
            searches = [(s.deal_sort, s.deal_window), (1, 0), (4, 1), (3, 2), (3, 0)]
            searches = list(dict.fromkeys(searches))
            enough = False
            for sort, window in searches:
                for page in range(s.max_deal_pages):
                    got_any = False
                    for price_type in types:
                        if not can_buy_page():
                            break
                        deals = client.deals(deal_selection(s, page, price_type, sort, window))
                        spent += DEAL_PAGE_COST
                        deals_seen += len(deals)
                        got_any = got_any or bool(deals)
                        for d in deals:
                            score, why = prefilter(d, price_type, s)
                            if score is None:
                                skipped[why] += 1
                            elif d["asin"] not in candidates or candidates[d["asin"]][0] < score:
                                candidates[d["asin"]] = (score, price_type)
                    enough = len(candidates) >= plan_quick_checks(budget - spent, 10**6)
                    if enough or not got_any or not can_buy_page():
                        break
                if enough or not can_buy_page():
                    break
            ranked = sorted(candidates.items(), key=lambda kv: -kv[1][0])

            # 3) quick check without buy box data (1 token each)
            n_quick = min(plan_quick_checks(budget - spent, len(ranked)), s.max_products_per_scan)
            quick_asins = [a for a, _ in ranked[:n_quick]]
            survivors: list[str] = []
            quick_plays: dict[str, tuple[dict, dict]] = {}
            for product in client.products(asins=quick_asins, buybox=False):
                spent += QUICK_COST
                info = parse_product(product)
                pt = candidates[info["asin"]][1]
                buy = info["amazon_price"] if pt == AMAZON else info["third_party_cost"]
                play = evaluate(info, buy, s)
                hard = any(h in r for r in play.get("rejects", []) for h in HARD_REJECTS)
                if not hard and (play.get("profit") or -1) >= s.min_profit * 0.5:
                    survivors.append(info["asin"])
                quick_plays[info["asin"]] = (product, play)

            # 4) full check with buy box data, survivors only
            n_full = min(len(survivors), max(0, budget - spent) // FULL_COST)
            full_checked = set()
            if n_full and s.use_buybox:
                for product in client.products(asins=survivors[:n_full], buybox=True):
                    spent += FULL_COST
                    quick_plays[product["asin"]] = (product, None)
                    full_checked.add(product["asin"])

            for asin, (product, play) in quick_plays.items():
                pt = candidates[asin][1]
                if play is None or asin in full_checked:
                    info = parse_product(product)
                    buy = info["amazon_price"] if pt == AMAZON else info["third_party_cost"]
                    play = evaluate(info, buy, s)
                elif asin in survivors:
                    # Passed the quick check but no tokens left for the full one: don't call it verified.
                    play = {**play, "passes": False, "verified": False,
                            "rejects": play.get("rejects", []) + ["Not fully checked yet (out of tokens)"]}
                self.db.save_raw(product, "deals", price_type=pt)
                self.db.upsert_play(play, "deals", scan_id, buy_url=buy_url(asin),
                                    store="Amazon" if pt == AMAZON else "Amazon 3rd-party seller")
                checked += 1
                found += int(play["passes"])

            self.last_tokens = client.tokens_left
            top_skips = ", ".join(f"{n} {why}" for why, n in skipped.most_common(4))
            msg = (f"{found} verified. {deals_seen} deals scanned, {len(candidates)} worth checking "
                   f"({top_skips} skipped free); {len(quick_asins)} quick-checked, "
                   f"{len(full_checked)} fully checked; ~{spent} tokens")
            self.db.finish_scan(scan_id, "ok", checked, found, client.tokens_left, msg)
            return {"status": "ok", "checked": checked, "found": found, "scan_id": scan_id,
                    "deals_seen": deals_seen, "candidates": len(candidates), "quick": len(quick_asins),
                    "full": len(full_checked), "tokens_spent": spent, "message": msg}
        except Exception as e:  # noqa: BLE001
            log.exception("deal scan failed")
            self.db.finish_scan(scan_id, "error", checked, found, client.tokens_left, str(e))
            return {"status": "error", "message": str(e)}
        finally:
            client.close()
            self.running = False
            self.lock.release()

    # --- deals from other stores ------------------------------------------
    def run_web_scan(self, max_tokens: int | None = None) -> dict:
        """Read deal feeds from around the web, match each to Amazon, verify with Keepa."""
        from .sources import matcher, slickdeals
        s = self.db.get_settings()
        if not s.web_sources:
            return {"status": "off"}
        scan_id = self.db.start_scan("web")
        client = make_client()
        checked = found = searched = 0
        skipped: Counter = Counter()
        try:
            deals = demo_store_deals() if config.DEMO_MODE else slickdeals.fetch()
            tokens = (client.token_status() or {}).get("tokensLeft") or 0
            budget = tokens if max_tokens is None else min(tokens, max_tokens)
            fresh = [d for d in deals if not self.db.seen_recently(d["id"])]
            for deal in fresh:
                why = slickdeals.skip_reason(deal, s)
                if why:
                    skipped[why] += 1
                    self.db.mark_seen(deal["id"], why)
                    continue
                if budget - SEARCH_COST < 10:
                    break                                   # leave the rest for the next run
                term = " ".join(sorted(deal["models"])) if deal["models"] else deal["query"]
                products = client.search(term)
                budget -= SEARCH_COST
                searched += 1
                product, how = matcher.match(deal, products)
                if product is None:
                    skipped["no exact Amazon match"] += 1
                    self.db.mark_seen(deal["id"], how)
                    continue
                cost = deal["price"]
                if not deal["free_shipping"] and cost < s.free_shipping_over:
                    cost += s.store_shipping_estimate
                info = parse_product(product)
                play = evaluate(info, cost, s)
                play["deal_title"], play["matched_by"] = deal["title"], how
                if not deal["free_shipping"] and deal["price"] < s.free_shipping_over:
                    play["flags"] = play.get("flags", []) + [f"Includes ${s.store_shipping_estimate:.2f} est. shipping"]
                self.db.save_raw(product, "web", store=deal["store"], buy_url=deal["url"], lead_cost=cost)
                self.db.upsert_play(play, "web", scan_id, buy_url=deal["url"], store=deal["store"])
                self.db.mark_seen(deal["id"], "verified" if play["passes"] else "checked")
                checked += 1
                found += int(play["passes"])
            self.last_tokens = client.tokens_left
            top = ", ".join(f"{n} {w}" for w, n in skipped.most_common(4))
            msg = (f"{found} verified from other stores. {len(deals)} deals read, {len(fresh)} new; "
                   f"{searched} searched on Amazon, {checked} matched exactly ({top})")
            self.db.finish_scan(scan_id, "ok", checked, found, client.tokens_left, msg)
            return {"status": "ok", "found": found, "checked": checked, "message": msg}
        except Exception as e:  # noqa: BLE001
            log.exception("web scan failed")
            self.db.finish_scan(scan_id, "error", checked, found, client.tokens_left, str(e))
            return {"status": "error", "message": str(e)}
        finally:
            client.close()

    def run_all(self) -> dict:
        """Store deals first (they're the point), then Keepa price drops with what's left."""
        web = self.run_web_scan()
        deals = self.run_deal_scan()
        return {"web": web, "amazon_deals": deals}

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
                best_raw = next(p for p in products if p.get("asin") == best["asin"])
                self.db.save_raw(best_raw, "leads", store=lead.get("store") or None,
                                 buy_url=lead.get("url") or None, lead_cost=cost)
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


    def rescore(self) -> dict:
        """Re-evaluate cached Keepa data under the current settings. Costs no tokens."""
        s = self.db.get_settings()
        n = found = 0
        for r in self.db.raw_products():
            info = parse_product(r["data"])
            if r["source"] in ("leads", "web"):
                buy, store, url = r["lead_cost"], r["store"], r["buy_url"]
            else:
                pt = r["price_type"] or AMAZON
                buy = info["amazon_price"] if pt == AMAZON else info["third_party_cost"]
                store = "Amazon" if pt == AMAZON else "Amazon 3rd-party seller"
                url = buy_url(info["asin"])
            play = evaluate(info, buy, s)
            self.db.upsert_play(play, r["source"], None, buy_url=url, store=store)
            n += 1
            found += int(play["passes"])
        return {"rescored": n, "verified": found}


SEARCH_COST = 10


def demo_store_deals() -> list[dict]:
    from .sources.slickdeals import clean_title, model_numbers
    rows = [("Hydro Flask 32 oz Wide Mouth Bottle $24.97 + Free S&H", "Target"),
            ("LEGO Classic Creative Bricks Set 11016 $19.99", "Walmart"),
            ("Refurbished: Some Drone $99 + Free S&H", "Woot"),
            ("Anker USB-C Charger 65W $22 + Free Shipping", "Best Buy")]
    out = []
    for i, (t, store) in enumerate(rows):
        price = float(t.split("$")[1].split()[0])
        out.append({"id": f"demo-{i}", "title": t, "query": clean_title(t), "price": price,
                    "free_shipping": "Free" in t, "store": store, "url": "https://slickdeals.net/",
                    "models": model_numbers(t), "feed": "demo", "published": None})
    return out


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
            scanner.run_all()
        stop.wait(30)
