"""Download historical MEXC USDT-M perpetual candles for the backtest.

    python backtest/history.py --coins config --from 2024-01-01            # whole universe + BTC
    python backtest/history.py --coins BTC,ETH,SOL --from 2024-01-01 --update

Saves data/history/<COIN>/<tf>.csv.gz (ts, open, high, low, close, volume — volume in base units,
closed candles only) and data/history/<COIN>/spec.json (live contract specs for lib/risk.py).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from dataclasses import asdict
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from lib import data
from lib.config import ROOT, load_config

log = logging.getLogger("history")
HIST = Path(os.environ.get("TA_HISTORY_DIR") or ROOT / "data" / "history")
TFS = ("1d", "4h", "1h", "15m")
COLS = ["ts", "open", "high", "low", "close", "volume"]


def coin_dir(coin: str) -> Path:
    return HIST / coin.upper()


def load(coin: str, tf: str) -> pd.DataFrame | None:
    p = coin_dir(coin) / f"{tf}.csv.gz"
    if not p.exists():
        return None
    df = pd.read_csv(p)
    df.index = pd.to_datetime(df["ts"].astype("int64"), unit="ms", utc=True)
    df.index.name = "time"
    df.attrs.update(symbol=data.to_symbol(coin), tf=tf)
    return df[["open", "high", "low", "close", "volume"]].astype(float)


def load_spec(coin: str) -> dict | None:
    p = coin_dir(coin) / "spec.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def fetch_range(ex, symbol: str, tf: str, since_ms: int, until_ms: int) -> pd.DataFrame:
    dur = ex.parse_timeframe(tf) * 1000
    limit = min(data.ohlcv_limit(ex), 1000)
    rows, cur, empty = [], since_ms, 0
    while cur < until_ms:
        batch = data.call(ex.fetch_ohlcv, symbol, tf, since=cur, limit=limit)
        if not batch:
            empty += 1
            if rows and empty >= 3:
                break  # data ended after real candles
            cur += limit * dur  # no data yet (before listing / beyond the exchange's history) — jump ahead
            continue
        empty = 0
        rows += batch
        nxt = int(batch[-1][0]) + dur
        if nxt <= cur:
            break
        cur = nxt
        time.sleep(ex.rateLimit / 1000)
    df = pd.DataFrame(rows, columns=COLS).drop_duplicates("ts").sort_values("ts")
    return df[df["ts"] + dur <= until_ms]


def download_coin(ex, coin: str, start: str, update: bool) -> dict:
    symbol = data.to_symbol(coin)
    d = coin_dir(coin)
    d.mkdir(parents=True, exist_ok=True)
    now = ex.milliseconds()
    since0 = int(pd.Timestamp(start, tz="UTC").timestamp() * 1000)
    m = ex.market(symbol)
    cs = float(m.get("contractSize") or 1)
    out = {"coin": coin, "symbol": symbol}
    for tf in TFS:
        p = d / f"{tf}.csv.gz"
        old = pd.read_csv(p) if (update and p.exists()) else None
        since = int(old["ts"].iloc[-1]) if old is not None and len(old) else since0
        new = fetch_range(ex, symbol, tf, since, now)
        if len(new):
            new["volume"] = new["volume"].astype(float) * cs
        df = pd.concat([old, new]) if old is not None else new
        df = df.drop_duplicates("ts", keep="last").sort_values("ts")
        df.to_csv(p, index=False, compression="gzip")
        out[tf] = len(df)
        if len(df):
            out[f"{tf}_from"] = f"{pd.to_datetime(df['ts'].iloc[0], unit='ms'):%Y-%m-%d}"
    try:
        from lib.risk import get_spec
        spec = get_spec(symbol, ex)
        (d / "spec.json").write_text(json.dumps(asdict(spec), indent=1), encoding="utf-8")
    except Exception as e:
        log.warning("spec for %s failed: %s", coin, e)
    return out


def universe_coins(cfg: dict) -> list[str]:
    coins = [u["coin"] for u in data.get_universe(cfg)]
    return ["BTC"] + [c for c in coins if c != "BTC"]


def main() -> int:
    from lib.config import fix_console

    fix_console()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(description="Download backtest history")
    ap.add_argument("--coins", default="config", help="'config' (universe + BTC) or e.g. BTC,ETH,SOL")
    ap.add_argument("--from", dest="start", default="2024-01-01")
    ap.add_argument("--update", action="store_true", help="append only the new candles")
    a = ap.parse_args()
    cfg = load_config()
    ex = data.get_exchange(cfg=cfg)
    data.markets(ex)
    coins = universe_coins(cfg) if a.coins == "config" else [c.strip().upper() for c in a.coins.split(",") if c.strip()]
    if "BTC" not in coins:
        coins.insert(0, "BTC")
    print(f"Downloading {len(coins)} coins from {a.start}: {', '.join(coins)}")
    (HIST).mkdir(parents=True, exist_ok=True)
    (HIST / "coins.json").write_text(json.dumps(coins), encoding="utf-8")
    for c in coins:
        t0 = time.time()
        try:
            r = download_coin(ex, c, a.start, a.update)
            print(f"  {c:8s} " + " ".join(f"{tf}={r.get(tf, 0)}" for tf in TFS)
                  + f"  (from {r.get('1h_from', '-')}, {time.time() - t0:.0f}s)", flush=True)
        except Exception as e:
            print(f"  {c:8s} FAILED: {e}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
