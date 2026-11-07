"""Fake Keepa client that returns Keepa-shaped data, so the whole pipeline
(and the dashboard) works without an API key."""
from __future__ import annotations

import random
import string
import time

from .keepa import KEEPA_EPOCH_MINUTES

CATALOG = [
    ("Toys & Games", ["LEGO Classic Creative Bricks Set", "Hasbro Connect 4 Strategy Game",
                      "Melissa & Doug Wooden Puzzle Set", "Nerf Elite 2.0 Blaster", "Play-Doh Kitchen Creations"]),
    ("Home & Kitchen", ["OXO Good Grips Salad Spinner", "Lodge Cast Iron Skillet 10 in",
                        "Ninja Blender Pitcher Replacement", "Rubbermaid Brilliance Food Storage 10pc"]),
    ("Health & Household", ["Oral-B Replacement Brush Heads 8ct", "Crest 3D White Strips 20ct",
                            "Philips Sonicare Brush Heads 3pk"]),
    ("Beauty & Personal Care", ["CeraVe Moisturizing Cream 19oz", "Olaplex No.3 Hair Perfector",
                                "Neutrogena Hydro Boost Gel"]),
    ("Sports & Outdoors", ["Hydro Flask 32 oz Wide Mouth", "Coleman Camping Lantern",
                           "Gaiam Yoga Mat Premium 6mm"]),
    ("Electronics", ["Anker USB-C Charger 65W", "JBL Go 3 Portable Speaker", "SanDisk 256GB microSD"]),
    ("Office Products", ["Sharpie Permanent Markers 24pk", "Post-it Super Sticky Notes 12pk"]),
    ("Pet Supplies", ["KONG Classic Dog Toy Large", "Greenies Dental Dog Treats 36oz"]),
]
BRANDS = ["LEGO", "Hasbro", "OXO", "Lodge", "Ninja", "Crest", "CeraVe", "Anker", "JBL", "KONG", "Coleman"]


def _asin(rng: random.Random) -> str:
    return "B0" + "".join(rng.choice(string.ascii_uppercase + string.digits) for _ in range(8))


class DemoKeepa:
    def __init__(self, domain: int = 1, seed: int | None = None):
        self.domain = domain
        self.rng = random.Random(seed if seed is not None else int(time.time() // 3600))
        self.tokens_left = 1200
        self.refill_in_ms = 60_000
        self._made: dict[str, dict] = {}

    def token_status(self) -> dict:
        return {"tokensLeft": self.tokens_left, "refillIn": self.refill_in_ms, "refillRate": 20}

    def deals(self, selection: dict) -> list[dict]:
        self.tokens_left -= 5
        return [{"asin": _asin(self.rng)} for _ in range(40)]

    def products(self, asins=(), codes=(), buybox: bool = True, stats_days: int = 90) -> list[dict]:
        out = []
        for a in asins:
            out.append(self._made.setdefault(a, self._fake(a)))
        for c in codes:
            out.append(self._fake(_asin(self.rng), upc=c))
        self.tokens_left -= (3 if buybox else 1) * (len(out))
        return out

    def close(self) -> None:
        pass

    def _fake(self, asin: str, upc: str | None = None) -> dict:
        r = self.rng
        cat, titles = r.choice(CATALOG)
        normal = r.uniform(14, 120)                    # typical buy box price
        drop = r.uniform(0.15, 0.6)                    # how far Amazon price fell
        amazon_now = normal * (1 - drop) if r.random() < 0.85 else -1
        bb_now = normal * r.uniform(0.75, 1.05)
        avg30 = normal * r.uniform(0.8, 1.05)
        rank = int(r.choice([r.uniform(500, 20_000), r.uniform(20_000, 120_000), r.uniform(120_000, 600_000)]))
        offers = r.randint(1, 45)
        grams = int(r.uniform(120, 3500))
        fba = int(r.uniform(320, 780))
        monthly = max(0, int(150_000 / (rank ** 0.75) * r.uniform(5, 15)))

        def arr(**vals):
            a = [-1] * 35
            for k, v in vals.items():
                a[int(k[1:])] = int(round(v * 100)) if isinstance(v, float) else v
            return a

        now_k = int(time.time() / 60) - KEEPA_EPOCH_MINUTES
        hist, ranks, t, price, rk = [], [], now_k - 90 * 1440, normal, rank
        while t < now_k:
            price = max(normal * 0.6, min(normal * 1.25, price * r.uniform(0.95, 1.05)))
            hist += [t, int(price * 100), 0]
            for _ in range(r.randint(0, 3)):   # sales-rank drops ~= sales
                rk = int(rk * r.uniform(0.6, 0.9)) or 1
                ranks += [t + r.randint(1, 600), rk]
                rk = int(rk * r.uniform(1.2, 1.7))
                ranks += [t + r.randint(601, 1200), rk]
            t += int(r.uniform(0.5, 4) * 1440)
        hist += [now_k, int(bb_now * 100), 0]

        return {
            "asin": asin,
            "title": f"{r.choice(titles)}",
            "brand": r.choice(BRANDS),
            "rootCategory": abs(hash(cat)) % 100000,
            "categoryTree": [{"catId": 1, "name": cat}],
            "imagesCSV": None,
            "upcList": [upc or str(r.randint(10**11, 10**12 - 1))],
            "packageWeight": grams,
            "fbaFees": {"pickAndPackFee": fba},
            "referralFeePercentage": r.choice([8.0, 15.0, 15.0, 15.0, 17.0]),
            "monthlySold": monthly if r.random() < 0.8 else -1,
            "isAdultProduct": False,
            "hazardousMaterials": [] if r.random() > 0.05 else [{"aspect": "battery"}],
            "csv": [None, None, None, ranks] + [None] * 14 + [hist],
            "stats": {
                "current": arr(i0=amazon_now if amazon_now > 0 else -1, i1=bb_now * 0.98, i3=rank,
                               i11=offers, i18=bb_now),
                "avg30": arr(i0=normal * 0.95, i1=avg30 * 0.98, i18=avg30),
                "avg90": arr(i0=normal, i1=normal * 0.98, i18=normal),
                "buyBoxIsAmazon": r.random() < 0.15,
                "salesRankDrops30": monthly,
            },
        }
