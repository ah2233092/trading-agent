---
name: deep-scan
description: Main trading command. Use when Rans runs /deep-scan, says a Telegram alert came, or asks to scan a coin for a trade. Picks up pending alerts (or the coin he names), runs every analysis skill in order and returns a final futures trade plan or NO TRADE.
---

# /deep-scan — Full professional analysis

Usage: `/deep-scan` (all pending alerts) or `/deep-scan SOL` (one coin, with or without an alert).

The engine `lib/deep_scan.py` runs the script part of every skill in one go and saves a JSON report.
You add what scripts cannot do: look at the charts, check the news, and judge.

## Step 0 — Pick the coins
1. `git pull` to get the newest alerts.
2. One coin: `python lib/deep_scan.py --symbol SOL --json`
   All pending: `python lib/deep_scan.py --all-pending --json` (strongest light score first).
3. Alerts older than `alert_max_age_hours` are re-validated by the engine (`report.alert.status`:
   `fresh` / `revalidated` / `expired`). If `expired`, report it in one line and skip the coin.

## Step 1 — What the engine already did (read it from the report)
1. **htf-bias** → `report.bias` (direction LONG / SHORT / NONE + reasons). NONE → NO TRADE.
2. **smc-ict-analysis** → `report.setup` (model A/B/C/D, POI, grade A+/A/B/reject, quality flags, pending notes).
3. **chart-vision** → PNGs in `report.charts` (1D, 4H, 1H, 15m) with the plan drawn on them.
4. **futures-data** → `report.futures` (funding, OI, L/S, liquidity, verdict for this side).
5. **risk-leverage** → `report.plan` (entry, SL, TP1/2/3, size, leverage, liquidation, fees) — or rejects.
6. **backtest stats** → `report.stats` from `backtest/results/setup_stats.json` (None = not enough data yet).
7. **score** → `report.score` (breakdown per category, blocks, decision). News is still `pending`.

## Step 2 — Your part
1. **Look at the charts** (chart-vision skill): open every PNG in `report.charts` with Read. If the picture
   rejects the setup (choppy, no real sweep, chasing, wall right above entry) → NO TRADE with that reason.
   If you want a different SL/TP, recompute with `python lib/risk.py ... --json` — never by hand.
2. **News** (news-check skill): WebSearch / WebFetch for the coin + macro calendar.
3. **Finalize** with the news verdict (re-scores and rebuilds the final answer):
   `python lib/deep_scan.py --finalize <report.report_path> --news clear|caution|block --news-note "<one line>" --json`

Score weights (config.yaml `score_weights`): HTF bias 25, SMC setup 25, entry precision 15,
displacement/volume 10, futures data 10, session/killzone 5, news 5, backtest edge 5.
Score < `min_trade_score` or any hard block → **NO TRADE**.

## Step 3 — Final answer (Roman Urdu, this exact format)
Use `report.answer` from the finalized report. You may rewrite the 🧠 Wajah bullets in better words
(add what you saw on the charts and in the news) but never change a number.

```
🟢 LONG / 🔴 SHORT — COIN/USDT (1H) | Setup: <type>
Score: XX/100

📍 Entry: <price> (<limit/market>, zone <a–b>)
🛑 Stop Loss: <price>  (<x.xx%>)
🎯 TP1: <price> (1:R) → 50% close, SL entry par
🎯 TP2: <price> (1:R)
🎯 TP3: <price> (runner / liquidity target)

⚙️ Leverage: <x>x (Isolated) | Margin: $<m>
💀 Liquidation: <price> ✅ SL se <x%> door
💵 Risk: $<r> | Reward: TP1 $<a> / TP2 $<b> / TP3 $<c>
📊 Is setup ka win rate: <xx%> (<n> trades backtest/journal) | Avg R: <x>

🧠 Wajah:
- HTF: ...
- SMC: ...
- Futures data: ...
- News: ...

❌ Invalidation: <condition>
⏳ Valid until: <time> (entry na mile to cancel)
```

Or:
```
❌ NO TRADE — COIN/USDT
Wajah: <1–3 short reasons>
Kya dekhna hai: <what would make it a trade>
```
A report that still says `⚠️ PROVISIONAL` has not been finalized — never present it as the final answer.

## Step 4 — Bookkeeping
- Journal every plan and every NO TRADE: `python lib/journal.py add --from <report.report_path>`
- Move handled alerts to history and push: `python lib/deep_scan.py --close-alerts --coin SOL`
  (status taken from the latest report: trade / no_trade / expired).
