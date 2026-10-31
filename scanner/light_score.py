"""Watchman light score (0-100) for one coin.

Not a trade signal — only "a setup may be forming, come and look".

Triggers (each carries a direction hint; volume is neutral):
    sweep      fresh 1H liquidity sweep (wick through, close back inside)   35 (+5 key/EQ level, +3 last candle)
    structure  fresh 1H BOS/CHoCH aligned with 4H structure                30 (+5 CHoCH, +3 displacement close)
    near_poi   price within near_poi_pct of an unmitigated 4H OB/FVG or
               PDH/PDL/PWH/PWL                                             30 (+5 inside zone, +3 confluence)
    volume     last 1H volume >= volume_spike_mult x 20-period average     20 (+ up to 8 for bigger spikes)
    + 5 when the hint matches 4H premium/discount (long in discount, short in premium)
BTC filter: during a BTC 1H dump of btc_dump_pct, long triggers are ignored.
One trigger alone cannot reach the default min_alert_score (60); two good ones can.
"""
from __future__ import annotations

import pandas as pd

from lib.config import cfg_get
from lib.indicators import candle_stats, volume_ratio
from lib.smc import (fair_value_gaps, find_swings, key_levels, liquidity_sweeps,
                     market_structure, order_blocks, premium_discount)

FVG_MAX_AGE_4H = 120  # only FVGs from the last ~20 days of 4H candles
HINT = {"bullish": "LONG", "bearish": "SHORT"}


def btc_context(df1: pd.DataFrame, df4: pd.DataFrame, cfg: dict) -> dict:
    """BTC 1H move of the last closed candle + 1H/4H trend."""
    length = int(cfg_get(cfg, "scanner.swing_length", 5))
    last = df1.iloc[-1]
    chg = (last["close"] - last["open"]) / last["open"] * 100
    dump_pct = float(cfg_get(cfg, "btc_dump_pct", 2.0))
    return {
        "price": float(last["close"]),
        "change_1h_pct": round(float(chg), 2),
        "dump": bool(chg <= -dump_pct),
        "trend_1h": market_structure(df1, length)["trend"],
        "trend_4h": market_structure(df4, length)["trend"],
        "candle_time": f"{df1.index[-1]:%Y-%m-%dT%H:%M:%SZ}",
    }


def _poi_triggers(df4: pd.DataFrame, price: float, near_pct: float, length: int, st4: dict) -> list[dict]:
    n4 = len(df4)
    pois = []
    for ob in order_blocks(df4, length, st4):
        if ob["status"] == "broken":
            continue
        if ob["status"] == "tapped" and ob["first_touch_idx"] < n4 - 2:
            continue  # already used earlier — no longer "unmitigated"
        pois.append(("4H OB", ob["direction"], ob["top"], ob["bottom"], 3))
    for g in fair_value_gaps(df4):
        if g["status"] != "filled" and g["idx"] >= n4 - FVG_MAX_AGE_4H:
            pois.append(("4H FVG", g["direction"], g["top"], g["bottom"], 2))
    keys = key_levels(df4)
    for name in ("PDH", "PWH", "PDL", "PWL"):
        if keys.get(name) is not None:
            d = "bearish" if name.endswith("H") else "bullish"
            pois.append((name, d, keys[name], keys[name], 1))

    near: dict[str, list[tuple]] = {"bullish": [], "bearish": []}
    for label, direction, top, bottom, rank in pois:
        inside = bottom <= price <= top
        dist = 0.0 if inside else min(abs(price - top), abs(price - bottom)) / price * 100
        if dist <= near_pct:
            near[direction].append((rank, inside and top > bottom, dist, label))

    out = []
    for direction, items in near.items():
        if not items:
            continue
        items.sort(key=lambda x: (-x[0], not x[1], x[2]))
        inside = any(i[1] for i in items)
        labels = []
        for i in items:
            if i[3] not in labels:
                labels.append(i[3])
        pts = 30 + (5 if inside else 0) + (3 if len(labels) >= 2 else 0)
        best = items[0]
        out.append({
            "key": "near_poi",
            "direction": HINT[direction],
            "points": pts,
            "label": ("in " if inside else "near ") + " + ".join(labels[:2]),
            "detail": {"pois": labels, "inside": inside, "nearest_dist_pct": round(best[2], 3)},
        })
    return out


def score_coin(df4: pd.DataFrame, df1: pd.DataFrame, btc: dict | None, cfg: dict) -> dict:
    sc = cfg_get(cfg, "scanner", {}) or {}
    length = int(sc.get("swing_length", 5))
    fresh = int(sc.get("fresh_candles", 3))
    near_pct = float(sc.get("near_poi_pct", 0.6))
    vol_mult = float(sc.get("volume_spike_mult", 2.0))
    n1 = len(df1)
    price = float(df1["close"].iloc[-1])

    st4 = market_structure(df4, length)
    trend4 = st4["trend"]
    pd4 = premium_discount(df4, length, st4["swings"])
    sw1 = find_swings(df1, length)
    st1 = market_structure(df1, length, sw1)

    triggers: list[dict] = []

    # 1) fresh 1H liquidity sweep
    best_sweep = {}
    for s in liquidity_sweeps(df1, length, fresh, sw1):
        cur = best_sweep.get(s["direction"])
        if cur is None or (s["rank"], -s["age"]) > (cur["rank"], -cur["age"]):
            best_sweep[s["direction"]] = s
    for direction, s in best_sweep.items():
        pts = 35 + (5 if s["rank"] >= 2 else 0) + (3 if s["age"] == 0 else 0)
        name = s["level_name"].replace("swing_", "swing ")
        triggers.append({"key": "sweep", "direction": HINT[direction], "points": pts,
                         "label": f"1H sweep of {name}", "detail": s})

    # 2) fresh 1H BOS/CHoCH aligned with 4H structure
    recent = [e for e in st1["events"] if e["idx"] >= n1 - fresh]
    if recent:
        e = recent[-1]
        if e["direction"] == trend4:
            body_ratio = candle_stats(df1)["body_ratio"].iloc[e["idx"]]
            disp = bool(pd.notna(body_ratio) and body_ratio >= 1.5)
            pts = 30 + (5 if e["type"] == "CHoCH" else 0) + (3 if disp else 0)
            triggers.append({"key": "structure", "direction": HINT[e["direction"]], "points": pts,
                             "label": f"1H {e['type']}", "detail": dict(e, displacement=disp)})

    # 3) near an unmitigated 4H POI or key level
    triggers.extend(_poi_triggers(df4, price, near_pct, length, st4))

    # 4) 1H volume spike (direction neutral)
    vr = volume_ratio(df1).iloc[-1]
    if pd.notna(vr) and vr >= vol_mult:
        pts = 20 + min(8, int(round((vr / vol_mult - 1) * 8)))
        triggers.append({"key": "volume", "direction": None, "points": pts,
                         "label": f"volume x{vr:.1f}", "detail": {"ratio": round(float(vr), 2)}})

    # BTC filter: no alt long triggers during a BTC 1H dump
    btc_blocked = False
    if btc and btc.get("dump"):
        if any(t["direction"] == "LONG" for t in triggers):
            btc_blocked = True
        triggers = [t for t in triggers if t["direction"] != "LONG"]

    def side_points(side: str) -> int:
        return sum(t["points"] for t in triggers if t["direction"] == side)

    long_pts, short_pts = side_points("LONG"), side_points("SHORT")
    if long_pts == 0 and short_pts == 0:
        direction = "NONE"
    elif long_pts != short_pts:
        direction = "LONG" if long_pts > short_pts else "SHORT"
    else:
        direction = {"bullish": "LONG", "bearish": "SHORT"}.get(trend4, "LONG")

    chosen = [t for t in triggers if t["direction"] in (direction, None)] if direction != "NONE" else \
        [t for t in triggers if t["direction"] is None]
    order = {"sweep": 0, "structure": 1, "near_poi": 2, "volume": 3}
    chosen.sort(key=lambda t: order[t["key"]])
    score = sum(t["points"] for t in chosen)

    pd_bonus = 0
    if pd4 and direction != "NONE":
        if (direction == "LONG" and pd4["zone"] == "discount") or (direction == "SHORT" and pd4["zone"] == "premium"):
            pd_bonus = 5
    score = min(100, score + pd_bonus)

    return {
        "symbol": df1.attrs.get("symbol"),
        "price": price,
        "score": int(score),
        "direction": direction,
        "triggers": [t["key"] for t in chosen],
        "trigger_labels": [t["label"] for t in chosen],
        "btc_blocked": btc_blocked,
        "candle_time": f"{df1.index[-1]:%Y-%m-%dT%H:%M:%SZ}",
        "context": {
            "trend_4h": trend4,
            "trend_1h": st1["trend"],
            "premium_discount_4h": pd4,
            "pd_bonus": pd_bonus,
            "opposite_side_points": short_pts if direction == "LONG" else long_pts,
        },
        "trigger_details": chosen,
    }
