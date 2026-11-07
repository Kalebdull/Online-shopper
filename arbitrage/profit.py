"""Turn a raw Keepa product into a scored flip opportunity."""
from __future__ import annotations

import time

from .config import Settings
from .keepa import (AMAZON, BUY_BOX_SHIPPING, COUNT_NEW, NEW, SALES,
                    keepa_minutes_to_unix)

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


def price_history(product: dict, days: int = 90) -> list[list[float]]:
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


def sold_at_prices(product: dict, days: int = 90) -> list[float]:
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
        if price:
            prices.append(round(price, 2))
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

    monthly = p.get("monthlySold")
    if not monthly or monthly < 0:
        monthly = stats.get("salesRankDrops30") if (stats.get("salesRankDrops30") or -1) >= 0 else None

    amazon_share = None
    bbs = stats.get("buyBoxStats") or {}
    if isinstance(bbs, dict) and bbs:
        amazon_share = round((bbs.get(AMAZON_SELLER_ID) or {}).get("percentageWon", 0.0), 1)

    history = price_history(p)
    sold = sold_at_prices(p)
    sold_sorted = sorted(sold)
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
        "buybox_now": buybox_now,
        "buybox_avg30": _cents(avg30, BUY_BOX_SHIPPING) or _cents(avg30, NEW),
        "buybox_avg90": _cents(avg90, BUY_BOX_SHIPPING) or _cents(avg90, NEW) or _cents(avg90, AMAZON),
        "amazon_avg90": _cents(avg90, AMAZON),
        "buybox_avg180": _cents(stats.get("avg180"), BUY_BOX_SHIPPING),
        "buybox_median90": time_weighted_median(history),
        "sold_at_median": sold_sorted[len(sold_sorted) // 2] if sold_sorted else None,
        "sold_events": len(sold),
        "buybox_is_amazon": bool(stats.get("buyBoxIsAmazon")),
        "amazon_bb_share": amazon_share,
        "is_variation": bool(p.get("parentAsin") or p.get("variations")),
        "hazmat": bool(p.get("hazardousMaterials")),
        "adult": bool(p.get("isAdultProduct")),
        "history": history,
    }


def resale_basis(info: dict) -> float | None:
    """Conservative 'what will it actually sell for' estimate.

    Keepa averages get polluted by placeholder / pre-launch prices (e.g. a
    $25 item showing a $277 180-day average), so take the lowest of the robust
    signals we have rather than trusting any single average.
    """
    if info.get("sold_at_median") and (info.get("sold_events") or 0) >= 3:
        candidates = [info["sold_at_median"]]
    else:
        candidates = []
    candidates += [v for v in (info.get("buybox_median90"), info.get("buybox_avg90"),
                               info.get("buybox_avg30")) if v]
    if not candidates:
        return info.get("buybox_now")
    return min(candidates)


def evaluate(info: dict, buy_price: float | None, s: Settings) -> dict:
    """Compute fees/profit/ROI/score. Returns the play with `passes` + `flags`."""
    flags: list[str] = []
    rejects: list[str] = []

    sell_basis = resale_basis(info)
    if not buy_price or not sell_basis:
        return {**info, "passes": False, "rejects": ["Missing buy or resale price"],
                "flags": [], "score": 0}

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
    if info.get("is_variation"):
        flags.append("Variation: sales data shared")
    if (info.get("sold_events") or 0) < 3:
        flags.append("Few observed sales - resale price is a guess")
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
    if (info.get("sold_events") or 0) < 3:
        score -= 10
    if info.get("is_variation"):
        score -= 6
    if trending_down:
        score -= 10
    score = round(max(0, min(100, score)), 1)

    est_monthly_profit = round(profit * min(velocity / max((offers or 1), 1), 30), 2) if profit > 0 else 0

    return {
        **info,
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
        "passes": not rejects,
    }
