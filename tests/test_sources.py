from arbitrage.config import Settings
from arbitrage.sources.matcher import match, pack_count, quantities
from arbitrage.sources.slickdeals import clean_title, model_numbers, skip_reason


def deal(title, store="Walmart", models=None, slug=""):
    m = PRICE_OF(title)
    return {"id": title, "title": title, "query": clean_title(title), "price": m, "free_shipping": True,
            "store": store, "url": "https://slickdeals.net/x", "feed": "t", "published": None,
            "models": models if models is not None else model_numbers(title, slug)}


def PRICE_OF(title):
    import re
    m = re.search(r"\$(\d+(?:\.\d+)?)", title)
    return float(m.group(1)) if m else None


def amz(title, brand, model=None):
    return {"asin": "B0TEST0001", "title": title, "brand": brand, "model": model}


def test_exact_model_match_and_refusal():
    d = deal("9-Pc. Craftsman 3/8\" Drive Shallow Socket Set $12", slug="craftsman socket set cmmt16881 11 98")
    assert d["models"] == {"CMMT16881"}
    wrong = [amz("CRAFTSMAN Socket Set, 11-Piece (CMMT12040)", "CRAFTSMAN", "CMMT12040")]
    assert match(d, wrong)[0] is None
    right = wrong + [amz("CRAFTSMAN Shallow Impact Socket Set, 9 Piece (CMMT16881)", "CRAFTSMAN", "CMMT16881")]
    assert match(d, right)[0]["model"] == "CMMT16881"


def test_pack_count_must_match():
    d = deal("2-Pk 18-Oz Dawn Platinum EZ-Squeeze Dish Soap $5.15")
    single = [amz("Dawn Platinum EZ-Squeeze Dish Soap, 18 oz", "Dawn")]
    twin = [amz("Dawn Platinum EZ-Squeeze Dish Soap, 18 oz, Pack of 2", "Dawn")]
    assert pack_count(d["query"]) == 2
    assert match(d, single)[0] is None
    assert match(d, twin)[0] is not None


def test_size_and_brand_must_match():
    d = deal("25,000mAh INIU 3-Port Mini Size Power Bank with 100W Output $30.60")
    assert "25000mah" in quantities(d["query"])
    other_size = [amz("INIU Power Bank 10000mAh 3-Port Mini, 100W", "INIU")]
    other_brand = [amz("Anker Power Bank 25000mAh 3-Port Mini Size, 100W Output", "Anker")]
    good = [amz("INIU Power Bank 25000mAh, 100W Output 3-Port Mini Size Portable Charger", "INIU")]
    assert match(d, other_size)[0] is None
    assert match(d, other_brand)[0] is None
    assert match(d, good)[0] is not None


def test_free_skips():
    s = Settings()
    assert skip_reason(deal("Refurbished: ASUS ROG Ally $780"), s) == "not new"
    assert skip_reason(deal("adidas Men's Ultraboost 5X Running Shoes (3 Colors) from $64"), s) is not None
    assert skip_reason(deal("2.8oz French's Onions: 3 for $2.80"), s) is not None
    assert skip_reason(deal("2-Pk Dawn Dish Soap $5.15 w/ S&S"), s) == "Amazon members-only price"
    assert skip_reason(deal("Victorinox Fibrox 10\" Chef's Knife $33.50"), s) is None
    assert skip_reason(deal("Victorinox Fibrox 10\" Chef's Knife $33.50", store="Amazon"), s) == "already an Amazon deal"
