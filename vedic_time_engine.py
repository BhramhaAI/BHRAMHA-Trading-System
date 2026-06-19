from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from panchang_engine import get_panchang, explain_tithi
from nakshatra_engine import get_current_nakshatra


INDIA_TZ = ZoneInfo("Asia/Kolkata")
DEFAULT_LAT = 28.6139
DEFAULT_LON = 77.2090
BLOCKED_PERIODS = {"RAHU KALAM", "YAMAGANDA"}
SIDEREAL_SUN_SIGN_RANGES = (
    ((1, 14), (2, 12), "CAPRICORN"),
    ((2, 13), (3, 14), "AQUARIUS"),
    ((3, 15), (4, 13), "PISCES"),
    ((4, 14), (5, 14), "ARIES"),
    ((5, 15), (6, 14), "TAURUS"),
    ((6, 15), (7, 16), "GEMINI"),
    ((7, 17), (8, 16), "CANCER"),
    ((8, 17), (9, 16), "LEO"),
    ((9, 17), (10, 17), "VIRGO"),
    ((10, 18), (11, 16), "LIBRA"),
    ((11, 17), (12, 15), "SCORPIO"),
    ((12, 16), (1, 13), "SAGITTARIUS"),
)
SUN_ELEMENT_GROUPS = {
    "FIRE": {"ARIES", "LEO", "SAGITTARIUS"},
    "EARTH": {"CAPRICORN", "TAURUS", "VIRGO"},
    "AIR": {"GEMINI", "LIBRA", "AQUARIUS"},
    "WATER": {"CANCER", "SCORPIO", "PISCES"},
}


def _date_in_range(day: date, start: tuple[int, int], end: tuple[int, int]) -> bool:
    current_md = (day.month, day.day)
    if start <= end:
        return start <= current_md <= end
    return current_md >= start or current_md <= end


def _get_sidereal_sun_sign(day: date) -> str:
    for start, end, sign in SIDEREAL_SUN_SIGN_RANGES:
        if _date_in_range(day, start, end):
            return sign
    return "PISCES"


def _get_sunrise_sunset(day: date, latitude: float, longitude: float, tz: ZoneInfo):
    try:
        from astral import LocationInfo
        from astral.sun import sun

        loc = LocationInfo(name="BHRAMHA", region="IN", timezone=str(tz), latitude=latitude, longitude=longitude)
        s = sun(loc.observer, date=day, tzinfo=tz)
        return s["sunrise"], s["sunset"]
    except Exception:
        # Deterministic fallback if astral is unavailable.
        sunrise = datetime.combine(day, time(6, 0), tz)
        sunset = datetime.combine(day, time(18, 0), tz)
        return sunrise, sunset


def _in_window(now_dt: datetime, start: datetime, end: datetime) -> bool:
    return start <= now_dt <= end


def get_vedic_time_quality(latitude: float = DEFAULT_LAT, longitude: float = DEFAULT_LON):
    """
    Return current Vedic time classification.

    {
      "current_period": "RAHU KALAM|YAMAGANDA|GULIKA|ABHIJIT|NORMAL",
      "timing_quality": "GOOD|NEUTRAL|AVOID",
      "sunrise": "...",
      "sunset": "..."
    }
    """
    now_dt = datetime.now(INDIA_TZ)
    today = now_dt.date()

    sunrise, sunset = _get_sunrise_sunset(today, latitude, longitude, INDIA_TZ)
    daylight = sunset - sunrise
    segment = daylight / 8

    # Python weekday: Monday=0 ... Sunday=6
    weekday = now_dt.weekday()

    rahu_seg = {0: 2, 1: 7, 2: 5, 3: 6, 4: 4, 5: 3, 6: 8}[weekday]
    yama_seg = {0: 4, 1: 3, 2: 2, 3: 1, 4: 7, 5: 6, 6: 5}[weekday]
    gulika_seg = {0: 6, 1: 5, 2: 4, 3: 3, 4: 2, 5: 1, 6: 7}[weekday]

    rahu_start = sunrise + segment * (rahu_seg - 1)
    rahu_end = sunrise + segment * rahu_seg

    yama_start = sunrise + segment * (yama_seg - 1)
    yama_end = sunrise + segment * yama_seg

    gulika_start = sunrise + segment * (gulika_seg - 1)
    gulika_end = sunrise + segment * gulika_seg

    midday = sunrise + (daylight / 2)
    abhijit_start = midday - timedelta(minutes=24)
    abhijit_end = midday + timedelta(minutes=24)

    if _in_window(now_dt, rahu_start, rahu_end):
        current_period, timing_quality = "RAHU KALAM", "AVOID"
    elif _in_window(now_dt, yama_start, yama_end):
        current_period, timing_quality = "YAMAGANDA", "AVOID"
    elif _in_window(now_dt, gulika_start, gulika_end):
        current_period, timing_quality = "GULIKA", "AVOID"
    elif _in_window(now_dt, abhijit_start, abhijit_end):
        current_period, timing_quality = "ABHIJIT", "GOOD"
    else:
        current_period, timing_quality = "NORMAL", "NEUTRAL"

    return {
        "current_period": current_period,
        "timing_quality": timing_quality,
        "sunrise": sunrise.isoformat(),
        "sunset": sunset.isoformat(),
    }


def get_vedic_trade_block_status(latitude: float = DEFAULT_LAT, longitude: float = DEFAULT_LON):
    """Return whether trading should be hard-blocked by the current Vedic window."""
    now_dt = datetime.now(INDIA_TZ)
    today = now_dt.date()

    sunrise, sunset = _get_sunrise_sunset(today, latitude, longitude, INDIA_TZ)
    daylight = sunset - sunrise
    segment = daylight / 8
    weekday = now_dt.weekday()

    window_map = {
        "RAHU KALAM": (
            sunrise + segment * ({0: 2, 1: 7, 2: 5, 3: 6, 4: 4, 5: 3, 6: 8}[weekday] - 1),
            sunrise + segment * {0: 2, 1: 7, 2: 5, 3: 6, 4: 4, 5: 3, 6: 8}[weekday],
        ),
        "YAMAGANDA": (
            sunrise + segment * ({0: 4, 1: 3, 2: 2, 3: 1, 4: 7, 5: 6, 6: 5}[weekday] - 1),
            sunrise + segment * {0: 4, 1: 3, 2: 2, 3: 1, 4: 7, 5: 6, 6: 5}[weekday],
        ),
        "GULIKA": (
            sunrise + segment * ({0: 6, 1: 5, 2: 4, 3: 3, 4: 2, 5: 1, 6: 7}[weekday] - 1),
            sunrise + segment * {0: 6, 1: 5, 2: 4, 3: 3, 4: 2, 5: 1, 6: 7}[weekday],
        ),
    }

    timing = get_vedic_time_quality(latitude=latitude, longitude=longitude)
    current_period = str(timing.get("current_period", "NORMAL")).upper()
    blocked = current_period in BLOCKED_PERIODS
    active_until = ""
    if blocked:
        active_until = window_map[current_period][1].strftime("%H:%M IST")

    return {
        "blocked": blocked,
        "current_period": current_period,
        "active_until": active_until,
        "message": (
            f"[VEDIC BLOCK] Trade blocked — {current_period} active until {active_until}"
            if blocked
            else ""
        ),
    }


def get_current_tithi_context(dt=None):
    """Return the Tithi number and sizing group at `dt` (defaults to now).

    Backed by vedic_core (true Sun-Moon elongation). Accepts a datetime so the
    backtester can evaluate the historical tithi for each candle.
    """
    from vedic_core import get_tithi

    t = get_tithi(dt)
    return {
        "tithi": t["tithi"],
        "tithi_group": t["tithi_group"],
        "paksha": t["paksha"],
        "moon_phase": t["moon_phase"],
        "high_lunar_volatility": t["high_lunar_volatility"],
        "tithi_explanation": explain_tithi(t["tithi"]),
    }


def get_current_nakshatra_context(dt=None):
    """Return the Nakshatra plus market-type grouping at `dt` (defaults to now).

    Backed by vedic_core's correct sidereal computation. Accepts a datetime so
    the backtester can evaluate the historical nakshatra for each candle.
    """
    from vedic_core import get_nakshatra

    nak = get_nakshatra(dt)
    return {
        "nakshatra_name": nak["nakshatra_name"],
        "nakshatra_type": nak["nakshatra_type"],
        "stop_hunt_risk": nak["stop_hunt_risk"],
        "pada": nak["pada"],
        "action": nak["action"],
        "win_rate": nak.get("prior_wr", 0),
    }


def get_muhurta_context():
    """Return fixed India-window overlays for Abhijit Muhurta and Sandhya Kaal."""
    now_dt = datetime.now(INDIA_TZ)
    today = now_dt.date()

    def _dt(hour: int, minute: int):
        return datetime.combine(today, time(hour, minute), INDIA_TZ)

    abhijit_active = _in_window(now_dt, _dt(11, 36), _dt(12, 24))
    sandhya_windows = {
        "PRATAH_SANDHYA": (_dt(5, 45), _dt(6, 15)),
        "MADHYANHA_SANDHYA": (_dt(11, 45), _dt(12, 15)),
        "SAYAM_SANDHYA": (_dt(17, 45), _dt(18, 15)),
    }

    active_sandhya = ""
    for name, (start, end) in sandhya_windows.items():
        if _in_window(now_dt, start, end):
            active_sandhya = name
            break

    return {
        "abhijit_active": abhijit_active,
        "reversal_warning": bool(active_sandhya),
        "sandhya_window": active_sandhya,
    }


def get_sun_transit_context(day: date | None = None):
    """Return an approximate sidereal Sun transit context for scoring overlays."""
    current_day = day or datetime.now(INDIA_TZ).date()
    sun_sign = _get_sidereal_sun_sign(current_day)

    if sun_sign in SUN_ELEMENT_GROUPS["FIRE"]:
        return {
            "sun_sign": sun_sign,
            "sun_element": "FIRE",
            "sun_transit_effect": "bullish fire energy",
            "sun_sign_bias": "LONG",
            "score_adjustment": 3,
            "volatility_warning": False,
            "reversal_warning": False,
        }
    if sun_sign in SUN_ELEMENT_GROUPS["EARTH"]:
        return {
            "sun_sign": sun_sign,
            "sun_element": "EARTH",
            "sun_transit_effect": "bearish earth energy",
            "sun_sign_bias": "SHORT",
            "score_adjustment": 3,
            "volatility_warning": False,
            "reversal_warning": False,
        }
    if sun_sign in SUN_ELEMENT_GROUPS["AIR"]:
        return {
            "sun_sign": sun_sign,
            "sun_element": "AIR",
            "sun_transit_effect": "choppy air energy",
            "sun_sign_bias": "VOLATILITY_WARNING",
            "score_adjustment": -2,
            "volatility_warning": True,
            "reversal_warning": False,
        }
    return {
        "sun_sign": sun_sign,
        "sun_element": "WATER",
        "sun_transit_effect": "emotional water energy",
        "sun_sign_bias": "REVERSAL_WARNING",
        "score_adjustment": -2,
        "volatility_warning": False,
        "reversal_warning": True,
    }
