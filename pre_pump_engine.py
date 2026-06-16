from __future__ import annotations

import time
from collections import deque

from binance_http import BINANCE_HTTP_TIMEOUT, session
from utils.binance_utils import get_open_interest, normalize_symbol

_oi_snapshots: dict[str, deque[tuple[float, float, float]]] = {}


def _timeframe_seconds(tf: str) -> int:
    return {"1m": 60, "5m": 300}.get(tf, 60)


def _fetch_depth(symbol: str, limit: int = 100) -> dict:
    response = session.get(
        "https://api.binance.com/api/v3/depth",
        params={"symbol": symbol, "limit": limit},
        timeout=BINANCE_HTTP_TIMEOUT,
    )
    response.raise_for_status()
    return response.json()


def _fetch_open_interest(symbol: str) -> float:
    oi = get_open_interest(symbol)
    if oi is None:
        raise RuntimeError(f"OI unavailable for {symbol}")
    return oi


def analyze_pre_pump(symbol, timeframe, df):
    symbol = normalize_symbol(symbol)
    if df is None or len(df) < 60:
        return {
            "pre_pump_score": 0,
            "pump_probability": "LOW",
            "orderflow_pressure": "NEUTRAL",
            "pressure_strength": 1.0,
            "volume_accumulation": False,
            "liquidity_support": False,
            "liquidity_resistance": False,
            "oi_buildup": False,
            "volatility_squeeze": False,
            "all_aligned": False,
        }

    price = float(df["close"].iloc[-1])
    try:
        depth = _fetch_depth(symbol)
        bids = depth.get("bids", [])[:20]
        asks = depth.get("asks", [])[:20]
    except Exception as exc:
        print(f"pre-pump depth error {symbol}: {exc}")
        bids = []
        asks = []

    bid_volume = sum(float(level[1]) for level in bids if len(level) >= 2)
    ask_volume = sum(float(level[1]) for level in asks if len(level) >= 2)
    imbalance = bid_volume / max(ask_volume, 1e-9)

    if imbalance > 1.8:
        orderflow_pressure = "BUYERS"
    elif imbalance < 0.55:
        orderflow_pressure = "SELLERS"
    else:
        orderflow_pressure = "NEUTRAL"

    bid_sizes = [float(level[1]) for level in bids if len(level) >= 2]
    ask_sizes = [float(level[1]) for level in asks if len(level) >= 2]
    avg_depth = (
        (sum(bid_sizes) + sum(ask_sizes)) / max(len(bid_sizes) + len(ask_sizes), 1)
    )
    liquidity_support = bool(bid_sizes and max(bid_sizes) > (3.0 * avg_depth))
    liquidity_resistance = bool(ask_sizes and max(ask_sizes) > (3.0 * avg_depth))

    volume_last_5 = float(df["volume"].tail(5).sum())
    avg_volume_50 = float(df["volume"].tail(50).mean())
    price_5_back = float(df["close"].iloc[-6]) if len(df) >= 6 else price
    price_move_pct = abs(price - price_5_back) / max(price_5_back, 1e-9)
    volume_accumulation = (
        volume_last_5 > (1.7 * avg_volume_50 * 5)
        and price_move_pct < 0.002
    )

    bb_width = ((df["bb_upper"] - df["bb_lower"]) / df["close"].replace(0, 1e-9)).tail(20)
    width_now = float(bb_width.iloc[-1])
    width_avg = float(bb_width.mean())
    atr_tail = df["atr"].tail(6).tolist()
    atr_falling = all(atr_tail[i] <= atr_tail[i - 1] for i in range(1, len(atr_tail)))
    volatility_squeeze = width_now < (width_avg * 0.22) and atr_falling

    now = time.time()
    key = f"{symbol}_{timeframe}"
    interval_seconds = _timeframe_seconds(timeframe)
    try:
        current_oi = _fetch_open_interest(symbol)
    except Exception as exc:
        print(f"pre-pump oi error {symbol}: {exc}")
        current_oi = 0.0

    history = _oi_snapshots.setdefault(key, deque())
    history.append((now, current_oi, price))
    while history and history[0][0] < now - (interval_seconds * 8):
        history.popleft()

    oi_buildup = False
    oi_change_pct = 0.0
    for snapshot in history:
        if now - snapshot[0] >= (interval_seconds * 5):
            old_oi = snapshot[1]
            old_price = snapshot[2]
            oi_change_pct = ((current_oi - old_oi) / max(old_oi, 1e-9)) * 100.0 if old_oi > 0 else 0.0
            old_price_move_pct = abs(price - old_price) / max(old_price, 1e-9)
            oi_buildup = oi_change_pct > 4.0 and old_price_move_pct < 0.002

    pre_pump_score = 0
    if orderflow_pressure in {"BUYERS", "SELLERS"}:
        pre_pump_score += 3
    if volume_accumulation:
        pre_pump_score += 3
    if oi_buildup:
        pre_pump_score += 3
    if volatility_squeeze:
        pre_pump_score += 3

    all_aligned = (
        orderflow_pressure in {"BUYERS", "SELLERS"}
        and volume_accumulation
        and oi_buildup
        and volatility_squeeze
    )
    if all_aligned:
        pre_pump_score += 12

    if all_aligned:
        pump_probability = "HIGH"
    elif pre_pump_score >= 9:
        pump_probability = "MEDIUM"
    else:
        pump_probability = "LOW"

    return {
        "pre_pump_score": int(pre_pump_score),
        "pump_probability": pump_probability,
        "orderflow_pressure": orderflow_pressure,
        "pressure_strength": round(imbalance, 2),
        "volume_accumulation": bool(volume_accumulation),
        "liquidity_support": liquidity_support,
        "liquidity_resistance": liquidity_resistance,
        "oi_buildup": bool(oi_buildup),
        "oi_change_pct": round(oi_change_pct, 2),
        "volatility_squeeze": bool(volatility_squeeze),
        "all_aligned": bool(all_aligned),
    }
