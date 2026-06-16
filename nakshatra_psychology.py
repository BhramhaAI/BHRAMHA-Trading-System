"""
BHRAMHA - Nakshatra Psychology Engine (FIXED v2)
================================================
KEY FIX: Each nakshatra now carries an explicit `direction_bias`:
  "LONG"    = favours long entries
  "SHORT"   = favours short entries
  "NEUTRAL" = no directional edge

Dhruva nakshatras (fixed/stable energy) were previously given a flat +6
bonus to BOTH directions, which caused the bot to spam LONGs in ranging/
falling markets.  The correct Vedic reading is:
  - Dhruva in uptrend  → continuation LONG
  - Dhruva in downtrend → continuation SHORT  (fixed = trend continues)
  - Dhruva in range    → NEUTRAL (fixed = stuck)

This file now returns the directional bias so signal_engine.py can apply
the bonus/penalty correctly per direction.
"""

# direction_bias values: "LONG" | "SHORT" | "NEUTRAL" | "REGIME_DEPENDENT"
# REGIME_DEPENDENT = check regime before giving credit (see signal_engine helper)

NAKSHATRA_PSYCHOLOGY = {
    0: {
        "name": "Ashwini",
        "type": "breakout",
        "score": 1.2,
        "direction_bias": "REGIME_DEPENDENT",   # fast move — follow regime
        "meaning": "Fast and impulsive energy. Markets may move quickly in the regime direction.",
    },
    1: {
        "name": "Bharani",
        "type": "pressure",
        "score": 1.1,
        "direction_bias": "SHORT",   # constrained/heavy energy → bearish pressure
        "meaning": "Heavy, pressurized energy. Price can feel constrained before a down-move.",
    },
    2: {
        "name": "Krittika",
        "type": "trend",
        "score": 1.15,
        "direction_bias": "REGIME_DEPENDENT",   # cutting → follow existing trend
        "meaning": "Cutting and decisive energy. Directional continuation is common.",
    },
    3: {
        "name": "Rohini",
        "type": "trend",
        "score": 1.3,
        "direction_bias": "LONG",   # growth / nurturing → upward
        "meaning": "Growth oriented energy. Trends often continue upward.",
    },
    4: {
        "name": "Mrigashira",
        "type": "search",
        "score": 1.1,
        "direction_bias": "NEUTRAL",   # probing / searching — no clear bias
        "meaning": "Restless exploration energy. Markets may probe multiple levels.",
    },
    5: {
        "name": "Ardra",
        "type": "chaos",
        "score": 1.4,
        "direction_bias": "SHORT",   # storm / destruction → bearish impulse
        "meaning": "Storm-like energy. High volatility and sharp DOWN moves possible.",
    },
    6: {
        "name": "Punarvasu",
        "type": "recovery",
        "score": 1.15,
        "direction_bias": "LONG",   # renewal / bounce
        "meaning": "Renewal energy. Pullbacks can recover and stabilize upward.",
    },
    7: {
        "name": "Pushya",
        "type": "stable",
        "score": 1.05,
        "direction_bias": "NEUTRAL",   # nurturing + range-bound
        "meaning": "Nurturing and steady energy. Range-bound behavior is more likely.",
    },
    8: {
        "name": "Ashlesha",
        "type": "manipulation",
        "score": 1.2,
        "direction_bias": "SHORT",   # coiling / deceptive → fakeout then down
        "meaning": "Coiling energy. Fakeouts and trap moves can appear — favour SHORT.",
    },
    9: {
        "name": "Magha",
        "type": "reversal",
        "score": 1.25,
        "direction_bias": "NEUTRAL",   # authority shift — wait for confirmed direction
        "meaning": "Authority shift energy. Markets sometimes reverse direction.",
    },
    10: {
        "name": "Purva Phalguni",
        "type": "momentum",
        "score": 1.2,
        "direction_bias": "LONG",   # expressive / celebratory → bullish momentum
        "meaning": "Expressive energy. Momentum bursts can emerge quickly upward.",
    },
    11: {
        "name": "Uttara Phalguni",
        "type": "trend",
        "score": 1.15,
        "direction_bias": "REGIME_DEPENDENT",   # commitment → existing trend holds
        "meaning": "Commitment energy. Existing trends may hold their structure.",
    },
    12: {
        "name": "Hasta",
        "type": "control",
        "score": 1.1,
        "direction_bias": "NEUTRAL",   # precision / control — clean technicals
        "meaning": "Precision and control energy. Cleaner technical reactions are common.",
    },
    13: {
        "name": "Chitra",
        "type": "volatility",
        "score": 1.25,
        "direction_bias": "NEUTRAL",   # strong swings both ways
        "meaning": "Constructive but intense energy. Strong swings can form clear patterns.",
    },
    14: {
        "name": "Swati",
        "type": "expansion",
        "score": 1.3,
        "direction_bias": "REGIME_DEPENDENT",   # wind: amplifies whatever is moving
        "meaning": "Wind-like expansion energy. Breakouts extend farther in regime direction.",
    },
    15: {
        "name": "Vishakha",
        "type": "breakout",
        "score": 1.25,
        "direction_bias": "REGIME_DEPENDENT",   # goal-driven: follow regime breakout
        "meaning": "Goal-driven energy. Price often pushes through key levels.",
    },
    16: {
        "name": "Anuradha",
        "type": "trend",
        "score": 1.15,
        "direction_bias": "REGIME_DEPENDENT",   # disciplined continuation
        "meaning": "Disciplined energy. Trend continuation with orderly pullbacks is likely.",
    },
    17: {
        "name": "Jyeshtha",
        "type": "manipulation",
        "score": 1.25,
        "direction_bias": "SHORT",   # dominance / stop-hunt then fall
        "meaning": "Dominance energy. Stop-hunts and sudden spikes lead to SHORT opportunities.",
    },
    18: {
        "name": "Mula",
        "type": "crash",
        "score": 1.35,
        "direction_bias": "SHORT",   # root-destroying → liquidation / panic sell
        "meaning": "Root-destroying energy. Panic or liquidation DOWN moves possible.",
    },
    19: {
        "name": "Purva Ashadha",
        "type": "momentum",
        "score": 1.2,
        "direction_bias": "LONG",   # invigorating → bullish momentum
        "meaning": "Invigorating energy. Fast one-sided UP moves can gain traction.",
    },
    20: {
        "name": "Uttara Ashadha",
        "type": "trend",
        "score": 1.15,
        "direction_bias": "LONG",   # endurance → persistent uptrend
        "meaning": "Endurance energy. Persistent trends continue with upward strength.",
    },
    21: {
        "name": "Shravana",
        "type": "stable",
        "score": 1.05,
        "direction_bias": "NEUTRAL",   # listening / observation — consolidate
        "meaning": "Listening and observation energy. Markets may consolidate and absorb.",
    },
    22: {
        "name": "Dhanishta",
        "type": "volatility",
        "score": 1.25,
        "direction_bias": "NEUTRAL",   # rhythmic bursts both directions
        "meaning": "Rhythmic but forceful energy. Alternating bursts of volatility are common.",
    },
    23: {
        "name": "Shatabhisha",
        "type": "correction",
        "score": 1.2,
        "direction_bias": "SHORT",   # cleansing / mean-revert → short overextended moves
        "meaning": "Cleansing energy. Overextended UP moves often correct downward.",
    },
    24: {
        "name": "Purva Bhadrapada",
        "type": "panic",
        "score": 1.3,
        "direction_bias": "SHORT",   # fear-driven candles → bearish
        "meaning": "Intense transformational energy. Fear-driven DOWN candles appear suddenly.",
    },
    25: {
        "name": "Uttara Bhadrapada",
        "type": "reversal",
        "score": 1.2,
        "direction_bias": "LONG",   # deep stabilizing → late-stage reversal upward
        "meaning": "Deep stabilizing energy. Late-stage reversals develop upward.",
    },
    26: {
        "name": "Revati",
        "type": "calm",
        "score": 0.95,
        "direction_bias": "NEUTRAL",   # peaceful / consolidation
        "meaning": "Peaceful energy. Markets often consolidate.",
    },
}

# ── Nakshatra type → BHRAMHA category ─────────────────────────────────────────
# Used by nakshatra_engine.py to map nakshatra_type classifications.
# DHRUVA nakshatras: Rohini (3), Uttara Phalguni (11), Uttara Ashadha (20), Uttara Bhadrapada (25)
# These are "fixed" energy — they confirm CONTINUATION of the existing trend, NOT reversals.
DHRUVA_NAKSHATRAS = {3, 11, 20, 25}   # regime-dependent: bonus only aligns with regime direction

# CHARA nakshatras (moveable): Punarvasu (6), Pushya (7), Shravana (21), Dhanishta (22), Shatabhisha (23)
CHARA_NAKSHATRAS = {6, 7, 21, 22, 23}

# TIKSHNA nakshatras (sharp/cutting): Ardra (5), Ashlesha (8), Mula (18), Jyeshtha (17)
TIKSHNA_NAKSHATRAS = {5, 8, 17, 18}

# MRDU nakshatras (soft): Mrigashira (4), Chitra (13), Anuradha (16), Revati (26)
MRDU_NAKSHATRAS = {4, 13, 16, 26}

# UGRA nakshatras (fierce): Bharani (1), Magha (9), Purva Phalguni (10), Purva Ashadha (19), Purva Bhadrapada (24)
UGRA_NAKSHATRAS = {1, 9, 10, 19, 24}


def get_nakshatra_psychology(nakshatra: int) -> dict | None:
    """Return psychology dict for a nakshatra index (0-26)."""
    return NAKSHATRA_PSYCHOLOGY.get(nakshatra)


def get_nakshatra_direction_bias(nakshatra: int, regime: str = "NORMAL") -> str:
    """
    Return the directional bias for a nakshatra, resolving REGIME_DEPENDENT
    entries using the current market regime.

    Returns: "LONG" | "SHORT" | "NEUTRAL"
    """
    data = NAKSHATRA_PSYCHOLOGY.get(nakshatra)
    if not data:
        return "NEUTRAL"

    bias = str(data.get("direction_bias", "NEUTRAL")).upper()
    if bias != "REGIME_DEPENDENT":
        return bias

    # Resolve regime-dependent nakshatras
    regime = str(regime).upper()
    if regime in {"TRENDING_BULL", "BREAKOUT"}:
        return "LONG"
    elif regime in {"TRENDING_BEAR"}:
        return "SHORT"
    else:
        # RANGING / VOLATILE / NORMAL → no directional edge
        return "NEUTRAL"


def get_nakshatra_score_modifier(
    nakshatra: int,
    direction: str,
    regime: str = "NORMAL",
) -> tuple[int, str]:
    """
    Return (score_adjustment, reason_string) for the given nakshatra,
    direction, and regime.

    Rules:
    - If nakshatra bias matches direction → +6 bonus
    - If nakshatra bias opposes direction → -8 penalty
    - If nakshatra is NEUTRAL            →  0
    - DHRUVA in ranging/volatile market  → -4 penalty for ANY direction
      (fixed energy in a ranging market means trapped / no trend)
    """
    direction = str(direction).upper()
    resolved_bias = get_nakshatra_direction_bias(nakshatra, regime)
    nak_data = NAKSHATRA_PSYCHOLOGY.get(nakshatra, {})
    nak_name = nak_data.get("name", f"Nak#{nakshatra}")

    # Special penalty for Dhruva in non-trending markets
    regime_upper = str(regime).upper()
    if nakshatra in DHRUVA_NAKSHATRAS and regime_upper in {"RANGING", "VOLATILE", "NORMAL"}:
        return -4, f"Dhruva ({nak_name}) in {regime_upper} market — fixed energy trapped"

    if resolved_bias == "NEUTRAL":
        return 0, ""

    if resolved_bias == direction:
        return 6, f"Nakshatra {nak_name} aligns with {direction}"
    else:
        return -8, f"Nakshatra {nak_name} opposes {direction} (favours {resolved_bias})"
