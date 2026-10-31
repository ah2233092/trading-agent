---
name: news-check
description: News and event check before a trade — coin-specific news, token unlocks, listings/delistings, hacks, and macro events (CPI, FOMC, NFP, rate decisions). Use in every deep scan, or when Rans asks what news is moving a coin or the market.
---

# News Check

Use WebSearch / WebFetch. Search fresh, include today's date in queries.

## Search
1. `<COIN> crypto news today` — last 24–48h headlines.
2. `<COIN> token unlock` — unlocks in the next 7 days (large unlock = sell pressure).
3. Exchange events: listing, delisting, maintenance, hack, exploit, lawsuit.
4. Macro calendar for today/tomorrow: CPI, PPI, FOMC, NFP, Fed speakers, rate decisions (USD events, times in UTC and PKT).
5. Market mood: overall crypto headlines (ETF flows, regulation, BTC big moves). Fear & Greed index if available.

## Rules for the verdict
- High-impact macro event within `news_block_hours` (config) → **block** new entries until after it.
- Hack / exploit / delisting for the coin → **block**.
- Large unlock within 3 days → **against** for longs.
- Strong positive catalyst aligned with direction → **supportive**.

## Output
```
News: clear / caution / block
- <headline summary, source, time>
- Upcoming: <event, time PKT>
Effect on trade: <one line>
```
Sources list at the end with links.
