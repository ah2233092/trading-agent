"""Marked candlestick charts (chart-vision skill).

Dark theme, last 150 candles + volume panel, with OB boxes, FVG boxes, breaker
boxes, liquidity lines (PDH/PDL/PWH/PWL/MO, EQH/EQL, Asia range), BOS/CHoCH
labels, swing points, the 4H 50% line and — when a plan is given — entry zone,
SL, TP1/TP2/TP3 and liquidation lines.

CLI:
    python lib/chart.py --symbol SOL/USDT:USDT --tf 1d,4h,1h,15m --marks auto [--plan plan.json] [--json]
Saves charts/<COIN>/<YYYYmmdd-HHMM>_<tf>.png
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import mplfinance as mpf  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

from lib import smc  # noqa: E402
from lib.config import ROOT, cfg_get, load_config  # noqa: E402

CHARTS = ROOT / "charts"
C = dict(bg="#131722", grid="#2a2e39", text="#d1d4dc", up="#26a69a", down="#ef5350",
         ob_bull="#26a69a", ob_bear="#ef5350", fvg_bull="#7cb342", fvg_bear="#ff8a65", breaker="#ba68c8",
         liq="#ffb74d", eq="#90caf9", entry="#42a5f5", sl="#ff1744", tp="#00e676", liqp="#e040fb",
         bos="#b0bec5", choch="#ffd54f", swing="#78909c")


def _style():
    mc = mpf.make_marketcolors(up=C["up"], down=C["down"], edge="inherit", wick="inherit",
                               volume={"up": C["up"], "down": C["down"]}, alpha=0.95)
    return mpf.make_mpf_style(base_mpf_style="nightclouds", marketcolors=mc, facecolor=C["bg"],
                              figcolor=C["bg"], edgecolor=C["grid"], gridcolor=C["grid"], gridstyle=":",
                              rc={"axes.labelcolor": C["text"], "xtick.color": C["text"],
                                  "ytick.color": C["text"], "font.size": 9})


def auto_marks(df: pd.DataFrame, length: int, levels: list[tuple[str, float]] | None = None,
               eq: float | None = None) -> dict:
    a = smc.analyze(df, length)
    return {
        "obs": [o for o in a["order_blocks"] if o["status"] != "broken"],
        "fvgs": [g for g in a["fvgs"] if g["status"] != "filled"],
        "breakers": [b for b in a["breakers"] if b["status"] != "invalid"],
        "events": a["structure"]["events"],
        "swings": a["swings"],
        "levels": list(levels or []) + [(e["kind"], e["price"]) for e in a["liquidity"]["equal_levels"] if not e["swept"]],
        "eq": eq,
    }


def render(df: pd.DataFrame, tf: str, symbol: str, marks: dict | None = None, plan: dict | None = None,
           out_dir: Path | None = None, n: int = 150, stamp: str | None = None) -> str:
    marks = marks or {}
    dfw = df.tail(n).copy()
    off, N = len(df) - len(dfw), len(dfw)
    dfw.index = dfw.index.tz_convert(None) if dfw.index.tz is not None else dfw.index
    fmt = "%Y-%m-%d" if tf in ("1d", "1w") else "%m-%d %H:%M"
    fig, axes = mpf.plot(dfw, type="candle", style=_style(), volume=True, returnfig=True, figsize=(15, 8.5),
                         panel_ratios=(4, 1), datetime_format=fmt, xrotation=0, tight_layout=True,
                         warn_too_much_data=100000)
    ax = axes[0]
    lo, hi = float(dfw["low"].min()), float(dfw["high"].max())
    span = hi - lo or hi * 0.01
    ylo, yhi = lo - span * 0.06, hi + span * 0.06
    notes = []
    if plan:
        vals = [v for v in [plan.get("entry"), plan.get("sl"), *(plan.get("tps") or [])] if v]
        if vals:
            ylo, yhi = min(ylo, min(vals) - span * 0.03), max(yhi, max(vals) + span * 0.03)
        lq = plan.get("liquidation")
        if lq and (ylo - span * 0.35) <= lq <= (yhi + span * 0.35):
            ylo, yhi = min(ylo, lq - span * 0.02), max(yhi, lq + span * 0.02)
    xr = N + 9  # room for labels on the right

    def X(i):
        return max(i - off, 0)

    def visible(top, bottom):
        return top >= ylo and bottom <= yhi

    def box(idx, top, bottom, color, label, alpha=0.16, ls="-"):
        if not visible(top, bottom):
            return
        x0 = X(idx)
        ax.add_patch(Rectangle((x0 - 0.5, bottom), N - x0 + 0.5, top - bottom, facecolor=color, alpha=alpha,
                               edgecolor=color, lw=0.8, ls=ls))
        ax.text(N + 0.3, (top + bottom) / 2, label, color=color, fontsize=7.5, va="center")

    for kind, items, cb, cs, alpha in (("OB", marks.get("obs", []), C["ob_bull"], C["ob_bear"], 0.18),
                                       ("FVG", marks.get("fvgs", []), C["fvg_bull"], C["fvg_bear"], 0.11)):
        for direction, color in (("bullish", cb), ("bearish", cs)):
            for z in [z for z in items if z["direction"] == direction][-3:]:
                box(z["idx"], z["top"], z["bottom"], color, f"{tf} {kind}", alpha)
    for b in marks.get("breakers", [])[-2:]:
        box(b["idx"], b["top"], b["bottom"], C["breaker"], f"{tf} breaker", 0.12, "--")

    for e in [e for e in marks.get("events", []) if e["idx"] >= off][-6:]:
        x0, x1 = X(e["swing_idx"]), X(e["idx"])
        col = C["choch"] if e["type"] == "CHoCH" else C["bos"]
        ax.plot([x0, x1], [e["level"], e["level"]], color=col, lw=1.0, ls="-")
        ax.text((x0 + x1) / 2, e["level"], e["type"], color=col, fontsize=8, ha="center",
                va="bottom" if e["direction"] == "bullish" else "top", fontweight="bold")

    sw = [s for s in marks.get("swings", []) if s["idx"] >= off]
    if sw:
        hs = [s for s in sw if s["kind"] == "high"]
        ls_ = [s for s in sw if s["kind"] == "low"]
        ax.scatter([X(s["idx"]) for s in hs], [s["price"] + span * 0.012 for s in hs], marker="v", s=16, color=C["swing"])
        ax.scatter([X(s["idx"]) for s in ls_], [s["price"] - span * 0.012 for s in ls_], marker="^", s=16, color=C["swing"])

    drawn = []
    for name, price in marks.get("levels", []):
        if price is None or not (ylo <= price <= yhi) or any(abs(price - d) / price < 0.0015 for d in drawn):
            continue
        drawn.append(price)
        ax.hlines(price, -1, N, color=C["liq"], lw=0.8, ls="--", alpha=0.85)
        ax.text(0.5, price, f" {name} {price:g}", color=C["liq"], fontsize=7.5, va="bottom")
    if marks.get("eq") and ylo <= marks["eq"] <= yhi:
        ax.hlines(marks["eq"], -1, N, color=C["eq"], lw=0.9, ls="-.", alpha=0.9)
        ax.text(0.5, marks["eq"], " 4H 50% (EQ)", color=C["eq"], fontsize=7.5, va="bottom")

    if plan:
        z = plan.get("entry_zone")
        if z:
            ax.add_patch(Rectangle((-1, min(z)), N + 1, max(z) - min(z), color=C["entry"], alpha=0.12, lw=0))
        for key, col, ls, lab in (("entry", C["entry"], "-", "ENTRY"), ("sl", C["sl"], "-", "SL")):
            if plan.get(key):
                ax.hlines(plan[key], -1, N, color=col, lw=1.3, ls=ls)
                ax.text(N + 0.3, plan[key], f"{lab} {plan[key]:g}", color=col, fontsize=8.5, va="center", fontweight="bold")
        for i, t in enumerate(plan.get("tps") or [], 1):
            ax.hlines(t, -1, N, color=C["tp"], lw=1.1, ls="--")
            ax.text(N + 0.3, t, f"TP{i} {t:g}", color=C["tp"], fontsize=8.5, va="center", fontweight="bold")
        lq = plan.get("liquidation")
        if lq:
            if ylo <= lq <= yhi:
                ax.hlines(lq, -1, N, color=C["liqp"], lw=1.0, ls=":")
                ax.text(N + 0.3, lq, f"LIQ {lq:g}", color=C["liqp"], fontsize=8, va="center")
            else:
                notes.append(f"Liquidation {lq:g} (off chart)")

    ax.set_ylim(ylo, yhi)
    ax.set_xlim(-1, xr)
    pair = symbol.split(":")[0]
    title = f"{pair}  {tf.upper()}  |  last close {dfw['close'].iloc[-1]:g}  |  {dfw.index[-1]:%Y-%m-%d %H:%M} UTC"
    if notes:
        title += "  |  " + "; ".join(notes)
    ax.set_title(title, color=C["text"], loc="left", fontsize=10)

    coin = pair.split("/")[0]
    out_dir = Path(out_dir) if out_dir else CHARTS / coin
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = stamp or datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    path = out_dir / f"{stamp}_{tf}.png"
    fig.savefig(path, dpi=110, facecolor=C["bg"])
    plt.close(fig)
    return str(path)


def render_all(symbol: str, frames: dict, cfg: dict | None = None, plan: dict | None = None,
               tfs=("1d", "4h", "1h", "15m"), marks: str = "auto", stamp: str | None = None,
               out_dir: Path | None = None) -> dict:
    cfg = cfg or load_config()
    length = int(cfg_get(cfg, "scanner.swing_length", 5))
    ltf_len = int(cfg_get(cfg, "smc.ltf_swing_length", 3))
    stamp = stamp or datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    levels, eq = [], None
    if "4h" in frames:
        k = smc.key_levels(frames["4h"])
        from lib.bias import monthly_open
        k["MO"] = monthly_open(frames["4h"])
        levels = [(n, k.get(n)) for n in ("PDH", "PDL", "PWH", "PWL", "MO") if k.get(n) is not None]
        pdz = smc.premium_discount(frames["4h"], length)
        eq = pdz["equilibrium"] if pdz else None
    out = {}
    for tf in tfs:
        df = frames.get(tf)
        if df is None or len(df) < 30:
            continue
        lv = list(levels)
        if tf in ("1h", "15m"):
            ar = smc.asia_range(df, cfg)
            if ar:
                lv += [("Asia H", ar["high"]), ("Asia L", ar["low"])]
        mk = auto_marks(df, ltf_len if tf == "15m" else length, lv, eq) if marks == "auto" else {"levels": [], "eq": eq}
        out[tf] = render(df, tf, symbol, mk, plan, out_dir, stamp=stamp)
    return out


def _load_plan(path: str | None) -> dict | None:
    if not path:
        return None
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return data.get("plan") if isinstance(data.get("plan"), dict) else data


def _main() -> None:
    from lib.config import fix_console
    from lib.data import get_ohlcv, to_symbol

    fix_console()
    ap = argparse.ArgumentParser(description="Render marked charts")
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--tf", default="1d,4h,1h,15m")
    ap.add_argument("--marks", choices=["auto", "none"], default="auto")
    ap.add_argument("--plan", help="plan JSON or deep-scan report JSON")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    sym = to_symbol(a.symbol)
    tfs = [t.strip() for t in a.tf.split(",") if t.strip()]
    limits = {"1d": 300, "4h": 300, "1h": 400, "15m": 300}
    frames = {tf: get_ohlcv(sym, tf, limits.get(tf, 300)) for tf in set(tfs) | {"4h"}}
    paths = render_all(sym, frames, plan=_load_plan(a.plan), tfs=tfs, marks=a.marks)
    print(json.dumps({"charts": paths}, indent=1) if a.json else "\n".join(paths.values()))


if __name__ == "__main__":
    _main()
