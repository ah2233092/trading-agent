# Setup (Rans ke liye, step by step)

> Commands mein `python` likha hai. **Mac** par `python3` likho, **Windows** par `python` (ya `py`).
> Saari commands `trading-agent` folder ke andar chalani hain.

## 0. Laptop par folder lao + Python setup

**Code kahan hai:** private repo `ah2233092/trading-agent`, branch `main`.

```bash
git clone https://github.com/ah2233092/trading-agent.git
cd trading-agent
```
Pehle se clone hai to sirf: `git checkout main` aur `git pull`.

Virtual environment + packages (ek hi command, OS khud detect karti hai):

| | Windows (PowerShell) | Mac (Terminal) |
|---|---|---|
| Setup | `py scripts\setup_venv.py` | `python3 scripts/setup_venv.py` |
| Har naye terminal mein | `.venv\Scripts\activate` | `source .venv/bin/activate` |

Windows par agar activate error de: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` ek dafa chalao.

## 1. Telegram bot banao (@BotFather)
1. Telegram kholo → search karo **@BotFather** (blue tick wala) → **Start**.
2. `/newbot` bhejo.
3. Bot ka naam do (misaal: `Rans Trading Watchman`).
4. Username do — aakhir mein `bot` hona chahiye (misaal: `rans_watchman_bot`).
5. BotFather ek **token** dega, aisa: `123456789:AAH...xyz`. Yeh password ki tarah hai — kisi ko mat dikhana.
6. `trading-agent/.env` file kholo (setup ne bana di hai) aur likho:
   `TELEGRAM_BOT_TOKEN=123456789:AAH...xyz`

## 2. Apna chat ID lo
1. Telegram mein apne naye bot ko kholo → **Start** dabao → `hi` bhejo.
2. Laptop par: `python scripts/test_telegram.py --chat-id`
3. Jo number aaye (misaal `987654321`) woh `.env` mein likho:
   `TELEGRAM_CHAT_ID=987654321`
4. Test: `python scripts/test_telegram.py` → phone par **"✅ Trading agent connected"** aana chahiye.

## 3. GitHub repo (private)
⚠️ `ah2233092/Mr` abhi **public** hai — alerts aur journal sab ko nazar aayenge. Do raaste:

**A) Asaan — Mr ko private kar do** (aap ke baaqi projects bhi private ho jayenge):
GitHub → `Mr` repo → **Settings** → **General** → neeche **Danger Zone** → **Change repository visibility** → **Private**.
Private repo mein GitHub Actions ke 2000 free minute/mahina hain; scanner taqreeban 750–1500 lega.

**B) Alag private repo `trading-agent`:**
1. github.com/new → naam `trading-agent` → **Private** → README mat lagao → Create.
2. `trading-agent` folder ki copy banao (Mr se bahar), us mein `.github/workflows/` folder banao aur
   `Mr/.github/workflows/scanner.yml` us mein copy karo. File mein `AGENT_DIR: trading-agent` ko `AGENT_DIR: "."` kar do.
3. Us copy mein:
   ```bash
   git init
   git add .
   git commit -m "Trading agent phase 1"
   git branch -M main
   git remote add origin https://github.com/<aapka-username>/trading-agent.git
   git push -u origin main
   ```

**Zaroori:** GitHub ka hourly schedule sirf **default branch** par chalta hai. Raasta A mein yeh code
`claude/kind-archimedes-5bixe3` branch par hai — isay default branch (`claude/practical-mendel-n7156l`) mein merge karna hoga (Claude PR bana sakta hai).

## 4. GitHub Secrets daalo
Repo → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**:
- Name `TELEGRAM_BOT_TOKEN` → value: BotFather wala token
- Name `TELEGRAM_CHAT_ID` → value: aap ka chat id

## 5. Workflow ek dafa manually chalao
Pehle check karo Actions on hain: repo → **Settings** → **Actions** → **General** → **Allow all actions** → Save.
Repo → **Actions** tab → left side **Watchman scanner** → **Run workflow** → (Dry run ka box khali chhoro) → **Run workflow**.
1–2 minute baad green ✅ aana chahiye. Run kholo → summary mein "Data source check" aur scan table nazar aayega.
Agar koi alert bana to Telegram aayega aur repo mein commit dikhega: `watchman: alerts ... [skip ci]`.

Note: GitHub ka schedule kabhi kabhi 5–20 minute late chalta hai — yeh normal hai.

## 6. Roz ka use
- Telegram par "⚡ Setup ban sakta hai…" aaye → laptop par `trading-agent` folder mein `claude` chalao.
- Session khulte hi hook `git pull` karega aur pending alerts dikhayega.
- `/deep-scan` (Phase 2 mein poora banega).

## Commands (yaad rakhne ke liye)
```bash
python scripts/test_data.py                      # universe + candles check
python scripts/test_telegram.py                  # Telegram test
python scanner/run_scan.py --dry-run             # scan, sirf print (koi message / file nahi)
python scanner/run_scan.py --coins SOL,ETH       # sirf yeh coins
python scanner/run_scan.py                       # asli run: alerts + Telegram
python lib/data.py --symbol SOL --tf 1d,4h,1h --limit 500
python lib/smc.py all --symbol SOL --tf 4h       # SMC read (structure, OB, FVG, liquidity...)
python lib/data.py --check                       # MEXC + fallback exchanges connectivity
python tests/test_smc.py                         # offline tests
```

## Phase 2 — laptop par check (Part A + live tests)
1. `git pull` → `python scripts/setup_venv.py` (Windows: `py scripts\setup_venv.py`) → venv activate.
2. `.env` mein token + chat id khud paste karo (`.env.example` dekho).
3. `python scripts/test_data.py` — universe + candles aane chahiye.
4. `python scripts/test_telegram.py` — phone par "✅ Trading agent connected".
5. Naya `claude` session kholo — shuru mein "📬 pending alerts" ya "📭 Koi pending alert nahi." nazar aana chahiye.
6. Offline tests: `python tests/test_smc.py` aur `python tests/test_phase2.py` (sab PASS).
7. Live deep scan:
   ```bash
   python lib/deep_scan.py --symbol BTC --json
   python lib/deep_scan.py --symbol SOL
   ```
   Charts `charts/SOL/` mein, report `reports/SOL/latest.json` mein.
8. Claude Code mein `/deep-scan SOL` → charts, news, final answer → journal:
   `python lib/journal.py add --from reports/SOL/latest.json` aur `python lib/journal.py report --period day`.
