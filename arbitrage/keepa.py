"""Thin Keepa API client.

Docs: https://keepa.com/#!discuss/t/using-the-keepa-api/47
Prices come back as integer cents; -1 / -2 mean "no data".
"""
from __future__ import annotations

import json
import time
from typing import Iterable

import httpx

BASE_URL = "https://api.keepa.com"

# Indices into Keepa's csv / stats.current / stats.avg90 arrays
AMAZON, NEW, USED, SALES, LISTPRICE = 0, 1, 2, 3, 4
NEW_FBM_SHIPPING, NEW_FBA, COUNT_NEW = 7, 10, 11
BUY_BOX_SHIPPING = 18

KEEPA_EPOCH_MINUTES = 21_564_000  # Keepa time = minutes since 2011-01-01


def keepa_minutes_to_unix(minutes: int) -> int:
    return (minutes + KEEPA_EPOCH_MINUTES) * 60


class KeepaError(RuntimeError):
    pass


class KeepaClient:
    def __init__(self, api_key: str, domain: int = 1, timeout: float = 60.0):
        if not api_key:
            raise ValueError("Keepa API key is required")
        self.api_key = api_key
        self.domain = domain
        self.tokens_left: int | None = None
        self.refill_in_ms: int | None = None
        self._http = httpx.Client(base_url=BASE_URL, timeout=timeout,
                                  headers={"Accept-Encoding": "gzip"})

    # -- plumbing -----------------------------------------------------------
    def _get(self, path: str, params: dict, retries: int = 3) -> dict:
        params = {"key": self.api_key, **params}
        for attempt in range(retries + 1):
            resp = self._http.get(path, params=params)
            data = resp.json() if resp.content else {}
            self.tokens_left = data.get("tokensLeft", self.tokens_left)
            self.refill_in_ms = data.get("refillIn", self.refill_in_ms)
            if resp.status_code == 429 and attempt < retries:
                # Out of tokens: wait for refill (Keepa tells us how long).
                time.sleep(min(max((self.refill_in_ms or 60_000) / 1000, 5), 120))
                continue
            if resp.status_code >= 400 or "error" in data:
                err = data.get("error", {})
                raise KeepaError(f"Keepa {path} HTTP {resp.status_code}: "
                                 f"{err.get('message') or err or resp.text[:200]}")
            return data
        raise KeepaError(f"Keepa {path}: out of tokens after {retries} retries")

    # -- endpoints ----------------------------------------------------------
    def token_status(self) -> dict:
        return self._get("/token", {})

    def deals(self, selection: dict) -> list[dict]:
        """Browse Keepa Deals (5 tokens / page of up to 150 deals)."""
        data = self._get("/deal", {"selection": json.dumps(selection, separators=(",", ":"))})
        return (data.get("deals") or {}).get("dr") or []

    def products(self, asins: Iterable[str] = (), codes: Iterable[str] = (),
                 buybox: bool = True, stats_days: int = 180) -> list[dict]:
        """Product lookup by ASIN or by UPC/EAN code (100 per request)."""
        asins, codes = list(asins), list(codes)
        out: list[dict] = []
        for key, items in (("asin", asins), ("code", codes)):
            for i in range(0, len(items), 100):
                params = {"domain": self.domain, key: ",".join(items[i:i + 100]),
                          "stats": stats_days, "history": 1, "days": stats_days}
                if buybox:
                    params["buybox"] = 1
                data = self._get("/product", params)
                out.extend(data.get("products") or [])
        return out

    def search(self, term: str, stats_days: int = 180) -> list[dict]:
        """Keyword product search: 10 tokens, up to 20 full products (with stats + history)."""
        data = self._get("/search", {"domain": self.domain, "type": "product", "term": term,
                                     "stats": stats_days, "history": 1, "days": stats_days})
        return data.get("products") or []

    def close(self) -> None:
        self._http.close()
