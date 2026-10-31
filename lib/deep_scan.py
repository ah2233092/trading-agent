"""Deep-scan engine behind /deep-scan: one coin -> one JSON report.

    python lib/deep_scan.py --symbol SOL --json                 # bias -> smc -> futures -> risk -> score -> charts
    python lib/deep_scan.py --all-pending [--json]              # every coin in alerts/pending.json, strongest first
    python lib/deep_scan.py --finalize reports/SOL/latest.json --news clear [--news-note "..."]   # add news, re-score
    python lib/deep_scan.py --close-alerts [--coin SOL] [--status trade|no_trade|expired|handled] [--no-git]

News is not a script: the news-check skill (WebSearch) runs between the scan and --finalize.
Reports are saved to reports/<COIN>/<stamp>.json and reports/<COIN>/latest.json.
"""
from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from lib import chart, data, futures, risk, smc
from lib.bias import BTC, analyze_bias
from lib.config import ROOT, cfg_get, fmt_local, iso_utc, load_config, utc_now
from lib.journal import read_setup_stats
from lib.score import compute_score, score_text
from scanner import store
from scanner.light_score import btc_context, score_coin

log = logging.getLogger("deep_scan")
REPORTS = ROOT / "reports"


def jsonable(o):
    if isinstance(o, dict):
        return {str(k): jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [jsonable(v) for v in o]
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return None if np.isnan(o) else float(o)
    if isinstance(o, float) and np.isnan(o):
        return None
    if isinstance(o, (pd.Timestamp,)):
        return o.strftime("%Y-%m-%dT%H:%M:%SZ")
    return o


def _g(x) -> str:
    return "-" if x is None else f"{x:g}"


# ------------------------------------------------------------------ alerts --

def latest_alert(symbol: str) -> dict | None:
    alerts = [a for a in store.load_pending() if a.get("symbol") == symbol]
    return alerts[-1] if alerts else None


def revalidate(alert: dict, frames: dict, btc_ctx: dict | None, cfg: dict, now) -> dict:
    """Is the trigger of an old alert still valid at the current price?"""
    lv = alert.get("levels") or {}
    h1 = frames["1h"]
    price = float(h1["close"].iloc[-1])
    hint = alert.get("direction_hint")
    created = store.parse_iso(alert["created_at"])
    since = h1[h1.index >= pd.Timestamp(created)]
    checks = []
    if lv.get("sweep_level") is not None:
        wick = lv.get("sweep_wick")
        if hint == "LONG":
            ok = price > lv["sweep_level"] and (wick is None or since.empty or float(since["low"].min()) >= wick)
        else:
            ok = price < lv["sweep_level"] and (wick is None or since.empty or float(since["high"].max()) <= wick)
        checks.append({"trigger": "sweep", "valid": bool(ok)})
    if lv.get("structure_level") is not None:
        ok = price > lv["structure_level"] if hint == "LONG" else price < lv["structure_level"]
        checks.append({"trigger": "structure", "valid": bool(ok)})
    now_res = score_coin(frames["4h"], h1, btc_ctx, cfg)
    if "near_poi" in (alert.get("triggers") or []):
        checks.append({"trigger": "near_poi", "valid": "near_poi" in now_res["triggers"] and now_res["direction"] == hint})
    still = any(c["valid"] for c in checks) or (
        now_res["direction"] == hint and now_res["score"] >= int(cfg_get(cfg, "scanner.min_alert_score", 60)))
    return {"status": "revalidated" if still else "expired", "checks": checks,
            "light_score_now": now_res["score"], "hint_now": now_res["direction"], "price_now": price}


# ------------------------------------------------------------- TP levels --

def build_levels(side: str, frames: dict, bias: dict, cfg: dict, price: float) -> list[dict]:
    """Opposing liquidity for TPs (buy-side above price for longs, sell-side below for shorts)."""
    length = int(cfg_get(cfg, "scanner.swing_length", 5))
    long = side == "long"
    out: list[dict] = []

    def add(name, p, strength):
        if p is None:
            return
        if (long and p > price) or (not long and p < price):
            out.append({"name": name, "price": float(p), "strength": strength})

    k = bias.get("key_levels") or {}
    for name in (("PDH", "PWH") if long else ("PDL", "PWL")):
        add(name, k.get(name), "key")
    add("MO", k.get("MO"), "key")
    for tf, sw_strength in (("1h", "minor"), ("4h", "htf")):
        pools = smc.liquidity_pools(frames[tf], length)
        for p in pools["buy_side" if long else "sell_side"]:
            strength = "key" if p["name"] in ("EQH", "EQL", "PDH", "PDL", "PWH", "PWL") else sw_strength
            add(f"{tf.upper()} {p['name'].replace('_', ' ')}", p["price"], strength)
    opp = "bearish" if long else "bullish"
    for ob in smc.order_blocks(frames["4h"], length):
        if ob["status"] != "broken" and ob["direction"] == opp:
            add(f"4H {opp} OB", ob["bottom"] if long else ob["top"], "htf")
    for tf, strength, max_age in (("1h", "minor", 72), ("4h", "htf", 120)):
        n = len(frames[tf])
        for g in smc.fair_value_gaps(frames[tf]):
            if g["direction"] == opp and g["status"] != "filled" and g["idx"] >= n - max_age:
                add(f"{tf.upper()} {opp} FVG", g["bottom"] if long else g["top"], strength)
    rng = (bias.get("h4") or {}).get("range") or {}
    add("4H range high" if long else "4H range low", rng.get("range_high" if long else "range_low"), "htf")
    if "1d" in frames and len(frames["1d"]) > 2 * length + 1:
        for p in smc.liquidity_pools(frames["1d"], length)["buy_side" if long else "sell_side"]:
            if p["name"].startswith("swing"):
                add(f"1D {p['name'].replace('_', ' ')}", p["price"], "htf")
    ar = smc.asia_range(frames["1h"], cfg)
    if ar and ar["complete"]:
        if long and not ar["swept_high"]:
            add("Asia high", ar["high"], "minor")
        if not long and not ar["swept_low"]:
            add("Asia low", ar["low"], "minor")
    return out


# --------------------------------------------------------------- analysis --

def analyze_symbol(symbol: str, cfg: dict | None = None, news: str | None = None, charts: bool = True,
                   now=None, alert: dict | None = None, save: bool = True) -> dict:
    cfg = cfg or load_config()
    now = now or utc_now()
    ex = data.get_exchange(cfg=cfg)
    length = int(cfg_get(cfg, "scanner.swing_length", 5))
    ltf_len = int(cfg_get(cfg, "smc.ltf_swing_length", 3))
    coin = data.coin_of(symbol)
    frames = data.get_frames(symbol, ex=ex)
    btc_frames = None if symbol == BTC else {"4h": data.get_ohlcv(BTC, "4h", 300, ex=ex),
                                             "1h": data.get_ohlcv(BTC, "1h", 400, ex=ex)}
    h1 = frames["1h"]
    price = float(h1["close"].iloc[-1])

    # 0) alert age / re-validation
    alert = alert if alert is not None else latest_alert(symbol)
    alert_info = None
    if alert:
        age = (now - store.parse_iso(alert["created_at"])).total_seconds() / 3600
        alert_info = {"id": alert.get("id"), "age_h": round(age, 2), "hint": alert.get("direction_hint"),
                      "light_score": alert.get("light_score"), "triggers": alert.get("trigger_labels")}
        if age > float(cfg_get(cfg, "alert_max_age_hours", 3)):
            bf = btc_frames or {"1h": h1, "4h": frames["4h"]}
            alert_info.update(revalidate(alert, frames, btc_context(bf["1h"], bf["4h"], cfg), cfg, now))
        else:
            alert_info["status"] = "fresh"

    # 1) HTF bias
    bias = analyze_bias(symbol, frames, btc_frames, cfg)
    direction = bias["direction"]
    side = direction.lower() if direction != "NONE" else None

    # 2) SMC setup
    if side:
        setup = smc.classify_setup(direction, frames["4h"], h1, frames.get("15m"), length, ltf_len, cfg)
    else:
        setup = {"direction": "NONE", "model": None, "name": None, "grade": "reject", "poi": None,
                 "reasons": ["HTF bias NONE — no setup search"], "pending": [], "candidates": []}
    for c in setup.get("candidates", [])[1:]:
        c.pop("detail", None)
    session = smc.session_info(pd.Timestamp(now), cfg)

    # 3) futures data
    try:
        fut = futures.analyze_futures(symbol, side, cfg)
    except Exception as e:
        log.warning("futures data failed: %s", e)
        fut = {"verdict": None, "reason": None, "liquidity": {"ok": True}, "text": f"Futures data unavailable ({e})"}

    # 4) risk plan
    plan, spec = None, None
    if side and setup.get("model"):
        try:
            spec = risk.get_spec(symbol, ex)
        except Exception as e:
            spec = risk.ContractSpec(symbol, source=f"default — live spec failed: {e}")
        levels = build_levels(side, frames, bias, cfg, price)
        plan = risk.plan_from_setup(side, setup, levels, setup.get("atr_1h") or 0, spec, cfg, price)

    # 5) setup stats
    stats = read_setup_stats(setup.get("model"))

    # 6) score
    score_inputs = {
        "direction": direction,
        "bias": {"d1": {"trend": bias["d1"]["trend"]}, "btc_for_direction": bias.get("btc_for_direction")},
        "setup": {k: setup.get(k) for k in ("model", "name", "grade", "quality", "displacement")},
        "futures": {"verdict": fut.get("verdict"), "reason": fut.get("reason"), "liquidity": fut.get("liquidity")},
        "session": session,
        "news": news or "pending",
        "stats": stats,
        "plan": {"ok": plan.get("ok"), "rejects": plan.get("rejects")} if plan else None,
    }
    score = compute_score(score_inputs, cfg)

    # 7) charts
    chart_paths = {}
    if charts:
        cp = {"entry": plan["entry"], "sl": plan["sl"], "tps": plan["tps"], "liquidation": plan.get("liquidation"),
              "entry_zone": plan.get("entry_zone")} if plan and plan.get("entry") else None
        try:
            chart_paths = chart.render_all(symbol, frames, cfg, plan=cp)
        except Exception as e:
            log.warning("chart render failed: %s", e)

    report = {
        "symbol": symbol, "coin": coin, "pair": data.pretty(symbol), "exchange": ex.id,
        "created_at": iso_utc(now), "created_at_pkt": fmt_local(now, cfg),
        "valid_until": iso_utc(now + timedelta(hours=float(cfg_get(cfg, "plan_valid_hours", 6)))),
        "price": price, "last_1h_candle": f"{h1.index[-1]:%Y-%m-%dT%H:%M:%SZ}",
        "alert": alert_info,
        "direction": direction,
        "decision": score["decision"],
        "bias": bias, "setup": setup, "session": session, "asia_range": smc.asia_range(h1, cfg),
        "futures": fut, "plan": plan, "stats": stats, "score": score, "score_inputs": score_inputs,
        "charts": chart_paths, "news": news or "pending", "news_note": None,
    }
    report["no_trade_reasons"] = no_trade_reasons(report) if score["decision"] != "TRADE" else []
    report["answer"] = format_answer(report, cfg)
    report = jsonable(report)
    if save:
        report["report_path"] = save_report(report)
    return report


# ----------------------------------------------------------------- answer --

def no_trade_reasons(rep: dict) -> list[str]:
    out = []
    bias, setup, plan, score = rep["bias"], rep["setup"], rep.get("plan"), rep["score"]
    if rep["direction"] == "NONE":
        out += [r for r in bias["reasons"] if "EMA" not in r][:2]
    elif not setup.get("model"):
        out.append("koi A/B/C/D entry model nahi bana")
    elif setup.get("grade") == "reject":
        out.append(f"setup {setup['model']} quality kam (grade reject)")
    if plan and not plan.get("ok"):
        out += plan.get("rejects", [])[:2]
    out += [b for b in score["blocks"] if b.startswith("score") or b == "news block"]
    seen, res = set(), []
    for r in out:
        if r not in seen:
            seen.add(r)
            res.append(r)
    return res[:3] or ["confluence kam"]


def what_to_watch(rep: dict) -> str:
    bias, setup, plan = rep["bias"], rep["setup"], rep.get("plan")
    tips = []
    if rep["direction"] == "NONE":
        if any("BTC" in r for r in bias["reasons"]):
            tips.append("BTC 1H stable hone ka intezar")
        if bias["h4"]["trend"] == "range":
            tips.append("4H par saaf BOS (structure) ka intezar")
        if not tips:
            tips.append("1D aur 4H direction ek hone ka intezar")
    else:
        tips += [p.split(": ", 1)[-1] for p in setup.get("pending", [])[:1]]
        if not setup.get("model"):
            tips.append("1H par liquidity sweep + CHoCH, ya 4H POI tap + 15m CHoCH")
        q = setup.get("quality") or {}
        if setup.get("model") and not q.get("ltf_confirmed"):
            tips.append("POI par 15m CHoCH confirmation")
        if plan and not plan.get("ok") and any("blocked" in r for r in plan.get("rejects", [])):
            tips.append("blocking level ke paar 1H close, ya POI ke andar behtar entry")
        if rep["news"] == "block":
            tips.append("news event guzarne ka intezar")
    return "; ".join(dict.fromkeys(tips)) or "agla watchman alert"


def format_answer(rep: dict, cfg: dict | None = None) -> str:
    cfg = cfg or load_config()
    pair = rep["pair"]
    if rep["decision"] != "TRADE":
        return "\n".join([f"❌ NO TRADE — {pair}",
                          "Wajah: " + "; ".join(rep.get("no_trade_reasons") or no_trade_reasons(rep)),
                          f"Kya dekhna hai: {what_to_watch(rep)}"])
    p, s, sc = rep["plan"], rep["setup"], rep["score"]
    provisional = rep.get("news") == "pending"
    side = p["side"].upper()
    emoji = "🟢" if side == "LONG" else "🔴"
    tps, rr = p["tps"], p["rr"]
    t1 = int(float(cfg_get(cfg, "tp1_close_pct", 50)))
    st = rep.get("stats")
    if st and st.get("trades"):
        low = " — low sample" if st["trades"] < 30 else ""
        wr = f"{st.get('win_rate')}% ({st['trades']} trades {st.get('source', 'journal')}{low}) | Avg R: {st.get('avg_r')}"
    else:
        wr = "not enough data yet (0 trades) | Avg R: n/a"
    tname = (p.get("targets") or [{}] * 3)[-1].get("name", "liquidity")
    nets = p["net_reward_usd"]
    b, fut = rep["bias"], rep["futures"]
    poi = s.get("poi") or {}
    news_line = {"pending": "abhi check nahi hui (news-check)", "clear": "clear", "caution": "caution",
                 "block": "block"}.get(rep.get("news"), rep.get("news"))
    if rep.get("news_note"):
        news_line += f" — {rep['news_note']}"
    inv_dir = "neeche" if side == "LONG" else "upar"
    valid = fmt_local(store.parse_iso(rep["valid_until"]), cfg)
    head = ["⚠️ PROVISIONAL — news-check abhi baqi hai (phir: python lib/deep_scan.py --finalize "
            f"{rep.get('report_path') or 'reports/' + rep['coin'] + '/latest.json'} --news clear|caution|block)"] \
        if provisional else []
    return "\n".join(head + [
        f"{emoji} {side} — {pair} (1H) | Setup: {s['model']} — {s['name']} ({s['grade']})",
        f"Score: {sc['total']:g}/100",
        "",
        f"📍 Entry: {_g(p['entry'])} ({p['order']}, zone {_g(min(p['entry_zone']))}–{_g(max(p['entry_zone']))})",
        f"🛑 Stop Loss: {_g(p['sl'])}  ({p['sl_pct']:.2f}%)",
        f"🎯 TP1: {_g(tps[0])} (1:{rr[0]:g}) → {t1}% close, SL entry par",
        f"🎯 TP2: {_g(tps[1])} (1:{rr[1]:g})",
        f"🎯 TP3: {_g(tps[2])} (1:{rr[2]:g}) — runner / {tname}",
        "",
        f"⚙️ Leverage: {p['leverage']}x (Isolated) | Margin: ${p['margin_usd']:.2f}",
        f"💀 Liquidation: {_g(p['liquidation'])} {'✅' if p['liq_ok'] else '❌'} SL se {p['liq_beyond_sl_pct']:.2f}% door",
        f"💵 Risk: ${p['risk_usd']:.2f} | Reward: TP1 ${nets[0]:.2f} / TP2 ${nets[1]:.2f} / TP3 ${nets[2]:.2f}",
        f"📊 Is setup ka win rate: {wr}",
        "",
        "🧠 Wajah:",
        f"- HTF: {b['direction']} — 1D {b['d1']['trend']}, 4H {b['h4']['trend']}"
        + (f", price 4H {b['h4']['range']['zone']} ({b['h4']['range']['pct']}%)" if b['h4'].get('range') else "")
        + f", BTC {b.get('btc_for_direction') or 'n/a'}",
        f"- SMC: {s['name']} — POI {poi.get('kind')} {_g(poi.get('bottom'))}–{_g(poi.get('top'))}, "
        + ", ".join(k.replace('_', ' ') for k, v in (s.get('quality') or {}).items() if v),
        f"- Futures data: {fut.get('verdict') or 'n/a'}" + (f" ({fut['reason']})" if fut.get("reason") else ""),
        f"- News: {news_line}",
        "",
        f"❌ Invalidation: 1H candle close {_g(p['sl'])} ke {inv_dir} (POI fail)",
        f"⏳ Valid until: {valid} (entry na mile to cancel)",
    ])


# ------------------------------------------------------------ persistence --

def save_report(rep: dict) -> str:
    d = REPORTS / rep["coin"]
    d.mkdir(parents=True, exist_ok=True)
    stamp = rep["created_at"].replace("-", "").replace(":", "")[:13]
    path = d / f"{stamp}.json"
    rep["report_path"] = str(path)
    text = json.dumps(rep, indent=1, ensure_ascii=False)
    path.write_text(text, encoding="utf-8")
    (d / "latest.json").write_text(text, encoding="utf-8")
    return str(path)


def finalize(path: str, news: str, note: str | None = None, cfg: dict | None = None) -> dict:
    """Add the news-check verdict, re-score, rebuild the final answer."""
    cfg = cfg or load_config()
    rep = json.loads(Path(path).read_text(encoding="utf-8"))
    rep["news"], rep["news_note"] = news, note
    rep["score_inputs"]["news"] = news
    rep["score"] = compute_score(rep["score_inputs"], cfg)
    rep["decision"] = rep["score"]["decision"]
    rep["no_trade_reasons"] = no_trade_reasons(rep) if rep["decision"] != "TRADE" else []
    rep["answer"] = format_answer(rep, cfg)
    text = json.dumps(rep, indent=1, ensure_ascii=False)
    Path(path).write_text(text, encoding="utf-8")
    latest = REPORTS / rep["coin"] / "latest.json"
    if Path(path).resolve() != latest.resolve() and latest.exists():
        cur = json.loads(latest.read_text(encoding="utf-8"))
        if cur.get("created_at") == rep.get("created_at"):
            latest.write_text(text, encoding="utf-8")
    return rep


def close_alerts(coin: str | None = None, status: str | None = None, push: bool = True) -> dict:
    """Move handled alerts from pending.json to alerts/history/YYYY-MM-DD.json, then commit + push."""
    now = utc_now()
    pending = store.load_pending()
    sel = [a for a in pending if coin is None or a.get("coin", "").upper() == coin.upper()]
    keep = [a for a in pending if a not in sel]
    moved = []
    for a in sel:
        st = status
        if not st:
            latest = REPORTS / a.get("coin", "?") / "latest.json"
            try:
                rep = json.loads(latest.read_text(encoding="utf-8"))
                st = "trade" if rep.get("decision") == "TRADE" else \
                    "expired" if (rep.get("alert") or {}).get("status") == "expired" else "no_trade"
            except (FileNotFoundError, json.JSONDecodeError):
                st = "handled"
        store.append_history([a], st, now)
        moved.append({"id": a.get("id"), "coin": a.get("coin"), "status": st})
    if sel:
        store.save_pending(keep)
    out = {"moved": moved, "remaining": len(keep), "git": None}
    if push and sel:
        coins = ",".join(sorted({m["coin"] for m in moved}))
        cmds = [["git", "add", "alerts"],
                ["git", "commit", "-q", "-m", f"deep-scan: close alerts {coins} [skip ci]"],
                ["git", "pull", "-q", "--rebase"],
                ["git", "push", "-q"]]
        for c in cmds:
            r = subprocess.run(c, cwd=ROOT, capture_output=True, text=True, timeout=60)
            if r.returncode != 0:
                out["git"] = f"failed at `{' '.join(c)}`: {(r.stderr or r.stdout).strip()[:300]}"
                break
        else:
            out["git"] = "committed and pushed"
    return out


# --------------------------------------------------------------------- CLI --

def _print_report(rep: dict) -> None:
    print(rep["answer"])
    print("\n--- details ---")
    print(rep["bias"]["text"])
    s = rep["setup"]
    print(f"Setup: {s.get('model') or '-'} {s.get('name') or ''} grade {s.get('grade')}")
    print(rep["futures"].get("text", ""))
    if rep.get("plan"):
        print(rep["plan"].get("text", ""))
    print(score_text(rep["score"]))
    if rep.get("alert"):
        a = rep["alert"]
        print(f"Alert {a.get('id')}: {a.get('status')} (age {a.get('age_h')}h)")
    for tf, p in (rep.get("charts") or {}).items():
        print(f"chart {tf}: {p}")
    print(f"report: {rep.get('report_path')}")


def _main() -> None:
    from lib.config import fix_console

    fix_console()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(description="Deep scan engine")
    ap.add_argument("--symbol", help="coin, e.g. SOL")
    ap.add_argument("--all-pending", action="store_true")
    ap.add_argument("--finalize", metavar="REPORT")
    ap.add_argument("--close-alerts", action="store_true")
    ap.add_argument("--coin", help="with --close-alerts: only this coin")
    ap.add_argument("--status", help="with --close-alerts: history status (default from the latest report)")
    ap.add_argument("--no-git", action="store_true")
    ap.add_argument("--news", choices=["clear", "caution", "block", "pending"])
    ap.add_argument("--news-note")
    ap.add_argument("--no-charts", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    cfg = load_config()

    def emit(obj, printer=None):
        if a.json:
            print(json.dumps(obj, indent=1, ensure_ascii=False))
        elif printer:
            printer(obj)
        else:
            print(json.dumps(obj, indent=1, ensure_ascii=False))

    if a.close_alerts:
        emit(close_alerts(a.coin, a.status, push=not a.no_git))
    elif a.finalize:
        if not a.news:
            ap.error("--finalize needs --news clear|caution|block")
        emit(finalize(a.finalize, a.news, a.news_note, cfg), _print_report)
    elif a.all_pending:
        alerts = store.load_pending()
        seen, order = set(), []
        for al in sorted(alerts, key=lambda x: -(x.get("light_score") or 0)):
            if al["symbol"] not in seen:
                seen.add(al["symbol"])
                order.append(al)
        reps = []
        for al in order:
            try:
                reps.append(analyze_symbol(al["symbol"], cfg, a.news, not a.no_charts, alert=al))
            except Exception as e:
                reps.append({"symbol": al["symbol"], "error": f"{type(e).__name__}: {e}"})
        if a.json:
            emit({"reports": reps})
        else:
            if not reps:
                print("📭 Koi pending alert nahi.")
            for r in reps:
                print(r.get("answer") or r.get("error"), "\n")
    elif a.symbol:
        emit(analyze_symbol(data.to_symbol(a.symbol), cfg, a.news, not a.no_charts), _print_report)
    else:
        ap.error("use --symbol, --all-pending, --finalize or --close-alerts")


if __name__ == "__main__":
    _main()
