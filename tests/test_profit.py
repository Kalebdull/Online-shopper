import json
from pathlib import Path

from arbitrage.config import Settings
from arbitrage.demo import DemoKeepa
from arbitrage.profit import evaluate, parse_product, resale_basis, time_weighted_median
from arbitrage.scanner import parse_leads_csv

SAMPLE = json.loads((Path(__file__).parent / "keepa_products_sample.json").read_text())


def base_info(**kw):
    info = {"asin": "B000TEST01", "title": "Thing", "brand": "Acme", "weight_lb": 1.0,
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
    assert p["passes"]


def test_rejects_unprofitable_and_amazon_brand():
    p = evaluate(base_info(brand="Amazon"), 35.0, Settings())
    assert not p["passes"]
    assert any("Profit" in r for r in p["rejects"])
    assert any("Amazon-brand" in r for r in p["rejects"])


def test_resale_ignores_placeholder_spike():
    # Real Keepa pattern: 90-day avg inflated by a placeholder price.
    assert resale_basis(base_info(buybox_avg90=277.0, buybox_avg30=46.0, buybox_median90=500.0)) == 46.0


def test_time_weighted_median():
    h = [[0, 10.0], [100, 1000.0], [101, 10.0]]
    assert time_weighted_median(h, now=200) == 10.0


def test_parses_real_keepa_products():
    for raw in SAMPLE:
        info = parse_product(raw)
        assert info["asin"] and info["title"]
        assert info["image"].startswith("https://m.media-amazon.com/")
        assert info["fba_fee"] and info["referral_pct"]
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
