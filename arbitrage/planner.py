"""Token-efficient scan planning.

Keepa costs (per call):  deal page = 5 tokens (up to 150 deals)
                         product   = 1 token (+2 with buy box data)
Deal records already carry enough to discard most junk for free, so we:
  1. pull many deal pages (cheap),
  2. pre-filter + rank them with zero product tokens,
  3. quick-check the best ones without buy box data (1 token),
  4. fully check only the survivors with buy box data (+3 tokens).
"""
from __future__ import annotations

import re
import time

from .keepa import AMAZON, BUY_BOX_SHIPPING, NEW, keepa_minutes_to_unix
from .profit import SIZE_IN_TITLE

NOT_NEW = re.compile(r"\b(renewed|refurbished|like[- ]new|pre-?owned)\b", re.I)
AMAZON_OWN = re.compile(r"\b(blink (?:outdoor|indoor|mini|video|doorbell)|ring (?:video|doorbell|camera|alarm|"
                        r"spotlight|floodlight|stick up)|echo (?:dot|show|pop|studio|hub)|kindle|fire tv|"
                        r"fire hd|eero|amazon basics|amazonbasics|amazon essentials)\b", re.I)

DEAL_PAGE_COST = 5
QUICK_COST = 1
FULL_COST = 3
DAY = 86400

# Rejections no amount of extra data can fix -> don't pay for a full check.
HARD_REJECTS = ("variation", "Hazmat", "Amazon-brand", "days of price history", "out of stock",
                "Sells too slowly", "Sales rank", "pricing error", "Buy price outside",
                "new normal", "swings", "another edition", "rarely get the sale")


def _c(arr, idx):
    if not arr or len(arr) <= idx or arr[idx] is None or arr[idx] <= 0:
        return None
    return arr[idx] / 100


def rough_resale(deal: dict) -> float | None:
    """Lowest of the month / 90-day buy box (or new/Amazon) averages in the deal record."""
    avg = deal.get("avg") or []
    vals = []
    for interval in (2, 3):
        row = avg[interval] if len(avg) > interval else None
        v = _c(row, BUY_BOX_SHIPPING) or _c(row, NEW) or _c(row, AMAZON)
        if v:
            vals.append(v)
    return min(vals) if vals else None


def prefilter(deal: dict, price_type: int, s) -> tuple[float | None, str | None]:
    """Return (rank_score, None) for a candidate worth tokens, or (None, reason) to skip it."""
    if deal.get("parentAsin"):
        return None, "variation"
    if deal.get("rootCat") in s.exclude_categories:
        return None, "excluded category"
    title = deal.get("title") or ""
    if SIZE_IN_TITLE.search(title):
        return None, "variation"
    if NOT_NEW.search(title):
        return None, "not new"
    if AMAZON_OWN.search(title):
        return None, "Amazon's own brand"
    drops90 = deal.get("salesRankDrops90")
    drops180 = deal.get("salesRankDrops180")
    if drops180 is not None and 0 <= drops180 < s.min_sales_at_resale:
        return None, "too few sales"
    if drops90 is not None and 0 <= drops90 < max(1, s.min_monthly_sold * 3 // 2):
        return None, "too few sales"
    buy = _c(deal.get("current"), price_type)
    resale = rough_resale(deal)
    if not buy or not resale:
        return None, "no price"
    if not (s.min_buy_price <= buy <= s.max_buy_price):
        return None, "buy price outside range"
    drop = 1 - buy / resale
    if drop * 100 >= s.max_drop_percent:
        return None, "pricing error"
    since = (deal.get("currentSince") or [])
    started = since[price_type] if len(since) > price_type and since[price_type] > 0 else None
    age_days = (time.time() - keepa_minutes_to_unix(started)) / DAY if started else 0
    if age_days > s.max_dip_days:
        return None, "old drop"
    # Rough fees: referral ~15% + ~$5 FBA/inbound/prep. Generous on purpose: this only
    # decides what's worth a token; the real check comes later.
    est_profit = resale * (1 - s.resale_haircut) * 0.85 - 5.0 - buy * (1 + s.sales_tax_rate)
    if est_profit < s.min_profit * 0.6 or est_profit / buy < s.min_roi * 0.6:
        return None, "can't reach profit target"
    velocity = min((drops90 or 0) / 30, 1)           # proof of demand
    freshness = 1.0 if age_days < 1 else 0.8 if age_days < 3 else 0.6
    return round(min(est_profit, 60) * (0.4 + 0.6 * velocity) * freshness, 2), None


def plan_quick_checks(tokens: int, n_candidates: int, reserve: int = 10) -> int:
    """How many candidates to quick-check, keeping room for full checks of ~15% survivors."""
    usable = max(0, tokens - reserve)
    return min(n_candidates, int(usable / (QUICK_COST + 0.15 * FULL_COST)))
