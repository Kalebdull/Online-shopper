import json
from pathlib import Path

from arbitrage.config import Settings
from arbitrage.demo import DemoKeepa
from arbitrage.profit import evaluate, parse_product, resale_basis, time_weighted_median
from arbitrage.scanner import parse_leads_csv

SAMPLE = json.loads((Path(__file__).parent / "keepa_products_sample.json").read_text())


def proven_history(price=40.0, days=180, sales=20):
    import time
    now = time.time()
    hist = [[now - days * 86400, price]]
    events = [(now - (i + 8) * 86400, price) for i in range(sales)]
    return hist, events


def base_info(**kw):
    hist, events = proven_history()
    info = {"_full_history": hist, "_sale_events": events, "history_days": 180,
            "established_price": 40.0, "is_variation": False,"asin": "B000TEST01", "title": "Thing", "brand": "Acme", "weight_lb": 1.0,
            "fba_fee": 4.00, "referral_pct": 15.0, "sales_rank": 5000, "monthly_sold": 300,
            "offer_count": 5, "buybox_avg90": 40.0, "buybox_avg30": 40.0, "buybox_median90": 40.0,
            "amazon_price": 20.0, "amazon_bb_share": 10.0, "history": []}
    info.update(kw)
    return info


def test_profit_math():
    s = Settings(resale_haircut=0, inbound_ship_per_lb=0.5, prep_cost_per_unit=0.5, storage_per_unit=0.0)
    p = evaluate(base_info(), 20.0, s)
    # 40 - 6 referral - 4 fba - 0.5 inbound - 0.5 prep - 20 cost
    assert p["profit"] == 9.0
    assert p["roi"] == 0.45
    assert p["passes"] and p["verified"]
    assert "_full_history" not in p


def test_rejects_unprofitable_and_amazon_brand():
    p = evaluate(base_info(brand="Amazon"), 35.0, Settings())
    assert not p["passes"]
    assert any("Profit" in r for r in p["rejects"])
    assert any("Amazon-brand" in r for r in p["rejects"])


def test_resale_ignores_placeholder_spike():
    # Real Keepa pattern: 90-day avg inflated by a placeholder price.
    assert resale_basis(base_info(buybox_avg90=277.0, buybox_avg180=300.0, buybox_median90=500.0)) == 40.0


def test_rejects_unproven_spike():
    # Listed at $40 for a few days only; sales happened at $15. Looks great, isn't real.
    import time
    now = time.time()
    hist = [[now - 180 * 86400, 15.0], [now - 20 * 86400, 40.0], [now - 15 * 86400, 15.0]]
    events = [(now - d * 86400, 15.0) for d in range(30, 170, 5)]
    info = base_info(_full_history=hist, _sale_events=events, established_price=40.0)
    p = evaluate(info, 12.0, Settings())
    assert not p["passes"]
    assert any("sales at" in r for r in p["rejects"])
    assert any("only" in r for r in p["rejects"])


def test_rejects_variations_and_pricing_errors():
    p = evaluate(base_info(is_variation=True), 20.0, Settings())
    assert any("variation" in r for r in p["rejects"])
    p = evaluate(base_info(), 3.0, Settings(min_buy_price=1))
    assert any("pricing error" in r for r in p["rejects"])


def test_time_weighted_median():
    h = [[0, 10.0], [100, 1000.0], [101, 10.0]]
    assert time_weighted_median(h, now=200) == 10.0


def test_parses_real_keepa_products():
    for raw in SAMPLE:
        info = parse_product(raw)
        assert info["asin"] and info["title"]
        assert info["image"].startswith("https://m.media-amazon.com/")
        assert info["fba_fee"] and info["referral_pct"]
        assert "established_price" in info and "history_days" in info
        play = evaluate(info, info["amazon_price"], Settings())
        # none of these real "deals" is actually a good flip
        assert not play["passes"]


def test_demo_pipeline():
    k = DemoKeepa(seed=1)
    asins = [d["asin"] for d in k.deals({})][:20]
    plays = [evaluate(parse_product(p), parse_product(p)["amazon_price"], Settings()) for p in k.products(asins)]
    assert len(plays) == 20
    assert all(0 <= p["score"] <= 100 for p in plays)


def test_parse_leads_csv():
    rows = parse_leads_csv("UPC,Price,Store\n012345678905,$12.99,Walmart\n,5,x\n")
    assert rows == [{"code": "012345678905", "cost": "$12.99", "store": "Walmart", "url": ""}]


def test_rejects_sales_while_out_of_stock():
    # Jackie Brown pattern: rank keeps "selling" while no offer exists -> rank is another edition's.
    import time
    now = time.time()
    events = [(now - d * 86400, None if d % 3 == 0 else 40.0) for d in range(8, 170, 2)]
    p = evaluate(base_info(_sale_events=events, oos_sales_share=0.33), 20.0, Settings())
    assert any("out of stock" in r for r in p["rejects"])


def test_rejects_long_running_dip():
    # Textbook pattern: price fell 26 days ago and stayed there -> new normal, not a dip.
    import time
    now = time.time()
    hist = [[now - 180 * 86400, 40.0], [now - 26 * 86400, 17.0]]
    p = evaluate(base_info(_full_history=hist), 17.0, Settings())
    assert any("new normal" in r for r in p["rejects"])


def test_rejects_unstable_price():
    p = evaluate(base_info(price_swing=2.4), 20.0, Settings())
    assert any("swings" in r for r in p["rejects"])


def test_resale_capped_by_todays_buy_box():
    # Lencioni pattern: other sellers win the buy box at $25 today -> can't assume $40.
    p = evaluate(base_info(buybox_now=25.0), 12.0, Settings())
    assert p["sell_price"] <= 25.0


def test_apparel_counts_as_variation():
    from arbitrage.profit import parse_product
    raw = dict(SAMPLE[0], title="Some Shirt, Black, XX-Large", parentAsin=None, variations=None,
               categoryTree=[{"catId": 7141123011, "name": "Clothing, Shoes & Jewelry"}])
    assert parse_product(raw)["is_variation"]
