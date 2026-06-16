from __future__ import annotations

from astrology_engine import earth, moon, ts
from panchang_engine import get_panchang


_MOON_SIGNS = [
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


def _moon_longitude():
    t = ts.now()
    astrometric = earth.at(t).observe(moon)
    ra, _dec, _distance = astrometric.radec()
    return (ra.hours * 15) % 360


def _moon_sign_from_longitude(lon: float):
    idx = int((lon % 360) // 30)
    return _MOON_SIGNS[max(0, min(11, idx))]


def predict_lunar_volatility():
    moon_long = _moon_longitude()
    moon_sign = _moon_sign_from_longitude(moon_long)

    p = get_panchang()
    moon_phase = float(p["moon_phase"])
    tithi = int(p["tithi"])

    score = 0

    if moon_phase > 0.9 or moon_phase < 0.1:
        score += 2
    elif 0.3 <= moon_phase <= 0.7:
        score += 1
    else:
        score -= 1

    if tithi in [8, 9, 14]:
        score += 1

    if score >= 2:
        lunar_volatility = "HIGH"
        lunar_psychology = "Heightened emotional reactions and sharp swings likely."
    elif score <= 0:
        lunar_volatility = "LOW"
        lunar_psychology = "Calmer sentiment with reduced impulsive participation."
    else:
        lunar_volatility = "NORMAL"
        lunar_psychology = "Balanced sentiment with moderate volatility."

    return {
        "lunar_volatility": lunar_volatility,
        "lunar_psychology": lunar_psychology,
        "moon_phase": moon_phase,
        "tithi": tithi,
        "moon_sign": moon_sign,
    }
