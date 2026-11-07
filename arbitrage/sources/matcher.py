"""Match a store deal to the exact same product on Amazon.

A wrong match is the worst kind of fake play (a 2-pack priced against a single,
a different model), so the rules are strict and "no match" is the safe answer:

- If the deal names a model number, the Amazon product must carry that exact
  model number (model / part number / title).
- Otherwise the brand must match, every size or quantity in the deal title
  (25,000mAh, 18 oz, 10", 9-piece) must appear in the Amazon title, the pack
  count must be equal, and most of the remaining words must overlap.
"""
from __future__ import annotations

import re

from .slickdeals import model_numbers

STOP = {"the", "and", "with", "for", "of", "in", "on", "a", "an", "to", "by", "or", "w", "new", "set",
        "free", "pack", "pk", "ct", "count", "piece", "pc", "pcs", "inch", "in", "ft", "oz"}
UNITS = {"mah": "mah", "oz": "oz", "ounce": "oz", "ounces": "oz", "lb": "lb", "lbs": "lb", "pound": "lb",
         "pounds": "lb", "in": "in", "inch": "in", "inches": "in", '"': "in", "ft": "ft", "foot": "ft",
         "feet": "ft", "qt": "qt", "quart": "qt", "gal": "gal", "gallon": "gal", "ml": "ml", "l": "l",
         "liter": "l", "w": "w", "watt": "w", "v": "v", "volt": "v", "gb": "gb", "tb": "tb", "mm": "mm",
         "cm": "cm", "pc": "pc", "pcs": "pc", "piece": "pc", "pieces": "pc", "ct": "ct", "count": "ct",
         "pk": "pk", "pack": "pk", "cu": "cu", "k": "k", "hz": "hz"}
QTY = re.compile(r"(\d+(?:\.\d+)?)\s*-?\s*(mah|ounces?|oz|lbs?|pounds?|inch(?:es)?|in\b|\"|ft|foot|feet|"
                 r"qt|quarts?|gal(?:lons?)?|ml|liters?|l\b|watts?|w\b|volts?|v\b|gb|tb|mm|cm|"
                 r"pcs?|pieces?|ct|count|pk|pack|cu|hz)", re.I)
PACK = re.compile(r"(?:(\d+)\s*-?\s*(?:pk|pack|ct|count)\b|pack of (\d+)|(\d+)\s*-?\s*pc\b(?=.*\bpack\b))", re.I)


def _norm_num(n: str) -> str:
    n = n.replace(",", "")
    return str(int(float(n))) if float(n).is_integer() else str(float(n))


def quantities(text: str) -> set[str]:
    t = text.replace(",", "")
    out = set()
    for num, unit in QTY.findall(t):
        raw = unit.lower()
        u = UNITS.get(raw) or UNITS.get(raw.rstrip("s")) or UNITS.get(re.sub(r"e?s$", "", raw)) or raw
        if u in ("pk", "ct"):
            continue                      # pack counts are compared separately
        out.add(f"{_norm_num(num)}{u}")
    return out


def pack_count(text: str) -> int:
    m = PACK.search(text or "")
    if not m:
        return 1
    return int(next(g for g in m.groups() if g))


def words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(w) >= 3 and w not in STOP}


def _amazon_models(p: dict) -> set[str]:
    vals = {str(p.get(k) or "").replace("-", "").replace(" ", "").upper() for k in ("model", "partNumber")}
    vals |= model_numbers(p.get("title") or "")
    return {v for v in vals if v}


def brand_matches(deal_query: str, p: dict) -> bool:
    brand = (p.get("brand") or "").lower().strip()
    q = deal_query.lower()
    if not brand:
        return False
    first = re.findall(r"[a-z0-9]+", brand)[:1]
    return brand in q or (first and re.search(rf"\b{re.escape(first[0])}\b", q) is not None)


def match(deal: dict, products: list[dict]) -> tuple[dict | None, str]:
    """Return (amazon_product, how_matched) or (None, why_not)."""
    if not products:
        return None, "Not found on Amazon"
    q = deal["query"]
    if deal.get("models"):
        for p in products:
            if deal["models"] & _amazon_models(p):
                if pack_count(q) != pack_count(p.get("title") or ""):
                    continue
                return p, "exact model number"
        return None, f"No Amazon listing with model {', '.join(sorted(deal['models']))}"

    want_q, want_pack, want_words = quantities(q), pack_count(q), words(q)
    best, best_score = None, 0.0
    for p in products:
        title = p.get("title") or ""
        if not brand_matches(q, p):
            continue
        if pack_count(title) != want_pack:
            continue
        if not want_q <= quantities(title):
            continue
        have = words(title)
        overlap = len(want_words & have) / max(len(want_words), 1)
        if overlap > best_score:
            best, best_score = p, overlap
    if best is not None and best_score >= 0.85:
        return best, f"brand, sizes and {best_score:.0%} of title words match"
    return None, "No exact Amazon match (brand, size or pack count differs)"
