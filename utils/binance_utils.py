from __future__ import annotations

from binance_http import BINANCE_HTTP_TIMEOUT, session
from config import COINS


def normalize_symbol(symbol: str) -> str:
    s = symbol.strip().upper()
    if not s.endswith("USDT"):
        s += "USDT"
    return s


def validate_symbol(symbol: str) -> bool:
    return symbol in COINS


def get_open_interest(symbol: str) -> float | None:
    try:
        resp = session.get(
            "https://fapi.binance.com/fapi/v1/openInterest",
            params={"symbol": symbol},
            timeout=BINANCE_HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        return float(data.get("openInterest", 0.0))
    except Exception:
        return None
