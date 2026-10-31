"""Phase 2 offline tests (synthetic candles, no network).

    python tests/test_phase2.py        (or: python -m pytest tests)
"""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from lib import smc  # noqa: E402
from lib.config import load_config  # noqa: E402
from lib.risk import ContractSpec, build_plan, choose_targets, liquidation_price, plan_from_setup  # noqa: E402
from lib.score import compute_score  # noqa: E402

CFG = load_config()
SPEC = ContractSpec("TEST/USDT:USDT", contract_size=0.1, min_qty=1, qty_step=1, tick_size=0.01,
                    maker_fee=0.0, taker_fee=0.0002, mmr=0.004, max_leverage=200, source="test")


def frame(rows, tf="1h", end="2026-10-31 08:00", vol=None) -> pd.DataFrame:
    idx = pd.date_range(end=end, periods=len(rows), freq=tf, tz="UTC")
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx, dtype=float)
    df["volume"] = vol if vol is not None else 100.0
    df.attrs.update(symbol="TEST/USDT:USDT", tf=tf)
    return df


def zigzag(points, per_leg=5, wick=0.2):
    rows = []
    for a, b in zip(points[:-1], points[1:]):
        for k in range(per_leg):
            o = a + (b - a) * k / per_leg
            c = a + (b - a) * (k + 1) / per_leg
            rows.append((o, max(o, c) + wick, min(o, c) - wick, c))
    return rows


def model_a_frames():
    """1H: downtrend, sweep of the last swing low, displacement up with an FVG that breaks
    the last swing high (CHoCH), then a pullback toward the FVG. 4H: uptrend."""
    rows = zigzag([120, 110, 116, 106, 111, 106.5], per_leg=5)
    rows += [
        (106.5, 106.7, 105.0, 106.6),   # sweep: wick under the 105.8 swing low, close back above
        (106.6, 106.9, 106.4, 106.8),
        (106.8, 109.9, 106.7, 109.7),   # displacement
        (109.7, 112.0, 108.2, 111.8),   # displacement + CHoCH (close above 111.2) -> FVG 106.9-108.2
        (111.8, 112.1, 110.0, 110.3),
        (110.3, 110.6, 109.0, 109.3),   # pullback, FVG untouched
    ]
    vol = [100.0] * len(rows)
    vol[-4], vol[-3] = 320.0, 360.0
    df1 = frame(rows, vol=vol)
    df4 = frame(zigzag([80, 95, 88, 104, 97, 115, 108], per_leg=6), tf="4h")
    return df4, df1


# ------------------------------------------------------------------- SMC --

def test_ote_zone():
    df = frame(zigzag([100, 110, 105, 118, 112.5], per_leg=6))
    o = smc.ote_zone(df, length=3)
    assert o and o["direction"] == "bullish", o
    rng = o["high"] - o["low"]
    assert abs(o["top"] - (o["high"] - 0.62 * rng)) < 1e-6 and abs(o["bottom"] - (o["high"] - 0.79 * rng)) < 1e-6
    assert o["bottom"] < o["sweet"] < o["top"] and not o["invalid"]


def test_displacement_needs_volume():
    rows = [(100, 100.6, 99.6, 100.3)] * 25 + [(100.3, 103.2, 100.2, 103.0), (103, 106.1, 102.9, 105.9)]
    vol = [100.0] * 25 + [80.0, 400.0]
    fl = smc.displacement_flags(frame(rows, vol=vol))
    assert bool(fl["body_ok"].iloc[-2]) and not bool(fl["displacement"].iloc[-2])  # big body, low volume
    assert bool(fl["displacement"].iloc[-1])


def test_breaker_block():
    # bullish OB forms on the way up, then price collapses through it with a bearish CHoCH
    df = frame(zigzag([100, 108, 103, 112, 106, 96, 101], per_leg=5), vol=None)
    st = smc.market_structure(df, 3)
    obs = smc.order_blocks(df, 3, st)
    br = smc.breaker_blocks(df, 3, st, obs)
    assert any(b["direction"] == "bearish" for b in br), (obs, st["events"])


def test_session_and_asia():
    s = smc.session_info(pd.Timestamp("2026-10-30 08:30", tz="UTC"))
    assert s["in_killzone"] and s["killzone"] == "london"
    s = smc.session_info(pd.Timestamp("2026-10-30 11:00", tz="UTC"))
    assert not s["in_killzone"] and s["next_killzone"] == "new_york" and s["hours_to_next"] == 1.0
    rows = [(100, 101 + (i == 3) * 2, 99 - (i == 4) * 3, 100) for i in range(12)]
    ar = smc.asia_range(frame(rows, end="2026-10-30 11:00"))
    assert ar and ar["high"] == 103 and ar["low"] == 96 and ar["complete"]


def test_ltf_confirmation():
    poi = {"top": 101.0, "bottom": 99.0}
    rows = zigzag([106, 102, 104, 100, 102.5, 99.8, 104], per_leg=4)
    df15 = frame(rows, tf="15min")
    res = smc.ltf_confirmation(df15, "LONG", poi, length=2, lookback=40)
    assert res["confirmed"], res
    miss = smc.ltf_confirmation(df15, "LONG", {"top": 90, "bottom": 89}, length=2, lookback=40)
    assert not miss["confirmed"]


def test_classify_model_a():
    df4, df1 = model_a_frames()
    res = smc.classify_setup("LONG", df4, df1, None, length=3)
    assert res["model"] == "A", (res["reasons"], [c["model"] for c in res["candidates"]])
    assert res["poi"]["bottom"] >= 106.0 and res["poi"]["top"] <= 108.3, res["poi"]
    assert res["state"] == "waiting" and res["quality"]["displacement"]
    assert res["sl_ref"] == 105.0
    assert res["grade"] in ("A+", "A", "B")


def test_classify_model_c():
    df1 = frame(zigzag([100, 110, 105, 118, 112.5], per_leg=6))
    df4 = frame(zigzag([80, 95, 88, 104, 97, 115, 108], per_leg=6), tf="4h")
    res = smc.classify_setup("LONG", df4, df1, None, length=3)
    assert "C" in [c["model"] for c in res["candidates"]], res


# ------------------------------------------------------------------ risk --

def test_risk_math():
    p = build_plan("long", 150, 147, [156.5, 160, 166], SPEC, CFG)
    # risk $1: loss/unit = 3 + 150*0 + 147*0.0002 = 3.0294 -> 0.330 SOL -> 3 contracts (0.1 each)
    assert p["contracts"] == 3 and abs(p["qty"] - 0.3) < 1e-9
    assert p["leverage"] == 6 and abs(p["margin_usd"] - 7.5) < 1e-9          # ceil(45 / 8) = 6x
    assert abs(p["risk_usd"] - (0.3 * 3 + 0.3 * 147 * 0.0002)) < 1e-3
    liq = 150 * (1 - 1 / 6) / (1 - 0.0042)
    assert abs(p["liquidation"] - round(liq, 2)) < 0.011 and p["liq_ok"]
    assert abs(p["net_reward_usd"][0] - 0.3 * 6.5) < 1e-6                     # maker TP fee 0
    assert p["ok"] and p["rr"][0] == 2.17


def test_spec_from_mexc_market():
    from lib.risk import spec_from_market
    m = {"symbol": "SOL/USDT:USDT", "contractSize": 0.1, "maker": 0.0, "taker": 0.0002,
         "precision": {"amount": 1, "price": 0.001}, "limits": {"amount": {"min": 1}, "leverage": {"max": 200}},
         "info": {"maintenanceMarginRate": 0.004, "volUnit": 1, "priceUnit": 0.001}}
    s = spec_from_market(m, "mexc")
    assert (s.contract_size, s.qty_step, s.tick_size, s.mmr, s.max_leverage) == (0.1, 1.0, 0.001, 0.004, 200.0)
    assert s.source == "mexc live"


def test_liquidation_formula_short():
    liq = liquidation_price("short", 100, 10, 0.005, 0.0)
    assert abs(liq - 100 * 1.1 / 1.005) < 1e-9


def test_risk_rejects_and_caps():
    p = build_plan("long", 150, 147, [153, 160, 166], SPEC, CFG)               # TP1 = 1R
    assert not p["ok"] and any("TP1" in r for r in p["rejects"])
    tight = build_plan("long", 150, 149.85, [150.6, 151, 152], SPEC, CFG)      # 0.1% SL -> big notional
    assert tight["leverage"] <= 20 and tight["margin_usd"] <= 8 + 1e-9
    assert any("size cut" in n for n in tight["notes"]) or tight["risk_usd"] <= 1.0
    big = ContractSpec("X", contract_size=10, min_qty=1, qty_step=1, tick_size=0.01)
    r = build_plan("long", 150, 140, [175, 185, 200], big, CFG)
    assert not r["ok"] and any("minimum" in x for x in r["rejects"])


def test_choose_targets_and_blocker():
    levels = [{"name": "1H swing high", "price": 151, "strength": "minor"},
              {"name": "PDH", "price": 156.5, "strength": "key"},
              {"name": "4H range high", "price": 161, "strength": "htf"},
              {"name": "PWH", "price": 170, "strength": "key"}]
    t = choose_targets("long", 150, 147, levels, CFG)
    assert t["blocked_by"] is None and [x["name"] for x in t["tp"]] == ["PDH", "4H range high", "PWH"]
    blocked = choose_targets("long", 150, 147, [{"name": "PDH", "price": 152, "strength": "key"}] + levels, CFG)
    assert blocked["blocked_by"]["name"] == "PDH"


def test_plan_from_setup():
    setup = {"poi": {"top": 108.2, "bottom": 106.9, "mid": 107.55}, "state": "waiting", "sl_ref": 105.0,
             "ltf": {"confirmed": False}}
    levels = [{"name": "1H swing high", "price": 112.2, "strength": "minor"},
              {"name": "PWH", "price": 116.0, "strength": "key"},
              {"name": "4H range high", "price": 121.0, "strength": "htf"}]
    p = plan_from_setup("long", setup, levels, 1.0, SPEC, CFG, price=109.3)
    assert p["order"] == "limit" and p["entry"] == 107.55
    assert p["sl"] == round(105.0 - 0.3, 2)
    # 1H swing high 112.2 is only 1.63R (minor level, not a blocker) -> TP1 = PWH one tick early
    assert p["tps"][0] == 115.99 and p["ok"], p["rejects"]
    assert p["targets"][0]["name"] == "PWH"


# ----------------------------------------------------------------- score --

def _inputs(**kw):
    base = {"direction": "LONG", "bias": {"d1": {"trend": "bullish"}, "btc_for_direction": "supportive"},
            "setup": {"model": "A", "name": "x", "grade": "A+",
                      "quality": {"ltf_confirmed": True, "confluence": True},
                      "displacement": {"displacement": True}},
            "futures": {"verdict": "supportive", "liquidity": {"ok": True}},
            "session": {"in_killzone": True, "killzone": "london"}, "news": "clear",
            "stats": {"trades": 40, "win_rate": 55, "avg_r": 0.6}, "plan": {"ok": True, "rejects": []}}
    base.update(kw)
    return base


def test_score_perfect_and_breakdown():
    s = compute_score(_inputs(), CFG)
    assert s["total"] == 100 and s["decision"] == "TRADE"
    assert sum(p["weight"] for p in s["breakdown"]) == 100


def test_score_blocks():
    assert compute_score(_inputs(direction="NONE"), CFG)["decision"] == "NO TRADE"
    assert "news block" in compute_score(_inputs(news="block"), CFG)["blocks"]
    weak = compute_score(_inputs(setup={"model": "C", "grade": "B", "quality": {}, "displacement": {}},
                                 futures={"verdict": "against"}, session={}, news="pending", stats=None), CFG)
    assert weak["decision"] == "NO TRADE" and weak["total"] < 75
    rej = compute_score(_inputs(plan={"ok": False, "rejects": ["TP1 blocked"]}), CFG)
    assert rej["decision"] == "NO TRADE"


# --------------------------------------------------------------- journal --

def _journal_tmp(tmp: Path):
    from lib import journal
    journal.JOURNAL, journal.STATS = tmp / "trades.csv", tmp / "setup_stats.json"
    return journal


def test_journal_add_simulate_report():
    with tempfile.TemporaryDirectory() as d:
        journal = _journal_tmp(Path(d))
        now = datetime(2026, 10, 31, 6, 0, tzinfo=timezone.utc)
        rep = {"symbol": "SOL/USDT:USDT", "coin": "SOL", "decision": "TRADE", "direction": "LONG",
               "setup": {"model": "A", "grade": "A", "name": "Sweep"}, "score": {"total": 80},
               "plan": {"side": "long", "entry": 100, "sl": 98, "tps": [104, 106, 110], "leverage": 5,
                        "risk_usd": 1.0, "qty": 0.5, "order": "limit", "ok": True}}
        src = Path(d) / "rep.json"
        src.write_text(json.dumps(rep))
        row = journal.add(str(src), CFG, now)
        assert row["status"] == "planned" and row["setup_type"] == "A"
        # fill at 100, TP1 104 hit, then back to entry -> tp1 = 0.5 * 2R = 1R
        candles = frame([(101, 101.5, 99.8, 100.5), (100.5, 104.5, 100.4, 104), (104, 104.2, 99.9, 100.1)],
                        tf="15min", end="2026-10-31 06:45")
        res = journal.simulate(journal.load()[0], candles, CFG)
        assert res["status"] == "tp1" and res["exit"] == 100, res
        # SL and TP1 inside the same candle -> SL wins
        same = frame([(101, 101.5, 99.8, 100.5), (100.5, 104.5, 97.5, 101)], tf="15min", end="2026-10-31 06:30")
        assert journal.simulate(journal.load()[0], same, CFG)["status"] == "sl"
        # never filled before valid_until -> expired
        late = frame([(103, 103.5, 102, 103)] * 30, tf="1h", end="2026-11-01 12:00")
        assert journal.simulate(journal.load()[0], late, CFG)["status"] == "expired"

        changes = journal.autocheck(fetch=lambda sym, created: candles, cfg=CFG, now=now + timedelta(hours=1))
        assert changes and journal.load()[0]["status"] == "tp1"
        assert float(journal.load()[0]["r_multiple"]) == 1.0 and float(journal.load()[0]["pnl_usd"]) == 1.0
        journal.update(row["id"], "tp2", cfg=CFG, now=now)
        assert float(journal.load()[0]["r_multiple"]) == 1.0 + 0.25 * 3
        r = journal.report("all", CFG, now)
        assert r["closed"] == 1 and r["win_rate"] == 100.0 and "Win rate" in r["text"]
        stats = json.loads(journal.STATS.read_text())
        assert stats["setups"]["A"]["journal"]["trades"] == 1


def test_journal_no_trade_row():
    with tempfile.TemporaryDirectory() as d:
        journal = _journal_tmp(Path(d))
        rep = {"symbol": "ETH/USDT:USDT", "coin": "ETH", "decision": "NO TRADE", "direction": "NONE",
               "setup": {}, "score": {"total": 30, "blocks": ["HTF bias NONE"]}, "plan": None,
               "no_trade_reasons": ["4H range"]}
        src = Path(d) / "rep.json"
        src.write_text(json.dumps(rep))
        row = journal.add(str(src), CFG)
        assert row["status"] == "no_trade" and "4H range" in row["notes"]
        assert journal.report("day", CFG)["no_trade"] == 1


# ------------------------------------------------------------- deep scan --

def test_deep_scan_offline():
    from lib import chart, data, deep_scan, futures, risk
    from scanner import store

    df4, df1 = model_a_frames()
    d1 = frame(zigzag([60, 80, 70, 95, 85, 110], per_leg=10), tf="1D")
    m15 = frame(zigzag([111, 109.2, 110.3, 109.4, 110.0, 109.1, 109.6], per_leg=6), tf="15min")
    frames = {"1d": d1, "4h": df4, "1h": df1, "15m": m15}

    class FakeEx:
        id = "fake"

    saved = (data.get_exchange, data.get_frames, data.get_ohlcv, risk.get_spec, futures.fetch_all,
             chart.CHARTS, deep_scan.REPORTS, store.PENDING, store.STATE, store.HISTORY)
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        try:
            data.get_exchange = lambda *a, **k: FakeEx()
            data.get_frames = lambda sym, limits=None, ex=None: frames
            data.get_ohlcv = lambda sym, tf, limit=300, use_cache=True, ex=None: {"1h": df1, "4h": df4}[tf]
            risk.get_spec = lambda sym, ex=None: SPEC
            futures.fetch_all = lambda sym, cfg: {
                "funding": {"source": "fake", "current_pct": -0.03, "history_pct": []},
                "oi": {"source": "fake", "change_1h_pct": 1.2, "change_4h_pct": 2.5},
                "ls": {"source": "fake", "ratio": 0.9}, "ticker": {"quote_volume": 5e8, "spread_pct": 0.01},
                "price_1h_pct": 0.4, "price_4h_pct": 1.0}
            chart.CHARTS, deep_scan.REPORTS = d / "charts", d / "reports"
            store.PENDING, store.STATE, store.HISTORY = d / "pending.json", d / "state.json", d / "history"
            old = (pd.Timestamp("2026-10-31 08:00", tz="UTC") - pd.Timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
            store.save_pending([{"id": "x-SOL", "coin": "SOL", "symbol": "SOL/USDT:USDT", "direction_hint": "LONG",
                                 "triggers": ["sweep"], "light_score": 70, "created_at": old,
                                 "levels": {"sweep_level": 105.8, "sweep_wick": 105.0}}])
            now = datetime(2026, 10, 31, 8, 30, tzinfo=timezone.utc)
            rep = deep_scan.analyze_symbol("SOL/USDT:USDT", CFG, now=now)
            assert rep["alert"]["status"] in ("revalidated", "expired") and rep["alert"]["age_h"] > 3
            assert rep["bias"]["direction"] in ("LONG", "SHORT", "NONE")
            assert set(rep["charts"]) == {"1d", "4h", "1h", "15m"} and all(Path(p).exists() for p in rep["charts"].values())
            assert Path(rep["report_path"]).exists() and (d / "reports" / "SOL" / "latest.json").exists()
            assert rep["answer"].startswith(("🟢", "🔴", "❌ NO TRADE"))
            json.dumps(rep)  # fully JSON serialisable
            if rep["plan"]:
                assert rep["plan"]["sl"] < rep["plan"]["entry"] < rep["plan"]["tps"][0]
            fin = deep_scan.finalize(rep["report_path"], "block", "FOMC in 1h", CFG)
            assert fin["decision"] == "NO TRADE" and fin["news"] == "block"
            out = deep_scan.close_alerts("SOL", push=False)
            assert out["moved"] and out["moved"][0]["status"] in ("no_trade", "expired", "trade")
            assert store.load_pending() == []
        finally:
            (data.get_exchange, data.get_frames, data.get_ohlcv, risk.get_spec, futures.fetch_all,
             chart.CHARTS, deep_scan.REPORTS, store.PENDING, store.STATE, store.HISTORY) = saved


def test_futures_verdicts():
    from lib.futures import analyze_futures
    base = {"funding": {"source": "t", "current_pct": 0.08}, "oi": {"change_1h_pct": 2.0, "change_4h_pct": 3.0},
            "ls": {"ratio": 1.2}, "ticker": {"quote_volume": 1e9, "spread_pct": 0.01},
            "price_1h_pct": 0.5, "price_4h_pct": 1.5}
    r = analyze_futures("X/USDT:USDT", "long", CFG, data=base)
    assert r["verdict"] == "against" and r["funding"]["level"] == "extreme"
    r = analyze_futures("X/USDT:USDT", "short", CFG, data=base)
    assert r["verdict"] == "supportive"          # longs crowded -> squeeze fuel for shorts
    calm = dict(base, funding={"current_pct": 0.005})
    assert analyze_futures("X/USDT:USDT", "long", CFG, data=calm)["verdict"] == "supportive"  # new longs
    thin = dict(calm, ticker={"quote_volume": 1e6, "spread_pct": 0.5})
    assert analyze_futures("X/USDT:USDT", "long", CFG, data=thin)["verdict"] == "against"


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except Exception as e:
            failed += 1
            import traceback
            print(f"FAIL {name}: {type(e).__name__}: {str(e)[:400]}")
            traceback.print_exc(limit=3)
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
