---
name: trade-journal
description: Record every trade plan and NO TRADE decision, update outcomes (TP1/TP2/TP3/SL/cancelled), and produce daily/weekly performance reports with win rate per setup type. Use after every deep scan, when Rans reports a trade result, or when he asks how the system is performing.
---

# Trade Journal

File: `journal/trades.csv` (via `lib/journal.py`).

## Columns
id, created_at, coin, side, setup_type, score, entry, sl, tp1, tp2, tp3, leverage, risk_usd, status (planned / taken / skipped / no_trade / cancelled / tp1 / tp2 / tp3 / sl / be), exit_price, pnl_usd, r_multiple, notes

## Actions
- **Log a plan**: `python lib/journal.py add --from plan.json`
- **Log NO TRADE**: same, status `no_trade` with reason.
- **Update result** (Rans says "SOL ka TP1 hit hua"): `python lib/journal.py update --id <id> --status tp1 --exit <price>`
- **Auto-check outcomes**: `python lib/journal.py autocheck` — replays candles after each open plan and marks what was hit first (for paper trading).
- **Report**: `python lib/journal.py report --period week`

## Report output (Roman Urdu)
```
📒 Is hafte ka report
Signals: <n> | Liye: <n> | NO TRADE: <n>
Win rate: <x%> | Avg R: <x> | Net PnL: $<>
Best setup: <type> (<x%>) | Weakest: <type> (<x%>)
Max losing streak: <n>
Note: <one improvement suggestion>
```
Setup stats from the journal are merged with backtest stats in `backtest/results/setup_stats.json`.
