"""Entry-rule variants re-simulated on the candidate plans of a backtest run (fast: no re-detection).

    python backtest/variants.py --trades backtest/results/trades_20261031.csv

Variants (all use the same SL / TP levels the engine found):
  V0 limit   — limit at the POI mid (what the engine does)
  V1 market  — enter at the next 15m open right after the signal; skip if TP1 < min_rr_tp1 from there
  V2 market + killzone only
  V3 market + shorts only
Rules tuned on the in-sample part must be judged on the out-of-sample part.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from backtest import engine
from lib.config import cfg_get, load_config


def run(trades: pd.DataFrame, variant: str, cfg: dict) -> pd.DataFrame:
    min_rr = float(cfg_get(cfg, "min_rr_tp1", 2.0))
    out = []
    for coin, g in trades.groupby("coin"):
        fr = engine.Frames(coin, tfs=("1h", "15m"))
        spec = engine.spec_for(coin)
        busy = pd.Timestamp(0, tz="UTC")
        for _, row in g.sort_values("time").iterrows():
            t = row["time"]
            if t < busy:
                continue
            if variant in ("V2",) and not row["killzone"]:
                continue
            if variant in ("V3",) and row["side"] != "short":
                continue
            c = {"time": t, "side": row["side"], "entry": row["entry"], "sl": row["sl"],
                 "tps": [x for x in (row["tp1"], row["tp2"], row["tp3"]) if pd.notna(x)], "order": "limit"}
            if variant != "V0":
                px = float(row["price"])
                d = abs(px - c["sl"])
                if (row["side"] == "long" and px <= c["sl"]) or (row["side"] == "short" and px >= c["sl"]):
                    continue
                if not d or abs(c["tps"][0] - px) / d < min_rr:
                    continue
                c.update(order="market", entry=px)
            res = engine.simulate(c, fr, cfg, spec)
            res.update(time=t, coin=coin, model=row["model"], side=row["side"], killzone=row["killzone"],
                       plan_ok=True, score=row["score"])
            out.append(res)
            busy = (max(t, res["exit_time"]) if res.get("filled") else t) + pd.Timedelta(hours=1)
    return pd.DataFrame(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trades", required=True)
    ap.add_argument("--min-score", type=float, default=None)
    ap.add_argument("--split", default=None, help="OOS start date (default: from summary.json)")
    a = ap.parse_args()
    cfg = load_config()
    t = pd.read_csv(a.trades, parse_dates=["time"])
    ms = a.min_score if a.min_score is not None else float(cfg_get(cfg, "min_trade_score", 75))
    t = t[(t["plan_ok"] == True) & (t["score"] >= ms)]  # noqa: E712
    if a.split:
        split = pd.Timestamp(a.split, tz="UTC")
    else:
        import json
        meta = json.loads((engine.RESULTS / "summary.json").read_text())["meta"]
        split = pd.Timestamp(meta["split"], tz="UTC")
    hdr = "| Variant | Part | Trades | Win% | Avg R | PF | Max DD R |\n|---|---|---|---|---|---|---|"
    print(hdr)
    for v in ("V0", "V1", "V2", "V3"):
        r = run(t, v, cfg)
        f = r[r["filled"] == True] if len(r) else r  # noqa: E712
        for part, sub in (("in-sample", f[f["time"] < split]), ("OOS", f[f["time"] >= split]), ("all", f)):
            s = engine._stats(sub.assign(filled=True)) if len(sub) else {"trades": 0}
            if not s.get("trades"):
                print(f"| {v} | {part} | 0 | - | - | - | - |")
                continue
            print(f"| {v} | {part} | {s['trades']} | {s['win_rate']}% | {s['avg_r']:+.2f} | "
                  f"{s['profit_factor']} | {s['max_dd_r']} |", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
