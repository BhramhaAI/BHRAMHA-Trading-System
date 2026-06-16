"""
BHRAMHA - Macro Correlation Engine
====================================
Fetches Gold, Oil (WTI), and S&P500 price changes using yfinance
(completely free, no API key needed) and scores how the global
macro environment affects crypto SHORT/LONG signals.

Install once:  pip install yfinance

Macro logic (research-backed):
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
OIL pumping fast (+2% in 24h)   → inflation fear → crypto dumps → SHORT +8
OIL crashing fast (-2% in 24h)  → depends on why:
    - S&P also falling           → growth fear → crypto dumps → SHORT +4
    - S&P stable/rising          → inflation relief → neutral/slight LONG +3

S&P500 pumping (+1% in 24h)     → risk-on → crypto pumps → LONG +6
S&P500 crashing (-1.5% in 24h)  → risk-off → everything dumps → SHORT +8

GOLD pumping fast (+1.5% in 24h) → fear trade active → risk-off → SHORT +5
GOLD crashing (-1% in 24h)       → calm markets → neutral

Combined signals stack. Max bonus/penalty capped at ±20.
Cache TTL = 15 minutes (markets don't need checking every scan).
"""

from __future__ import annotations

import logging
import time
from typing import Optional

logger = logging.getLogger("bhramha.macro")

# ── Cache ─────────────────────────────────────────────────────────────────────
_cache: dict = {}
CACHE_TTL = 900   # 15 minutes in seconds
MACRO_MAX_BONUS   =  20
MACRO_MAX_PENALTY = -20


def _fetch_change_pct(ticker_symbol: str, period: str = "2d") -> Optional[float]:
    """
    Fetch the 24h price change % for a Yahoo Finance ticker.
    Returns None if fetch fails (graceful degradation).
    """
    try:
        import yfinance as yf
        ticker = yf.Ticker(ticker_symbol)
        hist = ticker.history(period=period, interval="1h")
        if hist.empty or len(hist) < 2:
            return None
        # Compare last close to close 24 candles ago (24h)
        lookback = min(24, len(hist) - 1)
        price_now  = float(hist["Close"].iloc[-1])
        price_then = float(hist["Close"].iloc[-lookback])
        if price_then <= 0:
            return None
        change_pct = ((price_now - price_then) / price_then) * 100.0
        return round(change_pct, 3)
    except Exception as exc:
        logger.warning("yfinance fetch failed for %s: %s", ticker_symbol, exc)
        return None


def get_macro_data(force_refresh: bool = False) -> dict:
    """
    Returns current macro data with caching.
    Tickers:
        CL=F  → WTI Crude Oil futures
        GC=F  → Gold futures
        ^GSPC → S&P 500 index
    """
    global _cache
    now = time.time()

    if not force_refresh and _cache.get("timestamp") and (now - _cache["timestamp"]) < CACHE_TTL:
        return _cache

    oil_chg   = _fetch_change_pct("CL=F")
    gold_chg  = _fetch_change_pct("GC=F")
    sp500_chg = _fetch_change_pct("^GSPC")

    _cache = {
        "timestamp":  now,
        "oil_change_pct":   oil_chg,
        "gold_change_pct":  gold_chg,
        "sp500_change_pct": sp500_chg,
        "oil_state":   _classify_oil(oil_chg),
        "gold_state":  _classify_gold(gold_chg),
        "sp500_state": _classify_sp500(sp500_chg),
    }
    logger.info(
        "Macro data refreshed | Oil: %s%% (%s) | Gold: %s%% (%s) | S&P500: %s%% (%s)",
        oil_chg,   _cache["oil_state"],
        gold_chg,  _cache["gold_state"],
        sp500_chg, _cache["sp500_state"],
    )
    return _cache


def _classify_oil(chg: Optional[float]) -> str:
    if chg is None:         return "UNKNOWN"
    if chg >= 3.0:          return "PUMPING_HARD"   # +3% = aggressive spike
    if chg >= 1.5:          return "PUMPING"         # +1.5%
    if chg <= -3.0:         return "CRASHING_HARD"
    if chg <= -1.5:         return "FALLING"
    return "NEUTRAL"


def _classify_gold(chg: Optional[float]) -> str:
    if chg is None:         return "UNKNOWN"
    if chg >= 2.0:          return "PUMPING_HARD"   # gold +2% = major fear
    if chg >= 1.0:          return "PUMPING"
    if chg <= -1.5:         return "FALLING"
    return "NEUTRAL"


def _classify_sp500(chg: Optional[float]) -> str:
    if chg is None:         return "UNKNOWN"
    if chg >= 1.5:          return "PUMPING_HARD"   # strong risk-on
    if chg >= 0.5:          return "PUMPING"
    if chg <= -2.0:         return "CRASHING_HARD"  # panic sell
    if chg <= -1.0:         return "FALLING"
    return "NEUTRAL"


def get_macro_score_adjustment(direction: str) -> dict:
    """
    Main function called by signal_engine.py.
    Returns score adjustments for LONG and SHORT based on macro conditions.

    Usage in signal_engine.py:
        from macro_engine import get_macro_score_adjustment
        macro = get_macro_score_adjustment(direction)
        astro_bonus_long  += macro["long_bonus"]
        astro_bonus_short += macro["short_bonus"]
        if macro["long_bonus"] > 0:
            astro_bonus_reasons_long.append(macro["reason_long"])
        if macro["short_bonus"] > 0:
            astro_bonus_reasons_short.append(macro["reason_short"])
        if macro["long_penalty"] < 0:
            astro_penalty_long  += macro["long_penalty"]
            astro_penalty_reasons_long.append(macro["reason_long"])
        if macro["short_penalty"] < 0:
            astro_penalty_short += macro["short_penalty"]
            astro_penalty_reasons_short.append(macro["reason_short"])
    """
    try:
        data = get_macro_data()
    except Exception as exc:
        logger.warning("Macro engine failed: %s — skipping macro scoring", exc)
        return _neutral_result()

    oil_state   = data.get("oil_state",   "UNKNOWN")
    gold_state  = data.get("gold_state",  "UNKNOWN")
    sp500_state = data.get("sp500_state", "UNKNOWN")
    oil_chg     = data.get("oil_change_pct")
    gold_chg    = data.get("gold_change_pct")
    sp500_chg   = data.get("sp500_change_pct")

    long_bonus   = 0
    short_bonus  = 0
    long_penalty = 0
    short_penalty = 0
    reasons: list[str] = []

    # ── OIL SCORING ───────────────────────────────────────────────
    if oil_state == "PUMPING_HARD":
        # Oil spike = inflation = Fed hawkish = risk-off = crypto DUMPS
        short_bonus  += 8
        long_penalty -= 8
        reasons.append(f"Oil spiking hard ({oil_chg:+.1f}%) → SHORT edge")

    elif oil_state == "PUMPING":
        short_bonus  += 5
        long_penalty -= 5
        reasons.append(f"Oil rising ({oil_chg:+.1f}%) → SHORT lean")

    elif oil_state == "CRASHING_HARD":
        # Oil crash — the CAUSE determines crypto direction (Grok/Twitter data confirmed)
        # Key signal: check S&P and Gold together to determine WHY oil is crashing
        if sp500_state in {"FALLING", "CRASHING_HARD"}:
            # Oil + Stocks both falling = global recession/demand panic = crypto dumps
            # This is the rare bearish scenario
            short_bonus  += 5
            long_penalty -= 5
            reasons.append(f"Oil crash ({oil_chg:+.1f}%) + S&P falling = recession fear → SHORT")
        elif sp500_state in {"PUMPING", "PUMPING_HARD", "NEUTRAL"} and gold_state in {"FALLING", "NEUTRAL"}:
            # Oil crashing but stocks stable/up + gold calm = geopolitical relief
            # Supply-side relief (trade deals, Iran talks, OPEC+) = inflation fear gone
            # = crypto strongly bullish (BTC +3%+ historically in this scenario)
            long_bonus   += 8
            short_penalty -= 6
            reasons.append(f"Oil supply relief crash ({oil_chg:+.1f}%) + S&P stable = geopolitical de-escalation → strong LONG")
        else:
            # Mixed signals — moderate long lean
            long_bonus   += 4
            short_penalty -= 2
            reasons.append(f"Oil crash ({oil_chg:+.1f}%) + mixed signals → LONG lean")

    elif oil_state == "FALLING":
        if sp500_state in {"FALLING", "CRASHING_HARD"}:
            # Both falling = mild growth concern
            short_bonus  += 3
            reasons.append(f"Oil + S&P both weak → SHORT lean")
        else:
            # Oil easing with stable stocks = inflation relief = mild bullish
            long_bonus   += 4
            short_penalty -= 2
            reasons.append(f"Oil easing ({oil_chg:+.1f}%) + S&P stable → LONG tilt")

    # ── S&P500 SCORING ────────────────────────────────────────────
    if sp500_state == "PUMPING_HARD":
        # Strong risk-on = crypto pumps
        long_bonus   += 6
        short_penalty -= 4
        reasons.append(f"S&P500 strong ({sp500_chg:+.1f}%) → LONG edge")

    elif sp500_state == "PUMPING":
        long_bonus   += 4
        short_penalty -= 2
        reasons.append(f"S&P500 rising ({sp500_chg:+.1f}%) → LONG lean")

    elif sp500_state == "CRASHING_HARD":
        # Panic in stocks = everything dumps
        short_bonus  += 8
        long_penalty -= 8
        reasons.append(f"S&P500 crashing ({sp500_chg:+.1f}%) → strong SHORT edge")

    elif sp500_state == "FALLING":
        short_bonus  += 5
        long_penalty -= 5
        reasons.append(f"S&P500 falling ({sp500_chg:+.1f}%) → SHORT lean")

    # ── GOLD SCORING ──────────────────────────────────────────────
    if gold_state == "PUMPING_HARD":
        # Gold +2% = major fear event (war, crisis) = risk-off = crypto shorts
        short_bonus  += 5
        long_penalty -= 5
        reasons.append(f"Gold surging ({gold_chg:+.1f}%) = fear trade active → SHORT")

    elif gold_state == "PUMPING":
        short_bonus  += 3
        long_penalty -= 3
        reasons.append(f"Gold rising ({gold_chg:+.1f}%) → slight SHORT lean")

    elif gold_state == "FALLING":
        # Gold falling = calm markets = risk-on conditions possible
        long_bonus   += 2
        reasons.append(f"Gold easing ({gold_chg:+.1f}%) → calm markets, LONG tilt")

    # ── CONFLICTING SIGNALS HANDLING ──────────────────────────────
    # If oil pumping BUT S&P also pumping = inflation but risk-on (rare, cancel each other)
    if oil_state in {"PUMPING", "PUMPING_HARD"} and sp500_state in {"PUMPING", "PUMPING_HARD"}:
        # Reduce both, let technicals decide
        short_bonus  = max(0, short_bonus  - 3)
        long_penalty = min(0, long_penalty + 3)
        reasons.append("Conflicting macro (oil up + stocks up) → reduced weighting")

    # ── APPLY CAPS ────────────────────────────────────────────────
    long_bonus    = min(long_bonus,   MACRO_MAX_BONUS)
    short_bonus   = min(short_bonus,  MACRO_MAX_BONUS)
    long_penalty  = max(long_penalty, MACRO_MAX_PENALTY)
    short_penalty = max(short_penalty, MACRO_MAX_PENALTY)

    reason_str = " | ".join(reasons) if reasons else "Macro neutral"

    return {
        "long_bonus":    long_bonus,
        "short_bonus":   short_bonus,
        "long_penalty":  long_penalty,
        "short_penalty": short_penalty,
        "reason_long":   f"Macro: {reason_str}",
        "reason_short":  f"Macro: {reason_str}",
        "oil_state":     oil_state,
        "gold_state":    gold_state,
        "sp500_state":   sp500_state,
        "oil_chg":       oil_chg,
        "gold_chg":      gold_chg,
        "sp500_chg":     sp500_chg,
        "data_fresh":    True,
    }


def _neutral_result() -> dict:
    return {
        "long_bonus": 0, "short_bonus": 0,
        "long_penalty": 0, "short_penalty": 0,
        "reason_long": "Macro: unavailable",
        "reason_short": "Macro: unavailable",
        "oil_state": "UNKNOWN", "gold_state": "UNKNOWN", "sp500_state": "UNKNOWN",
        "oil_chg": None, "gold_chg": None, "sp500_chg": None,
        "data_fresh": False,
    }


def get_macro_summary() -> str:
    """Human-readable one-liner for Telegram messages."""
    try:
        data = get_macro_data()
        oil  = data.get("oil_change_pct")
        gold = data.get("gold_change_pct")
        sp   = data.get("sp500_change_pct")
        parts = []
        if oil  is not None: parts.append(f"Oil {oil:+.1f}%")
        if gold is not None: parts.append(f"Gold {gold:+.1f}%")
        if sp   is not None: parts.append(f"S&P {sp:+.1f}%")
        return " | ".join(parts) if parts else "Macro: N/A"
    except Exception:
        return "Macro: N/A"


if __name__ == "__main__":
    # Quick test
    logging.basicConfig(level=logging.INFO)
    print("Testing macro engine...")
    result = get_macro_score_adjustment("SHORT")
    print(f"Oil:   {result['oil_chg']}% ({result['oil_state']})")
    print(f"Gold:  {result['gold_chg']}% ({result['gold_state']})")
    print(f"S&P:   {result['sp500_chg']}% ({result['sp500_state']})")
    print(f"LONG  bonus/penalty: +{result['long_bonus']} / {result['long_penalty']}")
    print(f"SHORT bonus/penalty: +{result['short_bonus']} / {result['short_penalty']}")
    print(f"Reason: {result['reason_short']}")
    print()
    print(f"Summary: {get_macro_summary()}")