# START HERE — Rans ka Trading Agent

## Is folder mein kya hai
- `CLAUDE.md` — Claude Code ka "dimagh": poora system, flow aur rules
- `.claude/skills/` — 9 custom skills (deep-scan, htf-bias, smc-ict-analysis, chart-vision, futures-data, news-check, risk-leverage, trade-journal, backtest)
- `.claude/settings.json` — SessionStart hook (Claude Code kholte hi pending alerts dikhayega)
- `config.yaml` — coins, risk, leverage, score settings (yahan se sab kuch change ho sakta hai)
- `prompts/PHASE-1-PROMPT.md` — Claude Code ko dene wala pehla prompt

## Kaise shuru karna hai
1. Laptop par **Python** install ho (python.org se, install karte waqt "Add to PATH" tick karna).
2. **Git** install ho (git-scm.com).
3. Is zip ko extract karo, misaal: `Documents/trading-agent`.
4. Us folder mein terminal kholo aur `claude` likh ke Claude Code chalao.
5. `prompts/PHASE-1-PROMPT.md` ka poora text copy karke Claude Code mein paste karo.
6. Claude Code jo steps bataye (Telegram bot, GitHub repo, secrets) woh follow karo.

> Phase 1 ban chuka hai — setup ke steps (Telegram, GitHub, secrets, venv) **`SETUP.md`** mein hain.

## Roz ka use (Phase 2 ke baad)
1. Telegram par alert aaye: "⚡ Setup ban sakta hai…"
2. Laptop kholo → folder mein `claude` chalao → pending alerts khud nazar aayenge.
3. `/deep-scan` likho → full analysis → final trade ya NO TRADE.
4. Trade ka result Claude ko batao ("SOL ka TP1 hit hua") → journal update.

## Phases
- Phase 1: foundation + scanner + Telegram + GitHub + hook  ← abhi
- Phase 2: full analysis library + /deep-scan end to end
- Phase 3: backtest + asli win rates
- Phase 4: journal reports + paper trading + tuning
