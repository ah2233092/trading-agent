---
name: risk-leverage
description: Build the exact futures trade numbers — entry, structure-based stop loss, TP1/TP2/TP3, position size, leverage, margin and liquidation price — from account balance and risk settings. Use in every deep scan once a setup is accepted, or when Rans asks how much leverage or size to use.
---

# Risk & Leverage

All math in `lib/risk.py`. Never compute these numbers by hand.

`python lib/risk.py --symbol <COIN>/USDT:USDT --side long --entry <p> --sl <p> --tp <p1,p2,p3>`

## Inputs (config.yaml)
- `account_balance` (start: 20 USDT)
- `risk_per_trade_usd` / `risk_per_trade_pct`
- `max_leverage`, `margin_mode: isolated`
- `min_rr_tp1`, `min_rr_final`
- MEXC taker/maker fees and contract specs (min qty, step, tick) fetched live

## Method
1. **Entry**: middle or 50% of the POI/OTE zone (limit). Market entry only if price is already in the zone with confirmation.
2. **Stop loss**: beyond the structure that invalidates the idea (below sweep wick / OB low for longs) + buffer (ATR × `sl_atr_buffer`). Never a random %.
3. **Take profits**:
   - TP1 = first opposing level at ≥ `min_rr_tp1` R → close 50%, SL to entry.
   - TP2 = next liquidity pool / HTF level.
   - TP3 = major HTF liquidity (runner).
   - If TP1 is blocked by a level before `min_rr_tp1` → reject the trade.
4. **Position size**: qty = risk_usd / |entry − SL| (fees included), rounded to contract step.
5. **Leverage**: lowest leverage that fits the margin into `max_margin_per_trade`, capped at `max_leverage`.
6. **Liquidation check**: estimated isolated liquidation price (with maintenance margin rate) must be beyond the SL by at least `liq_buffer_pct`. If not → lower leverage, add margin or reject.
7. **Fees**: show net reward after round-trip fees.

## Output
```
Entry <> | SL <> (<x%>, <x R>)
TP1 <> (1:<R>) | TP2 <> (1:<R>) | TP3 <> (1:<R>)
Qty <contracts> | Notional $<> | Leverage <x>x isolated | Margin $<>
Liquidation ≈ <> (SL se <x%> door) ✅/❌
Risk $<> | Net reward TP1 $<> / TP2 $<> / TP3 $<> (fees ke baad)
```
