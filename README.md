# Flip Finder: Amazon arbitrage scanner + dashboard

Finds products you can buy cheap and resell on Amazon (FBA) at a profit, using your
Keepa account, and shows the best plays on a local dashboard.

## Quick start

```bash
pip install -r requirements.txt
cp .env.example .env        # then put your Keepa API key in .env
python -m arbitrage serve   # open http://127.0.0.1:8000
```

With no key in `.env`, the app runs on **demo data** so you can try the dashboard.
`.env` is gitignored, so never commit your key.

Other commands:

```bash
python -m arbitrage scan     # one deal scan from the terminal, prints the top 10
python -m arbitrage tokens   # check your Keepa token balance
python -m arbitrage rescore  # re-check cached scans under new settings (no tokens)
python -m arbitrage export   # write plays/scans as JSON (feeds the phone dashboard)
python -m pytest             # tests
```

## Where plays come from

1. **Keepa deals (automatic).** Every `scan_interval_minutes` (default 2h) it asks Keepa
   for products whose price dropped at least `min_drop_percent` below the 90-day
   average. It pulls full product data and runs each one through the profit model.
   Default source is Amazon's own price (Amazon-to-Amazon flips). Set
   "Buy from" to `1` to target 3rd-party sellers instead.
2. **Your retail leads (manual).** Found something at Walmart, Target, TJ Maxx or a
   clearance site? Paste `code,cost,store,url` rows in **Check retail leads**. The code
   can be a UPC, EAN or ASIN. It looks each one up on Amazon and tells you buy or pass.

## How profit is estimated (and why it's conservative)

- **Resale price = what it actually sold at.** Keepa's averages are often inflated by
  "regular" prices nobody pays (for example, Amazon shows $60 for months, then clears at $13).
  So the app finds each sales-rank drop in the last 90 days (≈ a sale) and takes the
  median buy-box price at those moments. It then uses the lowest of that, the 90-day
  median and the 30/90-day averages, minus a 5% haircut.
- **Fees:** Amazon referral % and FBA pick & pack fee straight from Keepa (estimated by
  weight if missing), plus inbound shipping per lb, prep, storage and sales tax.
  All costs are adjustable in **Settings**.
- **Score (0–100):** ROI, profit per unit, sales velocity, rank and competition,
  minus penalties for risk flags.

## Verification: only real flips get through

A deal only counts as **verified** when the Keepa history proves it. Each rule below
exists because a real scan produced a fake "play" that it catches:

| Rule (default) | Fake it catches |
|---|---|
| ≥ 4 sales at the resale price in 180 days | Inflated "regular" prices nobody ever paid |
| Held the resale price ≥ 40% of the time | A brief price spike used as the resale price |
| Low price is < 14 days old | A markdown that has become the new normal (textbook at $17 for 26 days) |
| Resale capped by today's buy box | Other sellers already at $50 while history says $104 |
| < 10% of "sales" while out of stock | Sales rank shared with another edition (import DVD) |
| Normal price swing ≤ 1.6x | A few sellers' wild asking prices, not a market |
| No size/color variations, no clothing | Sales data shared across the whole family |
| Drop < 85% | Pricing errors that get cancelled |

Each play shows its proof, for example "Sold 74x at $45+ in the last 180 days; held that
price 86% of the time". Rejected items keep the reason they failed (tick "Show rejected").
All rules are in **Settings**. After changing them, `python -m arbitrage rescore`
re-checks the last scans without spending Keepa tokens.

Expect few plays. Real flips are rare and sell out fast, so scans run every 30 minutes.

### Risk flags to read before buying
- **Amazon wins buy box X%**: you'll be competing with Amazon if it restocks.
- **Extreme drop**: possible pricing glitch, or Amazon liquidating. Check the Keepa chart.
- **Variation: sales data shared**: rank/sales are for the whole size/color family,
  so your exact size may sell much slower.
- **Few observed sales**: not enough history to know the real resale price.
- **Can I sell it?** link: brand/category gating isn't visible to Keepa. Check in
  Seller Central before buying.

## Keepa token use
About 5 tokens per deal page plus about 3 tokens per product (1 with "Use buy box data"
off). A default scan of 100 products costs about 320 tokens. Every 30 minutes that fits
a 20 tokens/minute refill. Tune
`max_products_per_scan` and the interval to fit your plan's refill rate.

## Layout
```
arbitrage/
  keepa.py     Keepa API client
  profit.py    parsing, resale estimate, fees, scoring, flags
  scanner.py   deal scan, lead checks, scheduler
  db.py        SQLite storage (data/arbitrage.db)
  app.py       FastAPI JSON API + dashboard
  demo.py      Keepa-shaped fake data for demo mode
  static/      dashboard (vanilla HTML/CSS/JS)
tests/
```
