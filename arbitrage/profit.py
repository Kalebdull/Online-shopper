"""Turn a raw Keepa product into a scored flip opportunity."""
from __future__ import annotations

import time

from .config import Settings
from .keepa import (AMAZON, BUY_BOX_SHIPPING, COUNT_NEW, NEW, NEW_FBA, NEW_FBM_SHIPPING, SALES,
                    keepa_minutes_to_unix)

import re

APPAREL_ROOTS = {"Clothing, Shoes & Jewelry"}   # sizes/colors share sales data
# Editions/formats of the same title often share one sales rank on Amazon.
MEDIA_ROOTS = {"Books", "CDs & Vinyl", "Movies & TV", "Video Games", "Kindle Store", "Digital Music"}
SIZE_IN_TITLE = re.compile(r",\s*(X{0,3}-?(Small|Medium|Large)|XS|XXL|\d{1,2}(\.5)?\s*(W|M|Wide|US)?|Size\s+\S+)\s*(,|$)", re.I)

DAY = 86400
WINDOW_DAYS = 180          # how far back we look for proof
RECENT_DIP_DAYS = 7        # ignore the current drop when judging the "normal" price

GRAMS_PER_LB = 453.592
AMAZON_SELLER_ID = "ATVPDKIKX0DER"
RESTRICTED_BRANDS = {"amazon", "amazonbasics", "amazon basics", "amazon essentials", "ring", "eero", "blink"}


def _cents(arr, idx) -> float | None:
    if not arr or len(arr) <= idx or arr[idx] is None:
        return None
    v = arr[idx]
    return round(v / 100, 2) if v > 0 else None


def _int(arr, idx) -> int | None:
    if not arr or len(arr) <= idx or arr[idx] is None:
        return None
    return arr[idx] if arr[idx] >= 0 else None


def image_url(product: dict) -> str | None:
    images = product.get("images")
    if images and isinstance(images, list) and isinstance(images[0], dict):
        name = images[0].get("m") or images[0].get("l")
        if name:
            return f"https://m.media-amazon.com/images/I/{name}"
    csv = product.get("imagesCSV")
    if csv:
        return f"https://m.media-amazon.com/images/I/{csv.split(',')[0]}"
    return None


def price_history(product: dict, days: int = WINDOW_DAYS) -> list[list[float]]:
    """[[unix_seconds, dollars], ...] for the buy box (or new) price."""
    csv = product.get("csv") or []
    series, step = None, 2
    if len(csv) > BUY_BOX_SHIPPING and csv[BUY_BOX_SHIPPING]:
        series, step = csv[BUY_BOX_SHIPPING], 3   # time, price, shipping
    elif len(csv) > NEW and csv[NEW]:
        series = csv[NEW]
    if not series:
        return []
    cutoff = time.time() - days * 86400
    out = []
    for i in range(0, len(series) - 1, step):
        t, p = keepa_minutes_to_unix(series[i]), series[i + 1]
        if step == 3 and i + 2 < len(series) and series[i + 2] > 0 and p > 0:
            p += series[i + 2]
        if t >= cutoff and p > 0:
            out.append([t, round(p / 100, 2)])
    if len(out) > 120:  # downsample for the dashboard
        k = len(out) / 120
        out = [out[int(i * k)] for i in range(120)] + [out[-1]]
    return out


def _series(csv: list, idx: int) -> list[list[float]]:
    """Keepa csv[idx] -> [[unix, dollars]] (handles the buy box's price+shipping triples)."""
    if len(csv) <= idx or not csv[idx]:
        return []
    raw, step = csv[idx], 3 if idx == BUY_BOX_SHIPPING else 2
    out = []
    for i in range(0, len(raw) - 1, step):
        price = raw[i + 1]
        if step == 3 and i + 2 < len(raw) and raw[i + 2] > 0 and price > 0:
            price += raw[i + 2]
        out.append([keepa_minutes_to_unix(raw[i]), price / 100 if price > 0 else None])
    return out


def _value_at(series: list[list[float]], t: float) -> float | None:
    val = None
    for ts, v in series:
        if ts > t:
            break
        val = v
    return val


def sale_events(product: dict, days: int = WINDOW_DAYS) -> list[tuple[float, float]]:
    """Prices the item was actually selling at: each sales-rank drop ~= a sale.

    Keepa's displayed-price averages get skewed by inflated "regular" prices
    nobody buys at. Reading the price at the moment the rank improved tells us
    where real sales happened.
    """
    csv = product.get("csv") or []
    ranks = [(keepa_minutes_to_unix(csv[SALES][i]), csv[SALES][i + 1])
             for i in range(0, len(csv[SALES]) - 1, 2)] if len(csv) > SALES and csv[SALES] else []
    if len(ranks) < 2:
        return []
    bb, amz, new = _series(csv, BUY_BOX_SHIPPING), _series(csv, AMAZON), _series(csv, NEW)
    cutoff = time.time() - days * 86400
    prices = []
    for (_, prev), (t, rank) in zip(ranks, ranks[1:]):
        if t < cutoff or prev <= 0 or rank <= 0 or rank >= prev:
            continue
        price = _value_at(bb, t)
        if price is None:
            cands = [v for v in (_value_at(amz, t), _value_at(new, t)) if v]
            price = min(cands) if cands else None
        prices.append((t, round(price, 2) if price else None))   # None = nothing for sale
    return prices


def time_weighted_median(history: list[list[float]], now: float | None = None) -> float | None:
    """Median price weighted by how long each price was live. Robust to brief spikes."""
    if not history:
        return None
    now = now or time.time()
    spans = []
    for i, (t, price) in enumerate(history):
        end = history[i + 1][0] if i + 1 < len(history) else now
        spans.append((price, max(end - t, 1)))
    spans.sort()
    half, acc = sum(w for _, w in spans) / 2, 0.0
    for price, w in spans:
        acc += w
        if acc >= half:
            return round(price, 2)
    return round(spans[-1][0], 2)


def _spans(history: list[list[float]], start: float, end: float) -> list[tuple[float, float]]:
    """(price, seconds) pieces of a step series clipped to [start, end]."""
    out = []
    for i, (t, price) in enumerate(history):
        t_end = history[i + 1][0] if i + 1 < len(history) else end
        a, b = max(t, start), min(t_end, end)
        if b > a:
            out.append((price or 0.0, b - a))      # out of stock counts as 0
    return out


def time_weighted_percentile(history, q: float, start: float, end: float) -> float | None:
    spans = sorted(x for x in _spans(history, start, end) if x[0] > 0)   # in-stock time only
    total = sum(w for _, w in spans)
    if not total:
        return None
    acc = 0.0
    for price, w in spans:
        acc += w
        if acc >= total * q:
            return round(price, 2)
    return round(spans[-1][0], 2)


def _swing(history, start: float, end: float) -> float | None:
    """How unstable the normal price is: 75th / 25th percentile (in-stock time)."""
    lo = time_weighted_percentile(history, 0.25, start, end)
    hi = time_weighted_percentile(history, 0.75, start, end)
    return round(hi / lo, 2) if lo and hi else None


def time_share_at_or_above(history, price: float, start: float, end: float) -> float:
    spans = _spans(history, start, end)
    total = sum(w for _, w in spans)
    return round(sum(w for p, w in spans if p >= price) / total, 3) if total else 0.0


def estimate_fba_fee(weight_lb: float) -> float:
    """Rough US FBA fulfillment fee for standard-size items when Keepa has none."""
    if weight_lb <= 0.5:
        return 3.30
    if weight_lb <= 1:
        return 4.00
    if weight_lb <= 2:
        return 5.10
    if weight_lb <= 3:
        return 6.10
    return round(6.10 + 0.40 * (weight_lb - 3), 2)


def parse_product(p: dict) -> dict:
    stats = p.get("stats") or {}
    cur, avg30, avg90 = stats.get("current"), stats.get("avg30"), stats.get("avg90")

    grams = p.get("packageWeight") or p.get("itemWeight") or 0
    weight_lb = round(grams / GRAMS_PER_LB, 2) if grams and grams > 0 else None

    fba = (p.get("fbaFees") or {}).get("pickAndPackFee")
    referral = p.get("referralFeePercentage") or p.get("referralFeePercent")

    buybox_now = _cents(cur, BUY_BOX_SHIPPING)
    if buybox_now is None and stats.get("buyBoxPrice", -1) > 0:
        buybox_now = round((stats["buyBoxPrice"] + max(stats.get("buyBoxShipping", 0), 0)) / 100, 2)

    offers = _int(cur, COUNT_NEW)
    if offers is None and (stats.get("offerCountFBA") is not None or stats.get("offerCountFBM") is not None):
        offers = max(stats.get("offerCountFBA") or 0, 0) + max(stats.get("offerCountFBM") or 0, 0)

    amazon_monthly = p.get("monthlySold") if (p.get("monthlySold") or 0) > 0 else None
    monthly = p.get("monthlySold")
    if not monthly or monthly < 0:
        monthly = stats.get("salesRankDrops30") if (stats.get("salesRankDrops30") or -1) >= 0 else None

    amazon_share = None
    bbs = stats.get("buyBoxStats") or {}
    if isinstance(bbs, dict) and bbs:
        amazon_share = round((bbs.get(AMAZON_SELLER_ID) or {}).get("percentageWon", 0.0), 1)

    csv = p.get("csv") or []
    full = _series(csv, BUY_BOX_SHIPPING) or _series(csv, NEW)
    # keep out-of-stock gaps as None: time out of stock is time NOT selling at the price
    now = time.time()
    w_start, w_end = now - WINDOW_DAYS * DAY, now - RECENT_DIP_DAYS * DAY
    history_days = round((now - full[0][0]) / DAY) if full else 0
    events = sale_events(p)
    if not monthly:
        # No "bought in past month" figure: estimate from observed sales (last 90 days / 3).
        recent = [t for t, _ in events if t >= now - 90 * DAY]
        monthly = round(len(recent) / 3) if events else None
    sold_sorted = sorted(v for _, v in events if v)
    root_name = (p.get("categoryTree") or [{}])[0].get("name", "")
    is_variation = bool(p.get("parentAsin") or p.get("variations") or p.get("variationCSV")
                        or root_name in APPAREL_ROOTS or SIZE_IN_TITLE.search(p.get("title") or ""))
    tree = p.get("categoryTree") or []
    return {
        "asin": p.get("asin"),
        "title": p.get("title") or "(untitled)",
        "brand": p.get("brand"),
        "category": tree[0]["name"] if tree else None,
        "root_category": p.get("rootCategory"),
        "image": image_url(p),
        "upc": (p.get("upcList") or [None])[0],
        "weight_lb": weight_lb,
        "fba_fee": round(fba / 100, 2) if fba and fba > 0 else None,
        "referral_pct": float(referral) if referral and referral > 0 else None,
        "sales_rank": _int(cur, SALES),
        "monthly_sold": monthly,
        "offer_count": offers,
        "amazon_price": _cents(cur, AMAZON),
        "new_price": _cents(cur, NEW),
        # what you'd actually pay a 3rd-party seller, shipping included
        "third_party_cost": min([v for v in (_cents(cur, NEW_FBA), _cents(cur, NEW_FBM_SHIPPING)) if v],
                                default=None) or _cents(cur, NEW),
        "buybox_now": buybox_now,
        "buybox_avg30": _cents(avg30, BUY_BOX_SHIPPING) or _cents(avg30, NEW),
        "buybox_avg90": _cents(avg90, BUY_BOX_SHIPPING) or _cents(avg90, NEW) or _cents(avg90, AMAZON),
        "amazon_avg90": _cents(avg90, AMAZON),
        "buybox_avg180": _cents(stats.get("avg180"), BUY_BOX_SHIPPING),
        "buybox_median90": time_weighted_median(price_history(p, 90)),
        "established_price": time_weighted_percentile(full, 0.25, w_start, w_end),
        "history_days": history_days,
        "lowest_fba_3p": _cents(cur, NEW_FBA),
        "sold_at_median": sold_sorted[len(sold_sorted) // 2] if sold_sorted else None,
        "sold_events": len(sold_sorted),
        "oos_sales_share": round(sum(1 for _, v in events if not v) / len(events), 3) if events else 0.0,
        "price_swing": _swing(full, w_start, w_end),
        "_full_history": full,
        "_sale_events": events,
        "buybox_is_amazon": bool(stats.get("buyBoxIsAmazon")),
        "amazon_bb_share": amazon_share,
        "is_variation": is_variation,
        "is_media": root_name in MEDIA_ROOTS,
        "amazon_monthly_sold": amazon_monthly,
        "hazmat": bool(p.get("hazardousMaterials")),
        "adult": bool(p.get("isAdultProduct")),
        "history": price_history(p),
    }


def resale_basis(info: dict) -> float | None:
    """Conservative 'what will it actually sell for' estimate: the LOWEST of
    the robust signals. Keepa averages get polluted by inflated "regular"
    prices nobody pays, so no single average is trusted.

    - established price: the price it sat at or above 75% of the time over
      the last 180 days, ignoring the last 7 days (the current dip)
    - 90 / 180-day buy box averages
    - median price at real sales (only when sales data is the item's own,
      i.e. not a size/color variation)
    """
    candidates = [v for v in (info.get("established_price"), info.get("buybox_avg90"),
                              info.get("buybox_avg180")) if v]
    if (not info.get("is_variation") and info.get("sold_at_median")
            and (info.get("sold_events") or 0) >= 3):
        candidates.append(info["sold_at_median"])
    return min(candidates) if candidates else None


def proof_of_demand(info: dict, resale: float) -> dict:
    """Evidence that the item really sells at the resale price (not just lists there)."""
    now = time.time()
    floor = resale * 0.95
    sales = sum(1 for _, v in info.get("_sale_events") or [] if v and v >= floor)
    share = time_share_at_or_above(info.get("_full_history") or [], floor,
                                   now - WINDOW_DAYS * DAY, now - RECENT_DIP_DAYS * DAY)
    # How long has the price been below 80% of resale? A long "dip" is the new normal.
    dip_start = now
    for t, v in reversed(info.get("_full_history") or []):
        if v and v >= resale * 0.8:
            break
        dip_start = t
    return {"sales_at_resale": sales, "time_at_resale": share,
            "dip_days": round((now - dip_start) / DAY, 1)}


def evaluate(info: dict, buy_price: float | None, s: Settings) -> dict:
    """Compute fees/profit/ROI/score. Returns the play with `passes` + `flags`."""
    flags: list[str] = []
    rejects: list[str] = []

    info = {k: v for k, v in info.items() if not k.startswith("_")} | {
        "_full_history": info.get("_full_history"), "_sale_events": info.get("_sale_events")}
    sell_basis = resale_basis(info)
    if not buy_price or not sell_basis:
        return _strip({**info, "passes": False, "rejects": ["Missing buy or resale price"],
                       "flags": [], "score": 0, "verified": False})
    # Today's market caps the resale price: if other sellers hold the buy box or
    # list via FBA above your cost, that's what you'll have to match.
    for market in (info.get("buybox_now"), info.get("lowest_fba_3p")):
        if market and market > buy_price * 1.05:
            sell_basis = min(sell_basis, market)
    proof = proof_of_demand(info, sell_basis)

    sell = round(sell_basis * (1 - s.resale_haircut), 2)
    weight = info.get("weight_lb")
    if weight is None:
        weight = 1.0
        flags.append("Weight unknown (assumed 1 lb)")

    referral_pct = info.get("referral_pct") or s.default_referral_pct
    referral_fee = round(max(sell * referral_pct / 100, 0.30), 2)
    fba_fee = info.get("fba_fee") or estimate_fba_fee(weight)
    if not info.get("fba_fee"):
        flags.append("FBA fee estimated")
    inbound = round(weight * s.inbound_ship_per_lb, 2)
    cost = round(buy_price * (1 + s.sales_tax_rate), 2)
    fees = {"referral": referral_fee, "fba": round(fba_fee, 2), "inbound": inbound,
            "prep": s.prep_cost_per_unit, "storage": s.storage_per_unit,
            "tax": round(cost - buy_price, 2)}
    total_fees = round(sum(v for k, v in fees.items() if k != "tax"), 2)
    profit = round(sell - total_fees - cost, 2)
    roi = round(profit / cost, 4) if cost else 0
    margin = round(profit / sell, 4) if sell else 0

    # --- risk flags ---
    rank, monthly, offers = info.get("sales_rank"), info.get("monthly_sold"), info.get("offer_count")
    share = info.get("amazon_bb_share")
    amazon_heavy = bool(info.get("buybox_is_amazon") or (share is not None and share >= 50))
    if amazon_heavy:
        flags.append(f"Amazon wins buy box{f' {share:.0f}%' if share is not None else ''}")
    drop = 1 - buy_price / sell_basis if sell_basis else 0
    extreme = drop >= 0.70
    undercut = False
    amz, bb_now = info.get("amazon_price"), info.get("buybox_now")
    if amz and bb_now and bb_now > amz * 1.1:
        flags.append("Amazon's cheap offer isn't winning the buy box (may be backordered)")
    if extreme:
        flags.append(f"Extreme {drop:.0%} drop - verify it's real")
    a30, a90 = info.get("buybox_avg30"), info.get("buybox_avg90")
    trending_down = bool(a30 and a90 and a30 < a90 * 0.85)
    if trending_down:
        flags.append("Resale price trending down")
    if weight >= 5:
        flags.append("Heavy item")

    # --- hard filters ---
    if profit < s.min_profit:
        rejects.append(f"Profit ${profit:.2f} < ${s.min_profit:.2f}")
    if roi < s.min_roi:
        rejects.append(f"ROI {roi:.0%} < {s.min_roi:.0%}")
    if not (s.min_buy_price <= buy_price <= s.max_buy_price):
        rejects.append("Buy price outside range")
    if rank is None or rank > s.max_sales_rank:
        rejects.append("Sales rank too high / unknown")
    if monthly is not None and monthly < s.min_monthly_sold:
        rejects.append("Sells too slowly")
    if offers is not None and offers > s.max_offer_count:
        rejects.append("Too many sellers")
    if s.skip_hazmat_and_adult and (info.get("hazmat") or info.get("adult")):
        rejects.append("Hazmat / adult")
    if (info.get("brand") or "").strip().lower() in RESTRICTED_BRANDS:
        rejects.append("Amazon-brand item (can't resell)")

    # --- verification: is this a real flip or noise? ---
    if info.get("is_variation") and not s.allow_variations:
        rejects.append("Size/color variation: can't verify this exact item's sales")
    if (info.get("history_days") or 0) < s.min_history_days:
        rejects.append(f"Only {info.get('history_days') or 0} days of price history")
    if proof["sales_at_resale"] < s.min_sales_at_resale:
        rejects.append(f"Only {proof['sales_at_resale']} sales at ${sell_basis * 0.95:.2f}+ in 180 days")
    if proof["time_at_resale"] < s.min_time_at_resale:
        rejects.append(f"Held ${sell_basis * 0.95:.2f}+ only {proof['time_at_resale']:.0%} of the time")
    if share is not None and share > s.max_amazon_bb_share:
        rejects.append(f"Amazon holds the buy box {share:.0f}% of the time: you'd rarely get the sale")
    if info.get("is_media") and not info.get("amazon_monthly_sold") and s.require_amazon_sales_for_media:
        rejects.append("Media item without Amazon's own 'bought in past month' count: "
                       "rank may belong to another edition")
    if (info.get("oos_sales_share") or 0) > s.max_oos_sales_share:
        rejects.append(f"{info['oos_sales_share']:.0%} of 'sales' happened while out of stock: "
                       "rank belongs to another edition")
    if (info.get("price_swing") or 1) > s.max_price_swing:
        rejects.append(f"Normal price swings {info['price_swing']:.1f}x: no stable resale price")
    if proof["dip_days"] > s.max_dip_days:
        rejects.append(f"Price has been this low for {proof['dip_days']:.0f} days: it's the new normal")
    if drop * 100 >= s.max_drop_percent:
        rejects.append(f"{drop:.0%} below normal: likely a pricing error")

    # --- score 0-100 ---
    velocity = monthly if monthly is not None else max(0, 300 - (rank or 10**7) / 500)
    score = (
        min(max(roi, 0) / 1.0, 1) * 30
        + min(max(profit, 0) / 20, 1) * 25
        + min(velocity / 200, 1) * 20
        + (max(0, 1 - (rank or s.max_sales_rank) / s.max_sales_rank) * 10)
        + (max(0, 1 - (offers if offers is not None else 15) / 30) * 15)
    )
    if amazon_heavy:
        score -= 12
    if extreme:
        score -= 8
    if undercut:
        score -= 8
    score += min(proof["sales_at_resale"] / 30, 1) * 5     # more proof, more confidence
    if trending_down:
        score -= 10
    score = round(max(0, min(100, score)), 1)

    est_monthly_profit = round(profit * min(velocity / max((offers or 1), 1), 30), 2) if profit > 0 else 0

    verified = not rejects
    proof_text = (f"Sold {proof['sales_at_resale']}x at ${sell_basis * 0.95:.2f}+ in the last 180 days; "
                  f"held that price {proof['time_at_resale']:.0%} of the time")
    return _strip({
        **info,
        **proof,
        "proof": proof_text,
        "verified": verified,
        "buy_price": round(buy_price, 2),
        "sell_price": sell,
        "fees": fees,
        "total_fees": total_fees,
        "profit": profit,
        "roi": roi,
        "margin": margin,
        "score": score,
        "est_monthly_profit": est_monthly_profit,
        "resale_basis": sell_basis,
        "drop_pct": round(drop, 4),
        "flags": flags,
        "rejects": rejects,
        "passes": verified,
    })


def _strip(d: dict) -> dict:
    return {k: v for k, v in d.items() if not k.startswith("_")}
