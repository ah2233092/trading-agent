"""Offline tests on synthetic candles (no network).

    python tests/test_smc.py        (or: python -m pytest tests)
"""
from __future__ import annotations

import sys
import tempfile
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from lib import smc  # noqa: E402
from lib.config import load_config, utc_now  # noqa: E402
from lib.indicators import atr, rsi, volume_ratio  # noqa: E402


def frame(rows, tf="1h", start="2026-10-01", vol=None) -> pd.DataFrame:
    idx = pd.date_range(start, periods=len(rows), freq=tf, tz="UTC")
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx, dtype=float)
    df["volume"] = vol if vol is not None else 100.0
    df.attrs.update(symbol="TEST/USDT:USDT", tf=tf)
    return df


def zigzag(points, per_leg=6, wick=0.2):
    rows = []
    for a, b in zip(points[:-1], points[1:]):
        for k in range(per_leg):
            o = a + (b - a) * k / per_leg
            c = a + (b - a) * (k + 1) / per_leg
            rows.append((o, max(o, c) + wick, min(o, c) - wick, c))
    return rows


def test_bullish_bos():
    df = frame(zigzag([100, 110, 105, 115, 110, 120, 116]))
    st = smc.market_structure(df, length=3)
    assert st["trend"] == "bullish", st["trend"]
    bos = [e for e in st["events"] if e["direction"] == "bullish"]
    assert bos and all(e["type"] == "BOS" for e in bos)
    assert abs(bos[0]["level"] - 110.2) < 1e-6  # swing high incl. wick


def test_bearish_then_choch():
    df = frame(zigzag([120, 110, 116, 104, 110, 98, 112, 118]))
    st = smc.market_structure(df, length=3)
    types = [(e["type"], e["direction"]) for e in st["events"]]
    assert ("BOS", "bearish") in types, types
    assert st["last_event"]["type"] == "CHoCH" and st["last_event"]["direction"] == "bullish", types
    assert st["trend"] == "bullish"


def test_fvg_and_fill():
    rows = [(100, 101, 99, 100.5), (100.5, 104.5, 100.4, 104.2), (104.2, 105, 103, 104.8),
            (104.8, 105.2, 104, 104.5), (104.5, 104.6, 101.8, 102)]
    df = frame([(100, 100.6, 99.4, 100)] * 20 + rows)
    gaps = [g for g in smc.fair_value_gaps(df) if g["direction"] == "bullish"]
    assert gaps, "bullish FVG not found"
    g = gaps[-1]
    assert (g["bottom"], g["top"]) == (101.0, 103.0), g
    assert g["status"] == "partial" and 55 <= g["fill_pct"] <= 65, g  # low 101.8 -> 60% filled


def test_sweep_of_swing_low():
    rows = zigzag([105, 100, 106, 103, 107, 104], per_leg=5)
    last_close = rows[-1][3]
    rows.append((last_close, last_close + 0.3, 99.0, 101.5))  # wick under swing low ~99.8, close back above
    df = frame(rows)
    sweeps = smc.liquidity_sweeps(df, length=3, lookback=1)
    bull = [s for s in sweeps if s["direction"] == "bullish"]
    assert bull, sweeps
    assert bull[0]["age"] == 0


def test_order_block_and_pd():
    df = frame(zigzag([100, 108, 103, 112, 106, 118, 110], per_leg=6))
    st = smc.market_structure(df, length=3)
    obs = smc.order_blocks(df, length=3, structure=st)
    assert any(o["direction"] == "bullish" for o in obs)
    pdz = smc.premium_discount(df, length=3)
    assert pdz and 0 <= pdz["pct"] <= 100


def test_key_levels():
    rows = zigzag([100, 104, 98, 103, 99, 105] * 20, per_leg=5)  # ~595h of data
    df = frame(rows[:400])
    k = smc.key_levels(df)
    for name in ("PDH", "PDL", "PWH", "PWL"):
        assert k[name] is not None, name
    assert k["PDH"] >= k["PDL"] and k["PWH"] >= k["PWL"]


def test_indicators():
    rng = np.random.default_rng(1)
    c = 100 + np.cumsum(rng.normal(0, 1, 300))
    df = frame([(x, x + 1, x - 1, x + 0.2) for x in c], vol=rng.uniform(50, 150, 300))
    assert atr(df).iloc[-1] > 0
    r = rsi(df["close"]).dropna()
    assert ((r >= 0) & (r <= 100)).all()
    assert volume_ratio(df).notna().sum() == 280


def test_score_random_walk_no_crash():
    from scanner.light_score import btc_context, score_coin
    cfg = load_config()
    rng = np.random.default_rng(7)
    for seed in range(5):
        rng = np.random.default_rng(seed)
        c1 = 50 + np.cumsum(rng.normal(0, 0.3, 400))
        o1 = np.r_[c1[0], c1[:-1]]
        rows1 = [(o, max(o, c) + abs(rng.normal(0, .2)), min(o, c) - abs(rng.normal(0, .2)), c) for o, c in zip(o1, c1)]
        df1 = frame(rows1, vol=rng.uniform(50, 150, 400))
        df4 = df1.resample("4h").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        df4.attrs.update(symbol="TEST/USDT:USDT", tf="4h")
        btc = btc_context(df1, df4, cfg)
        res = score_coin(df4, df1, btc, cfg)
        assert 0 <= res["score"] <= 100 and res["direction"] in ("LONG", "SHORT", "NONE")
        assert len(res["triggers"]) == len(res["trigger_labels"])


def test_btc_dump_blocks_longs():
    from scanner.light_score import score_coin
    cfg = load_config()
    rows = zigzag([105, 100, 106, 103, 107, 104], per_leg=5)
    lc = rows[-1][3]
    rows.append((lc, lc + 0.3, 99.0, 101.5))  # bullish sweep
    df1 = frame(rows, vol=[100.0] * (len(rows) - 1) + [400.0])
    df4 = frame(zigzag([100, 108, 103, 112, 106, 118, 110], per_leg=6), tf="4h")
    ok = score_coin(df4, df1, {"dump": False}, cfg)
    blocked = score_coin(df4, df1, {"dump": True}, cfg)
    assert "LONG" == ok["direction"] and "sweep" in ok["triggers"], ok["trigger_labels"]
    assert blocked["direction"] != "LONG" and blocked["btc_blocked"]


def test_store_cooldown_and_expiry():
    from scanner import store
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        store.PENDING, store.STATE, store.HISTORY = d / "pending.json", d / "state.json", d / "history"
        now = utc_now()
        old = {"id": "old-SOL", "symbol": "SOL/USDT:USDT", "created_at": (now - timedelta(hours=30)).strftime("%Y-%m-%dT%H:%M:%SZ")}
        store.save_pending([old])
        new = {"id": "new-ETH", "symbol": "ETH/USDT:USDT", "created_at": now.strftime("%Y-%m-%dT%H:%M:%SZ")}
        res = store.add_alerts([new], now, 6, 24)
        assert res == {"added": 1, "expired": 1, "changed": True}
        assert [a["id"] for a in store.load_pending()] == ["new-ETH"]
        assert store.in_cooldown(store.load_state(), "ETH/USDT:USDT", now + timedelta(hours=5), 6)
        assert not store.in_cooldown(store.load_state(), "ETH/USDT:USDT", now + timedelta(hours=7), 6)
        assert list(store.HISTORY.glob("*.json"))
        assert store.add_alerts([new], now, 6, 24)["changed"] is False  # duplicate id ignored


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except Exception as e:
            failed += 1
            print(f"FAIL {name}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
