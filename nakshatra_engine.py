from __future__ import annotations

from astrology_engine import earth, moon, ts


NAKSHATRA_NAMES = [
    "Ashwini",
    "Bharani",
    "Krittika",
    "Rohini",
    "Mrigashira",
    "Ardra",
    "Punarvasu",
    "Pushya",
    "Ashlesha",
    "Magha",
    "Purva Phalguni",
    "Uttara Phalguni",
    "Hasta",
    "Chitra",
    "Swati",
    "Vishakha",
    "Anuradha",
    "Jyeshtha",
    "Mula",
    "Purva Ashadha",
    "Uttara Ashadha",
    "Shravana",
    "Dhanishta",
    "Shatabhisha",
    "Purva Bhadrapada",
    "Uttara Bhadrapada",
    "Revati",
]


NAKSHATRA_MARKET_MAP = {
    "Ashwini": {"bias": "VOLATILE", "psychology": "impulsive", "volatility": "HIGH"},
    "Bharani": {"bias": "REVERSAL", "psychology": "pressure", "volatility": "MEDIUM"},
    "Krittika": {"bias": "BREAKOUT", "psychology": "aggressive", "volatility": "HIGH"},
    "Rohini": {"bias": "TREND", "psychology": "growth", "volatility": "MEDIUM"},
    "Mrigashira": {"bias": "SEARCHING", "psychology": "uncertain", "volatility": "MEDIUM"},
    "Ardra": {"bias": "CHAOTIC", "psychology": "panic", "volatility": "HIGH"},
    "Punarvasu": {"bias": "RECOVERY", "psychology": "hope", "volatility": "LOW"},
    "Pushya": {"bias": "STABLE", "psychology": "discipline", "volatility": "LOW"},
    "Ashlesha": {"bias": "TRAP", "psychology": "manipulation", "volatility": "HIGH"},
    "Magha": {"bias": "POWER", "psychology": "dominance", "volatility": "MEDIUM"},
    "Purva Phalguni": {"bias": "RELAXED", "psychology": "profit taking", "volatility": "LOW"},
    "Uttara Phalguni": {"bias": "STRUCTURE", "psychology": "stability", "volatility": "LOW"},
    "Hasta": {"bias": "CONTROL", "psychology": "precision", "volatility": "LOW"},
    "Chitra": {"bias": "PATTERN", "psychology": "structured movement", "volatility": "MEDIUM"},
    "Swati": {"bias": "DRIFT", "psychology": "independent", "volatility": "LOW"},
    "Vishakha": {"bias": "TARGET", "psychology": "goal oriented", "volatility": "MEDIUM"},
    "Anuradha": {"bias": "COOPERATION", "psychology": "steady", "volatility": "LOW"},
    "Jyeshtha": {"bias": "DOMINANCE", "psychology": "power struggle", "volatility": "HIGH"},
    "Mula": {"bias": "DESTRUCTION", "psychology": "deep reversal", "volatility": "HIGH"},
    "Purva Ashadha": {"bias": "EXPANSION", "psychology": "confidence", "volatility": "MEDIUM"},
    "Uttara Ashadha": {"bias": "VICTORY", "psychology": "determination", "volatility": "LOW"},
    "Shravana": {"bias": "OBSERVATION", "psychology": "information", "volatility": "LOW"},
    "Dhanishta": {"bias": "MOMENTUM", "psychology": "rhythm", "volatility": "MEDIUM"},
    "Shatabhisha": {"bias": "EXTREME", "psychology": "detachment", "volatility": "HIGH"},
    "Purva Bhadrapada": {"bias": "TRANSFORMATION", "psychology": "intense", "volatility": "HIGH"},
    "Uttara Bhadrapada": {"bias": "STABILITY", "psychology": "deep calm", "volatility": "LOW"},
    "Revati": {"bias": "COMPASSION", "psychology": "gentle market", "volatility": "LOW"},
}


def get_current_nakshatra(dt=None):
    """Nakshatra name at `dt` (defaults to now).

    Delegates to vedic_core, which uses the Moon's correct *sidereal ecliptic
    longitude* (Lahiri ayanamsa) instead of the old equatorial-RA approximation.
    Accepts a datetime so the backtester can ask for any historical moment.
    """
    from vedic_core import get_nakshatra
    return get_nakshatra(dt)["nakshatra_name"]


def get_nakshatra_market_bias(dt=None):
    name = get_current_nakshatra(dt)
    mapped = NAKSHATRA_MARKET_MAP.get(name, {"bias": "NEUTRAL", "psychology": "balanced", "volatility": "MEDIUM"})
    return {
        "nakshatra": name,
        "bias": mapped["bias"],
        "psychology": mapped["psychology"],
        "volatility": mapped["volatility"],
    }
