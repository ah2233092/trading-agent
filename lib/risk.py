"""Risk & leverage math (risk-leverage skill). Never compute these numbers by hand.

- Live MEXC contract specs (contract size, min qty, qty step, tick, fees, maintenance margin)
- Position size from risk_per_trade_usd including round-trip fees, rounded to the contract step
- Lowest leverage that fits max_margin_per_trade, capped at max_leverage (isolated)
- Isolated liquidation estimate; must sit beyond the SL by liq_buffer_pct
- TP1 must reach min_rr_tp1, the final TP min_rr_final; net reward after fees

CLI:
    python lib/risk.py --symbol SOL/USDT:USDT --side long --entry 150 --sl 146 --tp 158,162,170 [--order limit] [--json]
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.config import cfg_get, load_config


@dataclass
class ContractSpec:
    symbol: str
    contract_size: float = 1.0     # base units per contract
    min_qty: float = 1.0           # contracts
    qty_step: float = 1.0          # contracts
    tick_size: float = 0.0001
    maker_fee: float = 0.0         # fraction, 0.0002 = 0.02 %
    taker_fee: float = 0.0002
    mmr: float = 0.005             # maintenance margin rate (first risk tier)
    max_leverage: float = 100.0
    source: str = "default"


def spec_from_market(m: dict, exchange_id: str = "mexc", decimal_places: bool = False) -> ContractSpec:
    info = m.get("info") or {}
    prec, lim = m.get("precision") or {}, m.get("limits") or {}

    def step(v, default):
        if v is None:
            return default
        return 10 ** (-v) if decimal_places else float(v)

    qty_step = step(prec.get("amount"), 1.0)
    mmr = info.get("maintenanceMarginRate") or info.get("maintMarginRatio") or info.get("mmr")
    return ContractSpec(
        symbol=m["symbol"],
        contract_size=float(m.get("contractSize") or 1.0),
        min_qty=float((lim.get("amount") or {}).get("min") or qty_step),
        qty_step=qty_step,
        tick_size=step(prec.get("price"), 0.0001),
        maker_fee=float(m.get("maker") if m.get("maker") is not None else 0.0),
        taker_fee=float(m.get("taker") if m.get("taker") is not None else 0.0002),
        mmr=float(mmr) if mmr else 0.005,
        max_leverage=float((lim.get("leverage") or {}).get("max") or 100),
        source=f"{exchange_id} live" if mmr else f"{exchange_id} live (mmr default 0.5%)",
    )


def get_spec(symbol: str, ex=None) -> ContractSpec:
    import ccxt
    from lib.data import get_exchange, markets

    ex = ex or get_exchange()
    m = markets(ex)[symbol]
    return spec_from_market(m, ex.id, getattr(ex, "precisionMode", None) == ccxt.DECIMAL_PLACES)


# ------------------------------------------------------------------ helpers --

def _decimals(step: float) -> int:
    s = f"{step:.12f}".rstrip("0")
    return max(0, len(s.split(".")[1])) if "." in s else 0


def round_price(p: float, tick: float, mode: str = "nearest") -> float:
    if not tick:
        return p
    q = p / tick
    q = math.floor(q + 1e-9) if mode == "down" else math.ceil(q - 1e-9) if mode == "up" else round(q)
    return round(q * tick, _decimals(tick))


def floor_step(x: float, step: float) -> float:
    if step <= 0:
        return x
    return round(math.floor(x / step + 1e-9) * step, _decimals(step))


def liquidation_price(side: str, entry: float, leverage: float, mmr: float, taker_fee: float = 0.0) -> float:
    """Isolated, linear USDT contract. Liquidation when the loss eats the initial margin
    down to the maintenance margin (+ the closing fee, conservative)."""
    m = mmr + taker_fee
    if side == "long":
        return entry * (1 - 1 / leverage) / (1 - m)
    return entry * (1 + 1 / leverage) / (1 + m)


def liq_distance_pct(side: str, sl: float, liq: float) -> float:
    """How far the liquidation price sits beyond the SL, in % of the SL price (positive = safe side)."""
    return (sl - liq) / sl * 100 if side == "long" else (liq - sl) / sl * 100


def max_leverage_for_liq(side: str, entry: float, sl: float, spec: ContractSpec, buffer_pct: float, cap: int) -> int:
    for lev in range(int(cap), 0, -1):
        liq = liquidation_price(side, entry, lev, spec.mmr, spec.taker_fee)
        if liq_distance_pct(side, sl, liq) >= buffer_pct:
            return lev
    return 0


# --------------------------------------------------------------------- plan --

def build_plan(side: str, entry: float, sl: float, tps: list[float], spec: ContractSpec,
               cfg: dict | None = None, order: str = "limit") -> dict:
    """Size, leverage, liquidation and rewards for fixed entry / SL / TPs."""
    cfg = cfg or load_config()
    side = side.lower()
    tps = [t for t in tps if t is not None]
    rejects: list[str] = []
    notes: list[str] = []
    if side not in ("long", "short"):
        raise ValueError("side must be long or short")
    good = (sl < entry and all(t > entry for t in tps)) if side == "long" else (sl > entry and all(t < entry for t in tps))
    if not good or not tps:
        return {"ok": False, "rejects": ["invalid levels: for a long SL < entry < TPs, for a short TPs < entry < SL"],
                "side": side, "entry": entry, "sl": sl, "tps": tps}

    balance = float(cfg_get(cfg, "account_balance", 20))
    risk_target = min(float(cfg_get(cfg, "risk_per_trade_usd", 1.0)),
                      balance * float(cfg_get(cfg, "risk_per_trade_pct", 100)) / 100)
    max_margin = float(cfg_get(cfg, "max_margin_per_trade", balance))
    cap = int(min(float(cfg_get(cfg, "max_leverage", 20)), spec.max_leverage))
    liq_buf = float(cfg_get(cfg, "liq_buffer_pct", 1.5))
    min_rr1, min_rr_final = float(cfg_get(cfg, "min_rr_tp1", 2.0)), float(cfg_get(cfg, "min_rr_final", 3.0))
    tp1_close = float(cfg_get(cfg, "tp1_close_pct", 50)) / 100

    dist = abs(entry - sl)
    rr = [abs(t - entry) / dist for t in tps]
    if rr[0] < min_rr1:
        rejects.append(f"TP1 only 1:{rr[0]:.2f} (< 1:{min_rr1:g})")
    if rr[-1] < min_rr_final:
        rejects.append(f"final TP only 1:{rr[-1]:.2f} (< 1:{min_rr_final:g})")

    fee_in = spec.maker_fee if order == "limit" else spec.taker_fee
    fee_sl, fee_tp = spec.taker_fee, spec.maker_fee  # SL = stop-market, TPs = limit
    loss_per_unit = dist + entry * fee_in + sl * fee_sl
    cs = spec.contract_size
    contracts = floor_step(risk_target / loss_per_unit / cs, spec.qty_step)

    lev_liq = max_leverage_for_liq(side, entry, sl, spec, liq_buf, cap)
    if lev_liq == 0:
        rejects.append("liquidation too close to SL even at 1x")
        lev_liq = 1
    need = math.ceil((contracts * cs * entry) / max_margin - 1e-12) if contracts else 1
    if need > lev_liq:
        contracts = floor_step(max_margin * lev_liq / (cs * entry), spec.qty_step)
        notes.append(f"size cut to fit ${max_margin:g} margin at max safe leverage {lev_liq}x — risk below target")
    if contracts < spec.min_qty:
        min_risk = spec.min_qty * cs * loss_per_unit
        rejects.append(f"size below exchange minimum ({spec.min_qty:g} contracts needs ${min_risk:.2f} risk)")
        contracts = spec.min_qty

    qty = contracts * cs
    notional = qty * entry
    leverage = max(1, min(lev_liq, math.ceil(notional / max_margin - 1e-12)))
    margin = notional / leverage
    liq = liquidation_price(side, entry, leverage, spec.mmr, spec.taker_fee)
    liq_dist = liq_distance_pct(side, sl, liq)
    if liq_dist < liq_buf:
        rejects.append(f"liquidation only {liq_dist:.2f}% beyond SL (< {liq_buf:g}%)")

    entry_fee = notional * fee_in
    sl_fee = qty * sl * fee_sl
    risk_usd = qty * dist + entry_fee + sl_fee
    rewards = []
    for t in tps:
        gross = qty * abs(t - entry)
        rewards.append(gross - entry_fee - qty * t * fee_tp)
    # partial schedule: tp1_close at TP1, rest split between the later TPs, SL to entry after TP1
    rest = 1 - tp1_close
    shares = [tp1_close] + ([rest / (len(tps) - 1)] * (len(tps) - 1) if len(tps) > 1 else [])
    if len(tps) == 1:
        shares = [1.0]
    partial = sum(s * qty * abs(t - entry) for s, t in zip(shares, tps)) - entry_fee - \
        sum(s * qty * t * fee_tp for s, t in zip(shares, tps))

    tick = spec.tick_size
    plan = {
        "ok": not rejects,
        "rejects": rejects,
        "notes": notes,
        "side": side,
        "order": order,
        "entry": round_price(entry, tick),
        "sl": round_price(sl, tick),
        "tps": [round_price(t, tick) for t in tps],
        "sl_pct": round(dist / entry * 100, 3),
        "rr": [round(r, 2) for r in rr],
        "contracts": contracts,
        "qty": round(qty, 10),
        "notional_usd": round(notional, 4),
        "leverage": int(leverage),
        "max_safe_leverage": int(lev_liq),
        "margin_usd": round(margin, 4),
        "margin_mode": cfg_get(cfg, "margin_mode", "isolated"),
        "liquidation": round_price(liq, tick),
        "liq_beyond_sl_pct": round(liq_dist, 2),
        "liq_ok": liq_dist >= liq_buf,
        "risk_target_usd": round(risk_target, 4),
        "risk_usd": round(risk_usd, 4),
        "fees_usd": {"entry": round(entry_fee, 4), "sl_exit": round(sl_fee, 4)},
        "net_reward_usd": [round(r, 4) for r in rewards],
        "net_reward_partial_usd": round(partial, 4),
        "partial_schedule": [round(s * 100) for s in shares],
        "spec": asdict(spec),
    }
    plan["text"] = plan_text(plan)
    return plan


def plan_text(p: dict) -> str:
    tps = " | ".join(f"TP{i} {t:g} (1:{r:g})" for i, (t, r) in enumerate(zip(p["tps"], p["rr"]), 1))
    nets = " / ".join(f"TP{i} ${r:.2f}" for i, r in enumerate(p["net_reward_usd"], 1))
    return "\n".join([
        f"Entry {p['entry']:g} | SL {p['sl']:g} ({p['sl_pct']:.2f}%, 1 R)",
        tps,
        f"Qty {p['contracts']:g} contracts | Notional ${p['notional_usd']:.2f} | Leverage {p['leverage']}x "
        f"{p['margin_mode']} | Margin ${p['margin_usd']:.2f}",
        f"Liquidation ≈ {p['liquidation']:g} (SL se {p['liq_beyond_sl_pct']:.2f}% door) {'✅' if p['liq_ok'] else '❌'}",
        f"Risk ${p['risk_usd']:.2f} | Net reward {nets} (fees ke baad)",
    ] + ([f"❌ {r}" for r in p["rejects"]]) + ([f"ℹ️ {n}" for n in p["notes"]]))


# -------------------------------------------------------- levels -> plan --

def choose_targets(side: str, entry: float, sl: float, levels: list[dict], cfg: dict | None = None) -> dict:
    """Pick TP1/TP2/TP3 from opposing liquidity levels.

    levels: [{"name", "price", "strength": "key" | "htf" | "minor"}]
    TP1 = first level with R >= min_rr_tp1; a key/htf level closer than that blocks the trade.
    TP2 = next level beyond TP1 (key/htf preferred). TP3 = major HTF liquidity beyond TP2.
    Missing targets fall back to R multiples (marked as such).
    """
    cfg = cfg or load_config()
    min_rr1, min_rr_final = float(cfg_get(cfg, "min_rr_tp1", 2.0)), float(cfg_get(cfg, "min_rr_final", 3.0))
    sgn = 1 if side == "long" else -1
    dist = abs(entry - sl)
    lv = []
    for x in levels:
        p = x.get("price")
        if p is None or sgn * (p - entry) <= 0:
            continue
        if any(abs(p - y["price"]) / p < 0.001 for y in lv):
            continue  # duplicate within 0.1 %
        lv.append(dict(x, r=abs(p - entry) / dist))
    lv.sort(key=lambda x: x["r"])
    out = {"tp": [], "blocked_by": None, "fallback": []}
    blocker = next((x for x in lv if x["r"] < min_rr1 and x.get("strength") in ("key", "htf")), None)
    if blocker:
        out["blocked_by"] = blocker
    beyond = [x for x in lv if x["r"] >= min_rr1]
    if beyond:
        tp1 = beyond[0]
        out["tp"].append(tp1)
        later = [x for x in beyond[1:] if x["r"] > tp1["r"] * 1.1]
        tp2 = next((x for x in later if x.get("strength") in ("key", "htf")), later[0] if later else None)
        if tp2:
            out["tp"].append(tp2)
            later3 = [x for x in later if x["r"] > tp2["r"] * 1.1]
            tp3 = next((x for x in later3 if x.get("strength") == "htf" and x["r"] >= min_rr_final),
                       next((x for x in reversed(later3) if x["r"] >= min_rr_final), None))
            if tp3:
                out["tp"].append(tp3)
    while len(out["tp"]) < 3:
        r = out["tp"][-1]["r"] + 1 if out["tp"] else min_rr1
        if len(out["tp"]) == 2:
            r = max(r, min_rr_final)
        out["tp"].append({"name": f"{r:.1f}R (no level)", "price": entry + sgn * r * dist, "strength": "fallback", "r": r})
        out["fallback"].append(len(out["tp"]))
    return out


def plan_from_setup(side: str, setup: dict, levels: list[dict], atr_1h: float, spec: ContractSpec,
                    cfg: dict | None = None, price: float | None = None) -> dict:
    """Entry (POI middle, limit), structure SL + ATR buffer, TPs from liquidity, then build_plan."""
    cfg = cfg or load_config()
    side = side.lower()
    poi = setup.get("poi")
    if not poi:
        return {"ok": False, "rejects": ["no POI from the SMC setup"], "side": side}
    buf = float(cfg_get(cfg, "sl_atr_buffer", 0.3)) * (atr_1h or 0)
    ltf_ok = bool((setup.get("ltf") or {}).get("confirmed"))
    if setup.get("state") == "in_zone" and ltf_ok and price is not None:
        entry, order = price, "market"
    else:
        entry, order = poi["mid"], "limit"
    ref = setup.get("sl_ref")
    if side == "long":
        base = min(x for x in (ref, poi["bottom"]) if x is not None)
        sl = round_price(base - buf, spec.tick_size, "down")
    else:
        base = max(x for x in (ref, poi["top"]) if x is not None)
        sl = round_price(base + buf, spec.tick_size, "up")
    tg = choose_targets(side, entry, sl, levels, cfg)
    tick = spec.tick_size
    # take profit one tick in front of the liquidity level so the limit fills
    tps = [round_price(t["price"] - tick if side == "long" else t["price"] + tick, tick) for t in tg["tp"]]
    plan = build_plan(side, entry, sl, tps, spec, cfg, order)
    plan["entry_zone"] = [poi["bottom"], poi["top"]]
    plan["targets"] = [{"name": t["name"], "level": t["price"], "strength": t.get("strength")} for t in tg["tp"]]
    if tg["fallback"]:
        plan["notes"].append("no liquidity level for TP" + ",".join(map(str, tg["fallback"])) + " — R-multiple used")
    if tg["blocked_by"]:
        b = tg["blocked_by"]
        plan["rejects"].insert(0, f"TP1 blocked by {b['name']} at {b['price']:g} (only 1:{b['r']:.2f})")
        plan["ok"] = False
    plan["text"] = plan_text(plan)
    return plan


def _main() -> None:
    from lib.config import fix_console
    from lib.data import to_symbol

    fix_console()
    ap = argparse.ArgumentParser(description="Futures risk / leverage calculator")
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--side", required=True, choices=["long", "short"])
    ap.add_argument("--entry", type=float, required=True)
    ap.add_argument("--sl", type=float, required=True)
    ap.add_argument("--tp", required=True, help="comma list TP1,TP2,TP3")
    ap.add_argument("--order", choices=["limit", "market"], default="limit")
    ap.add_argument("--offline", action="store_true", help="use default contract spec (no exchange call)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    sym = to_symbol(a.symbol)
    spec = ContractSpec(sym) if a.offline else get_spec(sym)
    plan = build_plan(a.side, a.entry, a.sl, [float(x) for x in a.tp.split(",") if x.strip()], spec, order=a.order)
    print(json.dumps(plan, indent=1) if a.json else plan.get("text") or "\n".join(plan["rejects"]))


if __name__ == "__main__":
    _main()
