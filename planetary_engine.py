from __future__ import annotations

import datetime as _dt
import math
from itertools import combinations


_SIGNS = [
    "Aries",
    "Taurus",
    "Gemini",
    "Cancer",
    "Leo",
    "Virgo",
    "Libra",
    "Scorpio",
    "Sagittarius",
    "Capricorn",
    "Aquarius",
    "Pisces",
]


def _norm_deg(x: float) -> float:
    return x % 360.0


def _angle_diff(a: float, b: float) -> float:
    d = abs(_norm_deg(a) - _norm_deg(b))
    return min(d, 360.0 - d)


def _zodiac(longitude: float) -> str:
    return _SIGNS[int(_norm_deg(longitude) // 30)]


def _retrograde(current_lon: float, prev_lon: float) -> bool:
    # Negative wrapped delta indicates apparent backward motion.
    delta = (_norm_deg(current_lon - prev_lon) + 540.0) % 360.0 - 180.0
    return delta < 0


def _get_planetary_longitudes():
    from skyfield.api import load

    ts = load.timescale()
    eph = load("de421.bsp")
    earth = eph["earth"]

    names = {
        "Sun": "sun",
        "Moon": "moon",
        "Mercury": "mercury",
        "Venus": "venus",
        "Mars": "mars",
        "Jupiter": "jupiter barycenter",
        "Saturn": "saturn barycenter",
    }

    t_now = ts.now()
    t_prev = ts.from_datetime(_dt.datetime.utcnow().replace(tzinfo=_dt.timezone.utc) - _dt.timedelta(days=1))

    lon_now = {}
    lon_prev = {}
    for k, eph_name in names.items():
        body = eph[eph_name]
        _, lon, _ = earth.at(t_now).observe(body).apparent().ecliptic_latlon()
        _, lon_p, _ = earth.at(t_prev).observe(body).apparent().ecliptic_latlon()
        lon_now[k] = _norm_deg(lon.degrees)
        lon_prev[k] = _norm_deg(lon_p.degrees)

    # Mean lunar node approximation from Moon-Sun elongation derivative proxy:
    # Use opposite nodes for Rahu/Ketu on ecliptic longitude ring.
    rahu = _norm_deg(lon_now["Moon"] - 180.0)
    ketu = _norm_deg(rahu + 180.0)
    rahu_prev = _norm_deg(lon_prev["Moon"] - 180.0)
    ketu_prev = _norm_deg(rahu_prev + 180.0)

    lon_now["Rahu"] = rahu
    lon_now["Ketu"] = ketu
    lon_prev["Rahu"] = rahu_prev
    lon_prev["Ketu"] = ketu_prev

    return lon_now, lon_prev


def _fallback_planetary_longitudes():
    # Deterministic fallback if skyfield is unavailable.
    base = _dt.datetime.utcnow().timetuple().tm_yday * 0.9856
    lon_now = {
        "Sun": _norm_deg(base),
        "Moon": _norm_deg(base * 13.2),
        "Mercury": _norm_deg(base * 4.1),
        "Venus": _norm_deg(base * 1.6),
        "Mars": _norm_deg(base * 0.53),
        "Jupiter": _norm_deg(base * 0.08),
        "Saturn": _norm_deg(base * 0.03),
    }
    lon_now["Rahu"] = _norm_deg(lon_now["Moon"] - 180.0)
    lon_now["Ketu"] = _norm_deg(lon_now["Rahu"] + 180.0)
    lon_prev = {k: _norm_deg(v - 1.0) for k, v in lon_now.items()}
    return lon_now, lon_prev


def calculate_planetary_bias():
    """
    Return:
    {
      "cosmic_bias": "BULLISH|BEARISH|NEUTRAL",
      "volatility_bias": "HIGH|NORMAL|LOW",
      "planetary_summary": "..."
    }
    """
    try:
        lon_now, lon_prev = _get_planetary_longitudes()
    except Exception:
        lon_now, lon_prev = _fallback_planetary_longitudes()

    planets = ["Sun", "Moon", "Mercury", "Venus", "Mars", "Jupiter", "Saturn", "Rahu", "Ketu"]
    states = {
        p: {
            "longitude": lon_now[p],
            "sign": _zodiac(lon_now[p]),
            "retrograde": _retrograde(lon_now[p], lon_prev[p]),
        }
        for p in planets
    }

    conjunctions = []
    for a, b in combinations(planets, 2):
        if _angle_diff(lon_now[a], lon_now[b]) <= 5.0:
            conjunctions.append((a, b))

    aspects = []
    major_aspects = [60, 90, 120, 180]
    for a, b in combinations(planets, 2):
        d = _angle_diff(lon_now[a], lon_now[b])
        for asp in major_aspects:
            if abs(d - asp) <= 4.0:
                aspects.append((a, b, asp))
                break

    score = 0.0
    vol = 0.0
    notes = []

    mars_sign = states["Mars"]["sign"]
    if mars_sign in {"Aries", "Scorpio", "Capricorn"}:
        score += 0.5
        vol += 0.8
        notes.append("Mars strong: aggressive moves")

    if states["Mercury"]["retrograde"]:
        score -= 0.4
        vol += 0.6
        notes.append("Mercury retrograde: fakeouts/confusion")

    if states["Jupiter"]["sign"] in {"Sagittarius", "Pisces", "Cancer"}:
        score += 0.9
        notes.append("Jupiter strong: bullish expansion")

    if states["Saturn"]["sign"] in {"Capricorn", "Aquarius", "Libra"}:
        score -= 0.5
        vol -= 0.2
        notes.append("Saturn strong: slow grind/fear")

    if states["Venus"]["sign"] in {"Taurus", "Libra", "Pisces"}:
        score += 0.4
        notes.append("Venus strong: liquidity inflow")

    sun_moon_sep = _angle_diff(lon_now["Sun"], lon_now["Moon"])
    if sun_moon_sep >= 150:
        vol += 0.8
        notes.append("Moon near full: emotional volatility")

    if _angle_diff(lon_now["Rahu"], lon_now["Moon"]) <= 18 or _angle_diff(lon_now["Rahu"], lon_now["Sun"]) <= 18:
        vol += 1.0
        notes.append("Rahu influence: spikes/traps")

    if _angle_diff(lon_now["Ketu"], lon_now["Moon"]) <= 18 or _angle_diff(lon_now["Ketu"], lon_now["Sun"]) <= 18:
        vol += 0.9
        score -= 0.3
        notes.append("Ketu influence: sharp reversals")

    for a, b in conjunctions:
        if {"Mars", "Rahu"} == {a, b}:
            vol += 1.0
            notes.append("Mars-Rahu conjunction: explosive")
        if {"Venus", "Jupiter"} == {a, b}:
            score += 0.5
            notes.append("Venus-Jupiter conjunction: risk-on")

    for a, b, asp in aspects:
        if {"Saturn", "Moon"} == {a, b} and asp in {90, 180}:
            score -= 0.4
            notes.append("Saturn-Moon hard aspect: risk-off")

    if score > 0.6:
        cosmic_bias = "BULLISH"
    elif score < -0.6:
        cosmic_bias = "BEARISH"
    else:
        cosmic_bias = "NEUTRAL"

    if vol > 1.8:
        volatility_bias = "HIGH"
    elif vol < 0.2:
        volatility_bias = "LOW"
    else:
        volatility_bias = "NORMAL"

    summary = "; ".join(notes[:4]) if notes else "Balanced planetary backdrop"

    return {
        "cosmic_bias": cosmic_bias,
        "volatility_bias": volatility_bias,
        "planetary_summary": summary,
    }
