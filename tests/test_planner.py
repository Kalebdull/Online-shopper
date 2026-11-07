import time

from arbitrage.config import Settings
from arbitrage.keepa import KEEPA_EPOCH_MINUTES
from arbitrage.planner import plan_quick_checks, prefilter


def deal(**kw):
    now_k = int(time.time() / 60) - KEEPA_EPOCH_MINUTES
    cur = [-1] * 35
    cur[0] = 1500
    avg = [-1] * 35
    avg[18] = 4500
    d = {"asin": "B000TEST01", "title": "Board Game Deluxe", "rootCat": 165793011, "parentAsin": None,
         "current": cur, "avg": [avg, avg, avg, avg], "currentSince": [now_k - 120] * 35,
         "salesRankDrops30": 20, "salesRankDrops90": 60, "salesRankDrops180": 120}
    d.update(kw)
    return d


def test_good_deal_is_candidate():
    score, why = prefilter(deal(), 0, Settings())
    assert why is None and score > 0


def test_free_rejections():
    s = Settings()
    assert prefilter(deal(parentAsin="B0PARENT01"), 0, s)[1] == "variation"
    assert prefilter(deal(title="Shirt, Black, XX-Large"), 0, s)[1] == "variation"
    assert prefilter(deal(title="Renewed Phone"), 0, s)[1] == "not new"
    assert prefilter(deal(title="Like-New Blink Outdoor 4 Camera"), 0, s)[1] == "not new"
    assert prefilter(deal(title="Blink Outdoor 4 Camera"), 0, s)[1] == "Amazon's own brand"
    assert prefilter(deal(salesRankDrops90=2, salesRankDrops180=2), 0, s)[1] == "too few sales"
    old = int(time.time() / 60) - KEEPA_EPOCH_MINUTES - 30 * 1440
    assert prefilter(deal(currentSince=[old] * 35), 0, s)[1] == "old drop"
    cur = [-1] * 35
    cur[0] = 3900
    assert prefilter(deal(current=cur), 0, s)[1] == "can't reach profit target"


def test_title_filters_dont_overreach():
    s = Settings()
    assert prefilter(deal(title="Brass Key Ring Organizer"), 0, s)[1] is None
    assert prefilter(deal(title="The Most Widely Used Kitchen Scale"), 0, s)[1] is None


def test_fresher_and_faster_selling_ranks_higher():
    s = Settings()
    now_k = int(time.time() / 60) - KEEPA_EPOCH_MINUTES
    fresh = prefilter(deal(), 0, s)[0]
    stale = prefilter(deal(currentSince=[now_k - 5 * 1440] * 35), 0, s)[0]
    slow = prefilter(deal(salesRankDrops90=16), 0, s)[0]
    assert fresh > stale and fresh > slow


def test_budget_leaves_room_for_full_checks():
    n = plan_quick_checks(1000, 10_000)
    assert n > 0 and n * 1 + 0.15 * n * 3 <= 1000
    assert plan_quick_checks(5, 100) == 0
