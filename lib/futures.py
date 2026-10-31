"""Futures positioning (futures-data skill): funding, open interest, long/short ratio, liquidity.

MEXC first. MEXC's public API has funding (current + history) and the current
open interest (ticker holdVol) but no OI history and no long/short ratio — those
come from `futures_data_fallbacks` (default Binance USD-M, Bybit, OKX). Every
metric reports its source.

CLI:
    python lib/futures.py --symbol SOL/USDT:USDT [--side long] [--json]
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.config import cfg_get, load_config

log = logging.getLogger("futures")


# ------------------------------------------------------------------ fetchers --

def fetch_funding(symbol: str, ex) -> dict:
    from lib.data import call

    out = {"source": ex.id, "current_pct": None, "history_pct": [], "interval": None}
    fr = call(ex.fetch_funding_rate, symbol, retries=2)
    if fr.get("fundingRate") is not None:
        out["current_pct"] = round(float(fr["fundingRate"]) * 100, 5)
    out["interval"] = fr.get("interval")
    out["next_funding_utc"] = fr.get("fundingDatetime") or fr.get("nextFundingDatetime")
    try:
        hist = call(ex.fetch_funding_rate_history, symbol, None, 8, retries=2)
        out["history_pct"] = [round(float(h["fundingRate"]) * 100, 5) for h in hist[-8:] if h.get("fundingRate") is not None]
    except Exception as e:
        log.info("funding history unavailable on %s: %s", ex.id, e)
    return out


def _oi_value(row: dict) -> float | None:
    for k in ("openInterestAmount", "openInterestValue"):
        if row.get(k) is not None:
            return float(row[k])
    return None


def fetch_oi_history(symbol: str, exchanges: list) -> dict:
    """OI change over the last 1H and 4H (1H buckets) from the first exchange that has it."""
    from lib.data import call

    for ex in exchanges:
        if not ex.has.get("fetchOpenInterestHistory"):
            continue
        try:
            if not ex.markets:
                call(ex.load_markets, retries=2)
            if symbol not in ex.markets:
                continue
            rows = call(ex.fetch_open_interest_history, symbol, "1h", None, 6, retries=2)
            vals = [v for v in (_oi_value(r) for r in rows) if v]
            if len(vals) < 2:
                continue
            ch1 = (vals[-1] / vals[-2] - 1) * 100
            ch4 = (vals[-1] / vals[-5] - 1) * 100 if len(vals) >= 5 else None
            return {"source": ex.id, "latest": vals[-1], "change_1h_pct": round(ch1, 2),
                    "change_4h_pct": round(ch4, 2) if ch4 is not None else None}
        except Exception as e:
            log.info("OI history failed on %s: %s", ex.id, str(e)[:150])
    return {"source": None, "latest": None, "change_1h_pct": None, "change_4h_pct": None}


def fetch_long_short(symbol: str, exchanges: list) -> dict:
    from lib.data import call

    for ex in exchanges:
        if not ex.has.get("fetchLongShortRatioHistory"):
            continue
        try:
            if not ex.markets:
                call(ex.load_markets, retries=2)
            if symbol not in ex.markets:
                continue
            rows = call(ex.fetch_long_short_ratio_history, symbol, "1h", None, 5, retries=2)
            vals = [float(r["longShortRatio"]) for r in rows if r.get("longShortRatio") is not None]
            if vals:
                return {"source": ex.id, "ratio": round(vals[-1], 3),
                        "prev": round(vals[0], 3) if len(vals) > 1 else None}
        except Exception as e:
            log.info("L/S ratio failed on %s: %s", ex.id, str(e)[:150])
    return {"source": None, "ratio": None, "prev": None}


def fetch_mexc_oi_now(symbol: str, ex) -> float | None:
    """Current open interest (base units) from the MEXC ticker (holdVol x contractSize)."""
    from lib.data import call

    try:
        t = call(ex.fetch_ticker, symbol, retries=2)
        hold = (t.get("info") or {}).get("holdVol")
        cs = ex.market(symbol).get("contractSize") or 1
        return float(hold) * float(cs) if hold is not None else None
    except Exception:
        return None


# ------------------------------------------------------------ interpretation --

def funding_level(pct: float | None, extreme: float) -> tuple[str, str]:
    if pct is None:
        return "unknown", "none"
    a = abs(pct)
    level = "extreme" if a >= extreme else "elevated" if a >= extreme / 2 else "normal"
    crowd = "none" if level == "normal" else ("long" if pct > 0 else "short")
    return level, crowd


def oi_reading(price_chg: float | None, oi_chg: float | None, flat: float = 0.3) -> str:
    if price_chg is None or oi_chg is None:
        return "n/a"
    if abs(oi_chg) < flat:
        return "OI flat — no new positioning"
    if price_chg >= 0:
        return "new longs (healthy up move)" if oi_chg > 0 else "short covering (weaker up move)"
    return "new shorts" if oi_chg > 0 else "long liquidation / exit"


def analyze_futures(symbol: str, side: str | None = None, cfg: dict | None = None,
                    price_1h_pct: float | None = None, price_4h_pct: float | None = None,
                    data: dict | None = None) -> dict:
    """data: pre-fetched {"funding", "oi", "ls", "ticker", "oi_now"} (tests); otherwise fetched live."""
    cfg = cfg or load_config()
    side = (side or "").lower() or None
    if data is None:
        data = fetch_all(symbol, cfg)
    f, oi, ls, tk = data.get("funding") or {}, data.get("oi") or {}, data.get("ls") or {}, data.get("ticker") or {}
    if price_1h_pct is None:
        price_1h_pct = data.get("price_1h_pct")
    if price_4h_pct is None:
        price_4h_pct = data.get("price_4h_pct")

    extreme = float(cfg_get(cfg, "funding_extreme", 0.05))
    level, crowd = funding_level(f.get("current_pct"), extreme)
    read1, read4 = oi_reading(price_1h_pct, oi.get("change_1h_pct")), oi_reading(price_4h_pct, oi.get("change_4h_pct"))
    ls_ext = float(cfg_get(cfg, "long_short_extreme", 2.5))
    ratio = ls.get("ratio")
    ls_note = "n/a" if ratio is None else ("longs crowded (contrarian warning)" if ratio >= ls_ext else
                                           "shorts crowded (contrarian warning)" if ratio <= 1 / ls_ext else "balanced")
    min_vol = float(cfg_get(cfg, "universe.min_24h_volume_usd", 0) or 0)
    max_spread = cfg_get(cfg, "universe.max_spread_pct")
    vol, spread = tk.get("quote_volume"), tk.get("spread_pct")
    liquidity_ok = not ((vol is not None and vol < min_vol) or
                        (spread is not None and max_spread is not None and spread > float(max_spread)))

    def verdict_for(s: str) -> tuple[str, str]:
        if not liquidity_ok:
            return "against", "low volume / wide spread"
        mine, other = ("long", "short") if s == "long" else ("short", "long")
        if crowd == mine and level == "extreme":
            return "against", f"funding extreme, {mine}s crowded (squeeze risk)"
        if ratio is not None and ((s == "long" and ratio >= ls_ext) or (s == "short" and ratio <= 1 / ls_ext)):
            return "against", f"L/S ratio {ratio} — {mine}s crowded"
        healthy = "new longs" if s == "long" else "new shorts"
        if read1.startswith(healthy) or read4.startswith(healthy):
            return "supportive", f"{healthy} (price + OI agree)"
        if crowd == other:
            return "supportive", f"{other}s crowded — squeeze fuel for the {s}"
        if crowd == mine:
            return "neutral", f"funding {level}, {mine}s leaning in"
        if oi.get("change_1h_pct") is None and f.get("current_pct") is None:
            return "neutral", "no futures data available"
        return "neutral", "nothing unusual"

    verdicts = {s: dict(zip(("verdict", "reason"), verdict_for(s))) for s in ("long", "short")}
    chosen = verdicts.get(side) if side else None

    def pct(x, nd=2):
        return "n/a" if x is None else f"{x:+.{nd}f}%"

    lines = [
        f"Funding: {('n/a' if f.get('current_pct') is None else format(f['current_pct'], '.4f') + '%')} "
        f"({level}, crowd: {crowd}) [{f.get('source', 'n/a')}]",
        f"OI: 1H {pct(oi.get('change_1h_pct'))}, 4H {pct(oi.get('change_4h_pct'))} → {read1 if read1 != 'n/a' else read4}"
        f" [{oi.get('source') or 'n/a'}]",
        f"L/S ratio: {ratio if ratio is not None else 'n/a'} ({ls_note}) [{ls.get('source') or 'n/a'}]",
        f"Liquidity: 24h vol ${(vol or 0) / 1e6:,.1f}M, spread "
        f"{'n/a' if spread is None else format(spread, '.3f') + '%'}{'' if liquidity_ok else ' ❌'}",
    ]
    if chosen:
        lines.append(f"Verdict: {chosen['verdict']} ({chosen['reason']})")
    else:
        lines.append(f"Verdict: long {verdicts['long']['verdict']} / short {verdicts['short']['verdict']}")
    return {
        "symbol": symbol, "side": side,
        "funding": dict(f, level=level, crowd=crowd),
        "open_interest": dict(oi, reading_1h=read1, reading_4h=read4, mexc_now=data.get("oi_now")),
        "long_short": dict(ls, note=ls_note),
        "liquidity": {"quote_volume_24h": vol, "spread_pct": spread, "ok": liquidity_ok, "source": tk.get("source")},
        "price_change": {"1h_pct": price_1h_pct, "4h_pct": price_4h_pct},
        "verdicts": verdicts,
        "verdict": chosen["verdict"] if chosen else None,
        "reason": chosen["reason"] if chosen else None,
        "text": "\n".join(lines),
    }


def fetch_all(symbol: str, cfg: dict) -> dict:
    """Live fetch: MEXC funding + ticker + OI now; fallbacks for OI history and L/S ratio."""
    from lib.data import get_exchange, get_ohlcv, get_ticker, markets

    ex = get_exchange(cfg=cfg)
    markets(ex)
    out: dict = {}
    try:
        out["funding"] = fetch_funding(symbol, ex)
    except Exception as e:
        log.warning("funding unavailable on %s: %s", ex.id, str(e)[:150])
        out["funding"] = {"source": None, "current_pct": None, "history_pct": []}
    try:
        out["ticker"] = dict(get_ticker(symbol, ex), source=ex.id)
    except Exception as e:
        log.warning("ticker failed: %s", e)
        out["ticker"] = {}
    out["oi_now"] = fetch_mexc_oi_now(symbol, ex) if ex.id == "mexc" else None
    others = [ex] + [get_exchange(x) for x in (cfg_get(cfg, "futures_data_fallbacks", []) or []) if x != ex.id]
    out["oi"] = fetch_oi_history(symbol, others)
    out["ls"] = fetch_long_short(symbol, others)
    try:
        h1 = get_ohlcv(symbol, "1h", 10, ex=ex)
        c = h1["close"]
        out["price_1h_pct"] = round((c.iloc[-1] / c.iloc[-2] - 1) * 100, 3)
        out["price_4h_pct"] = round((c.iloc[-1] / c.iloc[-5] - 1) * 100, 3)
    except Exception:
        pass
    return out


def _main() -> None:
    from lib.config import fix_console
    from lib.data import to_symbol

    fix_console()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(description="Futures positioning data")
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--side", choices=["long", "short"])
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    res = analyze_futures(to_symbol(a.symbol), a.side)
    print(json.dumps(res, indent=1, default=str) if a.json else res["text"])


if __name__ == "__main__":
    _main()
