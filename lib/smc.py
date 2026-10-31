"""Smart Money Concepts detectors (first version).

Shared by the watchman scanner and the deep-scan skills, so results are
repeatable. Every function takes a closed-candle OHLCV DataFrame (oldest ->
newest, UTC DatetimeIndex) and returns plain dicts (JSON friendly).

No look-ahead: a swing at candle i is only *known* at candle i + length
(`confirmed_idx`), and structure breaks use candle closes.

CLI:
    python lib/smc.py structure --symbol SOL/USDT:USDT --tf 4h
    python lib/smc.py all --symbol SOL --tf 1h --limit 300 [--json]
    (commands: swings, structure, fvg, ob, liquidity, sweeps, pd, all)
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


# ----------------------------------------------------------------- summary --

def analyze(df: pd.DataFrame, length: int = 5, sweep_lookback: int = 3) -> dict:
    sw = find_swings(df, length)
    st = market_structure(df, length, sw)
    return {
        "symbol": df.attrs.get("symbol"),
        "tf": df.attrs.get("tf"),
        "last_time": _t(df.index[-1]),
        "price": _f(df["close"].iloc[-1]),
        "structure": {k: v for k, v in st.items() if k != "swings"},
        "swings": sw,
        "fvgs": fair_value_gaps(df),
        "order_blocks": order_blocks(df, length, st),
        "liquidity": liquidity_pools(df, length, sw),
        "sweeps": liquidity_sweeps(df, length, sweep_lookback, sw),
        "premium_discount": premium_discount(df, length, sw),
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


def _main() -> None:
    from lib.config import cfg_get, fix_console, load_config
    from lib.data import get_ohlcv, to_symbol

    fix_console()
    ap = argparse.ArgumentParser(description="SMC detectors")
    ap.add_argument("cmd", nargs="?", default="all",
                    choices=["swings", "structure", "fvg", "ob", "liquidity", "sweeps", "pd", "all"])
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--tf", default="1h", help="one or more, e.g. 4h,1h")
    ap.add_argument("--limit", type=int, default=300)
    ap.add_argument("--length", type=int, help="swing length (default: config scanner.swing_length)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    cfg = load_config()
    length = a.length or int(cfg_get(cfg, "scanner.swing_length", 5))
    for tf in [t.strip() for t in a.tf.split(",") if t.strip()]:
        df = get_ohlcv(to_symbol(a.symbol), tf, a.limit)
        res = analyze(df, length)
        if a.json:
            print(json.dumps(res, indent=1, default=str))
        else:
            _print_human(a.cmd, res, df)
            print()


if __name__ == "__main__":
    _main()
