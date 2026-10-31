"""Walk-forward backtest of the deep-scan setups (A/B/C/D) on MEXC history.

    python backtest/engine.py --coins config --from 2024-06-01 --to today
    python backtest/engine.py --coins BTC,ETH,SOL --from 2025-01-01 --workers 2

Same lib/ code as the live deep scan: bias -> SMC setup -> TP levels -> risk plan -> score.
At every 1H close only candles that were already closed are visible (no look-ahead).
News is treated as "clear" (live trades are only taken after a clear news check) and futures data as
"neutral" (no history for it); backtest_edge is neutral. Every candidate plan is recorded with its
score, so the report can show results per score bucket and per setup.

Fills / exits use 15m candles: limit entry must trade through, SL wins when SL and TP share a candle,
TP1 closes tp1_close_pct and moves SL to entry, the rest is split over TP2 / TP3.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from dataclasses import fields
from multiprocessing import Pool
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from backtest import history
from lib import risk, smc
from lib.bias import analyze_bias
from lib.config import ROOT, cfg_get, load_config
from lib.data import DEEP_LIMITS, to_symbol
from lib.score import compute_score

log = logging.getLogger("backtest")
RESULTS = ROOT / "backtest" / "results"
TF_DUR = {"1d": pd.Timedelta(days=1), "4h": pd.Timedelta(hours=4), "1h": pd.Timedelta(hours=1),
          "15m": pd.Timedelta(minutes=15)}
MIN_BARS = {"1d": 60, "4h": 150, "1h": 200, "15m": 60}
SIZE_REJECT = "size below exchange minimum"


# ------------------------------------------------------------------ views --

class Frames:
    """Full history per timeframe + fast 'closed at time t' slicing."""

    def __init__(self, coin: str, tfs=("1d", "4h", "1h", "15m")):
        self.coin = coin
        self.full: dict[str, pd.DataFrame] = {}
        self.close_ns: dict[str, np.ndarray] = {}
        for tf in tfs:
            df = history.load(coin, tf)
            if df is None or df.empty:
                continue
            df.index = df.index.as_unit("ns")
            self.full[tf] = df
            self.close_ns[tf] = (df.index + TF_DUR[tf]).asi8

    def view(self, tf: str, t: pd.Timestamp, n: int | None = None) -> pd.DataFrame | None:
        """Candles of `tf` whose close time <= t (i.e. fully closed at t), last n."""
        if tf not in self.full:
            return None
        i = int(np.searchsorted(self.close_ns[tf], pd.Timestamp(t).as_unit("ns").value, side="right"))
        n = n or DEEP_LIMITS.get(tf, 300)
        out = self.full[tf].iloc[max(0, i - n):i]
        out.attrs.update(self.full[tf].attrs)
        return out

    def at(self, t: pd.Timestamp, limits: dict | None = None) -> dict | None:
        limits = limits or DEEP_LIMITS
        out = {}
        for tf, n in limits.items():
            v = self.view(tf, t, n)
            if v is None or len(v) < MIN_BARS.get(tf, 1):
                if tf == "15m":
                    out[tf] = v if v is not None and len(v) else None
                    continue
                return None
            out[tf] = v
        return out

    def after(self, tf: str, t: pd.Timestamp) -> pd.DataFrame:
        """Candles that open at or after t (the future relative to t)."""
        df = self.full[tf]
        i = int(np.searchsorted(df.index.asi8, pd.Timestamp(t).as_unit("ns").value, side="left"))
        return df.iloc[i:]


def spec_for(coin: str) -> risk.ContractSpec:
    d = history.load_spec(coin) or {}
    names = {f.name for f in fields(risk.ContractSpec)}
    d = {k: v for k, v in d.items() if k in names}
    d.setdefault("symbol", to_symbol(coin))
    return risk.ContractSpec(**d)


# -------------------------------------------------------------- evaluate --

def evaluate(symbol: str, frames: dict, btc: dict | None, t: pd.Timestamp, cfg: dict,
             spec: risk.ContractSpec) -> dict | None:
    """The live deep-scan decision at time t (without futures history / news)."""
    from lib.deep_scan import build_levels

    length = int(cfg_get(cfg, "scanner.swing_length", 5))
    ltf_len = int(cfg_get(cfg, "smc.ltf_swing_length", 3))
    bias = analyze_bias(symbol, frames, btc, cfg)
    direction = bias["direction"]
    if direction == "NONE":
        return None
    setup = smc.classify_setup(direction, frames["4h"], frames["1h"], frames.get("15m"), length, ltf_len, cfg)
    if not setup.get("model") or setup.get("grade") == "reject" or not setup.get("poi"):
        return None
    side = direction.lower()
    price = float(frames["1h"]["close"].iloc[-1])
    levels = build_levels(side, frames, bias, cfg, price)
    plan = risk.plan_from_setup(side, setup, levels, setup.get("atr_1h") or 0, spec, cfg, price)
    if not plan.get("entry") or not plan.get("tps"):
        return None
    structural = [r for r in plan.get("rejects", []) if not r.startswith(SIZE_REJECT)]
    session = smc.session_info(t, cfg)
    score = compute_score({
        "direction": direction,
        "bias": {"d1": {"trend": bias["d1"]["trend"]}, "btc_for_direction": bias.get("btc_for_direction")},
        "setup": {k: setup.get(k) for k in ("model", "name", "grade", "quality", "displacement")},
        "futures": {"verdict": "neutral", "liquidity": {"ok": True}},
        "session": session, "news": "clear", "stats": None, "plan": None,
    }, cfg)
    q = setup.get("quality") or {}
    return {
        "time": t, "side": side, "model": setup["model"], "grade": setup["grade"],
        "score": score["total"], "plan_ok": not structural, "size_ok": not any(
            r.startswith(SIZE_REJECT) for r in plan.get("rejects", [])),
        "rejects": "; ".join(plan.get("rejects", [])),
        "order": plan.get("order", "limit"), "entry": plan["entry"], "sl": plan["sl"], "tps": plan["tps"],
        "rr": plan.get("rr"), "leverage": plan.get("leverage"),
        "killzone": bool(session.get("in_killzone")), "ltf_confirmed": bool(q.get("ltf_confirmed")),
        "btc": bias.get("btc_for_direction"), "d1": bias["d1"]["trend"], "h4": bias["h4"]["trend"],
        "price": price,
    }


# -------------------------------------------------------------- simulate --

def simulate(c: dict, fr: Frames, cfg: dict, spec: risk.ContractSpec) -> dict:
    """Walk 15m (or 1H) candles after the plan time. Returns outcome, R, times."""
    valid_h = float(cfg_get(cfg, "plan_valid_hours", 6))
    max_hold = float(cfg_get(cfg, "backtest.max_hold_hours", 168))
    slip = float(cfg_get(cfg, "backtest.slippage_pct", 0.02)) / 100
    tp1_close = float(cfg_get(cfg, "tp1_close_pct", 50)) / 100
    long = c["side"] == "long"
    sgn = 1 if long else -1
    entry, sl, tps = float(c["entry"]), float(c["sl"]), [float(x) for x in c["tps"]]
    dist = abs(entry - sl)
    rest = 1 - tp1_close
    shares = [tp1_close] + [rest / (len(tps) - 1)] * (len(tps) - 1) if len(tps) > 1 else [1.0]
    tf = "15m" if "15m" in fr.full and len(fr.after("15m", c["time"])) else "1h"
    fut = fr.after(tf, c["time"])
    t0 = c["time"]
    expiry = t0 + pd.Timedelta(hours=valid_h)

    o, h, l, cl = (fut[k].to_numpy() for k in ("open", "high", "low", "close"))
    idx = fut.index
    market = c.get("order") == "market"
    fee_in = spec.taker_fee if market else spec.maker_fee
    fill_i, fill_px = None, None
    i = 0
    n = len(fut)
    # ---- pending ----
    while i < n and idx[i] < expiry:
        if market:
            fill_i, fill_px = i, o[i] * (1 + sgn * slip)
            break
        if (long and h[i] >= tps[0]) or (not long and l[i] <= tps[0]):
            if not ((long and l[i] <= entry) or (not long and h[i] >= entry)):
                return {"outcome": "missed", "r": 0.0, "filled": False, "exit_time": idx[i]}
        if (long and l[i] <= entry) or (not long and h[i] >= entry):
            fill_i, fill_px = i, entry
            break
        i += 1
    if fill_i is None:
        return {"outcome": "expired" if i < n or n else "no_data", "r": 0.0, "filled": False,
                "exit_time": expiry}

    # ---- active ----
    stop = sl
    remaining = 1.0
    got = 0          # number of TPs hit
    pnl = 0.0        # in price units per unit size
    fees = entry * fee_in
    deadline = idx[fill_i] + pd.Timedelta(hours=max_hold)
    exit_time = idx[fill_i]
    outcome = None
    for j in range(fill_i, n):
        exit_time = idx[j]
        hit_stop = (long and l[j] <= stop) or (not long and h[j] >= stop)
        if hit_stop:
            px = stop * (1 - sgn * slip) if got == 0 else stop
            pnl += remaining * sgn * (px - fill_px)
            fees += remaining * px * spec.taker_fee
            remaining = 0
            outcome = "sl" if got == 0 else ("be" if got == 1 else f"tp{got}")
            break
        if j == fill_i:
            continue  # conservative: no TP on the fill candle
        while got < len(tps) and ((long and h[j] >= tps[got]) or (not long and l[j] <= tps[got])):
            part = shares[got] if got < len(tps) - 1 else remaining
            pnl += part * sgn * (tps[got] - fill_px)
            fees += part * tps[got] * spec.maker_fee
            remaining -= part
            got += 1
            if got == 1:
                stop = fill_px
        if remaining <= 1e-9:
            outcome = f"tp{got}"
            break
        if idx[j] >= deadline:
            pnl += remaining * sgn * (cl[j] - fill_px)
            fees += remaining * cl[j] * spec.taker_fee
            remaining = 0
            outcome = "timeout" if got == 0 else f"tp{got}"
            break
    if outcome is None:  # data ended while open
        pnl += remaining * sgn * (cl[-1] - fill_px)
        outcome = "open" if got == 0 else f"tp{got}"
    risk_dist = abs(fill_px - sl) or dist  # market fills: R on the real distance
    r = (pnl - fees) / risk_dist if risk_dist else 0.0
    return {"outcome": outcome, "r": round(float(r), 3), "filled": True, "tp_hits": got,
            "fill_time": idx[fill_i], "exit_time": exit_time,
            "hours": round((exit_time - idx[fill_i]).total_seconds() / 3600, 1)}


# -------------------------------------------------------------- per coin --

def run_coin(args: tuple) -> list[dict]:
    coin, start, end, cfg_over = args
    logging.basicConfig(level=logging.ERROR)
    cfg = load_config()
    for k, v in (cfg_over or {}).items():
        cfg[k] = v
    fr = Frames(coin)
    if "1h" not in fr.full:
        return []
    btc = None if coin == "BTC" else Frames("BTC", tfs=("4h", "1h"))
    spec = spec_for(coin)
    symbol = to_symbol(coin)
    h1 = fr.full["1h"]
    closes = h1.index + TF_DUR["1h"]
    sel = closes[(closes >= pd.Timestamp(start, tz="UTC")) & (closes <= pd.Timestamp(end, tz="UTC"))]
    busy_until = pd.Timestamp(0, tz="UTC")
    last_key, last_key_t = None, pd.Timestamp(0, tz="UTC")
    out: list[dict] = []
    for t in sel:
        if t < busy_until:
            continue
        frames = fr.at(t)
        if frames is None:
            continue
        bf = None
        if btc is not None:
            b4, b1 = btc.view("4h", t, 300), btc.view("1h", t, 400)
            if b4 is None or b1 is None or len(b4) < 50 or len(b1) < 50:
                continue
            bf = {"4h": b4, "1h": b1}
        try:
            c = evaluate(symbol, frames, bf, t, cfg, spec)
        except Exception as e:  # keep the run going; count it
            out.append({"coin": coin, "time": t, "error": str(e)[:200]})
            continue
        if c is None:
            continue
        key = (c["model"], c["side"], round(c["entry"], 8))
        if key == last_key and t - last_key_t < pd.Timedelta(hours=24):
            continue  # same setup re-detected after it expired
        res = simulate(c, fr, cfg, spec)
        c.update(res)
        c["coin"] = coin
        out.append(c)
        last_key, last_key_t = key, t
        if res.get("filled"):
            busy_until = max(t, res["exit_time"]) + TF_DUR["1h"]
        else:
            busy_until = t + TF_DUR["1h"]
    return out


# ------------------------------------------------------------------ stats --

def _stats(df: pd.DataFrame) -> dict:
    t = df[df["filled"] == True]  # noqa: E712
    n = len(t)
    if not n:
        return {"trades": 0, "candidates": int(len(df)), "fill_rate": 0.0}
    r = t["r"].astype(float)
    wins = t["outcome"].isin(["tp1", "tp2", "tp3", "be"])
    eq = r.cumsum()
    dd = float((eq.cummax() - eq).max()) if n else 0.0
    streak = cur = 0
    for v in r:
        cur = cur + 1 if v < 0 else 0
        streak = max(streak, cur)
    pos, neg = float(r[r > 0].sum()), float(-r[r < 0].sum())
    weeks = max(1.0, (t["time"].max() - t["time"].min()).total_seconds() / 604800) if n > 1 else 1.0
    return {
        "trades": int(n), "candidates": int(len(df)), "fill_rate": round(n / len(df) * 100, 1),
        "win_rate": round(float(wins.mean()) * 100, 1),
        "sl_rate": round(float((t["outcome"] == "sl").mean()) * 100, 1),
        "avg_r": round(float(r.mean()), 3), "expectancy_r": round(float(r.mean()), 3),
        "total_r": round(float(r.sum()), 2),
        "profit_factor": round(pos / neg, 2) if neg else None,
        "max_dd_r": round(dd, 2), "max_losing_streak": int(streak),
        "avg_hours": round(float(t["hours"].mean()), 1) if "hours" in t else None,
        "trades_per_week": round(n / weeks, 2),
        "low_sample": n < 30,
    }


def summarize(trades: pd.DataFrame, split: pd.Timestamp, min_score: float) -> dict:
    sel = trades[(trades["plan_ok"] == True) & (trades["score"] >= min_score)]  # noqa: E712
    ins, oos = sel[sel["time"] < split], sel[sel["time"] >= split]
    out = {"all": {"in_sample": _stats(ins), "oos": _stats(oos), "total": _stats(sel)}, "setups": {}}
    for m in sorted(sel["model"].dropna().unique()):
        out["setups"][m] = {"in_sample": _stats(ins[ins["model"] == m]), "oos": _stats(oos[oos["model"] == m]),
                            "total": _stats(sel[sel["model"] == m])}
    out["by_side"] = {s: _stats(sel[sel["side"] == s]) for s in ("long", "short")}
    out["by_killzone"] = {"inside": _stats(sel[sel["killzone"] == True]),  # noqa: E712
                          "outside": _stats(sel[sel["killzone"] == False])}  # noqa: E712
    out["by_coin"] = {c: _stats(sel[sel["coin"] == c]) for c in sorted(sel["coin"].unique())}
    ok = trades[trades["plan_ok"] == True]  # noqa: E712
    buckets = [(0, 60), (60, 70), (70, 75), (75, 80), (80, 90), (90, 101)]
    out["by_score"] = {f"{a}-{b - 1 if b == 101 else b}": _stats(ok[(ok["score"] >= a) & (ok["score"] < b)])
                       for a, b in buckets}
    return out


def write_setup_stats(summary: dict, meta: dict) -> Path:
    path = RESULTS / "setup_stats.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    setups = data.setdefault("setups", {})
    for m in list(setups):
        setups[m]["backtest"] = None
    for m, s in summary["setups"].items():
        oos, tot = s["oos"], s["total"]
        main = oos if (oos.get("trades") or 0) >= 30 else tot
        setups.setdefault(m, {})["backtest"] = dict(
            main, source="backtest (out-of-sample)" if main is oos else "backtest (all data)",
            sample_size=tot.get("trades", 0), oos=oos, in_sample=s["in_sample"], period=meta["period"])
    data["backtest_meta"] = meta
    RESULTS.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str) + "\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------- report --

def _row(name: str, s: dict) -> str:
    if not s.get("trades"):
        return f"| {name} | 0 | - | - | - | - | - |"
    pf = s["profit_factor"] if s["profit_factor"] is not None else "∞"
    flag = " ⚠️ low sample" if s.get("low_sample") else ""
    return (f"| {name}{flag} | {s['trades']} | {s['win_rate']}% | {s['avg_r']:+.2f} | {pf} | "
            f"{s['max_dd_r']} | {s['max_losing_streak']} |")


def report_md(summary: dict, meta: dict) -> str:
    hdr = "| | Trades | Win% (TP1+) | Avg R | PF | Max DD (R) | Lose streak |\n|---|---|---|---|---|---|---|"
    L = [f"# Backtest report — {meta['run_at']}",
         "",
         f"Period {meta['period']} | split (out-of-sample from) {meta['split']} | coins {len(meta['coins'])} | "
         f"min score {meta['min_score']} | {meta['candidates']} candidate plans, {meta['errors']} errors",
         "",
         "Win = TP1 reached (50% closed, SL to entry). R after fees + slippage. News assumed clear, futures neutral.",
         "",
         "## Overall", hdr,
         _row("In-sample", summary["all"]["in_sample"]),
         _row("Out-of-sample", summary["all"]["oos"]),
         _row("All", summary["all"]["total"]),
         "", "## Per setup (in-sample → out-of-sample)", hdr]
    for m, s in summary["setups"].items():
        L += [_row(f"{m} in-sample", s["in_sample"]), _row(f"{m} OOS", s["oos"]), _row(f"{m} all", s["total"])]
    L += ["", "## By score bucket (all accepted plans, any score)", hdr]
    L += [_row(k, v) for k, v in summary["by_score"].items()]
    L += ["", "## By side", hdr] + [_row(k, v) for k, v in summary["by_side"].items()]
    L += ["", "## Killzone", hdr] + [_row(k, v) for k, v in summary["by_killzone"].items()]
    L += ["", "## By coin", hdr] + [_row(k, v) for k, v in summary["by_coin"].items()]
    return "\n".join(L) + "\n"


def equity_chart(trades: pd.DataFrame, path: Path, min_score: float, split: pd.Timestamp) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    sel = trades[(trades["plan_ok"] == True) & (trades["score"] >= min_score) & (trades["filled"] == True)]  # noqa: E712
    sel = sel.sort_values("exit_time")
    fig, ax = plt.subplots(2, 1, figsize=(11, 6), sharex=True, gridspec_kw={"height_ratios": [3, 1]})
    fig.patch.set_facecolor("#131722")
    for a in ax:
        a.set_facecolor("#131722")
        a.tick_params(colors="#c8ccd4")
        for s in a.spines.values():
            s.set_color("#363c4e")
    if len(sel):
        x = pd.to_datetime(sel["exit_time"])
        eq = sel["r"].cumsum().to_numpy()
        ax[0].plot(x, eq, color="#26a69a", lw=1.6, label="All setups")
        for m, col in zip(sorted(sel["model"].unique()), ("#42a5f5", "#ffb74d", "#ab47bc", "#ef5350")):
            s = sel[sel["model"] == m]
            ax[0].plot(pd.to_datetime(s["exit_time"]), s["r"].cumsum(), lw=1, color=col, alpha=.8, label=f"Setup {m}")
        dd = eq - np.maximum.accumulate(eq)
        ax[1].fill_between(x, dd, 0, color="#ef5350", alpha=.5)
        for a in ax:
            a.axvline(split, color="#9598a1", ls="--", lw=1)
        ax[0].text(split, ax[0].get_ylim()[1], "  out-of-sample →", color="#9598a1", va="top")
    ax[0].set_title("Equity curve (R) — score ≥ %g" % min_score, color="#e0e3eb")
    ax[0].set_ylabel("Cumulative R", color="#c8ccd4")
    ax[1].set_ylabel("Drawdown R", color="#c8ccd4")
    ax[0].legend(facecolor="#1e222d", labelcolor="#c8ccd4", edgecolor="#363c4e", fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=110, facecolor=fig.get_facecolor())
    plt.close(fig)


# -------------------------------------------------------------------- CLI --

def main() -> int:
    from lib.config import fix_console, utc_now

    fix_console()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(description="Walk-forward backtest")
    ap.add_argument("--coins", default="config", help="'config' = every coin in data/history, or BTC,ETH,SOL")
    ap.add_argument("--from", dest="start", default="2024-06-01")
    ap.add_argument("--to", dest="end", default="today")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--split", type=float, default=0.7, help="in-sample share of the period")
    ap.add_argument("--no-stats", action="store_true", help="do not write setup_stats.json")
    a = ap.parse_args()
    cfg = load_config()
    end = pd.Timestamp.now(tz="UTC").floor("h") if a.end == "today" else pd.Timestamp(a.end, tz="UTC")
    start = pd.Timestamp(a.start, tz="UTC")
    if a.coins == "config":
        cj = history.HIST / "coins.json"
        coins = json.loads(cj.read_text()) if cj.exists() else sorted(p.name for p in history.HIST.iterdir() if p.is_dir())
    else:
        coins = [c.strip().upper() for c in a.coins.split(",") if c.strip()]
    coins = [c for c in coins if (history.coin_dir(c) / "1h.csv.gz").exists()]
    if not coins:
        print("No history found. Run: python backtest/history.py --coins config --from 2024-01-01")
        return 1
    print(f"Backtest {len(coins)} coins, {start:%Y-%m-%d} → {end:%Y-%m-%d}, {a.workers} workers", flush=True)
    t0 = time.time()
    rows: list[dict] = []
    jobs = [(c, str(start), str(end), None) for c in coins]
    with Pool(max(1, a.workers)) as pool:
        for k, res in enumerate(pool.imap_unordered(run_coin, jobs), 1):
            rows += res
            c = res[0]["coin"] if res else "?"
            print(f"  [{k}/{len(coins)}] {c}: {sum(1 for r in res if r.get('filled'))} trades, "
                  f"{len(res)} candidates ({time.time() - t0:.0f}s)", flush=True)
    errors = [r for r in rows if "error" in r]
    trades = pd.DataFrame([r for r in rows if "error" not in r])
    stamp = f"{utc_now():%Y%m%d}"
    RESULTS.mkdir(parents=True, exist_ok=True)
    min_score = float(cfg_get(cfg, "min_trade_score", 75))
    split = start + (end - start) * a.split
    meta = {"run_at": f"{utc_now():%Y-%m-%d %H:%M} UTC", "period": f"{start:%Y-%m-%d} → {end:%Y-%m-%d}",
            "split": f"{split:%Y-%m-%d}", "coins": coins, "min_score": min_score,
            "candidates": int(len(trades)), "errors": len(errors),
            "assumptions": "news clear, futures neutral, fees+slippage, SL first in same candle, 15m fills"}
    if trades.empty:
        print("No candidate plans found.")
        return 0
    trades = trades.sort_values("time")
    trades.drop(columns=[c for c in ("tps", "rr") if c in trades], errors="ignore").assign(
        tp1=[x[0] for x in trades["tps"]], tp2=[x[1] if len(x) > 1 else None for x in trades["tps"]],
        tp3=[x[2] if len(x) > 2 else None for x in trades["tps"]]).to_csv(RESULTS / f"trades_{stamp}.csv", index=False)
    summary = summarize(trades, split, min_score)
    md = report_md(summary, meta)
    (RESULTS / f"report_{stamp}.md").write_text(md, encoding="utf-8")
    (RESULTS / "latest_report.md").write_text(md, encoding="utf-8")
    (RESULTS / "summary.json").write_text(json.dumps({"meta": meta, "summary": summary}, indent=1, default=str),
                                          encoding="utf-8")
    equity_chart(trades, RESULTS / "equity_curve.png", min_score, split)
    if not a.no_stats:
        write_setup_stats(summary, meta)
    if errors:
        print(f"{len(errors)} evaluation errors, first: {errors[0]['error']}")
    print(md)
    print(f"Done in {time.time() - t0:.0f}s → {RESULTS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
