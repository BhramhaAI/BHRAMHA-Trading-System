from __future__ import annotations

from binance_http import BINANCE_HTTP_TIMEOUT, session


def _get_funding_rate(symbol):
    try:
        resp = session.get(
            "https://fapi.binance.com/fapi/v1/fundingRate",
            params={"symbol": symbol, "limit": 1},
            timeout=BINANCE_HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list) and data:
            return float(data[-1].get("fundingRate", 0.0))
    except Exception:
        pass
    return 0.0


def detect_liquidation_zones(open_interest, structure, liquidity_pools, symbol=None, current_price=None, volatility_pct=None):
    """
    Estimate likely liquidation clusters from OI + structure + pool layout + funding.
    Returns:
    {
      "liq_zone_above": price,
      "liq_zone_below": price,
      "liq_pressure": bullish|bearish|neutral
    }
    """
    liq_zone_above = float(liquidity_pools.get("liquidity_above", 0.0) or 0.0)
    liq_zone_below = float(liquidity_pools.get("liquidity_below", 0.0) or 0.0)
    price = float(current_price or 0.0)
    vol_pct = float(volatility_pct or 0.0)

    oi_5 = float(open_interest.get("oi_change_5", open_interest.get("oi_change", 0.0)) or 0.0)
    price_change_5 = float(open_interest.get("price_change_5", 0.0) or 0.0)
    funding_rate = _get_funding_rate(symbol) if symbol else 0.0

    liq_pressure = "neutral"
    buffer = max(price * (max(vol_pct, 0.2) / 100.0), price * 0.001, 1e-9) if price > 0 else 0.0

    # OI rising + price rising -> long build-up, liquidation risk below.
    if oi_5 > 0 and price_change_5 > 0:
        liq_pressure = "bearish"
        if price > 0:
            liq_zone_below = min(liq_zone_below, price - buffer) if liq_zone_below > 0 else price - buffer

    # OI rising + price falling -> short build-up, liquidation risk above.
    elif oi_5 > 0 and price_change_5 < 0:
        liq_pressure = "bullish"
        if price > 0:
            liq_zone_above = max(liq_zone_above, price + buffer)

    # Funding tilt for liquidation pressure.
    if funding_rate > 0.0002 and liq_pressure == "neutral":
        liq_pressure = "bearish"
    elif funding_rate < -0.0002 and liq_pressure == "neutral":
        liq_pressure = "bullish"

    return {
        "liq_zone_above": float(liq_zone_above),
        "liq_zone_below": float(liq_zone_below),
        "liq_pressure": liq_pressure,
        # Backward-compatible keys
        "liq_above": float(liq_zone_above),
        "liq_below": float(liq_zone_below),
    }
