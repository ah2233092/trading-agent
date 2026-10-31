"""Technical indicators on OHLCV DataFrames (columns: open, high, low, close, volume).

CLI:
    python lib/indicators.py --symbol SOL/USDT:USDT --tf 1h --tail 5
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd


def ema(series: pd.Series, n: int) -> pd.Series:
    return series.ewm(span=n, adjust=False, min_periods=n).mean()


def sma(series: pd.Series, n: int) -> pd.Series:
    return series.rolling(n, min_periods=n).mean()


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    return pd.concat(
        [df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()],
        axis=1,
    ).max(axis=1)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    """Wilder's ATR."""
    return true_range(df).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def rsi(series: pd.Series, n: int = 14) -> pd.Series:
    """Wilder's RSI."""
    delta = series.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = gain / loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(100.0).where(loss.notna())


def volume_ma(df: pd.DataFrame, n: int = 20) -> pd.Series:
    """Average volume of the previous n candles (current candle excluded)."""
    return df["volume"].rolling(n, min_periods=n).mean().shift(1)


def volume_ratio(df: pd.DataFrame, n: int = 20) -> pd.Series:
    """Candle volume / average volume of the previous n candles."""
    return df["volume"] / volume_ma(df, n)


def candle_stats(df: pd.DataFrame, n: int = 20) -> pd.DataFrame:
    """Body / wick stats per candle.

    body, range, upper_wick, lower_wick, body_pct (body / range),
    direction (+1 bull, -1 bear, 0 doji), avg_body (mean body of previous n),
    body_ratio (body / avg_body) — >= 1.5 is a displacement candle.
    """
    o, h, l, c = df["open"], df["high"], df["low"], df["close"]
    body = (c - o).abs()
    rng = h - l
    out = pd.DataFrame(index=df.index)
    out["body"] = body
    out["range"] = rng
    out["upper_wick"] = h - np.maximum(o, c)
    out["lower_wick"] = np.minimum(o, c) - l
    out["body_pct"] = (body / rng.replace(0, np.nan)).fillna(0.0)
    out["direction"] = np.sign(c - o).astype(int)
    out["avg_body"] = body.rolling(n, min_periods=n).mean().shift(1)
    out["body_ratio"] = body / out["avg_body"].replace(0, np.nan)
    return out


def add_all(df: pd.DataFrame) -> pd.DataFrame:
    """Copy of df with the common indicators added (for quick looks / charts)."""
    out = df.copy()
    for n in (20, 50, 200):
        out[f"ema{n}"] = ema(out["close"], n)
    out["atr14"] = atr(out)
    out["rsi14"] = rsi(out["close"])
    out["vol_ma20"] = volume_ma(out)
    out["vol_ratio"] = volume_ratio(out)
    out["body_ratio"] = candle_stats(out)["body_ratio"]
    return out


if __name__ == "__main__":
    from lib.config import fix_console
    from lib.data import get_ohlcv, to_symbol

    fix_console()
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--tf", default="1h")
    ap.add_argument("--limit", type=int, default=300)
    ap.add_argument("--tail", type=int, default=5)
    a = ap.parse_args()
    d = add_all(get_ohlcv(to_symbol(a.symbol), a.tf, a.limit))
    pd.set_option("display.width", 200)
    print(d.tail(a.tail).round(4).to_string())
