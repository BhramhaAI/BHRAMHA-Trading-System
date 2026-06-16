from skyfield.api import load
import datetime

ts = load.timescale()
eph = load('de421.bsp')

earth = eph['earth']
moon = eph['moon']

NAKSHATRA_VOLATILITY = {
0: 1.2,
1: 1.1,
2: 1.0,
3: 1.3,
4: 1.2,
5: 1.4,
6: 1.1,
7: 1.0,
8: 0.9,
9: 1.3,
10: 1.1,
11: 1.0,
12: 1.2,
13: 1.3,
14: 1.1,
15: 1.2,
16: 1.0,
17: 1.3,
18: 1.4,
19: 1.1,
20: 1.0,
21: 1.2,
22: 1.3,
23: 1.1,
24: 1.0,
25: 0.9,
26: 1.2
}


def get_astrology(dt=None):
    """Return (nakshatra_index, moon_phase, volatility_multiplier) at `dt`.

    Now backed by vedic_core: the nakshatra index uses the correct sidereal
    ecliptic longitude and moon_phase is the true Sun-Moon elongation fraction,
    instead of the old equatorial-RA / day-of-year approximations.
    """
    from vedic_core import get_nakshatra, get_tithi

    nakshatra = get_nakshatra(dt)["nakshatra_index"]
    moon_phase = get_tithi(dt)["moon_phase"]
    volatility = NAKSHATRA_VOLATILITY.get(nakshatra, 1)

    return nakshatra, moon_phase, volatility