"""
BHRAMHA - Fear & Greed Engine
==============================
Uses the Crypto Fear & Greed Index from alternative.me
Completely FREE, no API key needed, no registration.

API: https://api.alternative.me/fng/?limit=1

Score 0-100:
  0-24   = Extreme Fear   → best SHORT entries (market oversold/panic)
  25-44  = Fear           → SHORT lean
  45-55  = Neutral        → no adjustment
  56-74  = Greed          → SHORT caution (market overextended)
  75-100 = Extreme Greed  → strong SHORT signal (market euphoric = top)

Why this works:
  - Extreme Fear = everyone is selling = SHORT positions already crowded
    → price often bounces. Actually use for LONG entries.
  - Extreme Greed = everyone is buying = LONG positions crowded
    → price often tops. Best time to SHORT.
  - This is contrarian logic — same logic used by professional traders.

Real data validation from BHRAMHA signals_log:
  - London session wins mostly happened when fear index was 20-45
  - Most losses happened when fear index was 50-70 (neutral/greedy)

Cache TTL: 1 hour (index only updates once per day)
"""

from __future__ import annotations

import logging
import time
from typing import Optional

logger = logging.getLogger("bhramha.fear_greed")

# ── Cache ──────────────────────────────────────────────────────────────────────
_cache: dict = {}
CACHE_TTL = 3600   # 1 hour — index updates once daily, no need to hammer API

# ── Score adjustments ──────────────────────────────────────────────────────────
FG_MAX_BONUS   =  12
FG_MAX_PENALTY = -12


def get_fear_greed_index(force_refresh: bool = False) -> dict:
    """
    Fetch the current Crypto Fear & Greed index.
    Returns dict with: value (0-100), classification, timestamp, data_fresh
    """
    global _cache
    now = time.time()

    if not force_refresh and _cache.get("timestamp") and (now - _cache["timestamp"]) < CACHE_TTL:
        return _cache

    try:
        import requests
        response = requests.get(
            "https://api.alternative.me/fng/?limit=1",
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()
        entry = data["data"][0]
        value = int(entry["value"])
        classification = str(entry["value_classification"])

        _cache = {
            "timestamp":      now,
            "value":          value,
            "classification": classification,
            "data_fresh":     True,
        }
        logger.info("Fear & Greed: %d (%s)", value, classification)
        return _cache

    except Exception as exc:
        logger.warning("Fear & Greed fetch failed: %s — using neutral", exc)
        return {
            "timestamp":      now,
            "value":          50,
            "classification": "Neutral",
            "data_fresh":     False,
        }


def get_fg_score_adjustment() -> dict:
    """
    Returns score adjustments for LONG and SHORT based on Fear & Greed index.

    Contrarian logic:
    ──────────────────────────────────────────────────────────────
    Extreme Greed (75-100) → market is euphoric, top is near
        → SHORT gets +12 bonus, LONG gets -8 penalty

    Greed (56-74) → market leaning bullish, shorts risky
        → SHORT gets +5 bonus, LONG gets -4 penalty

    Neutral (45-55) → no edge from sentiment
        → no adjustment

    Fear (25-44) → market nervous, bounces likely
        → LONG gets +5 bonus, SHORT gets -3 penalty

    Extreme Fear (0-24) → panic selling, strong bounce likely
        → LONG gets +10 bonus, SHORT gets -6 penalty
        (best time to enter LONGs if technicals agree)
    ──────────────────────────────────────────────────────────────
    """
    data = get_fear_greed_index()
    value = int(data.get("value", 50))
    classification = str(data.get("classification", "Neutral"))
    data_fresh = bool(data.get("data_fresh", False))

    long_bonus   = 0
    short_bonus  = 0
    long_penalty = 0
    short_penalty = 0
    reason = ""

    if value >= 75:
        # Extreme Greed — euphoria = top signal = best SHORT opportunity
        short_bonus  = 12
        long_penalty = -8
        reason = f"Extreme Greed ({value}) — euphoria, top likely → SHORT+12"

    elif value >= 56:
        # Greed — market overextended upward
        short_bonus  = 5
        long_penalty = -4
        reason = f"Greed ({value}) — market leaning up, SHORT lean +5"

    elif value >= 45:
        # Neutral — no sentiment edge
        reason = f"Neutral ({value}) — no sentiment adjustment"

    elif value >= 25:
        # Fear — market nervous, bounces common
        long_bonus   = 5
        short_penalty = -3
        reason = f"Fear ({value}) — market nervous, LONG bounce edge +5"

    else:
        # Extreme Fear — panic selling = strong bounce
        long_bonus   = 10
        short_penalty = -6
        reason = f"Extreme Fear ({value}) — panic bottom, LONG+10"

    # Apply caps
    long_bonus    = min(long_bonus,   FG_MAX_BONUS)
    short_bonus   = min(short_bonus,  FG_MAX_BONUS)
    long_penalty  = max(long_penalty, FG_MAX_PENALTY)
    short_penalty = max(short_penalty, FG_MAX_PENALTY)

    return {
        "fg_value":       value,
        "fg_class":       classification,
        "long_bonus":     long_bonus,
        "short_bonus":    short_bonus,
        "long_penalty":   long_penalty,
        "short_penalty":  short_penalty,
        "reason":         f"F&G: {reason}",
        "data_fresh":     data_fresh,
    }


def get_fg_summary() -> str:
    """One-liner for Telegram messages."""
    try:
        data = get_fear_greed_index()
        return f"F&G: {data['value']} ({data['classification']})"
    except Exception:
        return "F&G: N/A"


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("Testing Fear & Greed engine...")
    result = get_fg_score_adjustment()
    print(f"Value: {result['fg_value']} ({result['fg_class']})")
    print(f"LONG  bonus/penalty: +{result['long_bonus']} / {result['long_penalty']}")
    print(f"SHORT bonus/penalty: +{result['short_bonus']} / {result['short_penalty']}")
    print(f"Reason: {result['reason']}")
    print(f"Summary: {get_fg_summary()}")