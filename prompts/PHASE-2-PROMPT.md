# PHASE 2 — Laptop setup + full /deep-scan analysis engine

Read `CLAUDE.md`, `config.yaml`, `SETUP.md`, every skill in `.claude/skills/`, and the existing code in `lib/`, `scanner/`, `scripts/` first. Phase 1 is done and live: the watchman scanner runs every hour on GitHub Actions (private repo `ah2233092/trading-agent`, branch `main`), uses MEXC USDT-M futures data, sends Telegram alerts and writes `alerts/pending.json`. Talk to me in Roman Urdu while you work.

## Part A — Laptop setup (do this first)
1. Make sure this folder is a clone of `https://github.com/ah2233092/trading-agent` on branch `main` and up to date (`git pull`).
2. Check my OS, then run `scripts/setup_venv.py` to create the virtual environment and install `requirements.txt`.
3. Create `.env` from `.env.example`. Ask me to paste my Telegram bot token and chat id into it myself.
4. Run `scripts/test_data.py` and `scripts/test_telegram.py` on my laptop and show me the result.
5. Confirm the SessionStart hook works on my OS: a new session must show the pending alerts summary. Fix `.claude/settings.json` if the python command is wrong for my OS.

## Part B — Build the full analysis library behind the skills
Every skill in `.claude/skills/` points to scripts in `lib/`. Build all of them so each skill works exactly as its SKILL.md describes. Every script needs a CLI with `--json` output so the skills can read results.

### 1. `lib/smc.py` — extend the Phase 1 detectors
- OTE zone (0.62–0.79 fib of the last impulse leg)
- Breaker blocks (failed OB that flipped after CHoCH)
- Displacement detection (body ≥ 1.5× avg body of last 20 + volume above 20-period avg)
- Killzones (London 07:00–10:00 UTC, New York 12:00–15:00 UTC) and the Asia range high/low
- 15m entry confirmation: LTF CHoCH inside the HTF POI
- Entry model classifier: A (sweep + CHoCH + FVG/OB retest), B (HTF POI tap + 15m CHoCH), C (BOS continuation + OTE retrace), D (breaker retest), with a quality grade A+ / A / B / reject

### 2. `lib/bias.py` — for the `htf-bias` skill
1D + 4H structure, EMA 20/50/200, 4H dealing range with premium/discount %, PDH/PDL/PWH/PWL and monthly open, BTC filter (`btc_dump_pct`). Output: LONG / SHORT / NONE with the reasons.

### 3. `lib/chart.py` — for the `chart-vision` skill
mplfinance dark-theme PNGs for 1D, 4H, 1H, 15m (last 150 candles + volume panel), with OB boxes, FVG boxes, liquidity lines, BOS/CHoCH labels, swing points, the 4H 50% line, and (when a plan JSON is given) entry zone, SL, TP1/TP2/TP3 and liquidation lines. Save to `charts/<COIN>/<timestamp>_<tf>.png`.

### 4. `lib/futures.py` — for the `futures-data` skill
Funding rate (current + last 8), open interest and its 1H / 4H change, long/short ratio, 24h volume and spread. Use MEXC first; if MEXC does not provide a metric, use another major exchange via CCXT and report which source was used. Give a verdict: supportive / neutral / against, with the price + OI interpretation from the skill.

### 5. `lib/risk.py` — for the `risk-leverage` skill
- Live MEXC contract specs: contract size, min qty, qty step, tick size, maker/taker fees, maintenance margin rate
- Entry (POI/OTE middle), structure SL + ATR buffer (`sl_atr_buffer`), TP1/TP2/TP3 from opposing levels and liquidity
- Reject when TP1 is blocked before `min_rr_tp1`
- Position size from `risk_per_trade_usd` including round-trip fees, rounded to contract step
- Lowest leverage that fits `max_margin_per_trade`, capped at `max_leverage`, isolated margin
- Isolated liquidation price estimate, which must be beyond SL by `liq_buffer_pct`; otherwise lower leverage or reject
- Net reward after fees for TP1/TP2/TP3

### 6. `lib/score.py` — confluence score
0–100 using `score_weights` from `config.yaml`, with a breakdown of points per category. Below `min_trade_score` → NO TRADE.

### 7. `lib/journal.py` — for the `trade-journal` skill
`journal/trades.csv` with the columns from the skill. Commands: `add --from plan.json`, `update --id --status --exit`, `autocheck` (replays candles after each open plan and marks TP1/TP2/TP3/SL/expired; SL wins if SL and TP are in the same candle), `report --period day|week|all` with win rate and avg R per setup type. Write setup stats to `backtest/results/setup_stats.json` (merged with backtest stats later in Phase 3).

### 8. `lib/deep_scan.py` — the engine behind `/deep-scan`
One command that runs everything for one coin and returns a single JSON report the skill reads:
`python lib/deep_scan.py --symbol SOL --json`
1. Re-validate the alert if older than `alert_max_age_hours`
2. bias → smc → futures → risk → score
3. Render the charts and return their paths (Claude then looks at them with the chart-vision skill)
4. Read the setup win rate from `backtest/results/setup_stats.json`; if there is no data, return "not enough data yet"
5. Return the plan or NO TRADE with reasons
Also: `--all-pending` for every coin in `alerts/pending.json`, and `--close-alerts` to move handled alerts to `alerts/history/YYYY-MM-DD.json` and commit + push.

News is not a script: the `news-check` skill uses WebSearch / WebFetch inside Claude Code.

### 9. Update the skills if needed
If any command name or output changed while building, update the matching `SKILL.md` and `CLAUDE.md` so the skills and code always match. Keep the final answer format from `deep-scan/SKILL.md`.

## Part C — Testing before you say done
1. Offline tests in `tests/` for the new detectors, risk math (size, leverage, liquidation, fees) and score, using synthetic candles. All must pass.
2. Live: run `python lib/deep_scan.py --symbol BTC --json` and `--symbol SOL --json` on my laptop and show me the output.
3. Show me the rendered charts for one coin.
4. Run the full `/deep-scan SOL` skill end to end: bias, SMC, charts (look at them), futures data, news, risk, score, then the final answer in the exact deep-scan format (trade plan or NO TRADE).
5. Journal: the plan from step 4 is logged in `journal/trades.csv` and `report --period day` works.
6. Commit and push everything to `main`.

At the end give me a short summary in Roman Urdu: what was built, the test results, and what Phase 3 (backtest) will add.
