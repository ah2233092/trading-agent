---
name: futures-data
description: Futures positioning check for a coin — funding rate, open interest change, long/short ratio, recent liquidations and spread/volume. Use in every deep scan to spot crowded trades and squeeze/trap risk.
---

# Futures Data

`python lib/futures.py --symbol <COIN>/USDT:USDT`

## Read
1. **Funding rate** (current + last 8 periods)
   - Strongly positive → longs crowded → LONG riskier, short squeeze-down risk.
   - Strongly negative → shorts crowded → SHORT riskier.
   - Thresholds in `config.yaml` (`funding_extreme`).
2. **Open interest** (1H and 4H change %)
   - Price ↑ + OI ↑ = new longs (healthy trend).
   - Price ↑ + OI ↓ = short covering (weaker move).
   - Price ↓ + OI ↑ = new shorts. Price ↓ + OI ↓ = long liquidation/exit.
   - OI spike into a level + sweep = fuel for the reversal.
3. **Long/short ratio** (if available): extreme readings = contrarian warning.
4. **Liquidity**: 24h volume and spread. Reject coins below `min_24h_volume_usd` or with wide spread.

Where MEXC does not provide a metric, fall back to CCXT data from another major exchange for the same coin and say which source was used.

## Output
```
Funding: <x.xxxx%> (<normal/elevated/extreme>, crowd: <long/short/none>)
OI: 1H <±x%>, 4H <±x%> → <interpretation>
L/S ratio: <x> (<note>)
Liquidity: 24h vol $<>, spread <x%>
Verdict: supportive / neutral / against (<reason>)
```
