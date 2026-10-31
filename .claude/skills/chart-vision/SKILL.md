---
name: chart-vision
description: Render candlestick charts with SMC markings (OB, FVG, liquidity, BOS/CHoCH, entry/SL/TP) and inspect them visually like a trader. Use during deep scans after smc-ict-analysis, or when Rans wants to see the chart for a coin.
---

# Chart Vision

## Render
`python lib/chart.py --symbol <COIN>/USDT:USDT --tf 1d,4h,1h,15m --marks auto --plan <optional plan.json>`

Saves PNGs to `charts/<COIN>/<timestamp>_<tf>.png` (dark theme, last 150 candles, volume panel) with:
- OB boxes (green/red), FVG boxes (translucent), liquidity lines (dashed) with labels
- BOS/CHoCH labels at the break, swing points
- Premium/discount 50% line of the 4H range
- If a plan exists: entry zone, SL, TP1/TP2/TP3 and liquidation lines

## Inspect
Open each PNG with the Read tool and actually look at it. Check:
1. Does the trend look the way the numbers say? (clean staircase vs choppy range)
2. Is the sweep obvious and the displacement strong, or is it a weak drift?
3. Is there overhead/underfoot resistance between entry and TP1 that the script missed?
4. Is price extended far from the POI (chasing)?
5. Any "too clean" level everyone sees (stop-hunt risk just beyond the SL)?

## Output
```
Chart read: <confirms / weakens / rejects> the setup
Notes: <2–4 short observations>
Adjustments: <e.g. move SL below the wick at X, TP1 before resistance at Y>
```
Rans can ask to see the charts — send the PNG files to him.
