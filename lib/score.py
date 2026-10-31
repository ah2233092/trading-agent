"""Confluence score 0-100 with a per-category breakdown (weights from config.yaml).

Each category gets a fraction 0..1 of its weight:
    htf_bias            1D+4H aligned 1.0 | 4H only 0.6 ; x BTC (supportive 1, neutral 0.85, against 0.4)
    smc_setup           grade A+ 1.0 | A 0.8 | B 0.55 | reject 0
    entry_precision     15m CHoCH confirmation 0.6 + confluence (OB+FVG / OTE / 4H POI) 0.4
    displacement_volume displacement (body + volume) 1.0 | big body only 0.5
    futures_data        supportive 1.0 | neutral / no data 0.5 | against 0
    session_timing      in killzone 1.0 | killzone within plan_valid_hours 0.6 | else 0.2
    news                clear 1.0 | caution 0.5 | block 0 | pending (not checked yet) 0
    backtest_edge       >= 30 trades: avg R > 0.3 -> 1.0, > 0 -> 0.7, <= 0 -> 0 ; less data 0.5
Hard blocks (always NO TRADE): no HTF direction, no/rejected setup, risk plan rejected,
news block, futures liquidity failure.

CLI:
    python lib/score.py --report reports/SOL/latest.json [--news clear] [--json]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.config import cfg_get, load_config

GRADE = {"A+": 1.0, "A": 0.8, "B": 0.55}
MIN_SAMPLE = 30


def compute_score(inp: dict, cfg: dict | None = None) -> dict:
    """inp keys: direction, bias, setup, futures, session, news, stats, plan."""
    cfg = cfg or load_config()
    w = cfg_get(cfg, "score_weights", {}) or {}
    direction = inp.get("direction") or "NONE"
    bias, setup = inp.get("bias") or {}, inp.get("setup") or {}
    fut, sess, stats, plan = inp.get("futures") or {}, inp.get("session") or {}, inp.get("stats"), inp.get("plan")
    news = (inp.get("news") or "pending").lower()
    parts, blocks = [], []

    def add(cat, frac, reason):
        weight = float(w.get(cat, 0))
        frac = max(0.0, min(1.0, frac))
        parts.append({"category": cat, "weight": weight, "fraction": round(frac, 3),
                      "points": round(weight * frac, 1), "reason": reason})

    # HTF bias
    if direction == "NONE":
        add("htf_bias", 0, "no allowed direction")
        blocks.append("HTF bias NONE")
    else:
        want = "bullish" if direction == "LONG" else "bearish"
        d1 = (bias.get("d1") or {}).get("trend")
        base, why = (1.0, "1D + 4H aligned") if d1 == want else (0.6, "4H only (1D disagrees)")
        btc = bias.get("btc_for_direction")
        mult = {"supportive": 1.0, "neutral": 0.85, "against": 0.4}.get(btc, 1.0)
        add("htf_bias", base * mult, f"{why}; BTC {btc or 'n/a'}")

    # SMC setup
    grade = setup.get("grade") or "reject"
    if not setup.get("model") or grade == "reject":
        add("smc_setup", 0, "no valid entry model" if not setup.get("model") else "setup graded reject")
        blocks.append("no valid SMC setup")
    else:
        add("smc_setup", GRADE.get(grade, 0), f"model {setup['model']} ({setup.get('name')}) grade {grade}")

    q = setup.get("quality") or {}
    # Entry precision
    frac = (0.6 if q.get("ltf_confirmed") else 0) + (0.4 if q.get("confluence") else 0)
    add("entry_precision", frac, f"15m CHoCH {'yes' if q.get('ltf_confirmed') else 'no'}, "
                                 f"confluence {'yes' if q.get('confluence') else 'no'}")

    # Displacement / volume
    disp = setup.get("displacement") or {}
    if disp.get("displacement"):
        add("displacement_volume", 1.0, "displacement candle with volume")
    elif disp.get("body_only"):
        add("displacement_volume", 0.5, "big body but no volume")
    else:
        add("displacement_volume", 0, "no displacement")

    # Futures data
    v = fut.get("verdict")
    add("futures_data", {"supportive": 1.0, "neutral": 0.5, "against": 0.0}.get(v, 0.5),
        f"{v or 'no data'}{' — ' + fut['reason'] if fut.get('reason') else ''}")
    if not (fut.get("liquidity") or {}).get("ok", True):
        blocks.append("coin fails volume / spread filter")

    # Session timing
    hours = float(cfg_get(cfg, "plan_valid_hours", 6))
    if sess.get("in_killzone"):
        add("session_timing", 1.0, f"inside {sess.get('killzone')} killzone")
    elif sess.get("hours_to_next") is not None and sess["hours_to_next"] <= hours:
        add("session_timing", 0.6, f"{sess.get('next_killzone')} killzone in {sess['hours_to_next']:.1f}h")
    else:
        add("session_timing", 0.2, "outside killzones")

    # News
    add("news", {"clear": 1.0, "caution": 0.5, "block": 0.0}.get(news, 0.0),
        "not checked yet (run news-check)" if news == "pending" else news)
    if news == "block":
        blocks.append("news block")

    # Backtest / journal edge
    if stats and (stats.get("trades") or 0) >= MIN_SAMPLE:
        ar = stats.get("avg_r") or 0
        add("backtest_edge", 1.0 if ar > 0.3 else 0.7 if ar > 0 else 0.0,
            f"{stats['trades']} trades, win {stats.get('win_rate')}%, avg R {ar}")
    else:
        n = (stats or {}).get("trades") or 0
        add("backtest_edge", 0.5, f"not enough data yet ({n} trades)")

    if plan is not None and not plan.get("ok"):
        blocks.append("risk plan rejected: " + "; ".join(plan.get("rejects") or []))

    total = round(sum(p["points"] for p in parts), 1)
    threshold = float(cfg_get(cfg, "min_trade_score", 75))
    if total < threshold:
        blocks.append(f"score {total:g} < {threshold:g}")
    return {"total": total, "threshold": threshold, "news": news, "breakdown": parts,
            "blocks": blocks, "decision": "TRADE" if not blocks else "NO TRADE",
            "max_without_news": round(sum(float(w.get(k, 0)) for k in w) - float(w.get("news", 0)), 1)}


def score_text(s: dict) -> str:
    rows = [f"  {p['category']:<20} {p['points']:>5.1f} / {p['weight']:<4g} {p['reason']}" for p in s["breakdown"]]
    return "\n".join([f"Score: {s['total']:g}/100 (min {s['threshold']:g}) → {s['decision']}", *rows] +
                     [f"  ✖ {b}" for b in s["blocks"]])


def _main() -> None:
    from lib.config import fix_console

    fix_console()
    ap = argparse.ArgumentParser(description="Confluence score from a deep-scan report")
    ap.add_argument("--report", required=True, help="deep-scan report JSON (reports/<COIN>/latest.json)")
    ap.add_argument("--news", choices=["clear", "caution", "block", "pending"])
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    rep = json.loads(Path(a.report).read_text(encoding="utf-8"))
    inp = dict(rep.get("score_inputs") or {})
    if a.news:
        inp["news"] = a.news
    s = compute_score(inp)
    print(json.dumps(s, indent=1) if a.json else score_text(s))


if __name__ == "__main__":
    _main()
