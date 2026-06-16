from __future__ import annotations

import time
from collections import deque

import pandas as pd

from astrology_engine import get_astrology
from binance_data import get_data
from chart_engine import cleanup_chart_images, create_chart
from confidence_engine import calculate_confidence
from config import COINS, SCALP_TIMEFRAMES
from crowd_psychology_engine import analyze_crowd_psychology
from indicators import add_indicators
from liquidation_engine import detect_liquidation_zones
from liquidity_engine import detect_liquidity_event, detect_liquidity_sweep_context, detect_liquidity_zones
from liquidity_pool_engine import detect_liquidity_pools
from logger import CSV_PATH
from lunar_volatility_engine import predict_lunar_volatility
from market_regime_engine import detect_market_regime
from nakshatra_engine import get_nakshatra_market_bias
from nakshatra_psychology import get_nakshatra_psychology
from oi_engine import detect_oi_momentum
from open_interest_engine import detect_oi_shift
from orderflow_engine import analyze_orderflow
from panchang_engine import explain_tithi, get_panchang
from planetary_engine import calculate_planetary_bias
from pre_pump_engine import analyze_pre_pump
from psychology_engine import psychology_score
from structure_engine import detect_structure
from trend_engine import get_trend
from utils.binance_utils import get_open_interest, normalize_symbol
from vacuum_engine import detect_liquidity_vacuum
from vedic_time_engine import get_vedic_time_quality
from volatility_expansion_engine import detect_volatility

BASE_SCORE = 70
MAX_SCORE = 98
SCALP_MIN_SCORE = 75
SCALP_MIN_CONFIDENCE = 75
SCALP_MIN_RR = 1.4
SCALP_MAX_PER_SCAN = 3
SCALP_COOLDOWN_SECONDS = 10 * 60
SCALP_MAX_TRADES_PER_COIN_PER_HOUR = 2
SCALP_MIN_SL_PCT = 0.0015
SCALP_MAX_SL_PCT = 0.0035
SCALP_MIN_TP_PCT = 0.004
SCALP_MAX_TP_PCT = 0.01
SCALP_TARGET_HOLD_MIN = 2
SCALP_TARGET_HOLD_MAX = 15
SCALP_REJECT_HOLD_MIN = 20

_signal_timestamps: dict[str, deque[float]] = {}
_last_signal_time: dict[str, float] = {}
_oi_snapshots: dict[str, deque[tuple[float, float, float]]] = {}


def _safe_rr(entry, tp, sl):
    reward = abs(tp - entry)
    risk = max(abs(entry - sl), 1e-9)
    return reward / risk


def _clamp(value, min_value, max_value):
    return max(min_value, min(max_value, value))


def _timeframe_seconds(tf: str) -> int:
    return {"1m": 60, "5m": 300}.get(tf, 60)


def _timeframe_minutes(tf: str) -> int:
    return {"1m": 1, "5m": 5}.get(tf, 1)


def _add_directional(scores, direction, value):
    if direction == "LONG":
        scores["LONG"] += value
    elif direction == "SHORT":
        scores["SHORT"] += value


def _penalize_other_side(scores, direction, penalty):
    other = "SHORT" if direction == "LONG" else "LONG"
    scores[other] -= penalty


def _detect_equal_levels(df, tolerance_pct=0.0015):
    highs = df["high"].tail(12).tolist()
    lows = df["low"].tail(12).tolist()
    equal_highs = False
    equal_lows = False
    equal_high_level = None
    equal_low_level = None

    for i in range(len(highs) - 1):
        for j in range(i + 1, len(highs)):
            base = max(abs(highs[i]), abs(highs[j]), 1e-9)
            if abs(highs[i] - highs[j]) / base < tolerance_pct:
                equal_highs = True
                equal_high_level = (highs[i] + highs[j]) / 2.0

    for i in range(len(lows) - 1):
        for j in range(i + 1, len(lows)):
            base = max(abs(lows[i]), abs(lows[j]), 1e-9)
            if abs(lows[i] - lows[j]) / base < tolerance_pct:
                equal_lows = True
                equal_low_level = (lows[i] + lows[j]) / 2.0

    return {
        "equal_highs": bool(equal_highs),
        "equal_lows": bool(equal_lows),
        "equal_high_level": equal_high_level,
        "equal_low_level": equal_low_level,
    }


def _detect_liquidity_sweep(df):
    if len(df) < 4:
        return {
            "triggered": False,
            "direction": "NONE",
            "label": "None",
            "equal_highs": False,
            "equal_lows": False,
            "score_boost": 0,
        }

    levels = _detect_equal_levels(df.iloc[:-1])
    last = df.iloc[-1]
    last_close = float(last["close"])
    last_high = float(last["high"])
    last_low = float(last["low"])

    breakout_above = (
        levels["equal_highs"]
        and levels["equal_high_level"] is not None
        and last_high > levels["equal_high_level"]
        and last_close < levels["equal_high_level"]
    )
    breakdown_below = (
        levels["equal_lows"]
        and levels["equal_low_level"] is not None
        and last_low < levels["equal_low_level"]
        and last_close > levels["equal_low_level"]
    )

    if breakout_above:
        return {
            "triggered": True,
            "direction": "SHORT",
            "label": "Liquidity sweep",
            "equal_highs": True,
            "equal_lows": bool(levels["equal_lows"]),
            "score_boost": 10,
            "sweep_level": levels["equal_high_level"],
        }

    if breakdown_below:
        return {
            "triggered": True,
            "direction": "LONG",
            "label": "Liquidity sweep",
            "equal_highs": bool(levels["equal_highs"]),
            "equal_lows": True,
            "score_boost": 10,
            "sweep_level": levels["equal_low_level"],
        }

    return {
        "triggered": False,
        "direction": "NONE",
        "label": "None",
        "equal_highs": bool(levels["equal_highs"]),
        "equal_lows": bool(levels["equal_lows"]),
        "score_boost": 0,
        "sweep_level": levels["equal_high_level"] or levels["equal_low_level"],
    }


def _detect_compression_breakout(df):
    if len(df) < 25:
        return {
            "compression": False,
            "volatility_expansion": False,
            "direction": "NONE",
            "score_boost": 0,
            "low_volatility_penalty": 0,
        }

    close = df["close"]
    bb_width = (df["bb_upper"] - df["bb_lower"]) / close.replace(0, 1e-9)
    avg_width = float(bb_width.tail(20).mean())
    width_now = float(bb_width.iloc[-1])
    compression = width_now < (avg_width * 0.25)

    atr_tail = df["atr"].tail(5).tolist()
    atr_decreasing = all(atr_tail[i] <= atr_tail[i - 1] for i in range(1, len(atr_tail)))
    recent_high = float(df["high"].iloc[-21:-1].max())
    recent_low = float(df["low"].iloc[-21:-1].min())
    last_close = float(close.iloc[-1])
    prev_close = float(close.iloc[-2])
    breakout_up = last_close > recent_high and prev_close <= recent_high
    breakout_down = last_close < recent_low and prev_close >= recent_low
    volatility_expansion = compression and atr_decreasing and (breakout_up or breakout_down)

    return {
        "compression": compression,
        "atr_decreasing": atr_decreasing,
        "volatility_expansion": volatility_expansion,
        "direction": "LONG" if breakout_up else ("SHORT" if breakout_down else "NONE"),
        "score_boost": 8 if volatility_expansion else 0,
        "low_volatility_penalty": -5 if compression and not volatility_expansion else 0,
        "bb_width_now": round(width_now, 6),
        "bb_width_avg": round(avg_width, 6),
    }


def _detect_orderflow_shock(symbol, direction):
    orderflow = analyze_orderflow(symbol)
    imbalance_ratio = float(orderflow.get("imbalance_ratio", 1.0))
    aligned = (
        direction == "LONG" and imbalance_ratio > 1.5
    ) or (
        direction == "SHORT" and imbalance_ratio < 0.6
    )
    against = (
        direction == "LONG" and imbalance_ratio < 0.6
    ) or (
        direction == "SHORT" and imbalance_ratio > 1.5
    )
    return {
        **orderflow,
        "aligned": aligned,
        "against": against,
        "score_boost": 8 if aligned else 0,
        "penalty": -6 if against else 0,
    }


def _fetch_open_interest(symbol):
    oi = get_open_interest(symbol)
    if oi is None:
        raise RuntimeError(f"OI unavailable for {symbol}")
    return oi


def _detect_oi_spike(symbol, timeframe, price, direction):
    key = f"{symbol}_{timeframe}"
    now = time.time()
    interval_seconds = _timeframe_seconds(timeframe)

    try:
        current_oi = _fetch_open_interest(symbol)
    except Exception as exc:
        print(f"Scalp OI error for {symbol} {timeframe}: {exc}")
        current_oi = 0.0

    history = _oi_snapshots.setdefault(key, deque())
    history.append((now, current_oi, price))
    while history and history[0][0] < now - (interval_seconds * 6):
        history.popleft()

    reference = None
    for snapshot in history:
        if now - snapshot[0] >= (interval_seconds * 3):
            reference = snapshot

    if reference is None or reference[1] <= 0:
        return {
            "oi_change_pct": 0.0,
            "price_change_pct": 0.0,
            "oi_spike": False,
            "oi_bias": "neutral",
            "score_boost": 0,
        }

    _, old_oi, old_price = reference
    oi_change_pct = ((current_oi - old_oi) / max(old_oi, 1e-9)) * 100.0
    price_change_pct = ((price - old_price) / max(old_price, 1e-9)) * 100.0
    oi_spike = oi_change_pct > 3.0

    if price_change_pct > 0 and oi_change_pct > 0:
        oi_bias = "squeeze_potential"
    elif price_change_pct < 0 and oi_change_pct > 0:
        oi_bias = "bearish_pressure"
    else:
        oi_bias = "neutral"

    aligned = (
        direction == "LONG" and oi_bias == "squeeze_potential"
    ) or (
        direction == "SHORT" and oi_bias == "bearish_pressure"
    )

    return {
        "oi_change_pct": round(oi_change_pct, 2),
        "price_change_pct": round(price_change_pct, 2),
        "oi_spike": oi_spike,
        "oi_bias": oi_bias,
        "score_boost": 6 if oi_spike and aligned else 0,
    }


def _calc_scalp_levels(entry, atr, direction, liquidity_pools, liquidation):
    atr_pct = atr / max(entry, 1e-9)
    stop_pct = _clamp(atr_pct * 0.55, SCALP_MIN_SL_PCT, SCALP_MAX_SL_PCT)
    tp_pct = _clamp(stop_pct * 2.2, SCALP_MIN_TP_PCT, SCALP_MAX_TP_PCT)

    if direction == "LONG":
        raw_sl = entry * (1.0 - stop_pct)
        raw_tp = entry * (1.0 + tp_pct)
        liq_tp = float(liquidation.get("liq_zone_above", liquidity_pools.get("liquidity_above", raw_tp)))
        tp = max(raw_tp, min(entry * (1.0 + SCALP_MAX_TP_PCT), liq_tp if liq_tp > entry else raw_tp))
        sl = raw_sl
    else:
        raw_sl = entry * (1.0 + stop_pct)
        raw_tp = entry * (1.0 - tp_pct)
        liq_tp = float(liquidation.get("liq_zone_below", liquidity_pools.get("liquidity_below", raw_tp)))
        tp = min(raw_tp, max(entry * (1.0 - SCALP_MAX_TP_PCT), liq_tp if liq_tp < entry else raw_tp))
        sl = raw_sl

    return sl, tp


def _estimate_hold_minutes(df, entry, tp, timeframe):
    atr = max(float(df["atr"].iloc[-1]), 1e-9)
    recent_speed = float(df["close"].diff().abs().tail(5).mean())
    volatility_speed = max(recent_speed, atr * 0.35)
    atr_move = abs(tp - entry)
    expected = max(1.0, atr_move / max(volatility_speed, 1e-9)) * _timeframe_minutes(timeframe)
    return round(expected, 1)


def _trade_allowed(symbol):
    now = time.time()
    recent = _signal_timestamps.setdefault(symbol, deque())
    while recent and now - recent[0] > 3600:
        recent.popleft()

    if len(recent) >= SCALP_MAX_TRADES_PER_COIN_PER_HOUR:
        return False, "hourly scalp limit reached"

    last_time = _last_signal_time.get(symbol)
    if last_time and now - last_time < SCALP_COOLDOWN_SECONDS:
        return False, "symbol cooldown active"

    try:
        df = pd.read_csv(CSV_PATH, on_bad_lines="skip")
        df.columns = [str(col).lower().strip() for col in df.columns]
        if {"signal_type", "coin", "time"}.issubset(df.columns):
            df["signal_type"] = df["signal_type"].fillna("").astype(str).str.upper()
            df["coin"] = df["coin"].fillna("").astype(str).str.upper()
            df["time"] = pd.to_datetime(df["time"], errors="coerce", utc=True)
            scalp_rows = df[(df["signal_type"] == "SCALP") & (df["coin"] == symbol)].copy()
            now_utc = pd.Timestamp.utcnow()
            if len(scalp_rows[scalp_rows["time"] >= (now_utc - pd.Timedelta(hours=1))]) >= SCALP_MAX_TRADES_PER_COIN_PER_HOUR:
                return False, "hourly scalp limit reached"
            if not scalp_rows[scalp_rows["time"] >= (now_utc - pd.Timedelta(seconds=SCALP_COOLDOWN_SECONDS))].empty:
                return False, "symbol cooldown active"
    except Exception:
        pass

    return True, ""


def _mark_trade(symbol):
    now = time.time()
    _signal_timestamps.setdefault(symbol, deque()).append(now)
    _last_signal_time[symbol] = now


def _build_scalp_message(data):
    drivers = "\n".join(f"• {item}" for item in data["signal_drivers"])
    return f"""⚡ BHRAMHA SCALP SIGNAL

Coin: {data['coin']}
Timeframe: {data['timeframe']}

Direction: {data['direction']}

Entry: {round(data['entry'], 4)}
SL: {round(data['sl'], 4)}
TP: {round(data['tp'], 4)}

Expected Hold: ~{data['expected_hold_minutes']} minutes

Score: {data['score']}
Confidence: {data['confidence']}%

Signal Drivers:
{drivers}

⚡ Pre-Move Indicators

Orderflow Pressure: {data['orderflow_pressure']}
Volume Accumulation: {str(data['volume_accumulation']).upper()}
Open Interest Buildup: {str(data['oi_buildup']).upper()}
Volatility Squeeze: {str(data['volatility_squeeze']).upper()}

🚨 Pump Probability: {data['pump_probability']}"""


def _driver_append(drivers, label, condition):
    if condition and label not in drivers:
        drivers.append(label)


def _collect_context(symbol, timeframe):
    symbol = normalize_symbol(symbol)
    df = add_indicators(get_data(symbol=symbol, interval=timeframe, limit=150))
    df_15m = add_indicators(get_data(symbol=symbol, interval="15m", limit=150))
    df_1h = add_indicators(get_data(symbol=symbol, interval="1h", limit=150))

    trend_local = get_trend(df)
    trend_15m = get_trend(df_15m)
    trend_1h = get_trend(df_1h)

    regime = detect_market_regime(df)
    liquidity_event = detect_liquidity_event(df)
    liquidity_zones = detect_liquidity_zones(df)
    liquidity_pools = detect_liquidity_pools(df)
    sweep_context = detect_liquidity_sweep_context(df)
    structure = detect_structure(df)
    volatility_state = detect_volatility(df)
    orderflow = analyze_orderflow(symbol)
    oi = get_open_interest(symbol)
    if oi is None:
        print(f"Skipping OI analysis for {symbol}")
        oi_shift = {
            "oi_change": 0.0,
            "bias": "neutral",
            "oi_change_5": 0.0,
            "oi_change_20": 0.0,
            "price_change_5": 0.0,
            "price_change_20": 0.0,
            "oi_bias": "neutral",
            "oi_strength": "low",
        }
        oi_momentum = {
            "oi_change_5": 0.0,
            "oi_change_20": 0.0,
            "price_change_5": 0.0,
            "price_change_20": 0.0,
            "oi_bias": "neutral",
            "oi_strength": "low",
        }
    else:
        oi_shift = detect_oi_shift(symbol)
        oi_momentum = detect_oi_momentum(symbol=symbol, interval="5m")
    liquidation = detect_liquidation_zones(
        open_interest=oi_momentum,
        structure=structure,
        liquidity_pools=liquidity_pools,
        symbol=symbol,
        current_price=float(df["close"].iloc[-1]),
        volatility_pct=(float(df["atr"].iloc[-1]) / max(float(df["close"].iloc[-1]), 1e-9)) * 100.0,
    )
    vacuum = detect_liquidity_vacuum(df, liquidity_pools)
    nakshatra, moon_phase, vol_factor = get_astrology()
    panchang = get_panchang()
    nakshatra_psy = get_nakshatra_psychology(nakshatra) or {
        "name": "Unknown",
        "meaning": "Unavailable",
        "type": "neutral",
        "score": 1.0,
    }
    crowd = analyze_crowd_psychology(df)
    planetary = calculate_planetary_bias()
    nak_cycle = get_nakshatra_market_bias()
    vedic_time = get_vedic_time_quality()
    lunar = predict_lunar_volatility()
    psychology = psychology_score(df)
    pre_pump = analyze_pre_pump(symbol, timeframe, df)

    return {
        "df": df,
        "df_15m": df_15m,
        "df_1h": df_1h,
        "trend_local": trend_local,
        "trend_15m": trend_15m,
        "trend_1h": trend_1h,
        "regime": regime,
        "liquidity_event": liquidity_event,
        "liquidity_zones": liquidity_zones,
        "liquidity_pools": liquidity_pools,
        "sweep_context": sweep_context,
        "structure": structure,
        "volatility_state": volatility_state,
        "oi_shift": oi_shift,
        "oi_momentum": oi_momentum,
        "liquidation": liquidation,
        "vacuum": vacuum,
        "nakshatra": nakshatra,
        "moon_phase": moon_phase,
        "vol_factor": vol_factor,
        "panchang": panchang,
        "nakshatra_psy": nakshatra_psy,
        "crowd": crowd,
        "planetary": planetary,
        "nak_cycle": nak_cycle,
        "vedic_time": vedic_time,
        "lunar": lunar,
        "psychology": psychology,
        "pre_pump": pre_pump,
    }


def _score_signal(symbol, timeframe, context):
    df = context["df"]
    price = float(df["close"].iloc[-1])
    atr = max(float(df["atr"].iloc[-1]), 1e-9)
    rsi = float(df["rsi"].iloc[-1])
    macd = float(df["macd"].iloc[-1])
    ema50 = float(df["ema50"].iloc[-1])
    volume = float(df["volume"].iloc[-1])
    avg_volume = float(df["volume"].tail(20).mean())
    pre_pump = context["pre_pump"]

    scores = {"LONG": float(BASE_SCORE), "SHORT": float(BASE_SCORE)}
    drivers = {"LONG": [], "SHORT": []}

    sweep = _detect_liquidity_sweep(df)
    compression = _detect_compression_breakout(df)
    micro_direction = sweep["direction"] if sweep["triggered"] else compression["direction"]

    if sweep["triggered"]:
        _add_directional(scores, sweep["direction"], 10)
        _driver_append(drivers[sweep["direction"]], "Liquidity sweep", True)

    if compression["volatility_expansion"]:
        _add_directional(scores, compression["direction"], 6)
        _driver_append(drivers[compression["direction"]], "Volatility expansion", True)
    elif compression["low_volatility_penalty"] < 0:
        scores["LONG"] += compression["low_volatility_penalty"]
        scores["SHORT"] += compression["low_volatility_penalty"]

    technical_long = (
        price > ema50
        and macd >= 0
        and rsi >= 50
        and context["trend_local"] == "UP"
    )
    technical_short = (
        price < ema50
        and macd <= 0
        and rsi <= 50
        and context["trend_local"] == "DOWN"
    )
    if technical_long:
        scores["LONG"] += 10
        _driver_append(drivers["LONG"], "Technical alignment", True)
    if technical_short:
        scores["SHORT"] += 10
        _driver_append(drivers["SHORT"], "Technical alignment", True)

    if context["trend_15m"] == "UP" and context["trend_1h"] == "UP":
        scores["LONG"] += 4
    elif context["trend_15m"] == "DOWN" and context["trend_1h"] == "DOWN":
        scores["SHORT"] += 4

    if context["sweep_context"].get("swept_below") or context["liquidity_pools"].get("sweep_lows"):
        scores["LONG"] += 4
    if context["sweep_context"].get("swept_above") or context["liquidity_pools"].get("sweep_highs"):
        scores["SHORT"] += 4

    if context["structure"].get("bos_up"):
        scores["LONG"] += 4
    else:
        scores["LONG"] -= 10
    if context["structure"].get("bos_down"):
        scores["SHORT"] += 4
    else:
        scores["SHORT"] -= 10

    shock_long = _detect_orderflow_shock(symbol, "LONG")
    shock_short = _detect_orderflow_shock(symbol, "SHORT")
    scores["LONG"] += shock_long["score_boost"]
    scores["SHORT"] += shock_short["score_boost"]
    scores["LONG"] += shock_long["penalty"]
    scores["SHORT"] += shock_short["penalty"]
    _driver_append(drivers["LONG"], "Orderflow imbalance", shock_long["aligned"])
    _driver_append(drivers["SHORT"], "Orderflow imbalance", shock_short["aligned"])

    oi_spike_long = _detect_oi_spike(symbol, timeframe, price, "LONG")
    oi_spike_short = _detect_oi_spike(symbol, timeframe, price, "SHORT")
    scores["LONG"] += oi_spike_long["score_boost"]
    scores["SHORT"] += oi_spike_short["score_boost"]
    _driver_append(drivers["LONG"], "Open interest spike", oi_spike_long["score_boost"] > 0)
    _driver_append(drivers["SHORT"], "Open interest spike", oi_spike_short["score_boost"] > 0)

    oi_bias = str(context["oi_momentum"].get("oi_bias", "neutral")).lower()
    if oi_bias in {"bullish_continuation", "short_squeeze"}:
        scores["LONG"] += 6
    if oi_bias in {"bearish_continuation", "longs_closing"}:
        scores["SHORT"] += 6

    liq_pressure = str(context["liquidation"].get("liq_pressure", "neutral")).lower()
    if liq_pressure == "bullish":
        scores["LONG"] += 4
    elif liq_pressure == "bearish":
        scores["SHORT"] += 4

    if context["vacuum"].get("vacuum_active"):
        vacuum_dir = str(context["vacuum"].get("vacuum_direction", "NONE")).upper()
        if vacuum_dir == "UP":
            scores["LONG"] += 5
            _driver_append(drivers["LONG"], "Liquidity vacuum", True)
        elif vacuum_dir == "DOWN":
            scores["SHORT"] += 5
            _driver_append(drivers["SHORT"], "Liquidity vacuum", True)

    regime_name = str(context["regime"].get("regime", "NORMAL")).upper()
    if regime_name in {"TRENDING_BULL", "BREAKOUT"}:
        scores["LONG"] += 4
    if regime_name in {"TRENDING_BEAR", "VOLATILE"}:
        scores["SHORT"] += 4

    cosmic_bias = str(context["planetary"].get("cosmic_bias", "NEUTRAL")).upper()
    if cosmic_bias == "BULLISH":
        scores["LONG"] += 2
    elif cosmic_bias == "BEARISH":
        scores["SHORT"] += 2

    vedic_quality = str(context["vedic_time"].get("timing_quality", "NEUTRAL")).upper()
    if vedic_quality == "GOOD":
        scores["LONG"] += 3
        scores["SHORT"] += 3
        _driver_append(drivers["LONG"], "Vedic timing influence", True)
        _driver_append(drivers["SHORT"], "Vedic timing influence", True)
    elif vedic_quality == "AVOID":
        scores["LONG"] -= 3
        scores["SHORT"] -= 3

    nak_bias = str(context["nak_cycle"].get("bias", "NEUTRAL")).upper()
    if nak_bias in {"TREND", "BREAKOUT", "EXPANSION", "MOMENTUM", "VICTORY", "POWER"}:
        scores["LONG"] += 2
        scores["SHORT"] += 2
        _driver_append(drivers["LONG"], "Nakshatra cycle", True)
        _driver_append(drivers["SHORT"], "Nakshatra cycle", True)
    elif nak_bias in {"TRAP", "REVERSAL", "DESTRUCTION"}:
        if micro_direction == "SHORT":
            scores["SHORT"] += 2
            _driver_append(drivers["SHORT"], "Nakshatra cycle", True)
        elif micro_direction == "LONG":
            scores["LONG"] += 2
            _driver_append(drivers["LONG"], "Nakshatra cycle", True)

    lunar_volatility = str(context["lunar"].get("lunar_volatility", "NORMAL")).upper()
    if lunar_volatility == "HIGH":
        scores["LONG"] += 2
        scores["SHORT"] += 2
    elif lunar_volatility == "LOW":
        scores["LONG"] -= 1
        scores["SHORT"] -= 1

    crowd_emotion = str(context["crowd"].get("emotion", "NEUTRAL")).upper()
    crowd_phase = str(context["crowd"].get("crowd_phase", "BALANCED")).upper()
    if crowd_emotion in {"PANIC", "FEAR"} and micro_direction == "LONG":
        scores["LONG"] += 2
    if crowd_emotion in {"GREED", "EUPHORIA"} and micro_direction == "SHORT":
        scores["SHORT"] += 2
    if micro_direction in {"LONG", "SHORT"} and crowd_phase in {"CAPITULATION", "BLOW_OFF", "MOMENTUM_CHASE"}:
        _driver_append(drivers[micro_direction], "Crowd psychology", micro_direction in drivers)

    if context["psychology"] >= 60:
        scores["LONG"] += 2
    elif context["psychology"] <= 40:
        scores["SHORT"] += 2

    volume_spike = volume > avg_volume
    if volume_spike:
        scores["LONG"] += 2
        scores["SHORT"] += 2

    pressure = str(pre_pump.get("orderflow_pressure", "NEUTRAL")).upper()
    if pressure == "BUYERS":
        scores["LONG"] += 4
        _driver_append(drivers["LONG"], "Pre-pump buyers", True)
    elif pressure == "SELLERS":
        scores["SHORT"] += 4
        _driver_append(drivers["SHORT"], "Pre-pump sellers", True)

    if bool(pre_pump.get("volume_accumulation")):
        scores["LONG"] += 2
        scores["SHORT"] += 2
        _driver_append(drivers["LONG"], "Volume accumulation", True)
        _driver_append(drivers["SHORT"], "Volume accumulation", True)

    if bool(pre_pump.get("oi_buildup")):
        scores["LONG"] += 2
        scores["SHORT"] += 2
        _driver_append(drivers["LONG"], "OI buildup", True)
        _driver_append(drivers["SHORT"], "OI buildup", True)

    if bool(pre_pump.get("volatility_squeeze")):
        scores["LONG"] += 2
        scores["SHORT"] += 2
        _driver_append(drivers["LONG"], "Volatility squeeze", True)
        _driver_append(drivers["SHORT"], "Volatility squeeze", True)

    if bool(pre_pump.get("all_aligned")):
        if pressure == "BUYERS":
            scores["LONG"] += int(pre_pump.get("pre_pump_score", 0))
            _driver_append(drivers["LONG"], "Pre-pump alignment", True)
        elif pressure == "SELLERS":
            scores["SHORT"] += int(pre_pump.get("pre_pump_score", 0))
            _driver_append(drivers["SHORT"], "Pre-pump alignment", True)
    else:
        if pressure == "BUYERS":
            scores["LONG"] += int(pre_pump.get("pre_pump_score", 0))
        elif pressure == "SELLERS":
            scores["SHORT"] += int(pre_pump.get("pre_pump_score", 0))

    vol_state = str(context["volatility_state"].get("state", "normal")).lower()
    if vol_state == "compressed" and not compression["volatility_expansion"]:
        scores["LONG"] -= 5
        scores["SHORT"] -= 5

    scores["LONG"] = max(0, min(MAX_SCORE, scores["LONG"]))
    scores["SHORT"] = max(0, min(MAX_SCORE, scores["SHORT"]))

    if scores["LONG"] >= scores["SHORT"]:
        direction = "LONG"
    else:
        direction = "SHORT"

    triggers_ok = sweep["triggered"] or compression["volatility_expansion"]
    orderflow_ok = shock_long["aligned"] if direction == "LONG" else shock_short["aligned"]
    volatility_ok = compression["volatility_expansion"]

    return {
        "direction": direction,
        "score": round(scores[direction], 2),
        "scores": scores,
        "drivers": drivers[direction],
        "sweep": sweep,
        "compression": compression,
        "oi_spike": oi_spike_long if direction == "LONG" else oi_spike_short,
        "orderflow_shock": shock_long if direction == "LONG" else shock_short,
        "pre_pump": pre_pump,
        "triggers_ok": triggers_ok,
        "orderflow_ok": orderflow_ok,
        "volatility_ok": volatility_ok,
    }


def generate_scalp_signal():
    candidates = []

    for coin in COINS:
        allowed, reason = _trade_allowed(coin)
        if not allowed:
            print(f"Scalp skipped {coin}: {reason}")
            continue

        for tf in SCALP_TIMEFRAMES:
            try:
                print(f"Scanning scalp {coin} {tf}")
                context = _collect_context(coin, tf)
                df = context["df"]
                if len(df) < 30:
                    continue

                scored = _score_signal(coin, tf, context)
                direction = scored["direction"]
                price = float(df["close"].iloc[-1])
                atr = max(float(df["atr"].iloc[-1]), 1e-9)

                if not scored["triggers_ok"]:
                    continue
                if not scored["orderflow_ok"]:
                    continue
                if not scored["volatility_ok"]:
                    continue

                sl, tp = _calc_scalp_levels(price, atr, direction, context["liquidity_pools"], context["liquidation"])
                rr = _safe_rr(price, tp, sl)
                expected_hold_minutes = _estimate_hold_minutes(df, price, tp, tf)
                confidence = calculate_confidence(scored["score"])
                confidence_value = float(confidence.get("confidence", 0.0))

                if expected_hold_minutes > SCALP_REJECT_HOLD_MIN:
                    continue
                if not (SCALP_TARGET_HOLD_MIN <= expected_hold_minutes <= SCALP_TARGET_HOLD_MAX):
                    continue
                if scored["score"] < SCALP_MIN_SCORE or confidence_value < SCALP_MIN_CONFIDENCE or rr < SCALP_MIN_RR:
                    continue

                chart_path = create_chart(df, coin, tf, price, sl, tp, chart_tag="scalp")
                cleanup_chart_images()

                tithi_text = explain_tithi(context["panchang"]["tithi"])
                signal_data = {
                    "signal_type": "SCALP",
                    "coin": coin,
                    "tf": tf,
                    "timeframe": tf,
                    "direction": direction,
                    "entry": price,
                    "tp": tp,
                    "sl": sl,
                    "score": round(scored["score"], 2),
                    "confidence": round(confidence_value, 2),
                    "risk_reward": round(rr, 2),
                    "expected_hold_minutes": expected_hold_minutes,
                    "expected_hold_time": f"{expected_hold_minutes} minutes",
                    "crowd_emotion": context["crowd"]["emotion"],
                    "crowd_phase": context["crowd"]["crowd_phase"],
                    "regime": context["regime"]["regime"],
                    "liquidity_event": context["liquidity_event"]["liquidity_event"],
                    "nakshatra_bias": context["nak_cycle"]["bias"],
                    "nakshatra_psychology": context["nak_cycle"]["psychology"],
                    "planetary_bias": context["planetary"]["cosmic_bias"],
                    "volatility_bias": context["planetary"]["volatility_bias"],
                    "vedic_time": context["vedic_time"]["current_period"],
                    "timing_quality": context["vedic_time"]["timing_quality"],
                    "lunar_volatility": context["lunar"]["lunar_volatility"],
                    "lunar_psychology": context["lunar"]["lunar_psychology"],
                    "orderflow_bias": scored["orderflow_shock"]["bias"],
                    "orderflow_imbalance": round(float(scored["orderflow_shock"].get("imbalance_ratio", 1.0)), 2),
                    "oi_change": scored["oi_spike"]["oi_change_pct"],
                    "oi_bias": scored["oi_spike"]["oi_bias"],
                    "pump_probability": scored["pre_pump"]["pump_probability"],
                    "orderflow_pressure": scored["pre_pump"]["orderflow_pressure"],
                    "pressure_strength": scored["pre_pump"]["pressure_strength"],
                    "volume_accumulation": bool(scored["pre_pump"]["volume_accumulation"]),
                    "oi_buildup": bool(scored["pre_pump"]["oi_buildup"]),
                    "volatility_squeeze": bool(scored["pre_pump"]["volatility_squeeze"]),
                    "liquidity_support": bool(scored["pre_pump"]["liquidity_support"]),
                    "liquidity_resistance": bool(scored["pre_pump"]["liquidity_resistance"]),
                    "liq_pressure": context["liquidation"]["liq_pressure"],
                    "liq_above": context["liquidation"]["liq_zone_above"],
                    "liq_below": context["liquidation"]["liq_zone_below"],
                    "vacuum_active": bool(context["vacuum"].get("vacuum_active")),
                    "vacuum_direction": context["vacuum"].get("vacuum_direction", "NONE"),
                    "pool_equal_highs": bool(scored["sweep"]["equal_highs"]),
                    "pool_equal_lows": bool(scored["sweep"]["equal_lows"]),
                    "volatility_expansion": bool(scored["compression"]["volatility_expansion"]),
                    "volatility_expansion_state": "expanding" if scored["compression"]["volatility_expansion"] else "compressed",
                    "market_structure": context["structure"].get("market_structure", "RANGE"),
                    "structure_bos_up": bool(context["structure"].get("bos_up")),
                    "structure_bos_down": bool(context["structure"].get("bos_down")),
                    "liquidity_above": context["liquidity_zones"].get("liquidity_above"),
                    "liquidity_below": context["liquidity_zones"].get("liquidity_below"),
                    "distance_to_liquidity": context["liquidity_pools"].get("distance_to_liquidity"),
                    "nakshatra": context["nakshatra_psy"]["name"],
                    "nakshatra_meaning": context["nakshatra_psy"]["meaning"],
                    "tithi": context["panchang"]["tithi"],
                    "tithi_text": tithi_text,
                    "moon_phase": round(context["moon_phase"], 4),
                    "signal_drivers": scored["drivers"],
                }
                message = _build_scalp_message(signal_data)
                candidates.append({
                    "score": signal_data["score"],
                    "message": message,
                    "chart": chart_path,
                    "data": signal_data,
                })
            except Exception as exc:
                print(f"scalp scan error {coin} {tf} {exc}")
            finally:
                time.sleep(0.1)

    candidates = sorted(candidates, key=lambda item: item["score"], reverse=True)
    selected = []
    for item in candidates:
        coin = item["data"]["coin"]
        allowed, _ = _trade_allowed(coin)
        if not allowed:
            continue
        selected.append(item)
        _mark_trade(coin)
        if len(selected) >= SCALP_MAX_PER_SCAN:
            break

    return selected
