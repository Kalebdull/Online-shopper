"""CLI: python -m arbitrage [serve|scan|tokens]"""
import argparse
import json


def main() -> None:
    ap = argparse.ArgumentParser(prog="arbitrage")
    ap.add_argument("cmd", nargs="?", default="serve", choices=["serve", "scan", "tokens", "export", "rescore"])
    ap.add_argument("--out", default="data/export")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    a = ap.parse_args()
    if a.cmd == "serve":
        import uvicorn
        uvicorn.run("arbitrage.app:app", host=a.host, port=a.port)
        return
    from . import config
    from .db import DB
    from .scanner import Scanner
    sc = Scanner(DB(config.DB_PATH))
    if a.cmd == "export":
        from .export import export
        print(json.dumps(export(sc.db, a.out), indent=2))
        return
    if a.cmd == "rescore":
        print(json.dumps(sc.rescore(), indent=2))
        return
    if a.cmd == "tokens":
        print(json.dumps(sc.tokens(), indent=2))
    else:
        print(json.dumps(sc.run_deal_scan(), indent=2))
        for p in sc.db.plays(limit=10):
            print(f"{p['score']:5.1f}  {p['asin']}  buy ${p['buy_price']:.2f} -> ${p['sell_price']:.2f}  "
                  f"profit ${p['profit']:.2f} ({p['roi']:.0%})  {p['title'][:60]}")


if __name__ == "__main__":
    main()
