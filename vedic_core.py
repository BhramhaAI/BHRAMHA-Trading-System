# -*- coding: utf-8 -*-
"""
vedic_core.py — BHRAMHA's astronomically-correct, time-parameterized Vedic engine.
================================================================================

This is the heart of BHRAMHA's edge: the Vedic (Panchang) timing layer.

Why this module exists
----------------------
The original engines computed Vedic factors with three mistakes that quietly
corrupted the edge:

  1. Nakshatra was derived from the Moon's *equatorial right ascension* with no
     ayanamsa correction. Nakshatra is defined by the Moon's **sidereal ecliptic
     longitude** — a different reference frame. This systematically mislabeled
     the lunar mansion.
  2. Tithi / moon-phase were derived from `day_of_year % 29.53`, a calendar
     approximation with no link to the real Moon.
  3. Every function read the wall clock (`now()`), so the same logic could not be
     evaluated at a historical candle's timestamp — making honest backtesting
     impossible.

`vedic_core` fixes all three:

  * The Moon's apparent **ecliptic longitude** is taken from JPL ephemeris DE421
    (via skyfield), then converted to sidereal longitude using the time-varying
    **Lahiri (Chitrapaksha) ayanamsa**. That gives the correct nakshatra + pada.
  * **Tithi** and **moon phase** come from the true Sun–Moon elongation.
  * Every public function accepts a `dt` (UTC-aware `datetime`, defaults to now),
    so the *exact same* timing logic powers both the live bot and the backtester.

All of BHRAMHA's classifications (nakshatra→market map, tikshna/chara groups,
tithi groups, hora sequence, win-rate buckets) are preserved — only the
underlying astronomy is corrected.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from functools import lru_cache

from skyfield.api import load
from skyfield.framelib import ecliptic_frame

# ──────────────────────────────────────────────────────────────────────────────
# Ephemeris (shared singletons — loaded once)
# ──────────────────────────────────────────────────────────────────────────────
_TS = load.timescale()
_EPH = load("de421.bsp")
_EARTH = _EPH["earth"]
_MOON = _EPH["moon"]
_SUN = _EPH["sun"]

NAK_ARC = 360.0 / 27.0          # 13.3333° per nakshatra
PADA_ARC = NAK_ARC / 4.0        # 3.3333° per pada
TITHI_ARC = 12.0               # 12° of elongation per tithi


# ──────────────────────────────────────────────────────────────────────────────
# Canonical Vedic reference tables (preserved from BHRAMHA's research)
# ──────────────────────────────────────────────────────────────────────────────
NAKSHATRA_NAMES = [
    "Ashwini", "Bharani", "Krittika", "Rohini", "Mrigashira", "Ardra",
    "Punarvasu", "Pushya", "Ashlesha", "Magha", "Purva Phalguni",
    "Uttara Phalguni", "Hasta", "Chitra", "Swati", "Vishakha", "Anuradha",
    "Jyeshtha", "Mula", "Purva Ashadha", "Uttara Ashadha", "Shravana",
    "Dhanishta", "Shatabhisha", "Purva Bhadrapada", "Uttara Bhadrapada",
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

# Nakshatra "guna"/temperament groups → BHRAMHA stop-hunt risk model
_TIKSHNA = {"Ardra", "Ashlesha", "Jyeshtha", "Mula"}
_CHARA = {"Punarvasu", "Swati", "Shravana", "Dhanishta", "Shatabhisha"}
_DHRUVA = {"Rohini", "Uttara Phalguni", "Uttara Ashadha", "Uttara Bhadrapada"}
_UGRA = {"Bharani", "Magha", "Purva Phalguni", "Purva Ashadha", "Purva Bhadrapada"}
_MRDU = {"Mrigashira", "Chitra", "Anuradha", "Revati"}

# Nakshatra action buckets used by the strategy as a *selectivity filter*.
# action ∈ {GOLDEN, TRADE, CAUTION, SHORT_ONLY, BLOCK}
#
# HONESTY NOTE (read before citing any number here):
#   `prior_wr` are the ORIGINAL small-sample live-log observations that first
#   motivated this layer. They DO NOT reproduce out-of-sample — a 548-trade
#   walk-forward backtest (see calibrate.py / validate_nakshatra.py) shows the
#   per-nakshatra win rates are statistical noise. Do NOT present `prior_wr` as
#   a proven win rate anywhere user-facing.
#
#   What IS data-supported (see ablation_vedic.py): enabling this block/overlay
#   set as a whole acts as a confluence filter that roughly doubled pooled
#   per-trade expectancy (+0.015R -> +0.034R over 365d, 6 symbols) by trimming
#   ~30% of lower-quality setups. The VALUE is the aggregate filter, not the
#   individual buckets. The buckets are kept as the (pre-specified, not
#   in-sample-fitted) mechanism that produces that filtering.
NAKSHATRA_EDGE = {
    "Revati":     {"prior_wr": 92.6, "action": "GOLDEN",     "note": "prior: favourable window"},
    "Krittika":   {"prior_wr": 63.6, "action": "TRADE",      "note": "prior: tradeable"},
    "Rohini":     {"prior_wr": 61.5, "action": "TRADE",      "note": "prior: tradeable"},
    "Shravana":   {"prior_wr": 38.9, "action": "CAUTION",    "note": "prior: reduce size"},
    "Ardra":      {"prior_wr": 29.2, "action": "SHORT_ONLY", "note": "prior: SHORT-leaning"},
    "Mrigashira": {"prior_wr": 15.0, "action": "BLOCK",      "note": "prior: avoid"},
    "Punarvasu":  {"prior_wr": 12.5, "action": "BLOCK",      "note": "prior: avoid"},
    "Bharani":    {"prior_wr": 10.0, "action": "BLOCK",      "note": "prior: avoid"},
    "Dhanishta":  {"prior_wr": 5.3,  "action": "BLOCK",      "note": "prior: avoid"},
    "Ashwini":    {"prior_wr": 0.0,  "action": "BLOCK",      "note": "prior: avoid"},
}

# Tithi (1..30) groups used for position sizing.
_RIKTA = {4, 9, 14, 19, 24, 29}
_NANDA = {1, 6, 11, 16, 21, 26}
_BHADRA = {2, 7, 12, 17, 22, 27}
_JAYA = {3, 8, 13, 18, 23, 28}
# remaining {5,10,15,20,25,30} → PURNA

TITHI_NAMES = [
    "Pratipada", "Dwitiya", "Tritiya", "Chaturthi", "Panchami", "Shashthi",
    "Saptami", "Ashtami", "Navami", "Dashami", "Ekadashi", "Dwadashi",
    "Trayodashi", "Chaturdashi", "Purnima",
    "Pratipada", "Dwitiya", "Tritiya", "Chaturthi", "Panchami", "Shashthi",
    "Saptami", "Ashtami", "Navami", "Dashami", "Ekadashi", "Dwadashi",
    "Trayodashi", "Chaturdashi", "Amavasya",
]

# Hora (planetary hour) — Chaldean order, classical day rulers (Mon=0..Sun=6)
HORA_SEQUENCE = ["SUN", "VENUS", "MERCURY", "MOON", "SATURN", "JUPITER", "MARS"]
DAY_RULERS = {0: "MOON", 1: "MARS", 2: "MERCURY", 3: "JUPITER",
              4: "VENUS", 5: "SATURN", 6: "SUN"}


# ──────────────────────────────────────────────────────────────────────────────
# Time helpers
# ──────────────────────────────────────────────────────────────────────────────
def _as_utc(dt: datetime | None) -> datetime:
    """Normalize any datetime (naive treated as UTC) to a UTC-aware datetime."""
    if dt is None:
        return datetime.now(timezone.utc)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def lahiri_ayanamsa(dt: datetime | None = None) -> float:
    """
    Lahiri (Chitrapaksha) ayanamsa in degrees, valid for the modern era.

    Linear model anchored at J2000.0 (≈23.853°) with the IAU precession rate of
    50.290966 arcsec/yr. Accurate to a few arcminutes across 1950–2050 — far
    better than the *zero* ayanamsa the original code used.
    """
    dt = _as_utc(dt)
    # Fractional Julian years since J2000.0 (2000-01-01 12:00 TT ≈ UTC here).
    j2000 = datetime(2000, 1, 1, 12, 0, tzinfo=timezone.utc)
    years = (dt - j2000).total_seconds() / (365.25 * 86400.0)
    return 23.853 + 50.290966 / 3600.0 * years


@lru_cache(maxsize=4096)
def _ecliptic_longitudes(epoch_minute: int) -> tuple[float, float]:
    """
    Apparent geocentric ecliptic longitudes (tropical) of Moon and Sun, in
    degrees, cached per UTC-minute to keep backtests fast.
    """
    dt = datetime.fromtimestamp(epoch_minute * 60, tz=timezone.utc)
    t = _TS.from_datetime(dt)
    _, moon_lon, _ = _EARTH.at(t).observe(_MOON).apparent().frame_latlon(ecliptic_frame)
    _, sun_lon, _ = _EARTH.at(t).observe(_SUN).apparent().frame_latlon(ecliptic_frame)
    return float(moon_lon.degrees) % 360.0, float(sun_lon.degrees) % 360.0


def _lonpair(dt: datetime | None) -> tuple[float, float]:
    dt = _as_utc(dt)
    return _ecliptic_longitudes(int(dt.timestamp() // 60))


# ──────────────────────────────────────────────────────────────────────────────
# Core astronomical quantities
# ──────────────────────────────────────────────────────────────────────────────
def sidereal_moon_longitude(dt: datetime | None = None) -> float:
    """Moon's sidereal ecliptic longitude (Lahiri), degrees in [0, 360)."""
    moon_lon, _ = _lonpair(dt)
    return (moon_lon - lahiri_ayanamsa(dt)) % 360.0


def sun_moon_elongation(dt: datetime | None = None) -> float:
    """Angular elongation Moon − Sun, degrees in [0, 360). Drives tithi/phase."""
    moon_lon, sun_lon = _lonpair(dt)
    return (moon_lon - sun_lon) % 360.0


# ──────────────────────────────────────────────────────────────────────────────
# Nakshatra
# ──────────────────────────────────────────────────────────────────────────────
def _nakshatra_type(name: str) -> tuple[str, str]:
    if name in _TIKSHNA:
        return "TIKSHNA", "HIGH"
    if name in _CHARA:
        return "CHARA", "NORMAL"
    if name in _DHRUVA:
        return "DHRUVA", "NORMAL"
    if name in _UGRA:
        return "UGRA", "NORMAL"
    if name in _MRDU:
        return "MRDU", "LOW"
    return "OTHER", "NORMAL"


def get_nakshatra(dt: datetime | None = None) -> dict:
    """
    Full nakshatra context at `dt`, computed from the correct sidereal longitude.

    Returns name, 0-based index, pada (1–4), degrees traversed within the
    nakshatra, the market-behaviour mapping, the temperament/stop-hunt model,
    and BHRAMHA's empirical edge bucket.
    """
    sid = sidereal_moon_longitude(dt)
    index = int(sid // NAK_ARC) % 27
    name = NAKSHATRA_NAMES[index]
    deg_in_nak = sid - index * NAK_ARC
    pada = int(deg_in_nak // PADA_ARC) + 1

    market = NAKSHATRA_MARKET_MAP.get(
        name, {"bias": "NEUTRAL", "psychology": "balanced", "volatility": "MEDIUM"})
    nak_type, stop_hunt_risk = _nakshatra_type(name)
    edge = NAKSHATRA_EDGE.get(name, {"prior_wr": None, "action": "TRADE", "note": "neutral"})

    return {
        "nakshatra_name": name,
        "nakshatra_index": index,
        "pada": pada,
        "degrees_in_nakshatra": round(deg_in_nak, 3),
        "sidereal_longitude": round(sid, 4),
        "bias": market["bias"],
        "psychology": market["psychology"],
        "volatility": market["volatility"],
        "nakshatra_type": nak_type,
        "stop_hunt_risk": stop_hunt_risk,
        "prior_wr": edge["prior_wr"],
        "action": edge["action"],
        "edge_note": edge["note"],
    }


# ──────────────────────────────────────────────────────────────────────────────
# Tithi & moon phase
# ──────────────────────────────────────────────────────────────────────────────
def _tithi_group(tithi: int) -> str:
    if tithi in _RIKTA:
        return "RIKTA"
    if tithi in _NANDA:
        return "NANDA"
    if tithi in _BHADRA:
        return "BHADRA"
    if tithi in _JAYA:
        return "JAYA"
    return "PURNA"


def get_tithi(dt: datetime | None = None) -> dict:
    """
    Tithi (1–30) and true moon phase from the Sun–Moon elongation at `dt`.

    Tithi 1–15 = Shukla (waxing) paksha, 16–30 = Krishna (waning) paksha.
    Tithi 15 = Purnima (full), Tithi 30 = Amavasya (new).
    """
    elong = sun_moon_elongation(dt)
    tithi = int(elong // TITHI_ARC) + 1          # 1..30
    tithi = max(1, min(30, tithi))
    paksha = "SHUKLA" if tithi <= 15 else "KRISHNA"

    # Illuminated fraction of the Moon's disk (0=new, 1=full).
    import math
    illumination = (1 - math.cos(math.radians(elong))) / 2.0

    # Distance (in days, ~) to nearest new/full moon — drives the lunar-vol gate.
    days_to_full = abs(((180 - elong + 180) % 360 - 180)) / 360.0 * 29.53059
    days_to_new = min(elong, 360 - elong) / 360.0 * 29.53059

    return {
        "tithi": tithi,
        "tithi_name": TITHI_NAMES[tithi - 1],
        "paksha": paksha,
        "tithi_group": _tithi_group(tithi),
        "elongation": round(elong, 3),
        "illumination": round(illumination, 4),
        "moon_phase": round(elong / 360.0, 4),
        "days_to_full": round(days_to_full, 2),
        "days_to_new": round(days_to_new, 2),
        "high_lunar_volatility": bool(days_to_full <= 2.0 or days_to_new <= 2.0),
    }


# ──────────────────────────────────────────────────────────────────────────────
# Hora (planetary hour)
# ──────────────────────────────────────────────────────────────────────────────
_INDIA_OFFSET = timedelta(hours=5, minutes=30)


def get_hora(dt: datetime | None = None) -> dict:
    """
    Current Hora (planetary hour) ruler, parameterized by time.

    Uses a fixed 06:00 IST sunrise approximation (matching BHRAMHA's model) so
    results are deterministic and reproducible in backtests.
    """
    dt = _as_utc(dt)
    ist = dt + _INDIA_OFFSET  # naive IST wall-clock
    sunrise = ist.replace(hour=6, minute=0, second=0, microsecond=0)
    if ist < sunrise:
        sunrise -= timedelta(days=1)

    day_ruler = DAY_RULERS[sunrise.weekday()]
    start_index = HORA_SEQUENCE.index(day_ruler)
    elapsed_hours = int((ist - sunrise).total_seconds() // 3600) % 24
    planet = HORA_SEQUENCE[(start_index + elapsed_hours) % len(HORA_SEQUENCE)]
    return {
        "hora_planet": planet,
        "hora_number": elapsed_hours + 1,
        "day_ruler": day_ruler,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Combined context — one call returns the full Panchang snapshot for any time
# ──────────────────────────────────────────────────────────────────────────────
def get_vedic_context(dt: datetime | None = None) -> dict:
    """
    Full, reproducible Vedic timing snapshot at `dt` (defaults to now).

    This is the single entry point the strategy core and backtester call. Every
    value is a pure function of `dt`, so the live bot and a historical replay see
    identical logic.
    """
    dt = _as_utc(dt)
    nak = get_nakshatra(dt)
    tithi = get_tithi(dt)
    hora = get_hora(dt)
    return {
        "timestamp": dt.isoformat(),
        "nakshatra": nak,
        "tithi": tithi,
        "hora": hora,
        "ayanamsa": round(lahiri_ayanamsa(dt), 4),
    }


if __name__ == "__main__":  # quick self-check
    import json
    print(json.dumps(get_vedic_context(), indent=2))
