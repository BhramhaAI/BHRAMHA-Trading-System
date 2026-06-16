import datetime
import math


def explain_tithi(tithi):

    if tithi in (15, 30):
        return "New/Full Moon energy. Emotional extremes and volatility possible."

    if tithi < 7:
        return "Early lunar cycle. Market energy slowly building."

    if tithi < 15:
        return "Momentum phase. Trends may strengthen."

    if tithi < 22:
        return "Distribution phase. Possible profit-taking."

    return "Late lunar cycle. Markets often slow or reverse."


def get_panchang(dt=None):
    """Panchang snapshot at `dt` (defaults to now).

    Tithi and moon phase now come from the true Sun-Moon elongation (via
    vedic_core) instead of the old `day_of_year % 29.53` calendar approximation.
    The returned `tithi` stays 1..30 and `moon_phase` stays a 0..1 fraction so
    existing callers keep working.
    """
    from vedic_core import get_tithi
    t = get_tithi(dt)
    tithi = t["tithi"]

    # Market psychology modifier — heightened near the new/full moon and the
    # quarter turning points.
    sentiment = 1.0
    if tithi in (15, 30):
        sentiment = 1.3   # full moon / new moon volatility
    elif tithi in (8, 23):
        sentiment = 1.2   # emotional turning points (quarters)

    return {
        "tithi": tithi,
        "tithi_name": t["tithi_name"],
        "paksha": t["paksha"],
        "moon_phase": t["moon_phase"],
        "illumination": t["illumination"],
        "sentiment": sentiment,
        "high_lunar_volatility": t["high_lunar_volatility"],
        "tithi_explanation": explain_tithi(tithi),
    }
