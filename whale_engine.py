"""
BHRAMHA - Whale Engine
========================
Tracks large aggressive trades (whale executions) on Binance Futures.
Completely FREE — uses public /fapi/v1/aggTrades endpoint, no API key needed.

DIFFERENCE FROM orderflow_engine.py:
  orderflow_engine  → reads passive order BOOK (bids/asks sitting waiting)
                      Can be spoofed — whales place fake walls then remove them.
  whale_engine      → reads EXECUTED trades — actual money that moved RIGHT NOW
                      Cannot be faked — these are real fills.

HOW IT WORKS:
  Binance /fapi/v1/aggTrades returns the last N aggregated trades.
  Each trade shows: price, quantity, buyer_is_maker (direction), timestamp.

  buyer_is_maker = True  → SELL order hit the bid → aggressive SELLER (bearish)
  buyer_is_maker = False → BUY order hit the ask  → aggressive BUYER  (bullish)

  We filter only trades above WHALE_THRESHOLD_USDT (default $50,000).
  Then sum whale buy volume vs whale sell volume in the last 5 minutes.

SCORING LOGIC (confirmed against BHRAMHA data):
  Strong whale buying  → market has upward pressure → SHORT risky → LONG boost
  Strong whale selling → market has downward pressure → SHORT boost
  Mixed / neutral      → no adjustment
  
CACHE: 60 seconds per symbol (whale trades change fast, unlike F&G which is daily)
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from binance_http import BINANCE_HTTP_TIMEOUT, session

logger = logging.getLogger("bhramha.whale")

# ── Config ─────────────────────────────────────────────────────────────────────
WHALE_THRESHOLD_USDT = 50_000   # Minimum trade size to count as "whale" in USDT
WHALE_LOOKBACK_MS    = 5 * 60 * 1000   # Look back 5 minutes of trades
WHALE_TRADE_LIMIT    = 500      # Max trades to fetch per call
CACHE_TTL            = 60       # Cache per symbol for 60 seconds

WHALE_MAX_BONUS   =  10
WHALE_MAX_PENALTY = -10

# ── Cache ──────────────────────────────────────────────────────────────────────
_cache: dict[str, dict] = {}


def _fetch_agg_trades(symbol: str) -> list:
    """
    Fetch recent aggregated trades from Binance Futures.
    Returns list of dicts with: price, qty, buyer_is_maker, timestamp
    """
    try:
        now_ms = int(time.time() * 1000)
        start_ms = now_ms - WHALE_LOOKBACK_MS

        url = "https://fapi.binance.com/fapi/v1/aggTrades"
        response = session.get(
            url,
            params={
                "symbol":    symbol,
                "startTime": start_ms,
                "endTime":   now_ms,
                "limit":     WHALE_TRADE_LIMIT,
            },
            timeout=BINANCE_HTTP_TIMEOUT,
        )
        response.raise_for_status()
        trades = response.json()

        if not isinstance(trades, list):
            return []

        result = []
        for t in trades:
            try:
                price         = float(t["p"])
                qty           = float(t["q"])
                buyer_is_maker = bool(t["m"])   # True = seller aggressive, False = buyer aggressive
                ts            = int(t["T"])
                notional      = price * qty
                result.append({
                    "price":          price,
                    "qty":            qty,
                    "notional":       notional,
                    "buyer_is_maker": buyer_is_maker,
                    "timestamp":      ts,
                })
            except Exception:
                continue

        return result

    except Exception as exc:
        logger.warning("aggTrades fetch failed for %s: %s", symbol, exc)
        return []


def analyze_whale_flow(symbol: str) -> dict:
    """
    Analyse whale trade flow for a symbol over the last 5 minutes.

    Returns:
        whale_buy_usdt    : Total USDT bought by whales
        whale_sell_usdt   : Total USDT sold by whales
        whale_bias        : "BIG_BUYERS" | "BIG_SELLERS" | "NEUTRAL"
        whale_ratio       : buy/sell ratio
        whale_count       : number of whale trades found
        whale_buy_count   : number of whale buy trades
        whale_sell_count  : number of whale sell trades
        long_bonus        : score bonus for LONG
        short_bonus       : score bonus for SHORT
        long_penalty      : score penalty for LONG
        short_penalty     : score penalty for SHORT
        reason            : human readable explanation
        data_fresh        : whether data was fetched successfully
    """
    symbol = str(symbol).upper().strip()
    now = time.time()

    # Check cache
    cached = _cache.get(symbol)
    if cached and (now - cached.get("fetched_at", 0)) < CACHE_TTL:
        return cached

    trades = _fetch_agg_trades(symbol)

    # Filter whale trades only
    whale_buys  = [t for t in trades if not t["buyer_is_maker"] and t["notional"] >= WHALE_THRESHOLD_USDT]
    whale_sells = [t for t in trades if t["buyer_is_maker"]     and t["notional"] >= WHALE_THRESHOLD_USDT]

    whale_buy_usdt  = sum(t["notional"] for t in whale_buys)
    whale_sell_usdt = sum(t["notional"] for t in whale_sells)
    whale_count     = len(whale_buys) + len(whale_sells)
    whale_ratio     = whale_buy_usdt / max(whale_sell_usdt, 1.0)

    # ── Determine bias ────────────────────────────────────────────────────────
    if whale_count == 0:
        whale_bias = "NEUTRAL"
        reason = f"No whale trades (>${WHALE_THRESHOLD_USDT/1000:.0f}k) in last 5 min"
    elif whale_ratio >= 2.0:
        whale_bias = "BIG_BUYERS"
        reason = f"Whales buying hard (${whale_buy_usdt/1e6:.2f}M vs ${whale_sell_usdt/1e6:.2f}M sold)"
    elif whale_ratio >= 1.4:
        whale_bias = "BIG_BUYERS"
        reason = f"Whale buy pressure (ratio {whale_ratio:.1f}x, ${whale_buy_usdt/1000:.0f}k bought)"
    elif whale_ratio <= 0.5:
        whale_bias = "BIG_SELLERS"
        reason = f"Whales dumping (${whale_sell_usdt/1e6:.2f}M vs ${whale_buy_usdt/1e6:.2f}M bought)"
    elif whale_ratio <= 0.72:
        whale_bias = "BIG_SELLERS"
        reason = f"Whale sell pressure (ratio {whale_ratio:.1f}x, ${whale_sell_usdt/1000:.0f}k sold)"
    else:
        whale_bias = "NEUTRAL"
        reason = f"Whale flow mixed ({whale_count} trades, ratio {whale_ratio:.1f}x)"

    # ── Score adjustments ─────────────────────────────────────────────────────
    long_bonus   = 0
    short_bonus  = 0
    long_penalty = 0
    short_penalty = 0

    if whale_bias == "BIG_BUYERS":
        # Whales aggressively buying = bullish pressure
        # For LONG: confirms direction → bonus
        # For SHORT: whales buying against us → penalty
        if whale_ratio >= 2.0:
            long_bonus   = 10
            short_penalty = -8
        else:
            long_bonus   = 6
            short_penalty = -5

    elif whale_bias == "BIG_SELLERS":
        # Whales aggressively selling = bearish pressure
        # For SHORT: confirms direction → bonus
        # For LONG: whales selling against us → penalty
        if whale_ratio <= 0.5:
            short_bonus  = 10
            long_penalty = -8
        else:
            short_bonus  = 6
            long_penalty = -5

    # Apply caps
    long_bonus    = min(long_bonus,    WHALE_MAX_BONUS)
    short_bonus   = min(short_bonus,   WHALE_MAX_BONUS)
    long_penalty  = max(long_penalty,  WHALE_MAX_PENALTY)
    short_penalty = max(short_penalty, WHALE_MAX_PENALTY)

    result = {
        "fetched_at":      now,
        "whale_buy_usdt":  round(whale_buy_usdt,  2),
        "whale_sell_usdt": round(whale_sell_usdt, 2),
        "whale_bias":      whale_bias,
        "whale_ratio":     round(whale_ratio, 2),
        "whale_count":     whale_count,
        "whale_buy_count": len(whale_buys),
        "whale_sell_count":len(whale_sells),
        "long_bonus":      long_bonus,
        "short_bonus":     short_bonus,
        "long_penalty":    long_penalty,
        "short_penalty":   short_penalty,
        "reason":          f"Whale: {reason}",
        "data_fresh":      len(trades) > 0,
    }

    _cache[symbol] = result
    return result


def get_whale_summary(symbol: str) -> str:
    """One-liner for Telegram messages."""
    try:
        data = analyze_whale_flow(symbol)
        bias  = data["whale_bias"]
        count = data["whale_count"]
        buys  = data["whale_buy_usdt"]
        sells = data["whale_sell_usdt"]
        if count == 0:
            return "🐋 No whale activity"
        return (f"🐋 {bias} | "
                f"Buy ${buys/1000:.0f}k Sell ${sells/1000:.0f}k "
                f"({count} trades)")
    except Exception:
        return "🐋 Whale: N/A"


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    import sys
    sym = sys.argv[1] if len(sys.argv) > 1 else "BTCUSDT"
    print(f"Testing whale engine for {sym}...")
    result = analyze_whale_flow(sym)
    print(f"Whale bias:   {result['whale_bias']}")
    print(f"Whale ratio:  {result['whale_ratio']}")
    print(f"Buy volume:   ${result['whale_buy_usdt']:,.0f}")
    print(f"Sell volume:  ${result['whale_sell_usdt']:,.0f}")
    print(f"Trade count:  {result['whale_count']} whale trades")
    print(f"LONG  bonus/penalty: +{result['long_bonus']} / {result['long_penalty']}")
    print(f"SHORT bonus/penalty: +{result['short_bonus']} / {result['short_penalty']}")
    print(f"Reason: {result['reason']}")
    print(f"Summary: {get_whale_summary(sym)}")