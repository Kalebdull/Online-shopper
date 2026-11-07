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
off). A default scan of 60 products costs about 190 tokens. Tune
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
