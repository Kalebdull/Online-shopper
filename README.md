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

1. **Keepa deals (automatic).** Every 30 minutes it asks Keepa for products whose price
   dropped at least 25% in the last week, from Amazon itself and from third-party sellers,
   skipping clothing. It pulls full product data and runs each one through the profit
   model and the verification rules below.
2. **Your retail leads (manual).** Found something at Walmart, Target, TJ Maxx or a
   clearance site? Paste `code,cost,store,url` rows in **Check retail leads**. The code
   can be a UPC, EAN or ASIN. It looks each one up on Amazon and tells you buy or pass.

## How profit is estimated (and why it's conservative)

- **Resale price** is the lowest of: the price the item sat at or above 75% of the time
  over 180 days (ignoring the current dip), its 90- and 180-day buy-box averages, the
  median price at real sales, and today's buy box when other sellers hold it above your
  cost. Then a 5% haircut. Keepa's averages alone are often inflated by "regular" prices
  nobody pays.
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
| Amazon holds the buy box ≤ 70% of the time | You'd rarely get the sale once Amazon restocks |
| Media must show Amazon's own "bought in past month" count | CD/DVD/book editions sharing one sales rank |

Each play shows its proof, for example "Sold 74x at $45+ in the last 180 days; held that
price 86% of the time". Rejected items keep the reason they failed (tick "Show rejected").
All rules are in **Settings**. After changing them, `python -m arbitrage rescore`
re-checks the last scans without spending Keepa tokens.

Expect few plays. Real flips are rare and sell out fast, so scans run every 30 minutes.

### Risk flags to read before buying
- **Amazon wins buy box X%**: you'll be competing with Amazon if it restocks.
- **Extreme drop**: possible pricing glitch, or Amazon liquidating. Check the Keepa chart.
- **Amazon's cheap offer isn't winning the buy box**: often means it's backordered.
- **Can I sell it?** link: brand/category gating isn't visible to Keepa. Check in
  Seller Central before buying.

## Keepa token use

Your plan adds tokens every minute (20/min on the entry plan). The bucket holds 60
minutes' worth, and **unused tokens expire after 60 minutes**, so the scanner spends
everything available on each run and runs every 15 minutes.

Each scan:
1. Pulls deal pages, 5 tokens per 150 deals, best sellers first, flip-friendly categories only.
2. Pre-filters them for free using the deal record: variations, clothing, renewed items,
   Amazon's own brands, slow sellers, weeks-old drops, likely pricing errors, and anything
   that can't reach your profit target even on a rough estimate.
3. Quick-checks the best candidates without buy box data (1 token each).
4. Fully checks only the survivors with buy box data (3 tokens each).

A live run scanned 1,950 deals for about 152 tokens. Checking the same deals directly
would have cost about 6,000.

## Layout
```
arbitrage/
  keepa.py     Keepa API client
  profit.py    parsing, resale estimate, fees, scoring, flags
  planner.py   token budget, free deal pre-filter and ranking
  scanner.py   deal scan, lead checks, scheduler
  db.py        SQLite storage (data/arbitrage.db)
  app.py       FastAPI JSON API + dashboard
  demo.py      Keepa-shaped fake data for demo mode
  static/      dashboard (vanilla HTML/CSS/JS)
tests/
```
