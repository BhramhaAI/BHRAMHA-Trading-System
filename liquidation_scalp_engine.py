from __future__ import annotations

import datetime
import os
import time
from collections import deque

import pandas as pd

from binance_data import get_data
from config import COINS
from indicators import add_indicators
from orderflow_engine import analyze_orderflow
from utils.binance_utils import get_open_interest, normalize_symbol

LIQUIDATION_SCALP_TIMEFRAMES = ["1m", "3m", "5m"]
LIQUIDATION_SCALP_MAX_SIGNALS = 3
LIQUIDATION_SCALP_COOLDOWN_SECONDS = 10 * 60
LIQUIDATION_SCALP_MIN_VOLATILITY = 0.002
CSV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scalp_signals_log.csv")

_cooldowns: dict[str, float] = {}
_oi_snapshots: dict[str, deque[tuple[float, float, float]]] = {}


def _ensure_log_file():
    if os.path.isfile(CSV_PATH):
        return
    df = pd.DataFrame(columns=["time", "coin", "timeframe", "direction", "entry", "tp", "sl", "trigger"])
    df.to_csv(CSV_PATH, index=False)


def log_liquidation_scalp_signal(data):
    _ensure_log_file()
    row = {
        "time": datetime.datetime.utcnow(),
        "coin": data.get("coin", ""),
        "timeframe": data.get("timeframe", ""),
        "direction": data.get("direction", ""),
        "entry": data.get("entry", ""),
        "tp": data.get("tp", ""),
        "sl": data.get("sl", ""),
        "trigger": data.get("trigger", ""),
    }
    df = pd.read_csv(CSV_PATH)
    df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    df.to_csv(CSV_PATH, index=False)


def _oi_metrics(symbol: str, timeframe: str, current_price: float) -> dict:
    now = time.time()
    interval_seconds = {"1m": 60, "3m": 180, "5m": 300}.get(timeframe, 60)
    current_oi = get_open_interest(symbol)
    if current_oi is None:
        return {
            "oi_change_pct": 0.0,
            "price_change_pct": 0.0,
            "leverage_buildup": False,
        }

    key = f"{symbol}_{timeframe}"
    history = _oi_snapshots.setdefault(key, deque())
    history.append((now, current_oi, current_price))
    while history and history[0][0] < now - (interval_seconds * 8):
        history.popleft()

    reference = None
    for snapshot in history:
        if now - snapshot[0] >= (interval_seconds * 5):
            reference = snapshot

    if reference is None or reference[1] <= 0:
        return {
            "oi_change_pct": 0.0,
            "price_change_pct": 0.0,
            "leverage_buildup": False,
        }

    old_oi = reference[1]
    old_price = reference[2]
    oi_change_pct = (current_oi - old_oi) / max(old_oi, 1e-9)
    price_change_pct = abs(current_price - old_price) / max(old_price, 1e-9)
    leverage_buildup = oi_change_pct > 0.03 and price_change_pct < 0.002

    return {
        "oi_change_pct": oi_change_pct * 100.0,
        "price_change_pct": price_change_pct * 100.0,
        "leverage_buildup": leverage_buildup,
    }


def _cooldown_active(symbol: str) -> bool:
    now = time.time()
    last = _cooldowns.get(symbol)
    if last and now - last < LIQUIDATION_SCALP_COOLDOWN_SECONDS:
        return True
    return False


def _mark_cooldown(symbol: str):
    _cooldowns[symbol] = time.time()


def detect_liquidation_scalp(df, symbol, timeframe="1m"):
    if df is None or len(df) < 30:
        return None

    price = float(df["close"].iloc[-1])
    atr = max(float(df["atr"].iloc[-1]), 1e-9)
    volatility_ratio = atr / max(price, 1e-9)
    if volatility_ratio <= LIQUIDATION_SCALP_MIN_VOLATILITY:
        return None

    bb_width = float((df["bb_upper"].iloc[-1] - df["bb_lower"].iloc[-1]) / max(price, 1e-9))
    compression_ok = bb_width < 0.008

    swing_high = float(df["high"].tail(20).max())
    swing_low = float(df["low"].tail(20).min())
    distance_high = abs(price - swing_high)
    distance_low = abs(price - swing_low)
    near_liquidity_high = distance_high < (0.6 * atr)
    near_liquidity_low = distance_low < (0.6 * atr)

    oi_metrics = _oi_metrics(symbol, timeframe, price)
    oi_change_pct = float(oi_metrics["oi_change_pct"])
    oi_spike = oi_change_pct > 2.0
    leverage_buildup = bool(oi_metrics["leverage_buildup"])
    if leverage_buildup:
        print("Leverage buildup detected")

    orderflow = analyze_orderflow(symbol)
    buy_ratio = float(orderflow.get("buy_volume", 0.0)) / max(float(orderflow.get("sell_volume", 0.0)), 1e-9)
    sell_ratio = float(orderflow.get("sell_volume", 0.0)) / max(float(orderflow.get("buy_volume", 0.0)), 1e-9)
    buy_dominance = buy_ratio > 1.6
    sell_dominance = sell_ratio > 1.6

    avg_volume_20 = max(float(df["volume"].tail(20).mean()), 1e-9)
    volume_ratio = float(df["volume"].iloc[-1]) / avg_volume_20
    volume_expansion = volume_ratio > 1.5

    long_conditions = [
        compression_ok,
        near_liquidity_low,
        oi_spike,
        buy_dominance,
        volume_expansion,
    ]
    short_conditions = [
        compression_ok,
        near_liquidity_high,
        oi_spike,
        sell_dominance,
        volume_expansion,
    ]

    long_score = sum(1 for item in long_conditions if item)
    short_score = sum(1 for item in short_conditions if item)
    signal_score = max(long_score, short_score)

    if leverage_buildup:
        signal_score += 15

    if long_score < 4 and short_score < 4:
        return None

    if long_score >= short_score and near_liquidity_low and buy_dominance:
        direction = "LONG"
        trigger_parts = ["Liquidity cluster", "Orderflow imbalance", "OI spike"]
    elif short_score > long_score and near_liquidity_high and sell_dominance:
        direction = "SHORT"
        trigger_parts = ["Liquidity cluster", "Orderflow imbalance", "OI spike"]
    else:
        return None

    entry = price
    if direction == "LONG":
        sl = entry - (0.4 * atr)
        tp = entry + (1.0 * atr)
    else:
        sl = entry + (0.4 * atr)
        tp = entry - (1.0 * atr)

    message = f"""⚡ BHRAMHA SCALP SIGNAL

Coin: {symbol}
Timeframe: {timeframe}

Direction: {direction}

Entry: {round(entry, 4)}
Stop Loss: {round(sl, 4)}
Take Profit: {round(tp, 4)}

Expected Duration:
30 seconds - 5 minutes

Trigger:
{' + '.join(trigger_parts)}"""
    if leverage_buildup:
        message += "\n\n⚡ Leverage Buildup Detected"

    return {
        "coin": symbol,
        "timeframe": timeframe,
        "direction": direction,
        "entry": entry,
        "tp": tp,
        "sl": sl,
        "trigger": " + ".join(trigger_parts),
        "message": message,
        "condition_count": signal_score,
        "oi_change_pct": round(oi_change_pct, 2),
        "price_change_pct": round(float(oi_metrics["price_change_pct"]), 2),
        "volume_ratio": round(volume_ratio, 2),
        "bb_width": round(bb_width, 6),
        "leverage_buildup": leverage_buildup,
    }


def generate_liquidation_scalp_signals():
    _ensure_log_file()
    signals = []
    for coin in COINS:
        coin = normalize_symbol(coin)
        if _cooldown_active(coin):
            continue

        for timeframe in LIQUIDATION_SCALP_TIMEFRAMES:
            try:
                df = add_indicators(get_data(symbol=coin, interval=timeframe, limit=120))
                signal = detect_liquidation_scalp(df, coin, timeframe)
                if signal is None:
                    continue
                signals.append(signal)
            except Exception as exc:
                print(f"liquidation scalp scan error {coin} {timeframe}: {exc}")
            finally:
                time.sleep(0.1)

    signals = sorted(signals, key=lambda item: item["condition_count"], reverse=True)
    selected = []
    for signal in signals:
        coin = signal["coin"]
        if _cooldown_active(coin):
            continue
        selected.append(signal)
        _mark_cooldown(coin)
        if len(selected) >= LIQUIDATION_SCALP_MAX_SIGNALS:
            break
    return selected


def run_liquidation_scalp_engine():
    return generate_liquidation_scalp_signals()
