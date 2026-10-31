"""Stage 1 — Watchman scanner. Runs once per hour, right after the 1H candle closes.

    python scanner/run_scan.py                  # real run: write alerts + Telegram
    python scanner/run_scan.py --dry-run        # print only (no Telegram, no file writes)
    python scanner/run_scan.py --coins SOL,ETH  # limit the universe
    python scanner/run_scan.py --digest london  # killzone digest: top 2 coins, always sends a message

Workflow helpers (used by GitHub Actions to survive push races):
    --save-result PATH   also write this run's new alerts to PATH
    --apply PATH         skip scanning; re-apply alerts from PATH to alerts/
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.config import cfg_get, fix_console, fmt_local, iso_utc, load_config, utc_now  # noqa: E402
from lib import data  # noqa: E402
from lib.notify import is_configured, send_telegram  # noqa: E402
from scanner import store  # noqa: E402
from scanner.light_score import btc_context, score_coin  # noqa: E402
from scanner import ready as readiness  # noqa: E402

log = logging.getLogger("scan")
BTC = "BTC/USDT:USDT"
LIMIT_4H = 300   # ~50 days: 4H structure, OBs, FVGs, PDH/PDL/PWH/PWL
LIMIT_1H = 400   # ~16 days: covers the full previous week for 1H sweeps
LIMIT_1D, LIMIT_15M = 400, 300
KZ_PKT = {"london": "12:00–15:00 PKT", "newyork": "17:00–20:00 PKT"}


def build_alert(res: dict, now, cfg: dict, exchange: str, btc: dict | None) -> dict:
    coin = data.coin_of(res["symbol"])
    return {
        "id": f"{now:%Y%m%d-%H%M}-{coin}",
        "coin": coin,
        "symbol": res["symbol"],
        "pair": data.pretty(res["symbol"]),
        "direction_hint": res["direction"],
        "triggers": res["triggers"],
        "trigger_labels": res["trigger_labels"],
        "light_score": res["score"],
        "price": res["price"],
        "candle_time": res["candle_time"],
        "created_at": iso_utc(now),
        "created_at_pkt": fmt_local(now, cfg),
        "exchange": exchange,
        "context": res["context"],
        "levels": trigger_levels(res),
        "btc": btc,
        "ready": res.get("ready_info"),
        "kind": res.get("alert_kind", "watch"),
        "status": "pending",
    }


def trigger_levels(res: dict) -> dict:
    """Prices that made each trigger fire — used later to re-validate old alerts."""
    out = {}
    for t in res.get("trigger_details", []):
        d = t.get("detail") or {}
        if t["key"] == "sweep":
            out["sweep_level"], out["sweep_wick"] = d.get("level"), d.get("wick")
        elif t["key"] == "structure":
            out["structure_level"] = d.get("level")
    return out


def _line(a: dict) -> str:
    r = a.get("ready") or {}
    side = (r.get("side") or a.get("direction_hint") or "").upper()
    bits = [side] if side else []
    if r.get("model"):
        bits.append(f"setup {r['model']}")
    if r.get("score") is not None:
        bits.append(f"score {r['score']:g}")
    return f"{a['pair']} — {', '.join(bits)}" if bits else a["pair"]


def telegram_text(alerts: list[dict]) -> str:
    lines = ["🟢 Trade ban rahi hai:"] + [f"• {_line(a)}" for a in alerts]
    coin = alerts[0]["coin"]
    lines += ["", f"Claude chat mein aa kar likho: \"{coin} scan karo\"",
              "Main deep scan + news check karke final entry / SL / TP dunga.",
              "⏳ Mauqa ~1–2 ghante valid hai."]
    return "\n".join(lines)


def digest_text(name: str, picks: list[dict]) -> str:
    title = {"london": "London", "newyork": "New York"}.get(name, name)
    lines = [f"📋 {title} killzone shuru ({KZ_PKT.get(name, '')})"]
    if picks:
        lines.append("Aaj ke top coins:")
        for i, a in enumerate(picks, 1):
            r = a.get("ready") or {}
            tag = "✅ ready" if r.get("ready") else "👀 dekhna hai"
            lines.append(f"{i}. {_line(a)} ({tag})")
        lines += ["", "Claude chat mein likho: \"scan karo\" — main dono coins ka deep scan karunga."]
    else:
        lines += ["Abhi kisi coin par achha setup nahi. Aaj trade na lena hi behtar.",
                  "Strong setup bana to foran alert aayega."]
    return "\n".join(lines)


def write_step_summary(lines: list[str]) -> None:
    path = os.getenv("GITHUB_STEP_SUMMARY")
    if path:
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
        except OSError:
            pass


def print_table(results: list[dict], kept_ids: set[str]) -> None:
    print(f"\n{'#':>2}  {'PAIR':<14} {'LIGHT':>5}  {'SIDE':<5} {'SETUP':<6} {'SCORE':>5}  STATUS")
    for i, r in enumerate(results, 1):
        mark = " ⚡" if r["symbol"] in kept_ids else ""
        ri = r.get("ready_info") or {}
        setup = f"{ri.get('model') or '-'}{ri.get('grade') or ''}"
        sc = f"{ri['score']:g}" if ri.get("score") is not None else "-"
        print(f"{i:>2}  {data.pretty(r['symbol']):<14} {r['score']:>5}  {r['direction']:<5} {setup:<6} {sc:>5}  "
              f"{ri.get('why', '-')}{mark}")


def apply_file(path: str, cfg: dict) -> None:
    with open(path, "r", encoding="utf-8") as f:
        new = json.load(f).get("alerts", [])
    res = store.add_alerts(new, utc_now(), float(cfg_get(cfg, "scanner.coin_cooldown_hours", 6)),
                           float(cfg_get(cfg, "scanner.pending_expire_hours", 24)))
    print(f"[apply] added={res['added']} expired={res['expired']}")


def main() -> int:
    fix_console()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(description="Watchman light scanner")
    ap.add_argument("--dry-run", action="store_true", help="print only: no Telegram, no file writes")
    ap.add_argument("--coins", help="comma list, e.g. SOL,ETH")
    ap.add_argument("--save-result", help="write this run's new alerts to this JSON file")
    ap.add_argument("--apply", help="re-apply alerts from a --save-result file and exit")
    ap.add_argument("--digest", choices=["london", "newyork"],
                    help="killzone digest: always send the top coins (min 2 alerts a day)")
    args = ap.parse_args()
    cfg = load_config()

    if args.apply:
        apply_file(args.apply, cfg)
        return 0

    sc = cfg_get(cfg, "scanner", {}) or {}
    max_alerts = int(sc.get("max_alerts_per_run", 3))
    cooldown_h = float(sc.get("coin_cooldown_hours", 6))
    expire_h = float(sc.get("pending_expire_hours", 24))

    t0 = time.time()
    now = utc_now()
    ex = data.get_exchange(cfg=cfg)
    coins = [c.strip() for c in args.coins.split(",") if c.strip()] if args.coins else None
    universe = data.get_universe(cfg, coins)
    log.info("exchange=%s universe=%d coins", ex.id, len(universe))

    frames: dict[tuple[str, str], object] = {}

    def ohlcv(sym: str, tf: str, limit: int):
        if (sym, tf) not in frames:
            frames[(sym, tf)] = data.get_ohlcv(sym, tf, limit, ex=ex)
        return frames[(sym, tf)]

    btc = None
    try:
        btc = btc_context(ohlcv(BTC, "1h", LIMIT_1H), ohlcv(BTC, "4h", LIMIT_4H), cfg)
        log.info("BTC 1H %+.2f%% (dump=%s) trend 1H %s / 4H %s",
                 btc["change_1h_pct"], btc["dump"], btc["trend_1h"], btc["trend_4h"])
    except Exception as e:
        log.warning("BTC context failed (%s) — BTC filter off this run", e)

    import pandas as pd
    from lib import risk as risk_mod

    t_now = pd.Timestamp(now).tz_convert("UTC")
    btc_frames = None
    try:
        btc_frames = {"4h": ohlcv(BTC, "4h", LIMIT_4H), "1h": ohlcv(BTC, "1h", LIMIT_1H)}
    except Exception as e:
        log.warning("BTC frames failed: %s", e)

    results, errors = [], []
    for row in universe:
        sym = row["symbol"]
        try:
            h4, h1 = ohlcv(sym, "4h", LIMIT_4H), ohlcv(sym, "1h", LIMIT_1H)
            res = score_coin(h4, h1, btc, cfg)
            res["symbol"] = sym
            try:
                fr = {"1d": ohlcv(sym, "1d", LIMIT_1D), "4h": h4, "1h": h1, "15m": ohlcv(sym, "15m", LIMIT_15M)}
                spec = risk_mod.get_spec(sym, ex)
                bf = None if sym == BTC else btc_frames
                res["ready_info"] = readiness.check(sym, fr, bf, t_now, cfg, spec)
            except Exception as e:
                log.warning("%s ready-check failed: %s", sym, str(e)[:200])
                res["ready_info"] = {"candidate": False, "ready": False, "rank": 0.0, "why": f"error: {e}"}
            ri = res["ready_info"]
            if ri.get("side"):
                res["direction"] = ri["side"].upper()
            res["rank"] = ri.get("rank", 0) + res["score"] / 10
            results.append(res)
        except Exception as e:
            errors.append(sym)
            log.warning("%s skipped: %s: %s", sym, type(e).__name__, str(e)[:200])

    results.sort(key=lambda r: r["rank"], reverse=True)
    state = store.load_state()
    if args.digest:
        candidates = [r for r in results if r["ready_info"].get("candidate")]
        for r in candidates:
            r["alert_kind"] = "digest"
        kept = candidates[:2]
    else:
        candidates = [r for r in results if r["ready_info"].get("ready")]
        for r in candidates:
            r["alert_kind"] = "ready"
        fresh = [r for r in candidates if not store.in_cooldown(state, r["symbol"], now, cooldown_h)]
        kept = fresh[:max_alerts]
    alerts = [build_alert(r, now, cfg, ex.id, btc) for r in kept]

    sent = False
    if args.dry_run:
        print_table(results, {r["symbol"] for r in kept})
        print(f"\nMode {'digest ' + args.digest if args.digest else 'hourly (ready setups only)'} | "
              f"candidates {len(candidates)} | would alert {len(alerts)}")
        msg = digest_text(args.digest, alerts) if args.digest else (telegram_text(alerts) if alerts else "")
        if msg:
            print("\nTelegram preview (not sent — dry run):\n" + msg)
    else:
        # always called: also expires stale pending alerts (writes only on change)
        res = store.add_alerts(alerts, now, cooldown_h, expire_h)
        if res["expired"]:
            log.info("moved %d old pending alert(s) to history as expired", res["expired"])
        if args.save_result:
            with open(args.save_result, "w", encoding="utf-8") as f:
                json.dump({"alerts": alerts}, f, ensure_ascii=False, indent=1)
        if args.digest:
            sent = send_telegram(digest_text(args.digest, alerts))
        elif alerts:
            sent = send_telegram(telegram_text(alerts))

    dur = time.time() - t0
    names = ", ".join(a["coin"] for a in alerts) or "-"
    if args.dry_run:
        tg = "dry-run"
    elif not alerts and not args.digest:
        tg = "-"
    elif sent:
        tg = "sent"
    else:
        tg = "not-configured" if not is_configured() else "failed"
    summary = (f"[scan] {now:%Y-%m-%d %H:%M} UTC | exchange={ex.id} | coins={len(universe)} "
               f"scored={len(results)} errors={len(errors)} | candidates={len(candidates)} "
               f"alerts={len(alerts)} ({names}) | telegram={tg} | {dur:.1f}s")
    print(summary)

    top = results[:10]
    write_step_summary([
        f"### Watchman scan — {now:%Y-%m-%d %H:%M} UTC ({fmt_local(now, cfg, with_date=False)})",
        f"`{summary}`", "",
        "| Pair | Light | Side | Setup | Score | Status |", "|---|---:|---|---|---:|---|",
        *[f"| {data.pretty(r['symbol'])} | {r['score']} | {r['direction']} | "
          f"{(r.get('ready_info') or {}).get('model') or '-'}{(r.get('ready_info') or {}).get('grade') or ''} | "
          f"{(r.get('ready_info') or {}).get('score', '-')} | {(r.get('ready_info') or {}).get('why', '-')} |"
          for r in top],
    ])
    if universe and len(errors) == len(universe):
        log.error("every coin failed — data source problem")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
