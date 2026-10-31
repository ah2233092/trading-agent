# Rans Trading Playbook (live rules)

## Roz ka schedule (Pakistan time)
| Waqt (PKT) | Kya hota hai | Aapko kya karna hai |
|---|---|---|
| Har ghante, :02 par | Watchman 22 coins scan karta hai (1D/4H/1H/15m, SMC/ICT, BTC filter, risk plan). Sirf jab trade **ready** ho: "🟢 Trade ban rahi hai" | Claude chat mein likho: "SOL scan karo" |
| 12:06 | 📋 London killzone digest — top 2 coins (har din, lazmi) | Chat mein: "scan karo" |
| 17:06 | 📋 New York killzone digest — top 2 coins (har din, lazmi) | Chat mein: "scan karo" |
| 12:00–15:00, 17:00–20:00 | Trade lene ka waqt (killzones) | Sirf in ghanton mein entry |
| Har ghante | Journal ki open trades auto-check: TP/SL hit par Telegram message | Apne MEXC par bhi SL/TP lagao |
| Itwaar 20:30 | Hafte ka report Telegram par | Parho, mujhe bhejo to tuning karenge |

Agar GitHub ya MEXC data mein masla ho: main aapke Chrome mein TradingView (MEXC:COINUSDT.P) khol kar charts dekh ke analysis karunga.

## Method (sab kuch ek saath, professional order)
1. **HTF bias** — 1D + 4H market structure (BOS/CHoCH), EMA 20/50/200, 4H premium/discount, PDH/PDL/PWH/PWL, monthly open.
2. **BTC filter** — BTC dump ho to altcoin longs band.
3. **SMC/ICT setup** — A: liquidity sweep + CHoCH + FVG/OB · B: 4H POI tap + 15m CHoCH · C: BOS + OTE (0.62–0.79) · D: breaker retest. Grade A+/A/B.
4. **Chart vision** — 1D/4H/1H/15m charts dekh kar confirm.
5. **Futures data** — funding, open interest, long/short ratio (crowd trap check).
6. **News** — coin news, token unlocks, CPI/FOMC/NFP; bari news 2 ghante ke andar ho to trade band.
7. **Entry rule (backtest se)** — sirf **London / New York killzone** mein, **market entry** current price se, aur TP1 kam se kam **1:3** ho (min $3 profit).
8. **Risk** — SL structure ke peeche, TP1 / TP2 / TP3 liquidity par. TP1 par poori position close.

## Paisa aur risk ($20 account)
- Har trade par risk **$1** (SL hit = −$1). Isolated margin, leverage system batata hai (liquidation SL se door).
- **Har jeetne wali trade kam se kam $3 profit:** TP1 kam se kam 1:3 par, aur poori position TP1 par close. Plan mein TP1 ka profit $3 se kam ho to system trade reject karta hai. TP2/TP3 optional hain.
- **Trades ki koi maximum limit nahi.** Roz kam se kam 2 coins (digests) scan honge.
- Deep scan ke 3 jawab: 🟢/🔴 **TRADE** (sab checks pass) · 🟡 **B-PLAN** (plan theek, lekin score ya killzone kam — risk aadha rakhna behtar) · ❌ **NO TRADE**.
- Safety: **3 SL ek din mein ho jayen to us din band.**
- Har trade se pehle chat mein deep scan; bina scan ke trade nahi.

## Sachi baat (backtest, 22 coins, ~2.3 saal ka data)
- Kisi bhi tested rule mein abhi **saabit edge nahi**: win rate ~13–31%, avg R zyada tar negative. 70% win rate is method se nahi aata.
- Jeetne wali trade loss se 2–4 guna badi hoti hai, phir bhi total abhi minus mein hai — is liye risk chhota ($1) aur daily limits sakht.
- Journal har trade ka asli result rakhega; 20–30 trades ke baad dekhenge system live mein kaisa hai, aur rules badlenge.
- Har din 2 digest alerts lazmi aate hain; trade sirf tab jab deep scan "TRADE" bole.
