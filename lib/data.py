"""Market data layer — CCXT, public endpoints only (no API keys).

Default: MEXC USDT-M perpetuals (`mexc`, defaultType=swap). If the main exchange
is unreachable (e.g. GitHub's US runners are blocked) set DATA_EXCHANGE to one of
`fallback_exchanges` in config.yaml; symbols stay the same (`SOL/USDT:USDT`).

CLI:
    python lib/data.py --symbol SOL/USDT:USDT --tf 1d,4h,1h --limit 500
    python lib/data.py --universe
    python lib/data.py --check            # connectivity test (main + fallbacks)
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ccxt
import pandas as pd

from lib.config import CACHE_DIR, cfg_get, fix_console, load_config

log = logging.getLogger("data")

STABLE_BASES = {"USDC", "FDUSD", "TUSD", "DAI", "USDE", "BUSD", "USDD", "PYUSD", "USD1"}
CACHE_MAX_ROWS = 3000
_EXCHANGES: dict[str, ccxt.Exchange] = {}


# ---------------------------------------------------------------- exchange --

def exchange_id(cfg: dict | None = None) -> str:
    """Active data exchange: env DATA_EXCHANGE overrides config.yaml `exchange`."""
    env = (os.getenv("DATA_EXCHANGE") or "").strip().lower()
    return env or str(cfg_get(cfg or load_config(), "exchange", "mexc")).lower()


def get_exchange(ex_id: str | None = None, cfg: dict | None = None) -> ccxt.Exchange:
    ex_id = (ex_id or exchange_id(cfg)).lower()
    if ex_id not in _EXCHANGES:
        if not hasattr(ccxt, ex_id):
            raise ValueError(f"Unknown CCXT exchange id: {ex_id}")
        klass = getattr(ccxt, ex_id)
        _EXCHANGES[ex_id] = klass(
            {
                "enableRateLimit": True,
                "timeout": 20000,
                "options": {"defaultType": "swap"},
            }
        )
    return _EXCHANGES[ex_id]


def call(fn, *args, retries: int = 4, **kwargs):
    """Call a CCXT method with retry + backoff on network / rate-limit errors."""
    delay = 1.0
    for attempt in range(1, retries + 1):
        try:
            return fn(*args, **kwargs)
        except (ccxt.RateLimitExceeded, ccxt.DDoSProtection) as e:
            wait, err = max(delay, 5.0), e
        except ccxt.NetworkError as e:  # timeouts, 5xx, exchange not available
            wait, err = delay, e
        if attempt == retries:
            raise err
        log.warning("%s failed (%s: %s) — retry %d/%d in %.0fs",
                    getattr(fn, "__name__", "call"), type(err).__name__,
                    str(err)[:160], attempt, retries - 1, wait)
        time.sleep(wait)
        delay *= 2


def markets(ex: ccxt.Exchange | None = None) -> dict:
    ex = ex or get_exchange()
    if not ex.markets:
        call(ex.load_markets)
    return ex.markets


def ohlcv_limit(ex: ccxt.Exchange) -> int:
    try:
        return int(ex.features["swap"]["linear"]["fetchOHLCV"]["limit"]) or 500
    except Exception:
        return 500


# ----------------------------------------------------------------- symbols --

def to_symbol(coin: str, quote: str = "USDT") -> str:
    """'sol' / 'SOLUSDT' / 'SOL/USDT' / 'SOL/USDT:USDT' -> 'SOL/USDT:USDT'."""
    c = coin.strip().upper()
    if ":" in c:
        return c
    if "/" in c:
        base, q = c.split("/", 1)
        return f"{base}/{q}:{q}"
    if c.endswith(quote) and len(c) > len(quote):
        c = c[: -len(quote)]
    return f"{c}/{quote}:{quote}"


def coin_of(symbol: str) -> str:
    return symbol.split("/")[0]


def pretty(symbol: str) -> str:
    """'SOL/USDT:USDT' -> 'SOL/USDT'"""
    return symbol.split(":")[0]


def is_usdt_perp(m: dict, quote: str = "USDT") -> bool:
    return bool(
        m.get("swap")
        and m.get("linear")
        and m.get("quote") == quote
        and m.get("settle") == quote
        and m.get("active") is not False
    )


# ---------------------------------------------------------------- universe --

def _ticker_row(symbol: str, t: dict) -> dict:
    last = t.get("last") or t.get("close")
    qv = t.get("quoteVolume")
    if qv is None and t.get("baseVolume") and last:
        qv = t["baseVolume"] * last
    bid, ask = t.get("bid"), t.get("ask")
    spread = None
    if bid and ask and bid > 0 and ask > 0:
        spread = (ask - bid) / ((ask + bid) / 2) * 100
    return {
        "symbol": symbol,
        "coin": coin_of(symbol),
        "last": float(last) if last else None,
        "quote_volume": float(qv) if qv else 0.0,
        "spread_pct": round(spread, 4) if spread is not None else None,
        "change_24h_pct": t.get("percentage"),
    }


def get_universe(cfg: dict | None = None, coins: list[str] | None = None) -> list[dict]:
    """Coins to scan.

    - `coins` given (e.g. from --coins SOL,ETH): exactly those, no filters.
    - universe.mode == fixed: the fixed list (minus exclude).
    - universe.mode == top_volume: top N USDT perps by 24h quote volume, with
      min_24h_volume_usd, max_spread_pct and exclude applied.
    """
    cfg = cfg or load_config()
    ex = get_exchange(cfg=cfg)
    quote = cfg_get(cfg, "quote", "USDT")
    mk = markets(ex)
    uni = cfg_get(cfg, "universe", {}) or {}
    exclude = {str(c).upper() for c in (uni.get("exclude") or [])}

    tickers = call(ex.fetch_tickers)
    rows = {s: _ticker_row(s, t) for s, t in tickers.items() if s in mk and is_usdt_perp(mk[s], quote)}

    def known(sym: str) -> bool:
        if sym in mk and is_usdt_perp(mk[sym], quote):
            return True
        log.warning("%s is not a %s perpetual on %s — skipped", sym, quote, ex.id)
        return False

    if coins:
        syms = [to_symbol(c, quote) for c in coins]
        return [rows.get(s, {"symbol": s, "coin": coin_of(s)}) for s in syms if known(s)]

    if uni.get("mode") == "fixed":
        syms = [to_symbol(c, quote) for c in uni.get("fixed") or []]
        return [rows.get(s, {"symbol": s, "coin": coin_of(s)})
                for s in syms if coin_of(s) not in exclude and known(s)]

    min_vol = float(uni.get("min_24h_volume_usd", 0) or 0)
    max_spread = uni.get("max_spread_pct")
    out = []
    for r in rows.values():
        if r["coin"] in exclude or r["coin"] in STABLE_BASES:
            continue
        if r["quote_volume"] < min_vol:
            continue
        if max_spread is not None and r["spread_pct"] is not None and r["spread_pct"] > float(max_spread):
            continue
        out.append(r)
    out.sort(key=lambda r: r["quote_volume"], reverse=True)
    return out[: int(uni.get("top_n", 30))]


def get_ticker(symbol: str, ex: ccxt.Exchange | None = None) -> dict:
    ex = ex or get_exchange()
    return _ticker_row(symbol, call(ex.fetch_ticker, symbol))


# ------------------------------------------------------------------- OHLCV --

def _cache_file(ex_id: str, symbol: str, tf: str) -> Path:
    safe = symbol.replace("/", "-").replace(":", "_")
    return CACHE_DIR / f"{ex_id}_{safe}_{tf}.csv"


def _read_cache(path: Path) -> pd.DataFrame | None:
    try:
        df = pd.read_csv(path)
        return df if len(df) else None
    except Exception:
        return None


def _write_cache(path: Path, df: pd.DataFrame) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        df.tail(CACHE_MAX_ROWS).to_csv(path, index=False)
    except Exception as e:  # read-only disk etc. — cache is optional
        log.debug("cache write failed: %s", e)


def _fetch_from(ex: ccxt.Exchange, symbol: str, tf: str, since: int, now: int) -> list[list]:
    dur = ex.parse_timeframe(tf) * 1000
    per_call = ohlcv_limit(ex)
    rows: list[list] = []
    cursor = since
    for _ in range(50):  # hard stop
        batch = call(ex.fetch_ohlcv, symbol, tf, since=cursor, limit=per_call)
        if not batch:
            break
        rows.extend(batch)
        last = batch[-1][0]
        if last + 2 * dur > now or last < cursor:  # reached the present (or no progress)
            break
        cursor = last + dur
    return rows


def get_ohlcv(symbol: str, tf: str = "1h", limit: int = 300, use_cache: bool = True,
              ex: ccxt.Exchange | None = None) -> pd.DataFrame:
    """Closed candles only, oldest -> newest.

    Columns: open, high, low, close, volume (base units); index: UTC open time.
    """
    ex = ex or get_exchange()
    markets(ex)
    dur = ex.parse_timeframe(tf) * 1000
    now = ex.milliseconds()
    path = _cache_file(ex.id, symbol, tf)
    cols = ["ts", "open", "high", "low", "close", "volume"]

    cached = _read_cache(path) if use_cache else None
    df = None
    if cached is not None:
        last_ts = int(cached["ts"].iloc[-1])
        if len(cached) >= limit and last_ts + 2 * dur > now:
            df = cached  # nothing new has closed since the cache was written
        elif last_ts > now - CACHE_MAX_ROWS * dur:
            new = pd.DataFrame(_fetch_from(ex, symbol, tf, last_ts - dur, now), columns=cols)
            df = pd.concat([cached, new], ignore_index=True)
            if len(df.drop_duplicates("ts")) < limit:
                df = None  # cache too short — do a full fetch

    if df is None:
        since = now - (limit + 3) * dur
        df = pd.DataFrame(_fetch_from(ex, symbol, tf, since, now), columns=cols)
        if cached is not None and len(df):
            df = pd.concat([cached, df], ignore_index=True)

    df = df.drop_duplicates("ts", keep="last").sort_values("ts")
    df = df[df["ts"] + dur <= now]  # drop the still-forming candle
    if use_cache and len(df):
        _write_cache(path, df)

    contract_size = (ex.market(symbol).get("contractSize") or 1) if symbol in ex.markets else 1
    out = df.tail(limit).copy()
    out["volume"] = out["volume"].astype(float) * float(contract_size)
    out.index = pd.to_datetime(out["ts"].astype("int64"), unit="ms", utc=True)
    out.index.name = "time"
    out = out[["open", "high", "low", "close", "volume"]].astype(float)
    out.attrs.update(symbol=symbol, tf=tf, exchange=ex.id)
    return out


DEEP_LIMITS = {"1d": 400, "4h": 300, "1h": 400, "15m": 300}


def get_frames(symbol: str, limits: dict | None = None, ex: ccxt.Exchange | None = None) -> dict:
    """{tf: DataFrame} for the deep-scan timeframes (1D/4H/1H/15m by default)."""
    limits = limits or DEEP_LIMITS
    return {tf: get_ohlcv(symbol, tf, n, ex=ex) for tf, n in limits.items()}


# ------------------------------------------------------------ connectivity --

def check_exchange(ex_id: str, symbol: str = "BTC/USDT:USDT") -> tuple[bool, str, float]:
    """Quick public-API test: load markets + fetch 3 closed 1H candles."""
    t0 = time.time()
    try:
        ex = get_exchange(ex_id)
        call(ex.load_markets, retries=2)
        if symbol not in ex.markets:
            return False, f"{symbol} not listed", time.time() - t0
        rows = call(ex.fetch_ohlcv, symbol, "1h", limit=3, retries=2)
        if not rows:
            return False, "empty OHLCV response", time.time() - t0
        return True, f"ok, {len(ex.markets)} markets, BTC last close {rows[-1][4]}", time.time() - t0
    except Exception as e:
        return False, f"{type(e).__name__}: {str(e)[:200]}", time.time() - t0


# --------------------------------------------------------------------- CLI --

def _main() -> None:
    fix_console()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(description="Fetch MEXC USDT-M futures data")
    ap.add_argument("--symbol", help="e.g. SOL/USDT:USDT or SOL")
    ap.add_argument("--tf", default="1h", help="comma list, e.g. 1d,4h,1h")
    ap.add_argument("--limit", type=int, default=300)
    ap.add_argument("--tail", type=int, default=5, help="rows to print per timeframe")
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--universe", action="store_true", help="print the scan universe")
    ap.add_argument("--check", action="store_true", help="test exchange connectivity")
    args = ap.parse_args()
    cfg = load_config()

    if args.check:
        for ex_id in [exchange_id(cfg)] + list(cfg_get(cfg, "fallback_exchanges", []) or []):
            ok, msg, sec = check_exchange(ex_id)
            print(f"{'OK  ' if ok else 'FAIL'} {ex_id:<14} {sec:5.1f}s  {msg}")
        return

    if args.universe:
        uni = get_universe(cfg)
        print(f"Universe on {exchange_id(cfg)}: {len(uni)} coins")
        for i, r in enumerate(uni, 1):
            sp = f"{r['spread_pct']:.3f}%" if r.get("spread_pct") is not None else "n/a"
            print(f"{i:>2}. {pretty(r['symbol']):<14} vol24h ${r['quote_volume'] / 1e6:>9,.1f}M  "
                  f"spread {sp:>7}  last {r['last']}")
        return

    if not args.symbol:
        ap.error("--symbol is required (or use --universe / --check)")
    symbol = to_symbol(args.symbol, cfg_get(cfg, "quote", "USDT"))
    pd.set_option("display.width", 140)
    for tf in [t.strip() for t in args.tf.split(",") if t.strip()]:
        df = get_ohlcv(symbol, tf, args.limit, use_cache=not args.no_cache)
        print(f"\n{symbol} {tf} — {len(df)} closed candles "
              f"({df.index[0]:%Y-%m-%d %H:%M} → {df.index[-1]:%Y-%m-%d %H:%M} UTC) [{df.attrs['exchange']}]")
        print(df.tail(args.tail).to_string())


if __name__ == "__main__":
    _main()
