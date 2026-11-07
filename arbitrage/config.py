"""Environment + tunable scan settings."""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_dotenv(path: Path = ROOT / ".env") -> None:
    """Minimal .env loader (no extra dependency). Real env vars win."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_dotenv()

KEEPA_API_KEY = os.environ.get("KEEPA_API_KEY", "").strip()
KEEPA_DOMAIN = int(os.environ.get("KEEPA_DOMAIN", "1") or 1)
DEMO_MODE = os.environ.get("DEMO_MODE", "0") == "1" or not KEEPA_API_KEY
DB_PATH = Path(os.environ.get("DB_PATH", str(ROOT / "data" / "arbitrage.db")))

AMAZON_HOSTS = {1: "amazon.com", 2: "amazon.co.uk", 3: "amazon.de", 4: "amazon.fr",
                5: "amazon.co.jp", 6: "amazon.ca", 8: "amazon.it", 9: "amazon.es",
                10: "amazon.in", 11: "amazon.com.mx"}


@dataclass
class Settings:
    """Everything the dashboard lets you tune. Money is in dollars."""

    # --- what counts as a play ---
    min_profit: float = 5.0
    min_roi: float = 0.30            # 0.30 = 30%
    max_sales_rank: int = 150_000
    min_monthly_sold: int = 10
    min_buy_price: float = 8.0
    max_buy_price: float = 150.0
    max_offer_count: int = 40
    skip_hazmat_and_adult: bool = True

    # --- verification: only show flips the data can prove ---
    min_sales_at_resale: int = 4     # sales (rank drops) at >= resale price in last 180 days
    min_time_at_resale: float = 0.4  # share of the last 180 days the price held at >= resale
    min_history_days: int = 60       # need at least this much price history
    max_drop_percent: int = 85       # deeper drops are usually pricing errors
    max_oos_sales_share: float = 0.10  # "sales" while nothing was for sale = rank isn't this item's
    max_price_swing: float = 1.6     # 75th/25th percentile price ratio; above = no stable price
    max_dip_days: int = 14           # low price lasting longer than this = new normal, not a dip
    allow_variations: bool = False   # size/color variations share sales data -> unverifiable

    # --- deal scan (Keepa /deal) ---
    min_drop_percent: int = 25       # price must have dropped at least this much...
    deal_window: int = 1             # ...vs its average over: 0=last day (fresh drops), 1=week, 2=month, 3=90 days
    deal_price_types: list[int] = field(default_factory=lambda: [0, 1])  # 0=Amazon, 1=3rd-party new
    include_categories: list[int] = field(default_factory=list)       # Keepa root category ids; empty=all
    exclude_categories: list[int] = field(default_factory=lambda: [7141123011])  # Clothing, Shoes & Jewelry
    max_products_per_scan: int = 100  # caps Keepa token spend (~1-3 tokens per product)
    use_buybox: bool = True          # +2 tokens/product, better resale estimate

    # --- cost model ---
    sales_tax_rate: float = 0.0      # tax you pay at the source, e.g. 0.07
    inbound_ship_per_lb: float = 0.60
    prep_cost_per_unit: float = 0.50
    storage_per_unit: float = 0.15
    default_referral_pct: float = 15.0
    resale_haircut: float = 0.05     # assume you sell 5% under the 90-day buy box

    # --- automation ---
    auto_scan: bool = True
    scan_interval_minutes: int = 30

    @classmethod
    def from_dict(cls, data: dict) -> "Settings":
        base = cls()
        known = {f.name: f for f in fields(cls)}
        for key, value in (data or {}).items():
            if key not in known:
                continue
            current = getattr(base, key)
            try:
                if isinstance(current, bool):
                    value = value if isinstance(value, bool) else str(value).lower() in ("1", "true", "yes", "on")
                elif isinstance(current, int):
                    value = int(value)
                elif isinstance(current, float):
                    value = float(value)
                elif isinstance(current, list):
                    if isinstance(value, str):
                        value = [v for v in value.replace(" ", "").split(",") if v]
                    value = [int(v) for v in value]
            except (TypeError, ValueError):
                continue
            setattr(base, key, value)
        return base

    def to_dict(self) -> dict:
        return asdict(self)
