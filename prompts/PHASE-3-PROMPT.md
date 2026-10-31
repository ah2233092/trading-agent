# PHASE 3 — Backtest engine + real win rates per setup

First run `git pull` on `main` to get the latest fixes, and use the `.venv` Python for every command.

Read `CLAUDE.md`, `config.yaml`, the `backtest` skill (`.claude/skills/backtest/SKILL.md`) and the existing code in `lib/` (especially `smc.py`, `bias.py`, `risk.py`, `score.py`, `journal.py`, `deep_scan.py`) and `scanner/` first. Phases 1 and 2 are done and live on `main` of `ah2233092/trading-agent`. Talk to me in Roman Urdu while you work.

Goal: find out, with real historical MEXC futures data, how each setup type (A/B/C/D) and the watchman scanner actually perform — win rate, average R, profit factor, drawdown — so `/deep-scan` shows a real "📊 win rate" instead of "not enough data yet", and so we can tune the system.

## 1. Historical data — `backtest/history.py`
- Download closed candles for 1d, 4h, 1h and 15m from MEXC USDT-M perpetuals via `lib/data.py` (paginate with `since`, respect rate limits, resume where it stopped).
- Coins: the current universe from `config.yaml` (top volume, excludes applied) plus BTC. Period: from 2024-01-01 (or the coin's listing date) to today.
- Store as Parquet (or CSV if Parquet is not available) in `data/history/<COIN>/<tf>.parquet`. Add `data/history/` to `.gitignore`.
- CLI: `python backtest/history.py --coins config --from 2024-01-01 [--update]`.

## 2. Backtest engine — `backtest/engine.py`
Uses the exact same `lib/` detection and risk code as the live deep scan (no copy of the logic).
- **Walk forward candle by candle on 1H.** At each 1H close, build the 1D/4H/1H/15m views using only candles that were closed at that moment (no look-ahead). Write a test that proves it.
- At each step run bias → SMC setup classifier → risk plan → score (without news and without backtest_edge, both set to neutral).
- A plan is created only when the score ≥ `min_trade_score` and the risk plan is accepted.
- Fill: limit order at the planned entry, filled only if price trades through it within `plan_valid_hours`; otherwise expired.
- Management exactly like live: TP1 closes `tp1_close_pct`, SL moves to entry, the rest runs to TP2/TP3 or break-even. If SL and a TP are inside the same candle, count SL (conservative). Use 15m candles to resolve the order of hits inside a 1H candle when available.
- Costs: MEXC maker/taker fees from `lib/risk.py`, plus a small slippage setting (`backtest_slippage_pct` in config).
- One open trade per coin at a time; optional `max_open_trades` across coins.
- Record every trade: coin, side, setup type, grade, score, entry/SL/TP, fill time, exit time, outcome (tp1/tp2/tp3/sl/be/expired), R multiple, PnL in $ with the config risk.
- CLI: `python backtest/engine.py --coins config --from 2024-01-01 --to today --setups A,B,C,D [--coins BTC,SOL,ETH]`.
- Make it fast enough to run the full universe in a reasonable time (cache indicators/swings per timeframe, use multiprocessing per coin). Show a progress bar.

## 3. Watchman scanner backtest — `backtest/scanner_bt.py`
Replay `scanner/light_score.py` on the same history: how many alerts per day it would send, and what % of alerts led to an accepted A/A+ deep-scan plan within the next `alert_max_age_hours`. Suggest a `min_alert_score` that keeps alerts useful (not spam, not silent).

## 4. Results and stats
- **In-sample / out-of-sample split:** first 70% of the period to build and tune, last 30% to verify. Report both separately. Never tune on the out-of-sample part.
- Per setup type and overall: trades, win rate (TP1 or better), avg R, expectancy, profit factor, max drawdown (R and $), longest losing streak, avg trade duration, trades per week.
- Also break down by side (long/short), by coin, by killzone vs outside, and by score bucket (75–80, 80–90, 90+).
- Write `backtest/results/setup_stats.json` in the shape `deep_scan.py` already reads: `setups.<A|B|C|D>.backtest` (keep the existing `.journal` part from Phase 2 untouched). Include `sample_size` and an `oos` block.
- Flag any setup with fewer than 30 trades as **low sample**. Recommend disabling any setup whose out-of-sample expectancy is negative.
- Save trades to `backtest/results/trades_<date>.csv` and a readable report `backtest/results/report_<date>.md` with charts (equity curve in R, drawdown, win rate per setup) as PNGs in `backtest/results/`.

## 5. Connect to the live system
- `lib/deep_scan.py` and `lib/score.py`: `backtest_edge` points and the "📊 Is setup ka win rate" line must now use the backtest stats (out-of-sample numbers first, journal numbers added when they exist).
- Update the `backtest` and `deep-scan` skills and `CLAUDE.md` with the new commands.
- GitHub Actions: add `.github/workflows/backtest.yml` (manual run + weekly on Sunday) that downloads history, runs the backtest, writes the report into the job summary, and commits `backtest/results/setup_stats.json` and the report to `main` (not the raw history data).

## 6. Testing before you say done
1. Offline tests in `tests/test_backtest.py` with synthetic candles: no look-ahead, fill/expire logic, TP1 partial + SL to entry, SL-first rule for same-candle hits, fees, R math. All existing tests must still pass.
2. Run the backtest on BTC, ETH and SOL first and show me the results table.
3. Then run the full universe and show me the per-setup table (in-sample and out-of-sample side by side).
4. Show me the equity curve chart.
5. Run `/deep-scan SOL` and show that the win rate line now uses the backtest numbers.
6. Commit and push everything to `main`.

At the end give me a short summary in Roman Urdu: real win rate of each setup, which setups are strong and which should be turned off, what `min_trade_score` and `min_alert_score` you recommend, and what Phase 4 (paper trading + journal + tuning) will add.
