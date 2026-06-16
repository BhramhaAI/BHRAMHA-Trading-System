from __future__ import annotations

from binance_http import BINANCE_HTTP_TIMEOUT, session
from utils.binance_utils import get_open_interest, normalize_symbol, validate_symbol


def _get_json(url, params, timeout=BINANCE_HTTP_TIMEOUT):
    resp = session.get(url, params=params, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def detect_oi_momentum(symbol, interval="5m"):
    """
    Returns:
    {
      "oi_change_5": float,
      "oi_change_20": float,
      "price_change_5": float,
      "price_change_20": float,
      "oi_bias": str,
      "oi_strength": str
    }
    """
    symbol = normalize_symbol(symbol)
    if not validate_symbol(symbol):
        return {
            "oi_change_5": 0.0,
            "oi_change_20": 0.0,
            "price_change_5": 0.0,
            "price_change_20": 0.0,
            "oi_bias": "neutral",
            "oi_strength": "low",
        }

    try:
        current_oi = get_open_interest(symbol)
        if current_oi is None:
            return {
                "oi_change_5": 0.0,
                "oi_change_20": 0.0,
                "price_change_5": 0.0,
                "price_change_20": 0.0,
                "oi_bias": "neutral",
                "oi_strength": "low",
            }

        oi_hist = _get_json(
            "https://fapi.binance.com/futures/data/openInterestHist",
            {"symbol": symbol, "period": interval, "limit": 21},
        )
        klines = _get_json(
            "https://fapi.binance.com/fapi/v1/klines",
            {"symbol": symbol, "interval": interval, "limit": 21},
        )

        if not isinstance(oi_hist, list) or len(oi_hist) < 21 or not isinstance(klines, list) or len(klines) < 21:
            raise ValueError("insufficient oi/price history")

        oi_series = [float(x.get("sumOpenInterest", 0.0)) for x in oi_hist]
        close_series = [float(k[4]) for k in klines]

        oi_change_5 = oi_series[-1] - oi_series[-6]
        oi_change_20 = oi_series[-1] - oi_series[-21]
        price_change_5 = close_series[-1] - close_series[-6]
        price_change_20 = close_series[-1] - close_series[-21]

        if price_change_5 > 0 and oi_change_5 > 0:
            oi_bias = "bullish_continuation"
        elif price_change_5 > 0 and oi_change_5 < 0:
            oi_bias = "short_squeeze"
        elif price_change_5 < 0 and oi_change_5 > 0:
            oi_bias = "bearish_continuation"
        elif price_change_5 < 0 and oi_change_5 < 0:
            oi_bias = "longs_closing"
        else:
            oi_bias = "neutral"

        oi_magnitude = abs(oi_change_5) + (abs(oi_change_20) * 0.5)
        if oi_magnitude > 0:
            base_oi = max(abs(current_oi), abs(oi_series[-1]), 1e-9)
            rel = oi_magnitude / base_oi
            if rel > 0.05:
                oi_strength = "high"
            elif rel > 0.02:
                oi_strength = "medium"
            else:
                oi_strength = "low"
        else:
            oi_strength = "low"

        return {
            "oi_change_5": round(oi_change_5, 2),
            "oi_change_20": round(oi_change_20, 2),
            "price_change_5": round(price_change_5, 4),
            "price_change_20": round(price_change_20, 4),
            "oi_bias": oi_bias,
            "oi_strength": oi_strength,
        }
    except Exception:
        return {
            "oi_change_5": 0.0,
            "oi_change_20": 0.0,
            "price_change_5": 0.0,
            "price_change_20": 0.0,
            "oi_bias": "neutral",
            "oi_strength": "low",
        }
