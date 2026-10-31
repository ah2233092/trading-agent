---
name: htf-bias
description: Higher-timeframe directional bias for a coin — 1D and 4H trend, market structure, premium/discount and the BTC filter. Use as the first step of every deep scan, or when Rans asks which direction a coin is in.
---

# HTF Bias

Goal: decide which direction is allowed on the 1H: **LONG**, **SHORT** or **NONE**.

## Run
`python lib/bias.py --symbol <COIN> --json` — does every check below and returns `direction`, `reasons`,
`d1` / `h4` (trend, last BOS/CHoCH, EMA state), `h4.range` (premium/discount), `key_levels`
(PDH/PDL/PWH/PWL + MO = monthly open), `btc` and `text` (the output block below).
Raw candles if needed: `python lib/data.py --symbol <COIN>/USDT:USDT --tf 1d,4h,1h --limit 500`

## Checks
1. **Market structure (1D, 4H)** — `lib/smc.py structure`: sequence of swing HH/HL (bullish) or LH/LL (bearish); last BOS and last CHoCH with prices.
2. **EMAs (1D, 4H)** — EMA 20/50/200: price position and slope. Supporting evidence only, structure wins.
3. **Dealing range (4H)** — from the last significant swing low to swing high. Equilibrium = 50%.
   - Longs preferred only in **discount** (below 50%), shorts only in **premium** (above 50%).
4. **Key HTF levels** — previous day/week high & low (PDH/PDL, PWH/PWL, UTC days, weeks from Monday), monthly open (MO), major 1D/4H OBs.
5. **BTC filter** — run the same structure check on BTC/USDT 4H and 1H.
   - Altcoin LONG not allowed if BTC 1H is making a fresh bearish BOS/CHoCH (last 3 candles) or the last 1H candle dumped `btc_dump_pct` → direction becomes NONE.
   - Alt SHORT during a BTC 1H pump / fresh bullish break is allowed but marked `against` (lower score).
   - Note BTC dominance direction if available (rising dominance = weaker alts).

## Output
```
HTF BIAS: LONG / SHORT / NONE
1D: <bullish/bearish/range> — last BOS <price>
4H: <...> — range <low–high>, price in <premium/discount> (<xx%>)
BTC: <supportive / neutral / against>
Key levels: PDH <>, PDL <>, PWH <>, PWL <>
```
If 1D and 4H disagree, allow only trades in the 4H direction **and** only from strong POIs; otherwise NONE.
