"""Trade-readiness check used by the watchman: the same decision path as the deep scan / backtest
(bias -> SMC setup -> TP levels -> risk plan -> score), plus the live entry rule from config:

    entry_mode: market_in_killzone   -> enter at the current price, only inside the London / New York
                                        killzones, and only if TP1 is still >= min_rr_tp1 from here
    entry_mode: limit                -> classic limit at the POI mid (backtest: no edge)

Returns a compact dict per coin so the watchman can rank coins and decide what to alert.
"""
from __future__ import annotations

import pandas as pd

from lib import risk
from lib.config import cfg_get


def market_rr(c: dict) -> float | None:
    """R to TP1 if entering at the current price with the planned SL."""
    px, sl, tp1 = c["price"], c["sl"], c["tps"][0]
    if (c["side"] == "long" and px <= sl) or (c["side"] == "short" and px >= sl):
        return None
    d = abs(px - sl)
    return abs(tp1 - px) / d if d else None


def check(symbol: str, frames: dict, btc_frames: dict | None, now: pd.Timestamp, cfg: dict,
          spec: risk.ContractSpec) -> dict:
    from backtest.engine import evaluate

    c = evaluate(symbol, frames, btc_frames, now, cfg, spec)
    if c is None:
        return {"symbol": symbol, "candidate": False, "ready": False, "rank": 0.0, "why": "no setup"}
    mode = cfg_get(cfg, "entry_mode", "market_in_killzone")
    min_rr = float(cfg_get(cfg, "min_rr_tp1", 2.0))
    min_score = float(cfg_get(cfg, "min_trade_score", 75))
    mrr = market_rr(c)
    why = []
    if not c["plan_ok"]:
        why.append("risk plan rejected")
    if c["score"] < min_score:
        why.append(f"score {c['score']:g} < {min_score:g}")
    if mode == "market_in_killzone":
        if mrr is None or mrr < min_rr:
            why.append(f"TP1 only 1:{mrr:.1f} from current price" if mrr else "price beyond SL")
        if not c["killzone"]:
            why.append("outside killzone")
    ready = not why
    # rank: ready first, then plan quality, then score
    rank = (100 if ready else 0) + (20 if c["plan_ok"] else 0) + c["score"] / 2 + min(mrr or 0, 5) * 2
    return {"symbol": symbol, "candidate": True, "ready": ready, "rank": round(rank, 1),
            "side": c["side"], "model": c["model"], "grade": c["grade"], "score": c["score"],
            "market_rr": round(mrr, 2) if mrr else None, "killzone": c["killzone"],
            "plan_ok": c["plan_ok"], "why": "; ".join(why) or "ready"}
