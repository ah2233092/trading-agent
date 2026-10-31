---
name: smc-ict-analysis
description: Smart Money Concepts and ICT analysis on 4H/1H/15m — market structure (BOS/CHoCH), order blocks, fair value gaps, liquidity pools and sweeps, breaker blocks, OTE and killzones. Use in every deep scan after htf-bias, or when Rans asks for an SMC/ICT read of a chart.
---

# SMC / ICT Analysis

All detection runs through `lib/smc.py` so results are repeatable. You then judge quality like a trader.

## Commands
- `python lib/smc.py all --symbol <COIN> --tf 4h,1h,15m [--json]` — swings, structure, OBs, FVGs, breakers,
  liquidity, sweeps, premium/discount, OTE, displacement candles, Asia range
- `python lib/smc.py setup --symbol <COIN> --side long|short [--json]` — entry model A/B/C/D + grade
- `python lib/smc.py killzones --symbol <COIN>` — active killzone, next one, Asia range
- Single parts: `swings`, `structure`, `fvg`, `ob`, `breakers`, `liquidity`, `sweeps`, `pd`, `ote`

## 1. Structure (4H → 1H → 15m)
- Swing points (fractal, length from config).
- **BOS** = continuation break with candle **close** beyond the swing.
- **CHoCH** = first break against the prior trend.
- Mark internal vs external structure; only external structure defines trend.

## 2. Liquidity
- Buy-side: equal highs, PDH/PWH, swing highs, trendline tops.
- Sell-side: equal lows, PDL/PWL, swing lows.
- **Sweep** = wick through the pool and close back inside. A sweep followed by displacement the other way is the core ICT entry model.
- Next opposite liquidity pool = natural TP target.

## 3. Points of Interest
- **Order Block**: last opposite candle before a displacement that caused a BOS. Valid only if unmitigated and it created imbalance.
- **Fair Value Gap**: 3-candle gap (candle 1 high < candle 3 low for bullish). Track fill %.
- **Breaker block**: failed OB that flipped after a CHoCH (OB closed through + CHoCH the other way). Status fresh / tapped / invalid.
- Prefer POIs that overlap (OB + FVG) and sit in HTF discount (longs) / premium (shorts).

## 4. Entry model (pick and name one)
- **A — Sweep + CHoCH + FVG/OB retest** (highest priority): 1H sweep, then a CHoCH/BOS within 24 candles,
  POI = OB/FVG born in that leg (OB+FVG overlap preferred), price at or above it (retest pending).
- **B — HTF POI tap + LTF (15m) CHoCH confirmation**: 4H OB/FVG tapped in the last 8 1H candles + 15m CHoCH after the tap.
- **C — BOS continuation, retrace into OTE (0.62–0.79 fib) of the impulse leg** (0.705 = sweet spot).
- **D — Breaker retest after failed swing** (within 2 ATR of the breaker).

Grade = count of quality points: 4H trend aligned, POI in discount (long) / premium (short), displacement
(body + volume), confluence (OB+FVG / OTE / 4H POI overlap), 15m CHoCH confirmation.
4–5 = **A+**, 3 = **A**, 2 = **B**, 0–1 = **reject**.

## 5. Timing
- Killzones (UTC): London 07:00–10:00, New York 12:00–15:00. Asia range (00:00–06:00 UTC) high/low as liquidity.
  Hours are in `config.yaml` → `smc.killzones_utc`.
- Outside killzones lower the score, do not auto-reject.

## 6. Displacement & volume
- Displacement candle body ≥ 1.5× average body of last 20 candles, with volume above its 20-period average
  (`displacement_flags` in `lib/smc.py`; a big body without volume counts only half in the score).

## Output
```
Setup: <A/B/C/D name> | Direction: <LONG/SHORT>
Structure: 4H <>, 1H <last BOS/CHoCH at price>, 15m <>
Liquidity: swept <pool at price> | target <opposite pool at price>
POI: <OB/FVG/breaker> <low–high>, in <discount/premium>
OTE zone: <a–b>
Displacement/volume: <strong/weak>
Quality: A+ / A / B / reject — <one-line reason>
```
