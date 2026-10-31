# PHASE 1 — Foundation + Watchman Scanner + Telegram + Auto Alerts

Read `CLAUDE.md`, `config.yaml`, `.claude/settings.json` and all skills in `.claude/skills/` first. They describe the full system. In this phase we build the foundation and Stage 1 (the watchman). Talk to me in Roman Urdu while you work.

## What to build

### 1. Project setup
- Initialize git in this folder, add a `.gitignore` (venv, `__pycache__`, `.env`, `charts/`, data cache).
- `requirements.txt`: ccxt, pandas, numpy, pyyaml, requests, python-dotenv, mplfinance, matplotlib.
- Create a virtual environment and install everything. Check my OS (Windows/Mac) and use the right commands.
- `.env.example` with `TELEGRAM_BOT_TOKEN=` and `TELEGRAM_CHAT_ID=`. Real values go in `.env` (local) and GitHub Secrets (cloud).
- Folder structure: `lib/`, `scanner/`, `scripts/`, `alerts/`, `alerts/history/`, `journal/`, `backtest/`, `backtest/results/`, `charts/`.

### 2. Shared library (`lib/`)
- `lib/config.py` — load `config.yaml` + `.env`.
- `lib/data.py` — CCXT `mexc` with `defaultType: swap` (USDT-M perpetuals, public endpoints only).
  - `get_universe()`: top N USDT perpetuals by 24h volume, apply `min_24h_volume_usd`, `max_spread_pct`, exclude list.
  - `get_ohlcv(symbol, tf, limit)`: returns a pandas DataFrame, only **closed** candles, with retry + rate-limit handling and a small local cache.
  - CLI: `python lib/data.py --symbol SOL/USDT:USDT --tf 1d,4h,1h --limit 500`.
- `lib/indicators.py` — EMA, ATR, RSI, volume moving average, candle body stats.
- `lib/smc.py` — first version of the SMC detectors (used by both the scanner and later the skills):
  - swing highs/lows (fractal, `swing_length`)
  - market structure: trend, BOS and CHoCH with prices and candle times (close-based)
  - fair value gaps (with fill %), order blocks (unmitigated, caused displacement + BOS)
  - liquidity pools: equal highs/lows, PDH/PDL, PWH/PWL
  - liquidity sweeps (wick through, close back inside)
  - premium/discount of the 4H dealing range
  - CLI for quick checks.
- `lib/notify.py` — Telegram send via Bot API using env token + chat id.

### 3. Watchman scanner (`scanner/run_scan.py`)
Runs once per hour, right after the 1H candle closes. For every coin in the universe:
1. Fetch 4H and 1H (and BTC 1H/4H once per run).
2. Compute a **light score** from these triggers:
   - price within `near_poi_pct` of an unmitigated 4H OB/FVG or a key level (PDH/PDL/PWH/PWL)
   - a fresh liquidity sweep on 1H
   - a fresh 1H BOS/CHoCH aligned with 4H structure
   - 1H volume ≥ `volume_spike_mult` × its 20-period average
   - BTC filter (no alt long trigger during a BTC 1H dump of `btc_dump_pct`)
3. Keep coins with score ≥ `min_alert_score`, skip coins still in `coin_cooldown_hours`, keep the top `max_alerts_per_run`.
4. For each kept coin, append to `alerts/pending.json`: id, coin, symbol, direction hint, triggers list, light score, price, created_at (UTC + PKT). Track cooldowns in `alerts/state.json`.
5. Send ONE short Telegram message for the run (only if there are alerts):
   ```
   ⚡ Setup ban sakta hai: SOL/USDT, ETH/USDT
   Claude Code kholo aur /deep-scan chalao.
   ```
   No entry, SL or TP in this message.
6. Flags: `--dry-run` (no Telegram, no file write, print results), `--coins SOL,ETH` (limit universe).
7. Log a one-line run summary (coins scanned, alerts, duration).

### 4. GitHub Actions (`.github/workflows/scanner.yml`)
- Schedule: every hour at minute 2 (`2 * * * *`) + manual `workflow_dispatch`.
- Python setup, pip cache, install requirements, run `python scanner/run_scan.py`.
- Secrets: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.
- `permissions: contents: write`; commit and push `alerts/` changes only when they changed, with `[skip ci]` in the message, using pull-rebase to avoid push conflicts.
- **Connectivity check:** GitHub runners are in the US. Add a step that tests the MEXC public API first. If MEXC blocks the runner, tell me and set up the best working alternative (e.g. same-coin data from another exchange's public API via CCXT for the scan, or another free host for the scanner) so the scanner still runs every hour.

### 5. SessionStart hook (`scripts/session_start.py`)
`.claude/settings.json` already calls `python scripts/session_start.py`. Make it:
- run `git pull --quiet` (if offline or it fails, continue silently with local files),
- read `alerts/pending.json`,
- print a short summary that will appear in the Claude Code session, e.g.:
  ```
  📬 2 pending alerts:
  - SOL/USDT (LONG hint) — 11:02 PKT — sweep + near 4H OB — score 68
  - ETH/USDT (SHORT hint) — 13:02 PKT — 1H CHoCH + volume — score 63
  Rans ko pucho: "Scan karoon?" → /deep-scan
  ```
  or `📭 Koi pending alert nahi.`
- finish fast (under a few seconds) and work on Windows and Mac. Make sure the hook command works on my OS (use `py`/`python3` if `python` is not right) and update `.claude/settings.json` if needed.

### 6. Helper scripts
- `scripts/test_telegram.py` — sends "✅ Trading agent connected" to my phone.
- `scripts/test_data.py` — fetches universe + candles for 3 coins and prints them.

## Setup help for me
Walk me step by step (Roman Urdu, simple) through:
1. Creating the Telegram bot with @BotFather and getting the bot token.
2. Getting my chat ID.
3. Creating a **private** GitHub repo, connecting this folder and pushing.
4. Adding the two GitHub Secrets.
5. Running the workflow manually once from the Actions tab.

## Testing before you say done
1. `scripts/test_data.py` works.
2. `scripts/test_telegram.py` — I receive the message on my phone.
3. `scanner/run_scan.py --dry-run` on the full universe — show me the output.
4. One real local run → alert written to `alerts/pending.json` (if any) and Telegram received.
5. Start a new Claude Code session → hook shows the pending alerts.
6. GitHub Actions manual run succeeds and commits alerts.

At the end give me a short summary: what was built, what I need to do, and what Phase 2 will add.
