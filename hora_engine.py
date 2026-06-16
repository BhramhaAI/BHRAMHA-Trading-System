from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


INDIA_TZ = ZoneInfo("Asia/Kolkata")
HORA_SEQUENCE = ["SUN", "VENUS", "MERCURY", "MOON", "SATURN", "JUPITER", "MARS"]
DAY_RULERS = {
    0: "MOON",     # Monday
    1: "MARS",     # Tuesday
    2: "MERCURY",  # Wednesday
    3: "JUPITER",  # Thursday
    4: "VENUS",    # Friday
    5: "SATURN",   # Saturday
    6: "SUN",      # Sunday
}


def get_current_hora(now_dt: datetime | None = None):
    """Return the current Hora planet using a fixed 06:00 IST sunrise.

    Delegates to vedic_core.get_hora (single source of truth) while preserving
    this module's return shape. Accepts any datetime so it is backtest-safe.
    """
    from vedic_core import get_hora

    if now_dt is not None:
        if now_dt.tzinfo is None:
            now_dt = now_dt.replace(tzinfo=INDIA_TZ)
    hora = get_hora(now_dt)

    # Recompute the IST sunrise used, for display parity with the old API.
    ref = (now_dt.astimezone(INDIA_TZ) if now_dt is not None
           else datetime.now(INDIA_TZ))
    sunrise = ref.replace(hour=6, minute=0, second=0, microsecond=0)
    if ref < sunrise:
        sunrise -= timedelta(days=1)

    return {
        "hora_planet": hora["hora_planet"],
        "hora_number": hora["hora_number"],
        "sunrise": sunrise.isoformat(),
    }
