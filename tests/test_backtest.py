"""Backtest engine offline tests (synthetic candles, no network).

    python tests/test_backtest.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from backtest import engine  # noqa: E402
from lib.config import load_config  # noqa: E402
from lib.risk import ContractSpec  # noqa: E402

CFG = load_config()
CFG["backtest"] = {"slippage_pct": 0.0, "max_hold_hours": 168}
CFG["tp1_close_pct"] = 50
CFG["plan_valid_hours"] = 6
SPEC0 = ContractSpec("TEST/USDT:USDT", maker_fee=0.0, taker_fee=0.0)
SPEC_FEE = ContractSpec("TEST/USDT:USDT", maker_fee=0.0, taker_fee=0.001)
T0 = pd.Timestamp("2026-10-01 00:00", tz="UTC")


def frames_from(rows15: list[tuple], start=T0) -> engine.Frames:
    fr = engine.Frames("NONE", tfs=())
    idx = pd.date_range(start, periods=len(rows15), freq="15min", tz="UTC").as_unit("ns")
    df = pd.DataFrame(rows15, columns=["open", "high", "low", "close"], index=idx, dtype=float)
    df["volume"] = 1.0
    fr.full["15m"] = df
    fr.close_ns["15m"] = (df.index + engine.TF_DUR["15m"]).asi8
    h1 = df.resample("1h").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
    fr.full["1h"] = h1
    fr.close_ns["1h"] = (h1.index + engine.TF_DUR["1h"]).asi8
    h4 = df.resample("4h").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
    fr.full["4h"] = h4
    fr.close_ns["4h"] = (h4.index + engine.TF_DUR["4h"]).asi8
    return fr


def flat(p, n):
    return [(p, p + 0.05, p - 0.05, p)] * n


def plan(**kw):
    c = {"time": T0, "side": "long", "order": "limit", "entry": 100.0, "sl": 99.0, "tps": [102.0, 103.0, 104.0]}
    c.update(kw)
    return c


def test_no_lookahead_views():
    fr = frames_from(flat(100, 4 * 30))
    t = T0 + pd.Timedelta(hours=10)                      # 10:00 UTC
    v1 = fr.view("1h", t, 999)
    assert v1.index[-1] == T0 + pd.Timedelta(hours=9)    # 09:00 candle closed at 10:00
    v4 = fr.view("4h", t, 999)
    assert v4.index[-1] == T0 + pd.Timedelta(hours=4)    # 08:00 4H candle still open at 10:00
    v15 = fr.view("15m", t, 999)
    assert v15.index[-1] == T0 + pd.Timedelta(hours=9, minutes=45)
    fut = fr.after("15m", t)
    assert fut.index[0] == t                              # simulation starts with the next candle


def test_tp1_then_breakeven():
    rows = flat(100.5, 2) + [(100.5, 100.6, 99.9, 100.2)]      # fill at 100
    rows += [(100.2, 102.1, 100.1, 102.0)]                        # TP1 (50%)
    rows += [(102.0, 102.2, 99.9, 100.0)]                         # back to entry -> BE on the rest
    r = engine.simulate(plan(), frames_from(rows + flat(100, 4)), CFG, SPEC0)
    assert r["filled"] and r["outcome"] == "be", r
    assert abs(r["r"] - 1.0) < 1e-6, r                            # 0.5 * 2R


def test_sl_first_same_candle():
    rows = flat(100.5, 1) + [(100.5, 100.6, 99.9, 100.1)]       # fill
    rows += [(100.1, 102.5, 98.5, 101.0)]                         # SL and TP1 in one candle -> SL
    r = engine.simulate(plan(), frames_from(rows + flat(100, 4)), CFG, SPEC0)
    assert r["outcome"] == "sl" and abs(r["r"] + 1.0) < 1e-6, r


def test_all_targets():
    rows = flat(100.5, 1) + [(100.5, 100.6, 99.95, 100.2)]
    rows += [(100.2, 104.5, 100.1, 104.2)]                        # TP1, TP2, TP3 together
    r = engine.simulate(plan(), frames_from(rows + flat(104, 4)), CFG, SPEC0)
    assert r["outcome"] == "tp3", r
    assert abs(r["r"] - (0.5 * 2 + 0.25 * 3 + 0.25 * 4)) < 1e-6, r


def test_missed_and_expired():
    rows = flat(100.5, 1) + [(100.5, 102.3, 100.4, 102.1)] + flat(102, 40)   # TP1 before fill
    assert engine.simulate(plan(), frames_from(rows), CFG, SPEC0)["outcome"] == "missed"
    rows = flat(100.6, 60)                                        # never trades down to 100 within 6h
    r = engine.simulate(plan(), frames_from(rows), CFG, SPEC0)
    assert r["outcome"] == "expired" and not r["filled"], r


def test_short_and_fees():
    c = plan(side="short", entry=100.0, sl=101.0, tps=[98.0, 97.0, 96.0])
    rows = flat(99.5, 1) + [(99.5, 100.1, 99.4, 99.8)]           # fill at 100
    rows += [(99.8, 101.2, 99.7, 101.0)]                          # SL
    r = engine.simulate(c, frames_from(rows + flat(101, 4)), CFG, SPEC_FEE)
    # loss 1R + taker fee on the SL exit (101 * 0.001 = 0.101 per unit, dist 1)
    assert r["outcome"] == "sl" and abs(r["r"] + 1.101) < 1e-6, r


def test_stats_and_split():
    rows = []
    for k, (m, rr, outc) in enumerate([("A", 2.0, "tp1"), ("A", -1.0, "sl"), ("B", -1.0, "sl"),
                                       ("A", 1.0, "be"), ("B", 3.0, "tp3")]):
        rows.append({"time": T0 + pd.Timedelta(days=k), "model": m, "r": rr, "outcome": outc, "filled": True,
                     "plan_ok": True, "score": 80, "side": "long", "killzone": k % 2 == 0, "coin": "X",
                     "hours": 5.0})
    df = pd.DataFrame(rows)
    s = engine.summarize(df, T0 + pd.Timedelta(days=3), 75)
    a = s["setups"]["A"]["total"]
    assert a["trades"] == 3 and a["win_rate"] == 66.7 and abs(a["avg_r"] - 0.667) < 1e-3, a
    assert s["all"]["in_sample"]["trades"] == 3 and s["all"]["oos"]["trades"] == 2


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
