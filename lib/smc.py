"""Smart Money Concepts detectors (first version).

Shared by the watchman scanner and the deep-scan skills, so results are
repeatable. Every function takes a closed-candle OHLCV DataFrame (oldest ->
newest, UTC DatetimeIndex) and returns plain dicts (JSON friendly).

No look-ahead: a swing at candle i is only *known* at candle i + length
(`confirmed_idx`), and structure breaks use candle closes.

CLI:
    python lib/smc.py structure --symbol SOL/USDT:USDT --tf 4h
    python lib/smc.py all --symbol SOL --tf 1h --limit 300 [--json]
    python lib/smc.py setup --symbol SOL --side long [--json]   # entry model A/B/C/D + grade
    python lib/smc.py killzones --symbol SOL                    # session + Asia range
    (commands: swings, structure, fvg, ob, liquidity, sweeps, pd, ote, breakers, killzones, setup, all)
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from lib.indicators import atr, candle_stats

DISPLACEMENT_MULT = 1.5   # body >= 1.5x average body of the previous 20 candles
EQ_TOLERANCE_PCT = 0.10   # equal highs/lows: within 0.10% of each other
MIN_FVG_ATR = 0.10        # ignore FVGs smaller than 0.1 ATR


def _t(ts) -> str:
    return pd.Timestamp(ts).strftime("%Y-%m-%dT%H:%M:%SZ")


def _f(x) -> float:
    return float(round(float(x), 10))


# ------------------------------------------------------------------ swings --

def find_swings(df: pd.DataFrame, length: int = 5) -> list[dict]:
    """Fractal swing highs/lows.

    Swing high at i: high[i] > every high in the `length` candles before it and
    >= every high in the `length` candles after it (lows mirrored).
    Labels: HH/LH for highs, HL/LL for lows (vs. the previous swing of its kind).
    """
    h, l = df["high"].to_numpy(), df["low"].to_numpy()
    n = len(df)
    out: list[dict] = []
    for i in range(length, n - length):
        if h[i] > h[i - length:i].max() and h[i] >= h[i + 1:i + length + 1].max():
            out.append({"kind": "high", "idx": i, "time": _t(df.index[i]), "price": _f(h[i]),
                        "confirmed_idx": i + length})
        if l[i] < l[i - length:i].min() and l[i] <= l[i + 1:i + length + 1].min():
            out.append({"kind": "low", "idx": i, "time": _t(df.index[i]), "price": _f(l[i]),
                        "confirmed_idx": i + length})
    last = {"high": None, "low": None}
    for s in out:
        prev = last[s["kind"]]
        if s["kind"] == "high":
            s["label"] = "H" if prev is None else ("HH" if s["price"] > prev else "LH")
        else:
            s["label"] = "L" if prev is None else ("HL" if s["price"] > prev else "LL")
        last[s["kind"]] = s["price"]
    return out


# --------------------------------------------------------------- structure --

def market_structure(df: pd.DataFrame, length: int = 5, swings: list[dict] | None = None) -> dict:
    """Close-based BOS / CHoCH.

    Walk candle by candle. The reference high/low is the latest *confirmed*
    swing. A close beyond it is a break: CHoCH if it goes against the current
    trend, BOS if it continues it (the very first break counts as BOS).
    """
    swings = find_swings(df, length) if swings is None else swings
    by_confirm: dict[int, list[dict]] = defaultdict(list)
    for s in swings:
        by_confirm[s["confirmed_idx"]].append(s)

    close = df["close"].to_numpy()
    trend = 0
    ref = {"high": None, "low": None}
    events: list[dict] = []
    for j in range(len(df)):
        for s in by_confirm.get(j, []):
            ref[s["kind"]] = dict(s, crossed=False)
        hi, lo = ref["high"], ref["low"]
        if hi and not hi["crossed"] and close[j] > hi["price"]:
            events.append(_break_event("bullish", "CHoCH" if trend == -1 else "BOS", hi, j, df))
            trend, hi["crossed"] = 1, True
        if lo and not lo["crossed"] and close[j] < lo["price"]:
            events.append(_break_event("bearish", "CHoCH" if trend == 1 else "BOS", lo, j, df))
            trend, lo["crossed"] = -1, True

    def last_of(kind):
        return next((e for e in reversed(events) if e["type"] == kind), None)

    return {
        "trend": {1: "bullish", -1: "bearish", 0: "range"}[trend],
        "events": events,
        "last_event": events[-1] if events else None,
        "last_bos": last_of("BOS"),
        "last_choch": last_of("CHoCH"),
        "swings": swings,
    }


def _break_event(direction: str, kind: str, swing: dict, j: int, df: pd.DataFrame) -> dict:
    return {
        "type": kind,
        "direction": direction,
        "level": swing["price"],
        "swing_idx": swing["idx"],
        "swing_time": swing["time"],
        "idx": j,
        "time": _t(df.index[j]),
        "close": _f(df["close"].iloc[j]),
    }


# --------------------------------------------------------- fair value gaps --

def fair_value_gaps(df: pd.DataFrame, min_size_atr: float = MIN_FVG_ATR) -> list[dict]:
    """3-candle imbalances with fill tracking.

    Bullish FVG at candle i: low[i] > high[i-2]  -> zone [high[i-2], low[i]].
    Bearish FVG at candle i: high[i] < low[i-2]  -> zone [high[i], low[i-2]].
    fill_pct = how deep later candles traded back into the gap (100 = filled).
    """
    h, l = df["high"].to_numpy(), df["low"].to_numpy()
    a = atr(df).to_numpy()
    n = len(df)
    out: list[dict] = []
    for i in range(2, n):
        min_size = (a[i] if not np.isnan(a[i]) else 0.0) * min_size_atr
        for direction, top, bottom in (
            ("bullish", l[i], h[i - 2]) if l[i] > h[i - 2] else (None, 0, 0),
            ("bearish", l[i - 2], h[i]) if h[i] < l[i - 2] else (None, 0, 0),
        ):
            if direction is None or top - bottom <= min_size:
                continue
            size = top - bottom
            if i + 1 < n:
                if direction == "bullish":
                    deepest = l[i + 1:].min()
                    fill = (top - deepest) / size
                else:
                    deepest = h[i + 1:].max()
                    fill = (deepest - bottom) / size
            else:
                fill = 0.0
            fill = float(min(max(fill, 0.0), 1.0))
            out.append({
                "kind": "FVG",
                "direction": direction,
                "top": _f(top),
                "bottom": _f(bottom),
                "mid": _f((top + bottom) / 2),
                "idx": i,
                "time": _t(df.index[i - 1]),  # time of the middle (displacement) candle
                "fill_pct": round(fill * 100, 1),
                "status": "filled" if fill >= 1 else ("partial" if fill > 0 else "open"),
            })
    return out


# ------------------------------------------------------------ order blocks --

def order_blocks(df: pd.DataFrame, length: int = 5, structure: dict | None = None,
                 disp_mult: float = DISPLACEMENT_MULT) -> list[dict]:
    """Order blocks that caused displacement + a structure break.

    For every bullish BOS/CHoCH: find the lowest low between the broken swing
    and the break (origin of the impulse leg); the OB is the last bearish
    candle at/just before that low. The leg must show displacement (a big-body
    bullish candle or an FVG). Bearish OBs mirrored.

    status: fresh (price never came back), tapped (wick entered the zone),
            broken (candle closed through the far side).
    """
    st = structure or market_structure(df, length)
    o, h, l, c = (df[k].to_numpy() for k in ("open", "high", "low", "close"))
    stats = candle_stats(df)
    body_ratio = stats["body_ratio"].fillna(0).to_numpy()
    n = len(df)
    seen: set[tuple[str, int]] = set()
    out: list[dict] = []

    for ev in st["events"]:
        s, j = ev["swing_idx"], ev["idx"]
        bull = ev["direction"] == "bullish"
        seg = l[s:j + 1] if bull else h[s:j + 1]
        m = s + int(np.argmin(seg) if bull else np.argmax(seg))
        k = m
        for kk in range(m, max(s, m - length) - 1, -1):
            if (c[kk] < o[kk]) if bull else (c[kk] > o[kk]):
                k = kk
                break
        key = (ev["direction"], k)
        if key in seen:
            continue
        seen.add(key)

        leg = range(k + 1, j + 1)
        displacement = any(body_ratio[x] >= disp_mult and ((c[x] > o[x]) if bull else (c[x] < o[x]))
                           for x in leg)
        fvg = any((l[x] > h[x - 2]) if bull else (h[x] < l[x - 2]) for x in leg if x - 2 >= k)
        if not (displacement or fvg):
            continue

        top, bottom = float(h[k]), float(l[k])
        status, touch_idx, broken_idx = "fresh", None, None
        for x in range(j + 1, n):
            if bull:
                if c[x] < bottom:
                    status, broken_idx = "broken", x
                    break
                if touch_idx is None and l[x] <= top:
                    status, touch_idx = "tapped", x
            else:
                if c[x] > top:
                    status, broken_idx = "broken", x
                    break
                if touch_idx is None and h[x] >= bottom:
                    status, touch_idx = "tapped", x
        out.append({
            "kind": "OB",
            "direction": ev["direction"],
            "top": _f(top),
            "bottom": _f(bottom),
            "mid": _f((top + bottom) / 2),
            "idx": k,
            "time": _t(df.index[k]),
            "break_type": ev["type"],
            "break_idx": j,
            "break_time": ev["time"],
            "displacement": bool(displacement),
            "has_fvg": bool(fvg),
            "status": status,
            "first_touch_idx": touch_idx,
            "broken_idx": broken_idx,
        })
    out.sort(key=lambda z: z["idx"])
    return out


# --------------------------------------------------------------- liquidity --

def key_levels(df: pd.DataFrame) -> dict:
    """PDH/PDL (previous UTC day) and PWH/PWL (previous week, Monday 00:00 UTC).

    Needs an intraday frame (1H/4H). A level is None when the frame does not
    cover the whole period.
    """
    if len(df) == 0:
        return {}
    last = df.index[-1]
    today = last.normalize()
    week_start = today - pd.Timedelta(days=today.weekday())
    first = df.index[0]
    levels: dict = {"PDH": None, "PDL": None, "PWH": None, "PWL": None}

    prev_day_start = today - pd.Timedelta(days=1)
    if first <= prev_day_start:
        d = df[(df.index >= prev_day_start) & (df.index < today)]
        if len(d):
            levels["PDH"], levels["PDL"] = _f(d["high"].max()), _f(d["low"].min())
    prev_week_start = week_start - pd.Timedelta(days=7)
    if first <= prev_week_start:
        w = df[(df.index >= prev_week_start) & (df.index < week_start)]
        if len(w):
            levels["PWH"], levels["PWL"] = _f(w["high"].max()), _f(w["low"].min())
    levels["day_start"] = _t(today)
    levels["week_start"] = _t(week_start)
    return levels


def equal_levels(df: pd.DataFrame, swings: list[dict], tol_pct: float = EQ_TOLERANCE_PCT,
                 max_swings: int = 12) -> list[dict]:
    """Equal highs (EQH) / equal lows (EQL) among recent swings, with swept flag."""
    h, l = df["high"].to_numpy(), df["low"].to_numpy()
    out = []
    for kind, label in (("high", "EQH"), ("low", "EQL")):
        pts = [s for s in swings if s["kind"] == kind][-max_swings:]
        used: set[int] = set()
        for a_i in range(len(pts) - 1, -1, -1):
            a = pts[a_i]
            if a["idx"] in used:
                continue
            group = [a]
            for b in pts[:a_i][::-1]:
                if b["idx"] not in used and abs(b["price"] - a["price"]) / a["price"] * 100 <= tol_pct:
                    group.append(b)
            if len(group) < 2:
                continue
            used.update(g["idx"] for g in group)
            level = max(g["price"] for g in group) if kind == "high" else min(g["price"] for g in group)
            after = max(g["idx"] for g in group) + 1
            swept = bool((h[after:] > level).any()) if kind == "high" else bool((l[after:] < level).any())
            out.append({"kind": label, "price": _f(level), "count": len(group),
                        "times": sorted(g["time"] for g in group), "last_idx": after - 1, "swept": swept})
    return out


def liquidity_pools(df: pd.DataFrame, length: int = 5, swings: list[dict] | None = None) -> dict:
    """Buy-side (above) and sell-side (below) liquidity relative to the last close."""
    swings = find_swings(df, length) if swings is None else swings
    h, l = df["high"].to_numpy(), df["low"].to_numpy()
    price = float(df["close"].iloc[-1])
    keys = key_levels(df)
    eq = equal_levels(df, swings)
    pools = []
    for name in ("PDH", "PWH", "PDL", "PWL"):
        if keys.get(name) is not None:
            pools.append({"name": name, "price": keys[name]})
    for e in eq:
        if not e["swept"]:
            pools.append({"name": e["kind"], "price": e["price"]})
    for s in swings[-20:]:
        later = h[s["idx"] + 1:] if s["kind"] == "high" else l[s["idx"] + 1:]
        intact = not (later > s["price"]).any() if s["kind"] == "high" else not (later < s["price"]).any()
        if intact:
            pools.append({"name": f"swing_{s['kind']}", "price": s["price"], "time": s["time"]})
    buy = sorted([p for p in pools if p["price"] > price], key=lambda p: p["price"])
    sell = sorted([p for p in pools if p["price"] < price], key=lambda p: -p["price"])
    return {"price": _f(price), "buy_side": buy, "sell_side": sell, "key_levels": keys, "equal_levels": eq}


def liquidity_sweeps(df: pd.DataFrame, length: int = 5, lookback: int = 3,
                     swings: list[dict] | None = None) -> list[dict]:
    """Sweeps in the last `lookback` closed candles.

    Sweep = wick through a liquidity level and close back inside.
    The level must still be untouched right up to the sweep candle.
    Levels: confirmed swing highs/lows, EQH/EQL, PDH/PDL/PWH/PWL.
    direction: bullish = sell-side swept (long idea), bearish = buy-side swept.
    """
    swings = find_swings(df, length) if swings is None else swings
    h, l, c = df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy()
    a = atr(df).to_numpy()
    n = len(df)
    keys = key_levels(df)
    idx_of = {t: i for i, t in enumerate(df.index)}
    day_i = idx_of.get(pd.Timestamp(keys["day_start"])) if keys.get("day_start") else None
    week_i = idx_of.get(pd.Timestamp(keys["week_start"])) if keys.get("week_start") else None
    eq = equal_levels(df, swings)
    out: list[dict] = []

    for k in range(max(n - lookback, 1), n):
        cands = []  # (name, side, price, start_idx_for_untouched_check, rank)
        for s in swings:
            if s["confirmed_idx"] < k:
                cands.append((f"swing_{s['kind']}", s["kind"], s["price"], s["idx"] + 1, 1))
        for e in eq:
            if e["last_idx"] + length < k:
                cands.append((e["kind"], "high" if e["kind"] == "EQH" else "low", e["price"], e["last_idx"] + 1, 2))
        for name, side, start in (("PDH", "high", day_i), ("PDL", "low", day_i),
                                  ("PWH", "high", week_i), ("PWL", "low", week_i)):
            if keys.get(name) is not None and start is not None:
                cands.append((name, side, keys[name], start, 3))

        best = {}
        for name, side, price, start, rank in cands:
            if start >= k:
                start = k
            if side == "high":
                if not (h[k] > price and c[k] < price):
                    continue
                if start < k and (h[start:k] > price).any():
                    continue  # already taken earlier
                direction = "bearish"
            else:
                if not (l[k] < price and c[k] > price):
                    continue
                if start < k and (l[start:k] < price).any():
                    continue
                direction = "bullish"
            depth = (h[k] - price) if side == "high" else (price - l[k])
            cur = best.get(direction)
            if cur is None or (rank, -abs(depth)) > (cur["rank"], -abs(cur["depth"])):
                best[direction] = {
                    "direction": direction,
                    "level_name": name,
                    "level": _f(price),
                    "idx": k,
                    "time": _t(df.index[k]),
                    "age": n - 1 - k,
                    "wick": _f(h[k] if side == "high" else l[k]),
                    "close": _f(c[k]),
                    "depth": _f(depth),
                    "depth_atr": round(float(depth / a[k]), 2) if a[k] and not np.isnan(a[k]) else None,
                    "rank": rank,
                }
        out.extend(best.values())
    out.sort(key=lambda z: (z["idx"], z["rank"]), reverse=True)
    return out


# -------------------------------------------------------- premium/discount --

def premium_discount(df: pd.DataFrame, length: int = 5, swings: list[dict] | None = None) -> dict | None:
    """Dealing range from the last confirmed swing high/low pair to now.

    pct = where the last close sits in the range (0 = range low, 100 = high).
    < 50 discount (longs preferred), > 50 premium (shorts preferred).
    """
    swings = find_swings(df, length) if swings is None else swings
    hs = [s for s in swings if s["kind"] == "high"]
    ls = [s for s in swings if s["kind"] == "low"]
    if not hs or not ls:
        return None
    start = min(hs[-1]["idx"], ls[-1]["idx"])
    seg = df.iloc[start:]
    hi, lo = float(seg["high"].max()), float(seg["low"].min())
    if hi <= lo:
        return None
    price = float(df["close"].iloc[-1])
    pct = (price - lo) / (hi - lo) * 100
    return {
        "range_high": _f(hi),
        "range_low": _f(lo),
        "equilibrium": _f((hi + lo) / 2),
        "price": _f(price),
        "pct": round(pct, 1),
        "zone": "premium" if pct > 50 else ("discount" if pct < 50 else "equilibrium"),
        "from_time": _t(df.index[start]),
    }


# ------------------------------------------------------------ displacement --

def displacement_flags(df: pd.DataFrame, body_mult: float = DISPLACEMENT_MULT, n: int = 20) -> pd.DataFrame:
    """Per candle: body >= body_mult x avg body (previous n) AND volume > avg volume (previous n)."""
    stats = candle_stats(df, n)
    vol_avg = df["volume"].rolling(n, min_periods=n).mean().shift(1)
    out = pd.DataFrame(index=df.index)
    out["direction"] = stats["direction"]
    out["body_ratio"] = stats["body_ratio"]
    out["vol_ratio"] = df["volume"] / vol_avg.replace(0, np.nan)
    out["body_ok"] = (stats["body_ratio"] >= body_mult).fillna(False)
    out["vol_ok"] = (df["volume"] > vol_avg).fillna(False)
    out["displacement"] = out["body_ok"] & out["vol_ok"]
    return out


def displacements(df: pd.DataFrame, lookback: int = 50, body_mult: float = DISPLACEMENT_MULT) -> list[dict]:
    """Displacement candles in the last `lookback` candles (newest last)."""
    fl = displacement_flags(df, body_mult)
    n = len(df)
    out = []
    for i in range(max(0, n - lookback), n):
        if fl["displacement"].iloc[i]:
            out.append({"idx": i, "time": _t(df.index[i]),
                        "direction": "bullish" if fl["direction"].iloc[i] > 0 else "bearish",
                        "body_ratio": round(float(fl["body_ratio"].iloc[i]), 2),
                        "vol_ratio": round(float(fl["vol_ratio"].iloc[i]), 2)})
    return out


def leg_has_displacement(df: pd.DataFrame, start: int, end: int, direction: str,
                         flags: pd.DataFrame | None = None) -> dict:
    """Does candles start..end (inclusive) contain a displacement candle in `direction`?"""
    fl = displacement_flags(df) if flags is None else flags
    want = 1 if direction in ("bullish", "LONG") else -1
    start, end = max(0, start), min(len(df) - 1, end)
    body = vol = False
    for i in range(start, end + 1):
        if fl["direction"].iloc[i] != want:
            continue
        if fl["displacement"].iloc[i]:
            return {"displacement": True, "body_only": False, "idx": i, "time": _t(df.index[i])}
        body = body or bool(fl["body_ok"].iloc[i])
        vol = vol or bool(fl["vol_ok"].iloc[i])
    return {"displacement": False, "body_only": body, "idx": None, "time": None}


# --------------------------------------------------------------------- OTE --

def impulse_leg(df: pd.DataFrame, length: int = 5, structure: dict | None = None) -> dict | None:
    """Last impulse leg: origin (extreme before the last BOS/CHoCH) -> extreme after the break."""
    st = structure or market_structure(df, length)
    ev = st.get("last_event")
    if not ev:
        return None
    h, l = df["high"].to_numpy(), df["low"].to_numpy()
    s, j = ev["swing_idx"], ev["idx"]
    if ev["direction"] == "bullish":
        m = s + int(np.argmin(l[s:j + 1]))
        e = j + int(np.argmax(h[j:]))
        lo, hi = float(l[m]), float(h[e])
    else:
        m = s + int(np.argmax(h[s:j + 1]))
        e = j + int(np.argmin(l[j:]))
        hi, lo = float(h[m]), float(l[e])
    return {"direction": ev["direction"], "break_type": ev["type"], "break_idx": j,
            "start_idx": m, "start_time": _t(df.index[m]), "end_idx": e, "end_time": _t(df.index[e]),
            "low": _f(lo), "high": _f(hi)}


def ote_zone(df: pd.DataFrame, length: int = 5, structure: dict | None = None,
             fib_lo: float = 0.62, fib_hi: float = 0.79) -> dict | None:
    """Optimal trade entry: 0.62-0.79 retracement of the last impulse leg (0.705 = sweet spot)."""
    leg = impulse_leg(df, length, structure)
    if not leg or leg["high"] <= leg["low"]:
        return None
    hi, lo = leg["high"], leg["low"]
    rng = hi - lo
    h, l = df["high"].to_numpy(), df["low"].to_numpy()
    price = float(df["close"].iloc[-1])
    after = slice(leg["end_idx"] + 1, len(df))
    if leg["direction"] == "bullish":
        top, bottom, sweet = hi - fib_lo * rng, hi - fib_hi * rng, hi - 0.705 * rng
        retrace = (hi - price) / rng * 100
        touched = bool((l[after] <= top).any())
        invalid = bool((l[after] < lo).any())
    else:
        bottom, top, sweet = lo + fib_lo * rng, lo + fib_hi * rng, lo + 0.705 * rng
        retrace = (price - lo) / rng * 100
        touched = bool((h[after] >= bottom).any())
        invalid = bool((h[after] > hi).any())
    return dict(leg, top=_f(top), bottom=_f(bottom), sweet=_f(sweet), price=_f(price),
                retrace_pct=round(retrace, 1), in_ote=bool(bottom <= price <= top),
                touched=touched, invalid=invalid)


# ----------------------------------------------------------------- breakers --

def breaker_blocks(df: pd.DataFrame, length: int = 5, structure: dict | None = None,
                   obs: list[dict] | None = None) -> list[dict]:
    """Failed order blocks that flipped after a CHoCH.

    A bullish OB that price closed through, with a bearish CHoCH after the OB
    formed, becomes a bearish breaker (resistance) — and vice versa.
    status: fresh / tapped (retested) / invalid (closed back through it).
    """
    st = structure or market_structure(df, length)
    obs = order_blocks(df, length, st) if obs is None else obs
    h, l, c = df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy()
    n = len(df)
    out = []
    for ob in obs:
        if ob["status"] != "broken":
            continue
        b = ob["broken_idx"]
        flip = "bearish" if ob["direction"] == "bullish" else "bullish"
        ch = next((e for e in st["events"] if e["type"] == "CHoCH" and e["direction"] == flip
                   and ob["break_idx"] < e["idx"] <= b + 2 * length), None)
        if not ch:
            continue
        top, bottom = ob["top"], ob["bottom"]
        status, touch = "fresh", None
        for x in range(max(b, ch["idx"]) + 1, n):
            if flip == "bearish":
                if c[x] > top:
                    status = "invalid"
                    break
                if touch is None and h[x] >= bottom:
                    status, touch = "tapped", x
            else:
                if c[x] < bottom:
                    status = "invalid"
                    break
                if touch is None and l[x] <= top:
                    status, touch = "tapped", x
        out.append({"kind": "breaker", "direction": flip, "top": top, "bottom": bottom,
                    "mid": _f((top + bottom) / 2), "idx": ob["idx"], "time": ob["time"],
                    "broken_idx": b, "choch_idx": ch["idx"], "choch_time": ch["time"],
                    "status": status, "first_touch_idx": touch})
    return out


# ---------------------------------------------------------------- sessions --

KILLZONES_UTC = {"asia": (0, 6), "london": (7, 10), "new_york": (12, 15)}


def _zones(cfg: dict | None) -> dict:
    z = ((cfg or {}).get("smc") or {}).get("killzones_utc") or {}
    out = dict(KILLZONES_UTC)
    out.update({k: tuple(v) for k, v in z.items()})
    return out


def session_info(now: pd.Timestamp | None = None, cfg: dict | None = None) -> dict:
    """Which ICT killzone is active (London / New York, UTC) and when the next one starts."""
    now = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now).tz_convert("UTC")
    zones = _zones(cfg)
    hour = now.hour + now.minute / 60
    active = next((k for k in ("london", "new_york") if zones[k][0] <= hour < zones[k][1]), None)
    starts = []
    for day in (0, 1):
        base = now.normalize() + pd.Timedelta(days=day)
        for k in ("london", "new_york"):
            t = base + pd.Timedelta(hours=zones[k][0])
            if t > now:
                starts.append((t, k))
    nxt_t, nxt = min(starts)
    return {"now_utc": _t(now), "in_killzone": active is not None, "killzone": active,
            "next_killzone": nxt, "next_start_utc": _t(nxt_t),
            "hours_to_next": round((nxt_t - now).total_seconds() / 3600, 2),
            "zones_utc": {k: list(v) for k, v in zones.items()}}


def asia_range(df: pd.DataFrame, cfg: dict | None = None) -> dict | None:
    """High/low of the latest Asia session (default 00:00-06:00 UTC) and whether it was swept."""
    if len(df) == 0:
        return None
    a0, a1 = _zones(cfg)["asia"]
    last = df.index[-1]
    day = last.normalize()
    start = day + pd.Timedelta(hours=a0)
    if last < start:
        day -= pd.Timedelta(days=1)
        start = day + pd.Timedelta(hours=a0)
    end = day + pd.Timedelta(hours=a1)
    seg = df[(df.index >= start) & (df.index < end)]
    if not len(seg):
        return None
    hi, lo = float(seg["high"].max()), float(seg["low"].min())
    after = df[df.index >= end]
    return {"start_utc": _t(start), "end_utc": _t(end), "high": _f(hi), "low": _f(lo),
            "complete": bool(last >= end - (df.index[1] - df.index[0] if len(df) > 1 else pd.Timedelta(0))),
            "swept_high": bool((after["high"] > hi).any()), "swept_low": bool((after["low"] < lo).any())}


# ------------------------------------------------------ 15m confirmation --

def ltf_confirmation(df15: pd.DataFrame, direction: str, poi: dict | None = None,
                     length: int = 3, lookback: int = 32) -> dict:
    """LTF CHoCH in the trade direction after price tapped the HTF POI.

    direction: LONG/SHORT. poi: {"top", "bottom"} (optional — without it any
    fresh CHoCH in the window counts). The CHoCH must come after the tap, and
    price must not have closed through the far side of the POI before it.
    """
    want = "bullish" if direction in ("LONG", "bullish") else "bearish"
    res = {"confirmed": False, "tap_idx": None, "tap_time": None, "choch": None, "reason": ""}
    if df15 is None or len(df15) < 3 * length + 5:
        res["reason"] = "not enough 15m data"
        return res
    st = market_structure(df15, length)
    h, l, c = df15["high"].to_numpy(), df15["low"].to_numpy(), df15["close"].to_numpy()
    n = len(df15)
    w0 = max(0, n - lookback)
    tap = w0
    if poi:
        top, bottom = poi["top"], poi["bottom"]
        tap = next((x for x in range(w0, n) if l[x] <= top and h[x] >= bottom), None)
        if tap is None:
            res["reason"] = "price has not tapped the POI on 15m"
            return res
        res.update(tap_idx=tap, tap_time=_t(df15.index[tap]))
    ch = next((e for e in reversed(st["events"]) if e["type"] == "CHoCH" and e["direction"] == want
               and e["idx"] > tap), None)
    if not ch:
        res["reason"] = "no 15m CHoCH yet after the tap"
        return res
    if poi:
        seg = c[tap:ch["idx"] + 1]
        failed = (seg < poi["bottom"]).any() if want == "bullish" else (seg > poi["top"]).any()
        if failed:
            res["reason"] = "price closed through the POI before the CHoCH"
            return res
    res.update(confirmed=True, choch=ch, reason=f"15m CHoCH at {ch['level']} ({ch['time']})")
    return res


# ------------------------------------------------------ setup classifier --

MODEL_NAMES = {
    "A": "Sweep + CHoCH + FVG/OB retest",
    "B": "HTF POI tap + 15m CHoCH",
    "C": "BOS continuation + OTE retrace",
    "D": "Breaker retest",
}


def _overlap(a: dict, b: dict) -> dict | None:
    top, bottom = min(a["top"], b["top"]), max(a["bottom"], b["bottom"])
    return {"top": top, "bottom": bottom} if top > bottom else None


def _zone(kind: str, z: dict, source: str) -> dict:
    return {"kind": kind, "top": _f(z["top"]), "bottom": _f(z["bottom"]),
            "mid": _f((z["top"] + z["bottom"]) / 2), "source": source, "time": z.get("time")}


def _state(price: float, poi: dict, want: str) -> str:
    """in_zone / waiting (price on the right side, retest pending) / failed (beyond the far side)."""
    if poi["bottom"] <= price <= poi["top"]:
        return "in_zone"
    if want == "bullish":
        return "waiting" if price > poi["top"] else "failed"
    return "waiting" if price < poi["bottom"] else "failed"


def classify_setup(direction: str, df4: pd.DataFrame, df1: pd.DataFrame, df15: pd.DataFrame | None = None,
                   length: int = 5, ltf_length: int = 3, cfg: dict | None = None) -> dict:
    """Pick the entry model for `direction` (LONG/SHORT) and grade it A+ / A / B / reject.

    A  1H sweep -> CHoCH/BOS -> FVG/OB formed in that leg, price at or above it (retest)
    B  price tapped a 4H OB/FVG recently + 15m CHoCH confirmation
    C  1H BOS continuation, price retracing into the OTE (0.62-0.79) of the impulse leg
    D  1H breaker block retest
    Quality points: 4H trend aligned, POI in discount/premium, displacement (body + volume),
    confluence (OB+FVG / OTE / 4H POI overlap), 15m confirmation. 4+ = A+, 3 = A, 2 = B, else reject.
    """
    sc = ((cfg or {}).get("smc") or {})
    lb_a = int(sc.get("setup_lookback_1h", 36))
    lb_tap = int(sc.get("poi_tap_lookback_1h", 8))
    lb_15 = int(sc.get("ltf_lookback_15m", 32))
    want = "bullish" if direction == "LONG" else "bearish"
    out = {"direction": direction, "model": None, "name": None, "grade": "reject", "poi": None,
           "sl_ref": None, "state": None, "quality": {}, "reasons": [], "candidates": [], "pending": []}
    if direction not in ("LONG", "SHORT"):
        out["reasons"].append("no direction from HTF bias")
        return out

    h1, l1 = df1["high"].to_numpy(), df1["low"].to_numpy()
    n1 = len(df1)
    price = float(df1["close"].iloc[-1])
    atr1 = float(atr(df1).iloc[-1])
    st4 = market_structure(df4, length)
    pd4 = premium_discount(df4, length, st4["swings"])
    sw1 = find_swings(df1, length)
    st1 = market_structure(df1, length, sw1)
    obs1 = order_blocks(df1, length, st1)
    fvgs1 = fair_value_gaps(df1)
    flags1 = displacement_flags(df1)
    n4 = len(df4)
    htf_pois = [_zone("4H OB", o, "4H") for o in order_blocks(df4, length, st4)
                if o["direction"] == want and o["status"] != "broken"]
    htf_pois += [_zone("4H FVG", g, "4H") for g in fair_value_gaps(df4)
                 if g["direction"] == want and g["status"] != "filled" and g["idx"] >= n4 - 120]
    ote = ote_zone(df1, length, st1)
    out.update(price=_f(price), atr_1h=_f(atr1), ote=ote, trend_4h=st4["trend"], trend_1h=st1["trend"],
               premium_discount_4h=pd4)
    cands = []

    # --- A: sweep -> CHoCH/BOS -> FVG/OB retest
    for sw in liquidity_sweeps(df1, length, lb_a, sw1):
        if sw["direction"] != want:
            continue
        ev = next((e for e in st1["events"] if e["direction"] == want and sw["idx"] < e["idx"] <= sw["idx"] + 24), None)
        if not ev:
            continue
        zones = [_zone("1H OB", o, "1H") for o in obs1 if o["direction"] == want and o["break_idx"] == ev["idx"]
                 and o["status"] != "broken" and o["idx"] >= sw["idx"] - length]
        gaps = [_zone("1H FVG", g, "1H") for g in fvgs1 if g["direction"] == want and g["status"] != "filled"
                and sw["idx"] < g["idx"] <= ev["idx"] + 1]
        if not zones and not gaps:
            continue
        poi, overlap = (zones or gaps)[0], False
        if zones and gaps:
            ov = _overlap(zones[0], gaps[0])
            if ov:
                poi, overlap = _zone("1H OB+FVG", dict(ov, time=zones[0]["time"]), "1H"), True
        state = _state(price, poi, want)
        if state == "failed":
            continue
        disp = leg_has_displacement(df1, sw["idx"], ev["idx"], want, flags1)
        cands.append({"model": "A", "poi": poi, "state": state, "overlap": overlap, "displacement": disp,
                      "sl_ref": sw["wick"], "detail": {"sweep": sw, "break": ev}})
        break

    # --- B: 4H POI tap + 15m CHoCH
    for poi in htf_pois:
        win = range(max(0, n1 - lb_tap), n1)
        tapped = any(l1[x] <= poi["top"] and h1[x] >= poi["bottom"] for x in win)
        if not tapped or _state(price, poi, want) == "failed":
            continue
        ltf = ltf_confirmation(df15, direction, poi, ltf_length, lb_15) if df15 is not None else \
            {"confirmed": False, "reason": "no 15m data"}
        if not ltf["confirmed"]:
            out["pending"].append(f"B: {poi['kind']} {poi['bottom']}–{poi['top']} tapped, waiting: {ltf['reason']}")
            continue
        seg_lo = slice(max(0, n1 - lb_tap), n1)
        ext = float(l1[seg_lo].min()) if want == "bullish" else float(h1[seg_lo].max())
        sl_ref = min(ext, poi["bottom"]) if want == "bullish" else max(ext, poi["top"])
        disp = leg_has_displacement(df15, ltf["tap_idx"], ltf["choch"]["idx"], want)
        cands.append({"model": "B", "poi": poi, "state": _state(price, poi, want), "overlap": False,
                      "displacement": disp, "sl_ref": sl_ref, "ltf": ltf, "detail": {"ltf": ltf}})
        break

    # --- C: BOS continuation + OTE retrace
    last = st1["last_event"]
    if ote and last and last["type"] == "BOS" and last["direction"] == want and st1["trend"] == want and not ote["invalid"]:
        poi = _zone("1H OTE", ote, "1H")
        state = _state(price, poi, want)
        if state != "failed":
            disp = leg_has_displacement(df1, ote["start_idx"], ote["end_idx"], want, flags1)
            cands.append({"model": "C", "poi": poi, "state": state, "overlap": False, "displacement": disp,
                          "sl_ref": ote["low"] if want == "bullish" else ote["high"], "detail": {"ote": ote}})

    # --- D: breaker retest
    for br in reversed(breaker_blocks(df1, length, st1, obs1)):
        if br["direction"] != want or br["status"] == "invalid":
            continue
        poi = _zone("1H breaker", br, "1H")
        state = _state(price, poi, want)
        dist = 0 if state == "in_zone" else min(abs(price - poi["top"]), abs(price - poi["bottom"]))
        if state == "failed" or dist > 2 * atr1:
            continue
        disp = leg_has_displacement(df1, br["broken_idx"], br["choch_idx"], want, flags1)
        sl_ref = br["bottom"] if want == "bullish" else br["top"]
        cands.append({"model": "D", "poi": poi, "state": state, "overlap": False, "displacement": disp,
                      "sl_ref": sl_ref, "detail": {"breaker": br}})
        break

    # --- grade every candidate
    for cd in cands:
        poi = cd["poi"]
        if cd["model"] != "B" and df15 is not None:
            cd["ltf"] = ltf_confirmation(df15, direction, poi, ltf_length, lb_15)
        ltf_ok = bool(cd.get("ltf", {}).get("confirmed"))
        pd_ok = bool(pd4 and ((want == "bullish" and poi["mid"] < pd4["equilibrium"]) or
                              (want == "bearish" and poi["mid"] > pd4["equilibrium"])))
        confl = cd["overlap"] or bool(ote and cd["model"] != "C" and _overlap(poi, ote)) or \
            any(_overlap(poi, z) for z in htf_pois if cd["model"] != "B")
        q = {"htf_aligned": st4["trend"] == want, "pd_ok": pd_ok,
             "displacement": bool(cd["displacement"]["displacement"]), "confluence": bool(confl),
             "ltf_confirmed": ltf_ok}
        pts = sum(q.values())
        cd["quality"] = q
        cd["grade"] = "A+" if pts >= 4 else "A" if pts == 3 else "B" if pts == 2 else "reject"
        dist = 0.0 if cd["state"] == "in_zone" else min(abs(price - poi["top"]), abs(price - poi["bottom"]))
        cd["distance_atr"] = round(dist / atr1, 2) if atr1 else None
        cd["name"] = MODEL_NAMES[cd["model"]]

    rank = {"A+": 0, "A": 1, "B": 2, "reject": 3}
    cands.sort(key=lambda z: (rank[z["grade"]], z["model"]))
    out["candidates"] = cands
    if not cands:
        out["reasons"].append("no A/B/C/D entry model found on 1H/15m for this direction")
        return out
    best = cands[0]
    out.update(model=best["model"], name=best["name"], grade=best["grade"], poi=best["poi"],
               sl_ref=_f(best["sl_ref"]), state=best["state"], quality=best["quality"],
               ltf=best.get("ltf"), displacement=best["displacement"], distance_atr=best["distance_atr"])
    q = best["quality"]
    out["reasons"] = [f"{k.replace('_', ' ')}: {'yes' if v else 'no'}" for k, v in q.items()]
    if best["grade"] == "reject":
        out["reasons"].insert(0, "setup quality too low (fewer than 2 quality points)")
    return out


# ----------------------------------------------------------------- summary --

def analyze(df: pd.DataFrame, length: int = 5, sweep_lookback: int = 3) -> dict:
    sw = find_swings(df, length)
    st = market_structure(df, length, sw)
    obs = order_blocks(df, length, st)
    return {
        "symbol": df.attrs.get("symbol"),
        "tf": df.attrs.get("tf"),
        "last_time": _t(df.index[-1]),
        "price": _f(df["close"].iloc[-1]),
        "structure": {k: v for k, v in st.items() if k != "swings"},
        "swings": sw,
        "fvgs": fair_value_gaps(df),
        "order_blocks": obs,
        "breakers": breaker_blocks(df, length, st, obs),
        "liquidity": liquidity_pools(df, length, sw),
        "sweeps": liquidity_sweeps(df, length, sweep_lookback, sw),
        "premium_discount": premium_discount(df, length, sw),
        "ote": ote_zone(df, length, st),
        "displacements": displacements(df, lookback=30),
        "asia_range": asia_range(df),
    }


# --------------------------------------------------------------------- CLI --

def _print_human(cmd: str, res: dict, df: pd.DataFrame) -> None:
    p = res["price"]
    print(f"{res['symbol']} {res['tf']} | last close {p} @ {res['last_time']} | candles {len(df)}")
    if cmd in ("swings", "all"):
        print("\nSwings (last 8):")
        for s in res["swings"][-8:]:
            print(f"  {s['time']}  {s['kind']:<4} {s['label']:<3} {s['price']}")
    if cmd in ("structure", "all"):
        st = res["structure"]
        print(f"\nStructure: trend {st['trend'].upper()}")
        for e in st["events"][-6:]:
            print(f"  {e['time']}  {e['type']:<5} {e['direction']:<8} level {e['level']} (close {e['close']})")
    if cmd in ("ob", "all"):
        print("\nOrder blocks (not broken, last 6):")
        for ob in [o for o in res["order_blocks"] if o["status"] != "broken"][-6:]:
            print(f"  {ob['time']}  {ob['direction']:<8} {ob['bottom']} – {ob['top']}  {ob['status']}"
                  f"  ({ob['break_type']}, disp={ob['displacement']}, fvg={ob['has_fvg']})")
    if cmd in ("fvg", "all"):
        print("\nFVGs (unfilled, last 6):")
        for g in [g for g in res["fvgs"] if g["status"] != "filled"][-6:]:
            print(f"  {g['time']}  {g['direction']:<8} {g['bottom']} – {g['top']}  fill {g['fill_pct']}%")
    if cmd in ("liquidity", "all"):
        lq = res["liquidity"]
        k = lq["key_levels"]
        print(f"\nKey levels: PDH {k.get('PDH')} PDL {k.get('PDL')} PWH {k.get('PWH')} PWL {k.get('PWL')}")
        print("  buy-side :", ", ".join(f"{x['name']} {x['price']}" for x in lq["buy_side"][:5]) or "-")
        print("  sell-side:", ", ".join(f"{x['name']} {x['price']}" for x in lq["sell_side"][:5]) or "-")
    if cmd in ("sweeps", "all"):
        print("\nRecent sweeps:")
        for s in res["sweeps"] or []:
            print(f"  {s['time']}  {s['direction']:<8} swept {s['level_name']} {s['level']} "
                  f"(wick {s['wick']}, close {s['close']}, {s['depth_atr']} ATR)")
        if not res["sweeps"]:
            print("  none")
    if cmd in ("pd", "all"):
        pdz = res["premium_discount"]
        if pdz:
            print(f"\nDealing range {pdz['range_low']} – {pdz['range_high']} (EQ {pdz['equilibrium']}): "
                  f"price in {pdz['zone'].upper()} ({pdz['pct']}%)")
    if cmd in ("ote", "all") and res.get("ote"):
        o = res["ote"]
        print(f"\nOTE ({o['direction']} leg {o['low']} – {o['high']}): zone {o['bottom']} – {o['top']} "
              f"(sweet {o['sweet']}), retrace {o['retrace_pct']}%, in OTE: {o['in_ote']}, invalid: {o['invalid']}")
    if cmd in ("breakers", "all"):
        print("\nBreaker blocks (not invalid):")
        live = [b for b in res.get("breakers", []) if b["status"] != "invalid"]
        for b in live[-4:]:
            print(f"  {b['time']}  {b['direction']:<8} {b['bottom']} – {b['top']}  {b['status']} (CHoCH {b['choch_time']})")
        if not live:
            print("  none")
    if cmd == "all":
        d = res.get("displacements") or []
        print(f"\nDisplacement candles (last 30): {len(d)}" +
              (f" — latest {d[-1]['direction']} {d[-1]['time']} body x{d[-1]['body_ratio']} vol x{d[-1]['vol_ratio']}" if d else ""))
        ar = res.get("asia_range")
        if ar:
            print(f"Asia range {ar['low']} – {ar['high']} (swept high {ar['swept_high']}, low {ar['swept_low']})")


def _print_setup(r: dict) -> None:
    print(f"Setup {r['direction']}: model {r['model'] or '-'} {r['name'] or ''} | grade {r['grade']}")
    if r.get("poi"):
        p = r["poi"]
        print(f"  POI {p['kind']} {p['bottom']} – {p['top']} (state {r['state']}, {r.get('distance_atr')} ATR away)")
        print(f"  SL reference {r['sl_ref']}")
    for x in r["reasons"]:
        print(f"  - {x}")
    for x in r.get("pending", []):
        print(f"  … {x}")
    for c in r.get("candidates", [])[1:]:
        print(f"  alt: {c['model']} {c['name']} grade {c['grade']} POI {c['poi']['bottom']} – {c['poi']['top']}")


def _main() -> None:
    from lib.config import cfg_get, fix_console, load_config
    from lib.data import get_ohlcv, to_symbol

    fix_console()
    ap = argparse.ArgumentParser(description="SMC detectors")
    ap.add_argument("cmd", nargs="?", default="all",
                    choices=["swings", "structure", "fvg", "ob", "liquidity", "sweeps", "pd", "ote",
                             "breakers", "killzones", "setup", "all"])
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--tf", default="1h", help="one or more, e.g. 4h,1h")
    ap.add_argument("--limit", type=int, default=300)
    ap.add_argument("--length", type=int, help="swing length (default: config scanner.swing_length)")
    ap.add_argument("--side", choices=["long", "short"], help="for `setup`: direction to classify")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    cfg = load_config()
    length = a.length or int(cfg_get(cfg, "scanner.swing_length", 5))
    sym = to_symbol(a.symbol)
    if a.cmd == "killzones":
        res = {"session": session_info(cfg=cfg), "asia_range": asia_range(get_ohlcv(sym, "1h", 48), cfg)}
        print(json.dumps(res, indent=1, default=str))
        return
    if a.cmd == "setup":
        if not a.side:
            ap.error("setup needs --side long|short")
        res = classify_setup(a.side.upper(), get_ohlcv(sym, "4h", 300), get_ohlcv(sym, "1h", 400),
                             get_ohlcv(sym, "15m", 300), length,
                             int(cfg_get(cfg, "smc.ltf_swing_length", 3)), cfg)
        print(json.dumps(res, indent=1, default=str)) if a.json else _print_setup(res)
        return
    for tf in [t.strip() for t in a.tf.split(",") if t.strip()]:
        df = get_ohlcv(sym, tf, a.limit)
        res = analyze(df, length)
        if a.json:
            print(json.dumps(res, indent=1, default=str))
        else:
            _print_human(a.cmd, res, df)
            print()


if __name__ == "__main__":
    _main()
