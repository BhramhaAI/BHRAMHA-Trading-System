from __future__ import annotations


def detect_liquidity_vacuum(data, liquidity_pools):
    """
    Detect liquidity vacuum when the gap between nearest pools exceeds ATR*2.
    Returns:
    {
      "vacuum_active": bool,
      "vacuum_direction": "UP"|"DOWN"|"NONE",
      "vacuum_strength": float
    }
    """
    if data is None or len(data) < 5:
        return {"vacuum_active": False, "vacuum_direction": "NONE", "vacuum_strength": 0.0}

    last_close = float(data["close"].iloc[-1])
    atr = float(data["atr"].iloc[-1]) if "atr" in data.columns else float((data["high"] - data["low"]).rolling(14).mean().iloc[-1])
    atr = max(atr, 1e-9)

    liq_above = float(liquidity_pools.get("liquidity_above", last_close))
    liq_below = float(liquidity_pools.get("liquidity_below", last_close))
    gap = abs(liq_above - liq_below)
    vacuum_active = gap > (atr * 2.0)

    dist_up = max(liq_above - last_close, 0.0)
    dist_down = max(last_close - liq_below, 0.0)
    if dist_up > dist_down:
        vacuum_direction = "UP"
    elif dist_down > dist_up:
        vacuum_direction = "DOWN"
    else:
        vacuum_direction = "NONE"

    vacuum_strength = max(gap / atr, 0.0) if vacuum_active else 0.0

    return {
        "vacuum_active": bool(vacuum_active),
        "vacuum_direction": vacuum_direction,
        "vacuum_strength": round(vacuum_strength, 2),
    }
