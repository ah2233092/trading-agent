---
name: backtest
description: Backtest the SMC/ICT setup types and the watchman scanner rules on historical MEXC futures data to get real win rate, average R, profit factor and drawdown per setup type. Use when Rans asks to test the system, when rules or config change, or when setup stats need refreshing.
---

# Backtest

Engine: `backtest/engine.py` (uses the same `lib/` detection code as live scans — no separate logic).

## Run
`python backtest/engine.py --coins config --tf 1h --from 2024-01-01 --to today --setups A,B,C,D`

## Rules
- Walk forward candle by candle; a candle's data is used only after it closes (no look-ahead).
- Entries as limit orders at the planned entry; fill only if price trades through.
- Same SL/TP/partial (50% at TP1, SL to entry) and fee model as `lib/risk.py`.
- If SL and TP are inside the same candle, count it as SL (conservative).
- Unfilled plans expire after `plan_valid_hours`.
- Split data: build on the first 70%, verify on the last 30% (out-of-sample). Report both.

## Output
Save `backtest/results/setup_stats.json` and a summary:
```
Setup | Trades | Win% | Avg R | Profit factor | Max DD (R) | OOS Win%
A     | ...
```
Plus scanner quality: how many watchman alerts turned into A/A+ setups.

Flag any setup type with fewer than 30 trades as "low sample". Recommend disabling setups whose out-of-sample expectancy is negative.
