"""Trade journal (trade-journal skill): journal/trades.csv.

    python lib/journal.py add --from reports/SOL/latest.json     # plan or NO TRADE from a deep scan
    python lib/journal.py update --id <id> --status tp1 [--exit 152.3] [--notes "..."]
    python lib/journal.py autocheck                              # replay candles for open plans (paper trading)
    python lib/journal.py report --period day|week|all [--json]
    python lib/journal.py list [--n 10]

Statuses: planned, taken, skipped, no_trade, cancelled, expired, tp1, tp2, tp3, sl, be.
Partial schedule (R per outcome): tp1_close_pct at TP1 (then SL to entry), the rest split
equally between TP2 and TP3. tp1 = 0.5*R1 ; tp2 = 0.5*R1 + 0.25*R2 ; tp3 = 0.5*R1 + 0.25*R2 + 0.25*R3 ;
sl = -1 R ; be = 0. PnL = R x risk_usd (risk already includes fees).
Setup stats (per setup type) are written to backtest/results/setup_stats.json under "journal".
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.config import ROOT, cfg_get, display_tz, iso_utc, load_config, utc_now

JOURNAL = ROOT / "journal" / "trades.csv"
STATS = ROOT / "backtest" / "results" / "setup_stats.json"
COLUMNS = ["id", "created_at", "coin", "side", "setup_type", "score", "entry", "sl", "tp1", "tp2", "tp3",
           "leverage", "risk_usd", "status", "exit_price", "pnl_usd", "r_multiple", "notes",
           # extra columns (used by autocheck / reports)
           "symbol", "grade", "order", "valid_until", "qty", "filled_at", "closed_at"]
OPEN = {"planned", "taken"}
CLOSED = {"tp1", "tp2", "tp3", "sl", "be"}
STATUSES = OPEN | CLOSED | {"skipped", "no_trade", "cancelled", "expired"}


# ---------------------------------------------------------------- storage --

def load(path: Path | None = None) -> list[dict]:
    p = path or JOURNAL
    if not p.exists():
        return []
    with open(p, newline="", encoding="utf-8") as f:
        return [dict(r) for r in csv.DictReader(f)]


def save(rows: list[dict], path: Path | None = None) -> None:
    p = path or JOURNAL
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") if r.get(k) is not None else "" for k in COLUMNS})
    os.replace(tmp, p)


def _num(x) -> float | None:
    try:
        return None if x in (None, "") else float(x)
    except ValueError:
        return None


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc)


def new_id(coin: str, now: datetime, rows: list[dict]) -> str:
    base = f"{now:%Y%m%d-%H%M}-{coin}"
    ids = {r["id"] for r in rows}
    i, out = 1, base
    while out in ids:
        i += 1
        out = f"{base}-{i}"
    return out


# --------------------------------------------------------------- R / PnL --

def shares(cfg: dict) -> list[float]:
    s1 = float(cfg_get(cfg, "tp1_close_pct", 50)) / 100
    return [s1, (1 - s1) / 2, (1 - s1) / 2]


def r_multiple(row: dict, status: str, exit_price: float | None = None, cfg: dict | None = None) -> float | None:
    cfg = cfg or load_config()
    entry, sl = _num(row.get("entry")), _num(row.get("sl"))
    if entry is None or sl is None or entry == sl:
        return None
    sgn = 1 if row.get("side") == "long" else -1
    risk = abs(entry - sl)
    if status in ("sl", "be") and exit_price is not None:
        return round(sgn * (exit_price - entry) / risk, 3)
    if status == "sl":
        return -1.0
    if status == "be":
        return 0.0
    if status in ("tp1", "tp2", "tp3"):
        k = int(status[2])
        tps = [_num(row.get(f"tp{i}")) for i in (1, 2, 3)]
        sh = shares(cfg)
        r = 0.0
        for i in range(k):
            if tps[i] is None:
                return None
            r += sh[i] * sgn * (tps[i] - entry) / risk
        return round(r, 3)
    return None


def _apply_outcome(row: dict, status: str, exit_price: float | None, cfg: dict, now: datetime) -> None:
    row["status"] = status
    if exit_price is not None:
        row["exit_price"] = exit_price
    r = r_multiple(row, status, exit_price if status in ("sl", "be") else None, cfg)
    if r is not None:
        row["r_multiple"] = r
        risk = _num(row.get("risk_usd"))
        row["pnl_usd"] = round(r * risk, 4) if risk is not None else ""
    if status in CLOSED | {"cancelled", "expired", "skipped"}:
        row["closed_at"] = row.get("closed_at") or iso_utc(now)


# ------------------------------------------------------------------ add --

def row_from_report(rep: dict, rows: list[dict], cfg: dict, now: datetime) -> dict:
    """Accepts a deep-scan report (lib/deep_scan.py) or a plain plan JSON."""
    symbol = rep.get("symbol") or ""
    coin = rep.get("coin") or symbol.split("/")[0]
    plan = rep.get("plan") if isinstance(rep.get("plan"), dict) else (rep if "entry" in rep else None)
    setup = rep.get("setup") or {}
    score = rep.get("score") or {}
    decision = rep.get("decision") or ("TRADE" if plan and plan.get("ok", True) else "NO TRADE")
    hours = float(cfg_get(cfg, "plan_valid_hours", 6))
    row = {
        "id": new_id(coin, now, rows),
        "created_at": iso_utc(now),
        "coin": coin,
        "symbol": symbol,
        "side": (plan or {}).get("side") or (rep.get("direction") or "").lower() or "",
        "setup_type": setup.get("model") or rep.get("setup_type") or "",
        "grade": setup.get("grade") or "",
        "score": score.get("total") if isinstance(score, dict) else score,
        "valid_until": iso_utc(now + timedelta(hours=hours)),
    }
    if plan and decision == "TRADE":
        tps = plan.get("tps") or [plan.get("tp1"), plan.get("tp2"), plan.get("tp3")]
        row.update(entry=plan.get("entry"), sl=plan.get("sl"), tp1=tps[0] if len(tps) > 0 else "",
                   tp2=tps[1] if len(tps) > 1 else "", tp3=tps[2] if len(tps) > 2 else "",
                   leverage=plan.get("leverage"), risk_usd=plan.get("risk_usd"), qty=plan.get("qty"),
                   order=plan.get("order", "limit"), status="planned",
                   notes=rep.get("notes") or f"{setup.get('name') or ''}".strip())
    else:
        reasons = rep.get("no_trade_reasons") or (score.get("blocks") if isinstance(score, dict) else None) or []
        if plan:  # keep the numbers for later review even when rejected
            tps = plan.get("tps") or []
            row.update(entry=plan.get("entry"), sl=plan.get("sl"), tp1=tps[0] if tps else "",
                       tp2=tps[1] if len(tps) > 1 else "", tp3=tps[2] if len(tps) > 2 else "")
        row.update(status="no_trade", notes="; ".join(reasons)[:500])
    return row


def add(path: str, cfg: dict | None = None, now: datetime | None = None) -> dict:
    cfg, now = cfg or load_config(), now or utc_now()
    rep = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = load()
    row = row_from_report(rep, rows, cfg, now)
    rows.append(row)
    save(rows)
    write_setup_stats(rows, cfg)
    return row


def update(row_id: str, status: str, exit_price: float | None = None, notes: str | None = None,
           cfg: dict | None = None, now: datetime | None = None) -> dict:
    cfg, now = cfg or load_config(), now or utc_now()
    if status not in STATUSES:
        raise SystemExit(f"unknown status {status}; use one of {sorted(STATUSES)}")
    rows = load()
    row = next((r for r in rows if r["id"] == row_id), None)
    if not row:
        raise SystemExit(f"id {row_id} not found")
    _apply_outcome(row, status, exit_price, cfg, now)
    if notes:
        row["notes"] = (row.get("notes") + " | " if row.get("notes") else "") + notes
    save(rows)
    write_setup_stats(rows, cfg)
    return row


# ------------------------------------------------------------- autocheck --

def simulate(row: dict, candles, cfg: dict) -> dict:
    """Replay candles (DataFrame, oldest first, opened after the plan) for one plan.

    Returns {"status", "exit", "filled_at", "stage", "closed_at"}. Conservative:
    SL wins when SL and a TP are inside the same candle; on the fill candle only the SL is checked.
    """
    side = row.get("side")
    entry, sl = _num(row.get("entry")), _num(row.get("sl"))
    tps = [t for t in (_num(row.get(f"tp{i}")) for i in (1, 2, 3)) if t is not None]
    long = side == "long"
    valid_until = _parse(row["valid_until"]) if row.get("valid_until") else None
    filled = bool(row.get("filled_at"))
    filled_at = row.get("filled_at") or None
    stage, stop = 0, sl
    for ts, c in candles.iterrows():
        t = ts.to_pydatetime()
        hi, lo = float(c["high"]), float(c["low"])
        if not filled:
            if valid_until and t >= valid_until:
                return {"status": "expired", "exit": None, "filled_at": None, "stage": 0, "closed_at": iso_utc(t)}
            touched = (lo <= entry) if long else (hi >= entry)
            if row.get("order") == "market" or touched:
                filled, filled_at = True, iso_utc(t)
                if (lo <= sl) if long else (hi >= sl):
                    return {"status": "sl", "exit": sl, "filled_at": filled_at, "stage": 0, "closed_at": iso_utc(t)}
            continue
        hit_stop = (lo <= stop) if long else (hi >= stop)
        if hit_stop:
            status = "sl" if stage == 0 else f"tp{stage}"
            return {"status": status, "exit": stop, "filled_at": filled_at, "stage": stage, "closed_at": iso_utc(t)}
        while stage < len(tps) and ((hi >= tps[stage]) if long else (lo <= tps[stage])):
            stage += 1
            stop = entry  # SL to entry after TP1
        if tps and stage == len(tps):
            return {"status": f"tp{stage}", "exit": tps[-1], "filled_at": filled_at, "stage": stage,
                    "closed_at": iso_utc(t)}
    return {"status": "taken" if filled else "planned", "exit": None, "filled_at": filled_at, "stage": stage,
            "closed_at": None}


def autocheck(fetch=None, cfg: dict | None = None, now: datetime | None = None) -> list[dict]:
    """fetch(symbol, created_at) -> DataFrame of 15m candles opened after created_at."""
    cfg, now = cfg or load_config(), now or utc_now()
    fetch = fetch or _fetch_after
    rows = load()
    changes = []
    for row in rows:
        if row.get("status") not in OPEN or not row.get("entry"):
            continue
        created = _parse(row["created_at"])
        candles = fetch(row.get("symbol") or f"{row['coin']}/USDT:USDT", created)
        res = simulate(row, candles, cfg)
        before = row["status"]
        if res["filled_at"]:
            row["filled_at"] = res["filled_at"]
        if res["status"] in CLOSED | {"expired"}:
            _apply_outcome(row, res["status"], res["exit"], cfg, now)
            row["closed_at"] = res["closed_at"]
            row["notes"] = (row.get("notes") + " | " if row.get("notes") else "") + "autocheck"
        elif res["status"] == "taken" and before == "planned":
            row["status"] = "taken"
        if row["status"] != before or res["stage"]:
            changes.append({"id": row["id"], "from": before, "to": row["status"], "stage": res["stage"]})
    save(rows)
    write_setup_stats(rows, cfg)
    return changes


def _fetch_after(symbol: str, created: datetime):
    from lib.data import get_ohlcv

    minutes = (utc_now() - created).total_seconds() / 60
    tf = "15m" if minutes <= 15 * 1900 else "1h"
    per = 15 if tf == "15m" else 60
    limit = int(min(1990, minutes / per + 4))
    df = get_ohlcv(symbol, tf, max(limit, 10))
    return df[df.index >= created]


# ---------------------------------------------------------------- report --

def setup_table(rows: list[dict]) -> dict:
    out: dict[str, dict] = {}
    for r in rows:
        if r.get("status") not in CLOSED:
            continue
        st = r.get("setup_type") or "?"
        d = out.setdefault(st, {"trades": 0, "wins": 0, "losses": 0, "be": 0, "sum_r": 0.0, "pos_r": 0.0, "neg_r": 0.0})
        rr = _num(r.get("r_multiple")) or 0.0
        d["trades"] += 1
        d["sum_r"] += rr
        if rr > 0:
            d["wins"] += 1
            d["pos_r"] += rr
        elif rr < 0:
            d["losses"] += 1
            d["neg_r"] += -rr
        else:
            d["be"] += 1
    for d in out.values():
        d["win_rate"] = round(d["wins"] / d["trades"] * 100, 1) if d["trades"] else None
        d["avg_r"] = round(d["sum_r"] / d["trades"], 3) if d["trades"] else None
        d["profit_factor"] = round(d["pos_r"] / d["neg_r"], 2) if d["neg_r"] else None
        for k in ("sum_r", "pos_r", "neg_r"):
            d[k] = round(d[k], 3)
    return out


def write_setup_stats(rows: list[dict], cfg: dict | None = None) -> None:
    try:
        data = json.loads(STATS.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    setups = data.setdefault("setups", {})
    table = setup_table(rows)
    for k in list(setups):
        setups[k]["journal"] = None
    for k, v in table.items():
        setups.setdefault(k, {})["journal"] = dict(v, source="journal")
    data["updated_at"] = iso_utc(utc_now())
    STATS.parent.mkdir(parents=True, exist_ok=True)
    STATS.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def read_setup_stats(model: str | None) -> dict | None:
    """Best available stats for a setup type: backtest (Phase 3) if it has data, else journal."""
    if not model:
        return None
    try:
        data = json.loads(STATS.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    s = (data.get("setups") or {}).get(model) or {}
    bt, jr = s.get("backtest"), s.get("journal")
    if bt and (bt.get("trades") or 0) > 0:
        return dict(bt, source=bt.get("source", "backtest"))
    if jr and (jr.get("trades") or 0) > 0:
        return jr
    return None


def report(period: str = "week", cfg: dict | None = None, now: datetime | None = None) -> dict:
    cfg, now = cfg or load_config(), now or utc_now()
    tz = display_tz(cfg)
    rows = load()
    if period == "day":
        start = now.astimezone(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "week":
        start = now - timedelta(days=7)
    else:
        start = None
    sel = [r for r in rows if start is None or _parse(r["created_at"]) >= start]
    no_trade = [r for r in sel if r.get("status") == "no_trade"]
    signals = [r for r in sel if r.get("status") != "no_trade"]
    taken = [r for r in sel if r.get("status") in CLOSED | {"taken"}]
    closed = sorted([r for r in sel if r.get("status") in CLOSED], key=lambda r: r.get("closed_at") or r["created_at"])
    rs = [_num(r.get("r_multiple")) or 0.0 for r in closed]
    wins = sum(1 for x in rs if x > 0)
    pnl = sum(_num(r.get("pnl_usd")) or 0.0 for r in closed)
    streak = cur = 0
    for x in rs:
        cur = cur + 1 if x < 0 else 0
        streak = max(streak, cur)
    table = setup_table(sel)
    ranked = sorted(((k, v) for k, v in table.items() if v["trades"]), key=lambda kv: (kv[1]["win_rate"], kv[1]["avg_r"]))
    best, worst = (ranked[-1] if ranked else None), (ranked[0] if len(ranked) > 1 else None)
    if not closed:
        note = "Abhi koi closed trade nahi — paper trading jaari rakho, autocheck chalate raho."
    elif worst and worst[1]["trades"] >= 5 and (worst[1]["avg_r"] or 0) < 0:
        note = f"Setup {worst[0]} ka avg R negative hai — is setup ko filhal skip karo."
    elif streak >= 3:
        note = f"{streak} losses lagataar — risk aadha karo jab tak 2 wins na hon."
    else:
        note = "System theek chal raha hai — sirf A/A+ setups lo."
    period_name = {"day": "Aaj", "week": "Is hafte", "all": "Ab tak"}.get(period, period)
    wr = f"{wins / len(closed) * 100:.0f}%" if closed else "n/a"
    avg_r = f"{sum(rs) / len(rs):.2f}" if rs else "n/a"
    text = "\n".join([
        f"📒 {period_name} ka report",
        f"Signals: {len(signals)} | Liye: {len(taken)} | NO TRADE: {len(no_trade)}",
        f"Win rate: {wr} | Avg R: {avg_r} | Net PnL: ${pnl:.2f}",
        f"Best setup: {best[0]} ({best[1]['win_rate']:.0f}%)" if best else "Best setup: n/a",
    ]).rstrip()
    text += f" | Weakest: {worst[0]} ({worst[1]['win_rate']:.0f}%)" if worst else ""
    text += f"\nMax losing streak: {streak}\nNote: {note}"
    write_setup_stats(rows, cfg)
    return {"period": period, "signals": len(signals), "taken": len(taken), "no_trade": len(no_trade),
            "closed": len(closed), "wins": wins, "win_rate": round(wins / len(closed) * 100, 1) if closed else None,
            "avg_r": round(sum(rs) / len(rs), 3) if rs else None, "net_pnl_usd": round(pnl, 4),
            "max_losing_streak": streak, "setups": table, "note": note, "text": text}


# ------------------------------------------------------------------- CLI --

def _main() -> None:
    from lib.config import fix_console

    fix_console()
    ap = argparse.ArgumentParser(description="Trade journal")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a_add = sub.add_parser("add")
    a_add.add_argument("--from", dest="src", required=True)
    a_up = sub.add_parser("update")
    a_up.add_argument("--id", required=True)
    a_up.add_argument("--status", required=True)
    a_up.add_argument("--exit", type=float)
    a_up.add_argument("--notes")
    sub.add_parser("autocheck")
    a_rep = sub.add_parser("report")
    a_rep.add_argument("--period", choices=["day", "week", "all"], default="week")
    a_ls = sub.add_parser("list")
    a_ls.add_argument("--n", type=int, default=10)
    for p in (a_add, a_up, a_rep, a_ls):
        p.add_argument("--json", action="store_true")
    a = ap.parse_args()

    if a.cmd == "add":
        row = add(a.src)
        print(json.dumps(row, indent=1) if a.json else f"✅ Journal: {row['id']} ({row['status']})")
    elif a.cmd == "update":
        row = update(a.id, a.status, a.exit, a.notes)
        print(json.dumps(row, indent=1) if a.json else
              f"✅ {row['id']} → {row['status']} | R {row.get('r_multiple')} | PnL ${row.get('pnl_usd')}")
    elif a.cmd == "autocheck":
        ch = autocheck()
        print(json.dumps(ch, indent=1) if ch else "Koi change nahi.")
    elif a.cmd == "report":
        r = report(a.period)
        print(json.dumps(r, indent=1) if a.json else r["text"])
    elif a.cmd == "list":
        rows = load()[-a.n:]
        if a.json:
            print(json.dumps(rows, indent=1))
        for r in rows if not a.json else []:
            print(f"{r['id']:<22} {r['side']:<5} {r['setup_type'] or '-':<2} {r['status']:<9} "
                  f"entry {r['entry'] or '-':<10} R {r['r_multiple'] or '-'}")


if __name__ == "__main__":
    _main()
