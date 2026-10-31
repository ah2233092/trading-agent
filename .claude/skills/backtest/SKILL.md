---
name: backtest
description: Backtest the SMC/ICT setup types and the watchman scanner rules on historical MEXC futures data to get real win rate, average R, profit factor and drawdown per setup type. Use when Rans asks to test the system, when rules or config change, or when setup stats need refreshing.
---

# Backtest

Engine: `backtest/engine.py` (uses the same `lib/` detection code as live scans — no separate logic).

## Run
1. History: GitHub "Backtest data" workflow (MEXC history → `history-data` branch), or locally
   `python backtest/history.py --coins config --from 2024-06-01` → `data/history/<COIN>/{1d,4h,1h,15m}.csv.gz` + `spec.json`.
   MEXC keeps 15m candles for only ~12 months, so the main backtest period starts ~12 months back.
2. `python backtest/engine.py --coins config --from 2025-11-10 --workers 2` (or `--coins BTC,ETH,SOL`).
   `TA_HISTORY_DIR=/path/to/history` if the history is not in `data/history/`.
3. Outputs in `backtest/results/`: `latest_report.md`, `summary.json`, `trades_<date>.csv`, `equity_curve.png`,
   `setup_stats.json` (`setups.<X>.backtest` — out-of-sample numbers when OOS has ≥ 30 trades, else all data).
4. Tests: `python tests/test_backtest.py`.

Assumptions: news = clear, futures data = neutral (no history), backtest_edge neutral; every candidate plan is
recorded with its score, so the report shows results per score bucket. Same-setup re-detections within 24h are skipped;
one trade per coin at a time.

## Rules
- Walk forward candle by candle; a candle's data is used only after it closes (no look-ahead).
- Entries as limit orders at the planned entry; fill only if price trades through.
- Same SL/TP/partial (50% at TP1, SL to entry) and fee model as `lib/risk.py`.
- If SL and TP are inside the same candle, count it as SL (conservative).
- Unfilled plans expire after `plan_valid_hours`.
- Split data: build on the first 70%, verify on the last 30% (out-of-sample). Report both.

## Output
Save to `backtest/results/setup_stats.json` under `setups.<type>.backtest` (keep the `journal` keys that
`lib/journal.py` writes) and print a summary:
```
Setup | Trades | Win% | Avg R | Profit factor | Max DD (R) | OOS Win%
A     | ...
```
Plus scanner quality: how many watchman alerts turned into A/A+ setups.

Flag any setup type with fewer than 30 trades as "low sample". Recommend disabling setups whose out-of-sample expectancy is negative.
