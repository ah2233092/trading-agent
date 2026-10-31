---
name: trade-journal
description: Record every trade plan and NO TRADE decision, update outcomes (TP1/TP2/TP3/SL/cancelled), and produce daily/weekly performance reports with win rate per setup type. Use after every deep scan, when Rans reports a trade result, or when he asks how the system is performing.
---

# Trade Journal

File: `journal/trades.csv` (via `lib/journal.py`).

## Columns
id, created_at, coin, side, setup_type, score, entry, sl, tp1, tp2, tp3, leverage, risk_usd, status (planned / taken / skipped / no_trade / cancelled / expired / tp1 / tp2 / tp3 / sl / be), exit_price, pnl_usd, r_multiple, notes
Extra columns used by autocheck: symbol, grade, order, valid_until, qty, filled_at, closed_at.

R per outcome (partial schedule from `tp1_close_pct`): sl = −1, be = 0, tp1 = 0.5·R1 (rest stopped at entry),
tp2 = 0.5·R1 + 0.25·R2, tp3 = 0.5·R1 + 0.25·R2 + 0.25·R3. PnL = R × risk_usd (risk already includes fees).

## Actions
- **Log a plan**: `python lib/journal.py add --from reports/<COIN>/latest.json` (a deep-scan report or a plain plan JSON)
- **Log NO TRADE**: same, status `no_trade` with reason.
- **Update result** (Rans says "SOL ka TP1 hit hua"): `python lib/journal.py update --id <id> --status tp1 --exit <price>`
- **Auto-check outcomes**: `python lib/journal.py autocheck` — replays 15m candles after each open plan: fill at entry,
  then TP1/TP2/TP3/SL (SL wins if SL and TP are in the same candle; SL moves to entry after TP1); unfilled after
  `valid_until` → expired.
- **Report**: `python lib/journal.py report --period day|week|all [--json]`
- **List**: `python lib/journal.py list --n 10`

## Report output (Roman Urdu)
```
📒 Is hafte ka report
Signals: <n> | Liye: <n> | NO TRADE: <n>
Win rate: <x%> | Avg R: <x> | Net PnL: $<>
Best setup: <type> (<x%>) | Weakest: <type> (<x%>)
Max losing streak: <n>
Note: <one improvement suggestion>
```
Setup stats are written to `backtest/results/setup_stats.json` as `setups.<type>.journal`
(`trades, wins, losses, be, win_rate, avg_r, profit_factor`). Phase 3 adds `setups.<type>.backtest`;
the deep scan prefers backtest stats when present.
