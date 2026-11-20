"""Offline tests for lib/pro_checks.py (synthetic candles).

    python tests/test_pro.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from lib import pro_checks  # noqa: E402

END = pd.Timestamp("2026-11-20 16:00", tz="UTC")   # a Friday


def mk(freq, n, drift, climax=False):
    idx = pd.date_range(end=END, periods=n, freq=freq, tz="UTC")
    c = 100 * np.cumprod(1 + drift + 0.003 * np.sin(np.arange(n) / 3))
    df = pd.DataFrame({"open": np.r_[c[0], c[:-1]], "close": c}, index=idx)
    df["high"] = df[["open", "close"]].max(axis=1) * 1.002
    df["low"] = df[["open", "close"]].min(axis=1) * 0.998
    df["volume"] = 100.0
    if climax:
        df.iloc[-1, df.columns.get_loc("volume")] = 900
        df.iloc[-1, df.columns.get_loc("low")] = df["low"].iloc[-1] * 0.97
    return df


def frames(drift, climax=False):
    return {"1h": mk("1h", 300, drift, climax), "4h": mk("4h", 200, drift), "15m": mk("15min", 400, drift / 4)}


def test_capitulation_short_is_exhausted():
    fr = frames(-0.004, climax=True)
    r = pro_checks.analyze("short", fr, float(fr["1h"].close.iloc[-1]), now=END, coin="ZZZ")
    assert r["exhausted"], r
    assert any("climax" in w for w in r["warnings"]) and any("overextended" in w for w in r["warnings"]), r


def test_calm_trend_short_not_exhausted():
    fr = frames(-0.0003)
    r = pro_checks.analyze("short", fr, float(fr["1h"].close.iloc[-1]), now=END, coin="ZZZ")
    assert not r["exhausted"], r


def test_weekend_flag():
    fr = frames(-0.0003)
    r = pro_checks.analyze("short", fr, float(fr["1h"].close.iloc[-1]), now=END + pd.Timedelta(days=1), coin="ZZZ")
    assert any("weekend" in w for w in r["warnings"]), r


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"FAIL {t.__name__}: {e!r}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
