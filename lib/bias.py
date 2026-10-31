"""HTF bias (htf-bias skill): which direction is allowed on the 1H — LONG, SHORT or NONE.

Inputs: 1D + 4H structure, EMA 20/50/200 (supporting evidence), 4H dealing range
(premium/discount), PDH/PDL/PWH/PWL + monthly open, and the BTC filter.

CLI:
    python lib/bias.py --symbol SOL [--json]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from lib.config import cfg_get, load_config
from lib.indicators import ema
from lib.smc import key_levels, market_structure, premium_discount

BTC = "BTC/USDT:USDT"
HINT = {"bullish": "LONG", "bearish": "SHORT"}


def _r(x):
    return None if x is None or pd.isna(x) else float(round(float(x), 10))


def ema_state(df: pd.DataFrame) -> dict:
    """Price vs EMA 20/50/200, stack order and 5-candle slope (%)."""
    price = float(df["close"].iloc[-1])
    out = {"price": _r(price)}
    vals = {}
    for n in (20, 50, 200):
        e = ema(df["close"], n)
        v, prev = e.iloc[-1], e.iloc[-6] if len(e) > 6 else None
        vals[n] = v
        out[f"ema{n}"] = _r(v)
        out[f"above_ema{n}"] = None if pd.isna(v) else bool(price > v)
        out[f"slope{n}_pct"] = None if pd.isna(v) or prev is None or pd.isna(prev) else round((v / prev - 1) * 100, 3)
    if any(pd.isna(v) for v in vals.values()):
        out["stack"] = "n/a (not enough candles for EMA200)" if pd.isna(vals[200]) else "mixed"
    elif vals[20] > vals[50] > vals[200]:
        out["stack"] = "bullish"
    elif vals[20] < vals[50] < vals[200]:
        out["stack"] = "bearish"
    else:
        out["stack"] = "mixed"
    return out


def monthly_open(df: pd.DataFrame) -> float | None:
    """Open of the first candle of the current UTC month (from an intraday or daily frame)."""
    last = df.index[-1]
    start = last.normalize().replace(day=1)
    seg = df[df.index >= start]
    return _r(seg["open"].iloc[0]) if len(seg) and df.index[0] <= start else None


def btc_filter(btc1: pd.DataFrame, btc4: pd.DataFrame, cfg: dict, length: int = 5, fresh: int = 3) -> dict:
    """BTC context for alt trades. LONG is blocked during a BTC 1H dump or fresh bearish 1H break."""
    last = btc1.iloc[-1]
    chg = (last["close"] - last["open"]) / last["open"] * 100
    dump_pct = float(cfg_get(cfg, "btc_dump_pct", 2.0))
    st1, st4 = market_structure(btc1, length), market_structure(btc4, length)
    n = len(btc1)
    recent = [e for e in st1["events"] if e["idx"] >= n - fresh]
    fresh_bear = any(e["direction"] == "bearish" for e in recent)
    fresh_bull = any(e["direction"] == "bullish" for e in recent)
    dump, pump = chg <= -dump_pct, chg >= dump_pct

    def side(against: bool, want: str) -> str:
        if against:
            return "against"
        if st1["trend"] == want and st4["trend"] == want:
            return "supportive"
        return "neutral"

    return {
        "price": _r(last["close"]),
        "change_1h_pct": round(float(chg), 2),
        "dump": bool(dump), "pump": bool(pump),
        "trend_1h": st1["trend"], "trend_4h": st4["trend"],
        "fresh_bearish_break": fresh_bear, "fresh_bullish_break": fresh_bull,
        "for_long": side(dump or fresh_bear, "bullish"),
        "for_short": side(pump or fresh_bull, "bearish"),
    }


def analyze_bias(symbol: str, frames: dict, btc_frames: dict | None, cfg: dict | None = None) -> dict:
    """frames: {"1d", "4h", "1h"}; btc_frames: {"4h", "1h"} (None for BTC itself)."""
    cfg = cfg or load_config()
    length = int(cfg_get(cfg, "scanner.swing_length", 5))
    d1, h4 = frames["1d"], frames["4h"]
    st_d1, st_h4 = market_structure(d1, length), market_structure(h4, length)
    t1, t4 = st_d1["trend"], st_h4["trend"]
    pd4 = premium_discount(h4, length, st_h4["swings"])
    levels = key_levels(h4)
    levels["MO"] = monthly_open(h4)
    is_btc = symbol.startswith("BTC/")
    btc = None if is_btc or not btc_frames else btc_filter(btc_frames["1h"], btc_frames["4h"], cfg, length)

    reasons: list[str] = []
    strong_only = False
    if t4 == "range":
        direction = "NONE"
        reasons.append("4H has no clear structure (range)")
    elif t1 == t4:
        direction = HINT[t4]
        reasons.append(f"1D and 4H both {t4}")
    else:
        direction = HINT[t4]
        strong_only = True
        reasons.append(f"1D {t1} vs 4H {t4} — only {direction} from strong POIs")

    if btc and direction == "LONG" and btc["for_long"] == "against":
        why = f"BTC 1H {btc['change_1h_pct']:+.2f}%" if btc["dump"] else "fresh bearish BTC 1H break"
        reasons.append(f"BTC filter blocks alt longs ({why})")
        direction = "NONE"
    elif btc and direction == "SHORT" and btc["for_short"] == "against":
        reasons.append("BTC is pushing up — caution for alt shorts")

    pd_ok = None
    if pd4 and direction != "NONE":
        pd_ok = (direction == "LONG" and pd4["zone"] == "discount") or (direction == "SHORT" and pd4["zone"] == "premium")
        if not pd_ok:
            reasons.append(f"price in 4H {pd4['zone']} ({pd4['pct']}%) — wait for a "
                           f"{'discount' if direction == 'LONG' else 'premium'} POI, do not chase")

    e1, e4 = ema_state(d1), ema_state(h4)
    if direction != "NONE":
        want = "bullish" if direction == "LONG" else "bearish"
        agree = [tf for tf, e in (("1D", e1), ("4H", e4)) if e["stack"] == want]
        reasons.append(f"EMA stack {want} on {', '.join(agree)}" if agree else "EMAs do not confirm (structure wins)")

    btc_side = None
    if btc:
        btc_side = btc["for_long"] if direction == "LONG" else btc["for_short"] if direction == "SHORT" else \
            ("against" if btc["for_long"] == "against" else "neutral")

    def lv(name):
        v = levels.get(name)
        return "-" if v is None else f"{v:g}"

    lb1, lb4 = st_d1["last_bos"], st_h4["last_bos"]
    text = "\n".join([
        f"HTF BIAS: {direction}" + (" (strong POIs only)" if strong_only else ""),
        f"1D: {t1} — last BOS {lb1['level'] if lb1 else '-'}",
        f"4H: {t4} — range {pd4['range_low']:g}–{pd4['range_high']:g}, price in {pd4['zone']} ({pd4['pct']}%)"
        if pd4 else f"4H: {t4} — no dealing range yet",
        f"BTC: {btc_side or ('n/a (this is BTC)' if is_btc else 'n/a')}",
        f"Key levels: PDH {lv('PDH')}, PDL {lv('PDL')}, PWH {lv('PWH')}, PWL {lv('PWL')}, MO {lv('MO')}",
    ])
    return {
        "symbol": symbol,
        "direction": direction,
        "strong_poi_only": strong_only,
        "pd_ok": pd_ok,
        "reasons": reasons,
        "d1": {"trend": t1, "last_bos": lb1, "last_choch": st_d1["last_choch"], "ema": e1},
        "h4": {"trend": t4, "last_bos": lb4, "last_choch": st_h4["last_choch"], "ema": e4, "range": pd4},
        "btc": btc,
        "btc_for_direction": btc_side,
        "key_levels": levels,
        "text": text,
    }


def _main() -> None:
    from lib.config import fix_console
    from lib.data import get_ohlcv, to_symbol

    fix_console()
    ap = argparse.ArgumentParser(description="HTF bias")
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    sym = to_symbol(a.symbol)
    frames = {"1d": get_ohlcv(sym, "1d", 400), "4h": get_ohlcv(sym, "4h", 300), "1h": get_ohlcv(sym, "1h", 400)}
    btc = None if sym == BTC else {"4h": get_ohlcv(BTC, "4h", 300), "1h": get_ohlcv(BTC, "1h", 400)}
    res = analyze_bias(sym, frames, btc)
    print(json.dumps(res, indent=1, default=str) if a.json else res["text"] + "\n- " + "\n- ".join(res["reasons"]))


if __name__ == "__main__":
    _main()
