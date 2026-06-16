from __future__ import annotations

from oi_engine import detect_oi_momentum
from utils.binance_utils import normalize_symbol


def detect_oi_shift(symbol):
    """
    Detect open-interest shift context from Binance USD-M futures.
    Returns:
    {
      "oi_change": float,
      "bias": "bullish" | "bearish" | "neutral" | "short_squeeze"
    }
    """
    symbol = normalize_symbol(symbol)
    data = detect_oi_momentum(symbol=symbol, interval="5m")
    oi_bias = str(data.get("oi_bias", "neutral")).lower()
    mapped_bias = "neutral"
    if oi_bias == "bullish_continuation":
        mapped_bias = "bullish"
    elif oi_bias == "bearish_continuation":
        mapped_bias = "bearish"
    elif oi_bias == "short_squeeze":
        mapped_bias = "short_squeeze"

    return {
        "oi_change": float(data.get("oi_change_5", 0.0)),
        "bias": mapped_bias,
        "oi_change_5": float(data.get("oi_change_5", 0.0)),
        "oi_change_20": float(data.get("oi_change_20", 0.0)),
        "price_change_5": float(data.get("price_change_5", 0.0)),
        "price_change_20": float(data.get("price_change_20", 0.0)),
        "oi_bias": str(data.get("oi_bias", "neutral")),
        "oi_strength": str(data.get("oi_strength", "low")),
    }
