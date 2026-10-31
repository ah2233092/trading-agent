---
name: deep-scan
description: Main trading command. Use when Rans runs /deep-scan, says a Telegram alert came, or asks to scan a coin for a trade. Picks up pending alerts (or the coin he names), runs every analysis skill in order and returns a final futures trade plan or NO TRADE.
---

# /deep-scan — Full professional analysis

Usage: `/deep-scan` (all pending alerts) or `/deep-scan SOL` (one coin, with or without an alert).

## Step 0 — Pick the coins
1. `git pull` to get the newest alerts.
2. Read `alerts/pending.json`. If Rans named a coin, use only that coin.
3. For each alert, note its age. If older than `alert_max_age_hours` (config.yaml), first check that the trigger (sweep / level / BOS) is still valid at current price. If it is gone, mark it `expired` and report it in one line.
4. If several coins are pending, do a quick first pass and deep-scan the strongest first.

## Step 1 — Run the skills in this exact order
For each coin:
1. **htf-bias** → direction allowed (LONG / SHORT / NONE). If NONE → NO TRADE, stop.
2. **smc-ict-analysis** → setup type, POI zone, structure, liquidity targets.
3. **chart-vision** → render the 1D, 4H, 1H (and 15m for entry refinement) charts and look at them. Confirm or reject what the numbers say.
4. **futures-data** → funding, OI, long/short ratio. Flag crowding/trap risk.
5. **news-check** → any event that blocks the trade in the next hours.
6. **risk-leverage** → entry, SL, TP1/TP2/TP3, size, leverage, liquidation.
7. **backtest stats** → read `backtest/results/setup_stats.json` (and journal stats) for the win rate and average R of this setup type.

## Step 2 — Confluence score (0–100)
Score with `lib/score.py`, weights from `config.yaml`. Typical weights:
- HTF bias alignment (1D + 4H + BTC) — 25
- Clean SMC setup (sweep + displacement + OB/FVG in discount/premium) — 25
- Entry precision (OTE / LTF CHoCH confirmation) — 15
- Volume / displacement quality — 10
- Futures data supportive (no crowd trap, funding ok) — 10
- Session / killzone timing — 5
- News clear — 5
- Backtest edge of this setup type — 5

Score < `min_trade_score` → **NO TRADE**.

## Step 3 — Final answer (Roman Urdu, this exact format)

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

## Step 4 — Bookkeeping
- Log every plan (and every NO TRADE) with `trade-journal`.
- Move handled alerts to `alerts/history/YYYY-MM-DD.json`, commit and push.
