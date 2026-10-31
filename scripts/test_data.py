"""Data check: fetch the scan universe + 4H/1H candles for 3 coins and print them.

    python scripts/test_data.py
    python scripts/test_data.py --coins BTC,ETH,SOL
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from lib.config import fix_console, load_config  # noqa: E402
from lib import data  # noqa: E402


def main() -> int:
    fix_console()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--coins", help="3 coins to fetch candles for (default: top 3 of the universe)")
    a = ap.parse_args()
    cfg = load_config()
    pd.set_option("display.width", 140)

    t0 = time.time()
    uni = data.get_universe(cfg)
    print(f"\n✅ Universe on {data.exchange_id(cfg)}: {len(uni)} coins ({time.time() - t0:.1f}s)")
    for i, r in enumerate(uni, 1):
        sp = f"{r['spread_pct']:.3f}%" if r.get("spread_pct") is not None else "n/a"
        print(f"{i:>2}. {data.pretty(r['symbol']):<14} vol24h ${r['quote_volume'] / 1e6:>9,.1f}M  spread {sp:>7}")

    coins = [c.strip() for c in a.coins.split(",")] if a.coins else [r["coin"] for r in uni[:3]]
    for coin in coins[:3]:
        sym = data.to_symbol(coin)
        for tf in ("4h", "1h"):
            df = data.get_ohlcv(sym, tf, 300)
            print(f"\n✅ {sym} {tf}: {len(df)} closed candles, last {df.index[-1]:%Y-%m-%d %H:%M} UTC")
            print(df.tail(3).to_string())
    print(f"\nDone in {time.time() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
