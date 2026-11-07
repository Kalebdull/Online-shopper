"""Deals from around the web via Slickdeals' public RSS feeds.

Slickdeals aggregates deals from Walmart, Target, Home Depot, Lowe's, Best Buy,
Kohl's, Woot and hundreds of other stores. We read the feeds, keep only deals
that could be a resellable new item at a fixed price, and hand them to the
matcher, which finds the exact same product on Amazon via Keepa.
"""
from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

import httpx

FEEDS = {
    "Frontpage": "https://slickdeals.net/newsearch.php?mode=frontpage&searcharea=deals&searchin=first&rss=1",
    "Popular": "https://slickdeals.net/newsearch.php?mode=popdeals&searcharea=deals&searchin=first&rss=1",
    "Hot Deals": "https://slickdeals.net/newsearch.php?searchin=first&forumchoice%5B%5D=9&rss=1",
}
# Store and clearance searches widen the net beyond the front page.
SEARCHES = ["walmart", "target", "home depot", "lowes", "best buy", "kohls", "costco", "woot",
            "clearance", "lego", "toys", "sams club", "staples", "academy", "dicks sporting goods"]
for _q in SEARCHES:
    FEEDS[f"Search: {_q}"] = ("https://slickdeals.net/newsearch.php?q=" + _q.replace(" ", "+")
                              + "&searcharea=deals&searchin=first&rss=1")

STORES = {
    "walmart.com": "Walmart", "target.com": "Target", "homedepot.com": "Home Depot",
    "lowes.com": "Lowe's", "bestbuy.com": "Best Buy", "kohls.com": "Kohl's", "woot.com": "Woot",
    "costco.com": "Costco", "samsclub.com": "Sam's Club", "macys.com": "Macy's", "ebay.com": "eBay",
    "newegg.com": "Newegg", "bhphotovideo.com": "B&H", "staples.com": "Staples", "officedepot.com": "Office Depot",
    "acehardware.com": "Ace Hardware", "harborfreight.com": "Harbor Freight", "dickssportinggoods.com": "Dick's",
    "academy.com": "Academy", "gamestop.com": "GameStop", "walgreens.com": "Walgreens", "cvs.com": "CVS",
    "zoro.com": "Zoro", "dell.com": "Dell", "microcenter.com": "Micro Center", "bjs.com": "BJ's",
    "amazon.com": "Amazon", "amzn.to": "Amazon",
}
STORE_IN_TEXT = re.compile(r"\b(Amazon|Walmart|Target|Home Depot|Lowe'?s|Best Buy|Kohl'?s|Woot|Costco|Sam'?s Club|"
                           r"Macy'?s|Newegg|Staples|Office Depot|Ace Hardware|Harbor Freight|Dick'?s|"
                           r"Academy|GameStop|Walgreens|CVS|Zoro|Micro Center|BJ'?s|eBay|B&H|Dell|Woot)"
                           r"(?:\.com)? (?:has|is offering|offers|is having|now has|via)\b", re.I)
STORE_IN_TITLE = re.compile(r"\b(Walmart|Target|Home Depot|Lowe'?s|Best Buy|Kohl'?s|Woot|Costco|Sam'?s Club|"
                            r"Macy'?s|Newegg|Staples|Office Depot|Ace Hardware|Harbor Freight|Dick'?s|"
                            r"Academy|GameStop|Walgreens|CVS|Zoro|Micro Center|BJ'?s)\b", re.I)

# Free filters: things that can't be bought at a fixed price and resold as new on Amazon.
SKIP = [
    (re.compile(r"\b(refurb\w*|renewed|open[- ]box|pre-?owned|used|certified)\b", re.I), "not new"),
    (re.compile(r"\$\s?\d[\d.,]*\s*off\b|kohl'?s cash|\bafter\b.*\b(coupon|rebate|cash)\b|\bfrom \$|\bstarting at\b|\bup to\b.*\boff\b|\d+\s*for\s*\$|\bbogo\b|\bbuy \d+", re.I),
     "no single fixed price"),
    (re.compile(r"\bS&S\b|subscribe\s*&\s*save|\bPrime Members\b|\bPrime Day\b", re.I),
     "Amazon members-only price"),
    (re.compile(r"\b(gift card|e-?gift|ticket|subscription|membership|digital code|download|app|"
                r"streaming|trial|credit|cashback|rebate|insurance|hotel|flight|cruise|movie ticket)\b", re.I),
     "not a physical product"),
    (re.compile(r"\(\s*\d+\s*colors?\b|\bvarious\b|\bassorted\b|\bmen'?s\b|\bwomen'?s\b|"
                r"\b(shirt|shoes?|sneakers?|jacket|pants|shorts|hoodie|dress|socks|boots)\b", re.I),
     "size/color variations"),
    (re.compile(r"\bYMMV\b|\bin[- ]store only\b|\bselect .* stores\b", re.I),
     "local / in-store only"),
]
PRICE = re.compile(r"\$\s?(\d{1,4}(?:,\d{3})*(?:\.\d{1,2})?)")


def clean_title(title: str) -> str:
    t = html.unescape(title)
    t = re.sub(r"^(Select [^:]+|Prime Members|Today Only|Ends Soon|Back in Stock)\s*:\s*", "", t, flags=re.I)
    t = re.sub(r"\$\s?\d[\d,]*(\.\d+)?.*$", "", t)          # price and everything after it
    t = re.sub(r"\bw/\s*", "with ", t)
    return re.sub(r"\s+", " ", t).strip(" -,:")


def model_numbers(*texts: str) -> set[str]:
    """Model-like tokens: letters and digits mixed, 5+ chars (e.g. CMMT16881, DCD771C2, RC-70...)."""
    out = set()
    for text in texts:
        for tok in re.findall(r"\b[A-Za-z0-9][A-Za-z0-9-]{3,}[A-Za-z0-9]\b", text or ""):
            norm = tok.replace("-", "").upper()
            digits = len(re.findall(r"\d", norm))
            if (len(norm) >= 5 and digits >= 2 and re.search(r"[A-Z]", norm)
                    and not re.fullmatch(r"\d+[A-Z]+", norm)          # 3PORT, 18OZ, 25000MAH: sizes, not models
                    and not re.fullmatch(r"(UTM|RSS|HTTP)\w*", norm)):
                out.add(norm)
    return out


def fetch(timeout: float = 20.0) -> list[dict]:
    deals, seen = [], set()
    with httpx.Client(timeout=timeout, headers={"User-Agent": "FlipFinder/1.0 (RSS reader)"}) as http:
        for feed_name, url in FEEDS.items():
            try:
                resp = http.get(url)
                resp.raise_for_status()
                root = ET.fromstring(resp.content)
            except Exception:  # noqa: BLE001 - one bad feed shouldn't stop the others
                continue
            for item in root.iter("item"):
                deal = parse_item(item, feed_name)
                if deal and deal["id"] not in seen:
                    seen.add(deal["id"])
                    deals.append(deal)
    return deals


def parse_item(item, feed_name: str) -> dict | None:
    title = html.unescape(item.findtext("title") or "").strip()
    link = item.findtext("link") or ""
    guid = item.findtext("guid") or link
    desc = html.unescape(item.findtext("description") or "")
    content = html.unescape(item.findtext("{http://purl.org/rss/1.0/modules/content/}encoded") or "")
    if not title:
        return None
    m = PRICE.search(title)
    price = float(m.group(1).replace(",", "")) if m else None
    store = None
    sm = STORE_IN_TITLE.search(title)
    text = re.sub(r"<[^>]+>", " ", content) + " " + desc
    tm = STORE_IN_TEXT.search(text)
    if sm:
        store = sm.group(1)
    elif tm:
        store = tm.group(1)
    elif re.search(r"-w-prime|amazon", link, re.I):
        store = "Amazon"
    else:
        for dom, name in STORES.items():
            if dom in desc or dom in content:
                store = name
                break
    if store:
        key = re.sub(r"[^a-z&]", "", store.lower())
        store = next((v for v in STORES.values() if re.sub(r"[^a-z&]", "", v.lower()) == key), store.title())
    try:
        published = parsedate_to_datetime(item.findtext("pubDate")).timestamp()
    except Exception:  # noqa: BLE001
        published = None
    slug = re.sub(r"[?#].*$", "", link.rsplit("/", 1)[-1])
    return {
        "id": guid.strip(),
        "title": title,
        "query": clean_title(title),
        "price": price,
        "free_shipping": bool(re.search(r"free (s&h|shipping|pickup|store pickup)|\bfs\b", title, re.I)),
        "store": store or "See deal",
        "url": re.sub(r"\?utm_.*$", "", link),
        "models": model_numbers(title, slug.replace("-", " ")),
        "feed": feed_name,
        "published": published,
    }


def skip_reason(deal: dict, s) -> str | None:
    if deal["price"] is None:
        return "no price"
    for rx, why in SKIP:
        if rx.search(deal["title"]):
            return why
    if deal["store"].lower() == "amazon":
        return "already an Amazon deal"
    if not (s.min_buy_price <= deal["price"] <= s.max_buy_price):
        return "price outside your range"
    if len(deal["query"]) < 8:
        return "title too vague to match"
    return None
