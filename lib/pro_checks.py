"""Extra professional checks on top of the SMC/ICT setup (Nov 2026).

What a discretionary pro looks at before pulling the trigger, beyond structure and POIs:
  - VWAP: daily session VWAP and weekly anchored VWAP (institutional fair value) — trade with them
  - RSI 1H / 4H: room to move vs exhausted (oversold short / overbought long = late entry)
  - RSI divergence on 1H against the trade (momentum fading)
  - Overextension: distance from the 1H EMA20 in ATRs (chasing a move that already happened)
  - Volume climax: a huge-volume 1H candle with a rejection wick against the trade (capitulation)
  - Weekend: thin liquidity, more stop hunts
  - Correlation: another trade in the same direction is already open / planned

    python lib/pro_checks.py --symbol SOL --side short
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from lib import indicators as ind  # noqa: E402
from lib.config import cfg_get, load_config  # noqa: E402


def _vwap(df: pd.DataFrame, start: pd.Timestamp) -> float | None:
    d = df[df.index >= start]
    if d.empty or d["volume"].sum() <= 0:
        return None
    tp = (d["high"] + d["low"] + d["close"]) / 3
    return float((tp * d["volume"]).sum() / d["volume"].sum())


def _pivots(s: pd.Series, length: int, kind: str) -> list[int]:
    a = s.to_numpy()
    out = []
    for i in range(length, len(a) - length):
        w = a[i - length:i + length + 1]
        if (kind == "low" and a[i] == w.min()) or (kind == "high" and a[i] == w.max()):
            out.append(i)
    return out


def divergence(df1: pd.DataFrame, side: str, lookback: int = 60, length: int = 3) -> str | None:
    """Regular RSI divergence AGAINST the trade on the last two 1H pivots (None if none)."""
    d = df1.tail(lookback)
    r = ind.rsi(df1["close"]).tail(lookback)
    if side == "short":   # price lower low + RSI higher low = bullish divergence (bad for shorts)
        piv = _pivots(d["low"], length, "low")
        if len(piv) >= 2:
            a, b = piv[-2], piv[-1]
            if d["low"].iloc[b] < d["low"].iloc[a] and r.iloc[b] > r.iloc[a] + 2:
                return f"bullish RSI divergence (low {d['low'].iloc[b]:g} < {d['low'].iloc[a]:g}, RSI {r.iloc[b]:.0f} > {r.iloc[a]:.0f})"
    else:                 # price higher high + RSI lower high = bearish divergence (bad for longs)
        piv = _pivots(d["high"], length, "high")
        if len(piv) >= 2:
            a, b = piv[-2], piv[-1]
            if d["high"].iloc[b] > d["high"].iloc[a] and r.iloc[b] < r.iloc[a] - 2:
                return f"bearish RSI divergence (high {d['high'].iloc[b]:g} > {d['high'].iloc[a]:g}, RSI {r.iloc[b]:.0f} < {r.iloc[a]:.0f})"
    return None


def open_same_side(coin: str, side: str, now: pd.Timestamp, hours: float = 8) -> list[str]:
    """Other journal trades (planned / taken / tp1) in the same direction from the last `hours`."""
    try:
        from lib import journal
        rows = journal.load()
    except Exception:  # noqa: BLE001
        return []
    out = []
    for r in rows:
        if (r.get("status") or "") not in ("planned", "taken", "tp1") or (r.get("side") or "").lower() != side:
            continue
        if (r.get("coin") or "") == coin:
            continue
        try:
            t = pd.Timestamp(r.get("created_at"))
            t = t.tz_localize("UTC") if t.tzinfo is None else t
        except Exception:  # noqa: BLE001
            continue
        if now - t <= pd.Timedelta(hours=hours):
            out.append(r.get("coin"))
    return out


def analyze(side: str, frames: dict, price: float, cfg: dict | None = None,
            now: pd.Timestamp | None = None, coin: str | None = None) -> dict:
    cfg = cfg or load_config()
    side = (side or "").lower()
    pc = cfg_get(cfg, "pro_checks", {}) or {}
    now = now or pd.Timestamp.now(tz="UTC")
    h1, h4 = frames.get("1h"), frames.get("4h")
    m15 = frames.get("15m") if frames.get("15m") is not None else h1
    good, warn, info = [], [], {}
    if not side or h1 is None or len(h1) < 60:
        return {"ok": False, "good": [], "warnings": [], "info": {}, "fraction": 0.5, "exhausted": False}
    short = side == "short"

    # VWAP (UTC day session + weekly anchored from Monday 00:00 UTC)
    last = m15.index[-1]
    day0 = last.normalize()
    week0 = day0 - pd.Timedelta(days=day0.weekday())
    vd, vw = _vwap(m15, day0), _vwap(m15, week0)
    info.update(vwap_day=vd, vwap_week=vw)
    for name, v in (("daily VWAP", vd), ("weekly VWAP", vw)):
        if v is None:
            continue
        if (price < v) == short:
            good.append(f"price {'below' if short else 'above'} {name} {v:.6g}")
        else:
            warn.append(f"price {'above' if short else 'below'} {name} {v:.6g} (against)")

    # RSI room / exhaustion
    r1 = float(ind.rsi(h1["close"]).iloc[-1])
    r4 = float(ind.rsi(h4["close"]).iloc[-1]) if h4 is not None and len(h4) > 20 else None
    info.update(rsi_1h=round(r1, 1), rsi_4h=round(r4, 1) if r4 is not None else None)
    lo, hi = float(pc.get("rsi_oversold", 25)), float(pc.get("rsi_overbought", 75))
    if (short and r1 <= lo) or (not short and r1 >= hi):
        warn.append(f"1H RSI {r1:.0f} — already {'oversold' if short else 'overbought'} (late entry)")
    elif (short and r1 < 50) or (not short and r1 > 50):
        good.append(f"1H RSI {r1:.0f} with the trade, room left")
    if r4 is not None and ((short and r4 <= lo) or (not short and r4 >= hi)):
        warn.append(f"4H RSI {r4:.0f} — {'oversold' if short else 'overbought'} on 4H")

    div = divergence(h1, side)
    if div:
        warn.append(div)

    # Overextension from the 1H EMA20 (in ATRs)
    e20 = float(ind.ema(h1["close"], 20).iloc[-1])
    a1 = float(ind.atr(h1).iloc[-1]) or 1e-12
    ext = (e20 - price) / a1 if short else (price - e20) / a1
    info.update(ema20_1h=e20, ext_atr=round(ext, 2))
    if ext >= float(pc.get("max_ext_atr", 2.5)):
        warn.append(f"overextended: {ext:.1f} ATR {'below' if short else 'above'} 1H EMA20 (chasing)")

    # Volume climax with a rejection wick against the trade (last 3 closed 1H candles)
    vr = ind.volume_ratio(h1)
    for i in range(1, 4):
        c = h1.iloc[-i]
        rng = float(c["high"] - c["low"]) or 1e-12
        wick = (min(c["open"], c["close"]) - c["low"]) if short else (c["high"] - max(c["open"], c["close"]))
        if float(vr.iloc[-i]) >= float(pc.get("climax_volume_x", 3.0)) and wick / rng >= 0.4:
            warn.append(f"volume climax {float(vr.iloc[-i]):.1f}x with a {'lower' if short else 'upper'} wick "
                        f"({h1.index[-i]:%d %b %H:%M} UTC) — possible capitulation")
            break

    # Weekend liquidity
    if now.weekday() >= 5:
        warn.append("weekend — thin liquidity, more stop hunts")

    # Correlation with other open / planned trades
    same = open_same_side(coin or "", side, now)
    if same:
        warn.append(f"already {side} on {', '.join(same)} (correlated — pick one)")

    exhaustion_keys = ("oversold", "overbought", "divergence", "overextended", "climax")
    n_exh = sum(any(k in w for k in exhaustion_keys) for w in warn)
    frac = 0.5 + 0.25 * min(len(good), 2) - 0.25 * len(warn)
    return {"ok": True, "good": good, "warnings": warn, "info": info,
            "fraction": round(max(0.0, min(1.0, frac)), 3),
            "exhausted": n_exh >= int(pc.get("exhaustion_block_count", 2))}


def lines(pro: dict) -> list[str]:
    if not pro or not pro.get("ok"):
        return []
    out = [f"- Pro checks: ✅ {g}" for g in pro["good"]] + [f"- Pro checks: ⚠️ {w}" for w in pro["warnings"]]
    return out


def _main() -> None:
    from lib import data
    from lib.config import fix_console
    fix_console()
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--side", required=True, choices=["long", "short"])
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    sym = data.to_symbol(a.symbol)
    frames = data.get_frames(sym)
    price = float(frames["15m"]["close"].iloc[-1])
    res = analyze(a.side, frames, price, coin=sym.split("/")[0])
    print(json.dumps(res, indent=1, default=str) if a.json else "\n".join(lines(res)))


if __name__ == "__main__":
    _main()
