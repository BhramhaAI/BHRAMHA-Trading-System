# -*- coding: utf-8 -*-
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd

from binance_http import BINANCE_HTTP_TIMEOUT, session
from indicators import add_indicators
from astrology_engine import get_astrology
from panchang_engine import get_panchang, explain_tithi
from nakshatra_psychology import (
    get_nakshatra_psychology,
    get_nakshatra_score_modifier,
    get_nakshatra_direction_bias,
)
from crowd_psychology_engine import analyze_crowd_psychology
from trend_engine import get_trend
from liquidity_engine import (
    detect_liquidity_sweep,
    detect_liquidity_event,
    detect_liquidity_zones,
    detect_liquidity_sweep_context,
)
from liquidity_pool_engine import detect_liquidity_pools
from volatility_engine import get_volatility_state
from volatility_expansion_engine import detect_volatility
from confidence_engine import calculate_confidence
from market_regime_engine import detect_market_regime
from planetary_engine import calculate_planetary_bias
from nakshatra_engine import get_nakshatra_market_bias
from vedic_time_engine import (
    get_current_nakshatra_context,
    get_muhurta_context,
    get_sun_transit_context,
    get_vedic_time_quality,
    get_vedic_trade_block_status,
    get_current_tithi_context,
)
from lunar_volatility_engine import predict_lunar_volatility
from hora_engine import get_current_hora
from chart_engine import create_chart, cleanup_chart_images
from signal_memory import is_duplicate
from psychology_engine import psychology_score
from binance_data import get_data
from smart_exit_engine import (smart_stop_loss, smart_take_profit,
                                calculate_scalp_tp1,
                                calculate_breakeven_sl,
                                apply_basis_adjustment,
                                apply_timeframe_tp_cap)
from orderflow_engine import analyze_orderflow
from open_interest_engine import detect_oi_shift
from oi_engine import detect_oi_momentum
from liquidation_engine import detect_liquidation_zones
from vacuum_engine import detect_liquidity_vacuum
from config import (
    COINS,
    TECH_MAX_BONUS,
    TIMEFRAMES,
    ENTRY_CONFIRMATION_ENABLED,
    VEDIC_HARD_BLOCK_ENABLED,
    HORA_SCORING_ENABLED,
    NAKSHATRA_SCORING_ENABLED,
    MUHURTA_SCORING_ENABLED,
    LUNAR_VOLATILITY_GATE_ENABLED,
    WIN_PATTERN_BONUS_ENABLED,
    LATE_ENTRY_FILTER_ENABLED,
    LATE_ENTRY_THRESHOLD_PCT,
    MIN_CANDLE_AGE_MINUTES,
    MOMENTUM_CANDLES_REQUIRED,
    ASTRO_MAX_BONUS,
    BASIS_ADJUSTMENT_ENABLED,
    ASTRO_MAX_PENALTY,
    NAKSHATRA_DIRECTION_BLOCK_ENABLED,
    RAHU_OVERRIDE_ENABLED,
    RAHU_OVERRIDE_MIN_SCORE,
    SESSION_SCORE_GATE_ENABLED,
    VYAVHAR_BLOCK_THRESHOLD,
    VYAVHAR_HARD_BLOCK_ENABLED,
    MIN_RR,
)
from structure_engine import detect_structure
from score_engine import score_candle, detect_low_volume_nodes, find_lvn_between
from adaptive_score_engine import get_adaptive_engine, apply_adaptive_weights
from orderblock_engine import analyze_ob_fvg, format_ob_fvg_for_telegram
from utils.binance_utils import get_open_interest, normalize_symbol
from logger import log_rejected_signal
from macro_engine import get_macro_score_adjustment, get_macro_summary
from fear_greed_engine import get_fg_score_adjustment, get_fg_summary
from whale_engine import analyze_whale_flow, get_whale_summary

signals = []
strength_levels = {
    "WEAK": 1,
    "GOOD": 2,
    "STRONG": 3,
    "HIGH": 4,
    "EXTREME": 5,
    "ULTRA": 6
}
# Deprecated: final score gating now uses adaptive_engine.get_score_threshold(regime_name).
MIN_SCORE = 95
MIN_CONFIDENCE = 95

MIN_STRENGTH = strength_levels["GOOD"]
BASE_SCORE = 70
MAX_SCORE = 98
BOS_PULLBACK_THRESHOLD = 0.30
LAF_LBF_ENABLED = True
LAF_LBF_SCORE_BOOST = 10
FUNDING_RATE_FILTER_ENABLED = True
FUNDING_RATE_CACHE_TTL = 300
FUNDING_RATE_LONG_POSITIVE = 0.0001
FUNDING_RATE_SHORT_NEGATIVE = -0.0001
FUNDING_RATE_SQUEEZE_NEGATIVE = -0.0003

_funding_rate_cache = {}
INDIA_TZ = ZoneInfo("Asia/Kolkata")

# Post-win cooldown: tracks coins that recently won — blocks re-entry for 1 scan cycle
# WIFUSDT won at 18:15 then bot re-entered twice at 19:00 and 19:15 → both losses
# Key = coin, Value = timestamp of win
_recent_win_cooldown: dict = {}
WIN_COOLDOWN_SECONDS = 400  # ~1 full scan cycle (SCAN_INTERVAL=300 + buffer)


def _has_open_trade(coin: str) -> bool:
    """
    Returns True if there is already an open (unresolved) trade for this coin.

    Checks TWO files in order:
    1. signals_log.csv  — updated immediately when signal fires (faster)
    2. trades_log.csv   — updated after Binance order placement (5-15s delay)

    ROOT CAUSE OF DUPLICATE BUG:
    The original guard only checked trades_log.csv. But trade_engine takes
    5-15 seconds to place orders and write to trades_log. A second scan cycle
    can start in that window — trades_log has no entry yet, so the guard
    passes and a duplicate signal fires (as happened with TRXUSDT).

    signals_log.csv gets result='OPEN' written the moment a signal is
    approved — before trade_engine even starts. So checking it first
    catches duplicates within the same scan cycle.
    """
    import os
    coin = str(coin).upper().strip()

    # ── Check 1: signals_log.csv (fast, written immediately on signal approval) ──
    try:
        signals_path = "signals_log.csv"
        if os.path.exists(signals_path):
            df = pd.read_csv(signals_path, on_bad_lines="skip")
            if not df.empty:
                df.columns = [str(c).lower().strip() for c in df.columns]
                if "coin" in df.columns and "result" in df.columns:
                    coin_rows = df[df["coin"].astype(str).str.upper() == coin]
                    if not coin_rows.empty:
                        open_rows = coin_rows[
                            coin_rows["result"].astype(str).str.upper() == "OPEN"
                        ]
                        if len(open_rows) > 0:
                            return True
    except Exception as exc:
        print(f"[OPEN TRADE CHECK] signals_log read error: {exc}")

    # ── Check 2: trades_log.csv (written after Binance order confirmed) ──
    try:
        trades_path = "trades_log.csv"
        if not os.path.exists(trades_path):
            return False

        df = pd.read_csv(trades_path)
        if df.empty:
            return False

        df.columns = [str(c).lower().strip() for c in df.columns]
        if "coin" not in df.columns:
            return False

        coin_trades = df[df["coin"].astype(str).str.upper() == coin]
        if coin_trades.empty:
            return False

        # Check by status column
        if "status" in coin_trades.columns:
            open_trades = coin_trades[
                coin_trades["status"].astype(str).str.upper().isin(["OPEN", "ACTIVE", "RUNNING", ""])
            ]
            if len(open_trades) > 0:
                return True

        # Fallback: no close_time = still open
        if "close_time" in coin_trades.columns:
            no_close = coin_trades[
                coin_trades["close_time"].isna() |
                (coin_trades["close_time"].astype(str).str.strip() == "") |
                (coin_trades["close_time"].astype(str).str.strip() == "nan")
            ]
            if len(no_close) > 0:
                return True

        return False

    except Exception as exc:
        print(f"[OPEN TRADE CHECK] trades_log read error: {exc} — allowing signal")
        return False


def _safe_rr(entry, tp, sl):
    reward = abs(tp - entry)
    risk = max(abs(entry - sl), 1e-9)
    return reward / risk


def _log_rejected_signal(data, reasons):
    payload = dict(data or {})
    payload["result"] = "REJECTED"
    payload["status"] = "REJECTED"
    payload["insight"] = " | ".join(str(reason) for reason in (reasons or []) if reason)
    try:
        log_rejected_signal(payload)
    except Exception as exc:
        print(f"Rejected signal log error: {exc}")


def _is_rahu_override_eligible(score_value, confidence_value, regime_name, current_period):
    return bool(
        RAHU_OVERRIDE_ENABLED
        and str(current_period).upper() == "RAHU KALAM"
        and float(score_value) >= float(RAHU_OVERRIDE_MIN_SCORE)
        and float(confidence_value) >= float(RAHU_OVERRIDE_MIN_SCORE)
        and str(regime_name).upper() in {"TRENDING_BULL", "TRENDING_BEAR"}
    )


def get_ist_hour():
    return datetime.now(INDIA_TZ).hour


def get_session_threshold(base_score=95):
    """
    Session-aware score thresholds based on real win rate data:
      London (14-18 IST):   69% win rate → threshold 92 (easiest, best session)
      Asia-London (10-14):  51% win rate → threshold 94
      NY Late (0-4 IST):    31% win rate → threshold 95
      NY Open (18-24 IST):  20% win rate → threshold 97 (very hard to pass)
      Asia Open (4-10 IST):  6% win rate → threshold 97
    """
    ist_hour = get_ist_hour()
    if 18 <= ist_hour < 24:
        return 97, "NY_OPEN"
    elif 14 <= ist_hour < 18:
        return 92, "LONDON"
    elif 10 <= ist_hour < 14:
        return 94, "ASIA_LONDON"
    elif 4 <= ist_hour < 10:
        return 97, "ASIA_OPEN"
    else:
        return 95, "NY_LATE"


def _detect_psychological_level(price: float, direction: str, tolerance_pct: float = 0.003) -> int:
    """
    Detect if price is at or near a major psychological (round number) level.
    Round numbers are magnets and reversal points in crypto — institutional
    orders cluster at these levels.

    Returns:
      +6  if price is AT a round number from above (SHORT setup — resistance)
      -6  if price is AT a round number from below (LONG setup — support)
       0  if not near any significant round number

    Levels checked per coin price range:
      > $10,000  → nearest $1,000 (BTC: $80k, $90k, $100k)
      > $1,000   → nearest $100  (ETH: $3,000, $4,000)
      > $100     → nearest $50   (SOL: $150, $200, $250)
      > $10      → nearest $5    (BNB: $500, LTC: $55, $60)
      > $1       → nearest $1    (XRP: $2, $3 | ADA: $1, $2)
      > $0.1     → nearest $0.1  (DOGE: $0.1, $0.2 | TRX: $0.30)
      > $0.01    → nearest $0.01 (small alts)
    """
    try:
        direction = str(direction).upper()
        price = float(price)
        if price <= 0:
            return 0

        # Determine round number grid based on price magnitude
        if price > 10_000:
            grid = 1_000.0
        elif price > 1_000:
            grid = 100.0
        elif price > 100:
            grid = 50.0
        elif price > 10:
            grid = 5.0
        elif price > 1:
            grid = 1.0
        elif price > 0.1:
            grid = 0.1
        elif price > 0.01:
            grid = 0.01
        else:
            return 0  # too small to matter

        # Find nearest round number
        nearest_round = round(price / grid) * grid
        distance_pct  = abs(price - nearest_round) / price

        if distance_pct > tolerance_pct:
            return 0  # not close enough to a round number

        # Price IS at a round number — is it resistance or support?
        # If price approached from below and stalled → SHORT setup (resistance)
        # If price approached from above and held → LONG setup (support)
        if direction == "SHORT":
            return 6   # round number acting as resistance = SHORT bonus
        else:
            return -6  # round number acting as support = LONG bonus (negative = caller adds to long)

    except Exception:
        return 0


def _detect_rsi_divergence(df, direction):
    """
    Detect RSI divergence — price and RSI moving in opposite directions.
    This is one of the highest-probability reversal signals in technical analysis.

    BEARISH divergence (for SHORT signals):
      Price makes a HIGHER high, but RSI makes a LOWER high.
      Means: buyers are exhausted — momentum is fading even as price climbs.
      This is the "parabolic top" signal from the Marios video.

    BULLISH divergence (for LONG signals):
      Price makes a LOWER low, but RSI makes a HIGHER low.
      Means: sellers are exhausted — momentum is recovering even as price falls.

    Logic:
      - Look at last 20 candles
      - Find the two most recent swing highs/lows
      - Compare price direction vs RSI direction between them
    """
    try:
        direction = str(direction).upper()
        if len(df) < 20:
            return False

        window = df.tail(20).copy()
        closes = pd.to_numeric(window["close"], errors="coerce").values
        highs  = pd.to_numeric(window["high"],  errors="coerce").values
        lows   = pd.to_numeric(window["low"],   errors="coerce").values
        rsi_vals = pd.to_numeric(window["rsi"], errors="coerce").values

        if direction == "SHORT":
            # Find two most recent swing highs (local maxima in 3-bar window)
            swing_high_idx = []
            for i in range(1, len(highs) - 1):
                if highs[i] > highs[i-1] and highs[i] > highs[i+1]:
                    swing_high_idx.append(i)

            if len(swing_high_idx) < 2:
                return False

            # Take the two most recent swing highs
            idx1, idx2 = swing_high_idx[-2], swing_high_idx[-1]

            price_higher_high = highs[idx2] > highs[idx1]
            rsi_lower_high    = rsi_vals[idx2] < rsi_vals[idx1]

            # Bearish divergence: price higher high + RSI lower high
            # Minimum RSI drop of 3 points to avoid noise
            rsi_drop = rsi_vals[idx1] - rsi_vals[idx2]
            return bool(price_higher_high and rsi_lower_high and rsi_drop >= 3.0)

        else:  # LONG
            # Find two most recent swing lows (local minima in 3-bar window)
            swing_low_idx = []
            for i in range(1, len(lows) - 1):
                if lows[i] < lows[i-1] and lows[i] < lows[i+1]:
                    swing_low_idx.append(i)

            if len(swing_low_idx) < 2:
                return False

            idx1, idx2 = swing_low_idx[-2], swing_low_idx[-1]

            price_lower_low = lows[idx2] < lows[idx1]
            rsi_higher_low  = rsi_vals[idx2] > rsi_vals[idx1]

            # Bullish divergence: price lower low + RSI higher low
            rsi_rise = rsi_vals[idx2] - rsi_vals[idx1]
            return bool(price_lower_low and rsi_higher_low and rsi_rise >= 3.0)

    except Exception:
        return False


def _detect_vwap_reclaim(df, direction):
    """
    Detect VWAP reclaim — price crosses back above/below VWAP after being on wrong side.
    This is an intraday momentum shift signal used by professional traders.

    LONG reclaim:
      Price was BELOW VWAP for at least 2 candles, then crosses BACK ABOVE it.
      Means: buyers are reclaiming control of the session's average price.
      Entry: as price reclaims VWAP from below.

    SHORT reclaim fail:
      Price tried to reclaim VWAP from below but FAILED — closed back below.
      Means: sellers defending VWAP aggressively.
      Entry: on the failed reclaim candle.

    Logic:
      - Look at last 5 candles
      - Check if price was below VWAP then crossed above (LONG)
      - Or was below VWAP, touched VWAP but closed below again (SHORT fail)
    """
    try:
        direction = str(direction).upper()
        if len(df) < 5:
            return False

        window = df.tail(5).copy()
        closes = pd.to_numeric(window["close"], errors="coerce").values
        highs  = pd.to_numeric(window["high"],  errors="coerce").values

        # Get VWAP — use column if available, else use rolling close mean as proxy
        if "vwap" in window.columns:
            vwap_vals = pd.to_numeric(window["vwap"], errors="coerce").values
        else:
            vwap_vals = pd.to_numeric(window["close"], errors="coerce").rolling(20).mean().values

        if any(v != v for v in vwap_vals):  # NaN check
            return False

        current_close  = closes[-1]
        prev_close     = closes[-2]
        prev2_close    = closes[-3]
        current_vwap   = vwap_vals[-1]
        prev_vwap      = vwap_vals[-2]
        current_high   = highs[-1]

        if direction == "LONG":
            # Bullish VWAP reclaim:
            # Previous 2 candles were below VWAP, current candle closes above
            was_below = prev_close < prev_vwap and prev2_close < vwap_vals[-3]
            now_above = current_close > current_vwap
            return bool(was_below and now_above)

        else:  # SHORT
            # Failed VWAP reclaim (bearish):
            # Previous candle was below VWAP, current candle HIGH touched/exceeded VWAP
            # but CLOSE failed back below VWAP — sellers defended it
            was_below      = prev_close < prev_vwap
            touched_vwap   = current_high >= current_vwap * 0.999  # within 0.1%
            closed_below   = current_close < current_vwap
            return bool(was_below and touched_vwap and closed_below)

    except Exception:
        return False


def _get_candle_age_minutes(df):
    try:
        last_candle = df.index[-1]
        # Handle all possible timestamp formats
        if hasattr(last_candle, 'timestamp'):
            candle_ts = last_candle.timestamp()
        elif isinstance(last_candle, (int, float)):
            # Unix ms from Binance
            if last_candle > 1e10:
                candle_ts = last_candle / 1000.0
            else:
                candle_ts = float(last_candle)
        else:
            candle_ts = pd.Timestamp(
                last_candle, tz='UTC').timestamp()
        now_ts = datetime.now(timezone.utc).timestamp()
        age = (now_ts - candle_ts) / 60.0
        if age < 0 or age > 10:
            return 999
        return age
    except Exception:
        return 999


def _momentum_alignment_score(df, direction):
    closes = pd.to_numeric(df["close"], errors="coerce").dropna().tail(4).tolist()
    if len(closes) < 4:
        return False
    moves = [closes[i] > closes[i - 1] for i in range(1, len(closes))] if direction == "LONG" else [closes[i] < closes[i - 1] for i in range(1, len(closes))]
    return sum(1 for move in moves if move) >= int(MOMENTUM_CANDLES_REQUIRED)


def _entry_timing_block(df, direction):
    direction = str(direction).upper()
    close = float(df["close"].iloc[-1])
    rsi = float(df["rsi"].iloc[-1])
    vwap = float(df.get("vwap", df["close"]).iloc[-1])
    bb_upper = float(df.get("bb_upper", df["close"]).iloc[-1])
    bb_lower = float(df.get("bb_lower", df["close"]).iloc[-1])

    far_from_vwap = abs(close - vwap) / max(vwap, 1e-9) > 0.01
    near_upper = close >= (bb_upper * 0.998)
    near_lower = close <= (bb_lower * 1.002)

    if direction == "LONG" and (rsi > 70 or (far_from_vwap and close > vwap) or near_upper):
        return True
    if direction == "SHORT" and (rsi < 30 or (far_from_vwap and close < vwap) or near_lower):
        return True
    return False


def _last_swings_local(df, lookback=3):
    highs = df["high"].tolist()
    lows = df["low"].tolist()
    swing_highs = []
    swing_lows = []

    for i in range(lookback, len(df) - lookback):
        high = float(highs[i])
        low = float(lows[i])
        if all(high >= x for x in highs[i - lookback:i]) and all(high >= x for x in highs[i + 1:i + 1 + lookback]):
            swing_highs.append((i, high))
        if all(low <= x for x in lows[i - lookback:i]) and all(low <= x for x in lows[i + 1:i + 1 + lookback]):
            swing_lows.append((i, low))

    return swing_highs, swing_lows


def _get_bos_pullback_state(df, direction, threshold=BOS_PULLBACK_THRESHOLD):
    direction = str(direction).upper()
    structure_df = df.tail(160).copy()
    if len(structure_df) < 20:
        return {"applicable": False, "pullback_met": True}

    swing_highs, swing_lows = _last_swings_local(structure_df, lookback=3)
    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return {"applicable": False, "pullback_met": True}

    breakout_idx = None
    breakout_level = None
    if direction == "LONG":
        breakout_level = float(swing_highs[-2][1])
        for i in range(swing_highs[-2][0] + 1, len(structure_df)):
            if float(structure_df["close"].iloc[i]) > breakout_level:
                breakout_idx = i
                break
    elif direction == "SHORT":
        breakout_level = float(swing_lows[-2][1])
        for i in range(swing_lows[-2][0] + 1, len(structure_df)):
            if float(structure_df["close"].iloc[i]) < breakout_level:
                breakout_idx = i
                break
    else:
        return {"applicable": False, "pullback_met": True}

    if breakout_idx is None:
        return {"applicable": False, "pullback_met": True}

    breakout_candle = structure_df.iloc[breakout_idx]
    breakout_high = float(breakout_candle["high"])
    breakout_low = float(breakout_candle["low"])
    breakout_range = max(breakout_high - breakout_low, 1e-9)
    required_pullback = breakout_range * max(float(threshold), 0.0)

    after_breakout = structure_df.iloc[breakout_idx + 1:]
    if direction == "LONG":
        trigger_price = breakout_high - required_pullback
        pullback_met = len(after_breakout) > 0 and float(after_breakout["low"].min()) <= trigger_price
    else:
        trigger_price = breakout_low + required_pullback
        pullback_met = len(after_breakout) > 0 and float(after_breakout["high"].max()) >= trigger_price

    return {
        "applicable": True,
        "pullback_met": pullback_met,
        "breakout_idx": breakout_idx,
        "breakout_level": breakout_level,
        "breakout_high": breakout_high,
        "breakout_low": breakout_low,
        "breakout_range": breakout_range,
        "required_pullback": required_pullback,
        "trigger_price": trigger_price,
    }


def _get_funding_rate(symbol):
    if not FUNDING_RATE_FILTER_ENABLED:
        return None

    symbol = normalize_symbol(symbol)
    now = time.time()
    cached = _funding_rate_cache.get(symbol)
    if cached and (now - float(cached.get("timestamp", 0))) < FUNDING_RATE_CACHE_TTL:
        return cached.get("funding_rate")

    try:
        response = session.get(
            "https://fapi.binance.com/fapi/v1/fundingRate",
            params={"symbol": symbol, "limit": 1},
            timeout=BINANCE_HTTP_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
        if not payload:
            return None
        funding_rate = float(payload[0].get("fundingRate"))
        _funding_rate_cache[symbol] = {
            "funding_rate": funding_rate,
            "timestamp": now,
        }
        return funding_rate
    except Exception:
        return None


def _get_funding_divergence(df, funding_rate):
    if funding_rate is None or len(df) < 4:
        return {"long_boost": 0, "short_boost": 0, "reason": None}

    recent = df.tail(3).copy()
    opens = recent["open"].astype(float).tolist()
    closes = recent["close"].astype(float).tolist()
    highs = recent["high"].astype(float).tolist()
    lows = recent["low"].astype(float).tolist()

    all_bearish = all(close_ < open_ for open_, close_ in zip(opens, closes))
    all_bullish = all(close_ > open_ for open_, close_ in zip(opens, closes))
    price_change = float(closes[-1] - closes[0])
    total_range = max(max(highs) - min(lows), 1e-9)
    slow_rise = all_bullish and price_change > 0 and (abs(price_change) / max(abs(closes[-1]), 1e-9) <= 0.003 or abs(price_change) <= total_range * 0.35)

    if all_bearish and funding_rate > FUNDING_RATE_LONG_POSITIVE:
        return {"long_boost": 7, "short_boost": 0, "reason": "fake_breakdown"}
    if slow_rise and funding_rate < FUNDING_RATE_SQUEEZE_NEGATIVE:
        return {"long_boost": 7, "short_boost": 0, "reason": "short_squeeze"}
    if all_bullish and funding_rate < FUNDING_RATE_SHORT_NEGATIVE:
        return {"long_boost": 0, "short_boost": 7, "reason": "fake_breakout"}

    return {"long_boost": 0, "short_boost": 0, "reason": None}


def _find_retested_level(swings, tolerance, breakout_idx, min_tests=2):
    prior_swings = [(idx, float(level)) for idx, level in swings if idx < breakout_idx]
    if len(prior_swings) < min_tests:
        return None

    best_level = None
    best_tests = 0
    best_last_idx = -1

    for idx, level in prior_swings:
        tests = [s_idx for s_idx, s_level in prior_swings if abs(float(s_level) - level) <= tolerance]
        test_count = len(tests)
        if test_count < min_tests:
            continue

        last_idx = max(tests)
        if test_count > best_tests or (test_count == best_tests and last_idx > best_last_idx):
            best_level = level
            best_tests = test_count
            best_last_idx = last_idx

    if best_level is None:
        return None

    return {
        "level": best_level,
        "tests": best_tests,
        "last_test_idx": best_last_idx,
    }


def _detect_laf_lbf(df):
    if not LAF_LBF_ENABLED:
        return {"detected": False}

    window = df.tail(50).copy()
    if len(window) < 12:
        return {"detected": False}

    swing_highs, swing_lows = _last_swings_local(window, lookback=2)
    avg_volume = float(window["volume"].mean())
    avg_range = float((window["high"] - window["low"]).tail(50).mean())

    for breakout_idx in range(len(window) - 3, len(window)):
        candle = window.iloc[breakout_idx]
        candle_volume = float(candle["volume"])
        if candle_volume <= (avg_volume * 1.2):
            continue

        price_anchor = max(abs(float(candle["close"])), 1e-9)
        tolerance = max(avg_range * 0.35, price_anchor * 0.001)

        resistance = _find_retested_level(swing_highs, tolerance, breakout_idx, min_tests=2)
        if resistance:
            level = float(resistance["level"])
            if float(candle["high"]) > level and float(candle["close"]) < level:
                return {
                    "detected": True,
                    "signal_type": "LAF",
                    "direction": "SHORT",
                    "key_level": level,
                    "breakout_high": float(candle["high"]),
                    "breakout_low": float(candle["low"]),
                    "breakout_close": float(candle["close"]),
                    "breakout_volume": candle_volume,
                    "avg_volume": avg_volume,
                    "level_tests": int(resistance["tests"]),
                }

        support = _find_retested_level(swing_lows, tolerance, breakout_idx, min_tests=2)
        if support:
            level = float(support["level"])
            if float(candle["low"]) < level and float(candle["close"]) > level:
                return {
                    "detected": True,
                    "signal_type": "LBF",
                    "direction": "LONG",
                    "key_level": level,
                    "breakout_high": float(candle["high"]),
                    "breakout_low": float(candle["low"]),
                    "breakout_close": float(candle["close"]),
                    "breakout_volume": candle_volume,
                    "avg_volume": avg_volume,
                    "level_tests": int(support["tests"]),
                }

    return {"detected": False}


def estimate_trade_duration(data, entry, tp, timeframe):
    distance = abs(tp - entry)

    if "ATR" in data.columns:
        atr = data["ATR"].iloc[-1]
    else:
        atr = data["atr"].iloc[-1]

    if atr <= 0:
        return {
            "expected_candles": 0,
            "expected_hold_time": "Unknown",
        }

    candles = distance / atr
    candles = max(candles, 1)

    timeframe_minutes = {
        "5m": 5,
        "15m": 15,
        "1h": 60,
        "4h": 240,
    }.get(timeframe)

    if not timeframe_minutes:
        return {
            "expected_candles": round(candles, 2),
            "expected_hold_time": "Unknown",
        }

    total_minutes = candles * timeframe_minutes
    if total_minutes < 60:
        readable_time = f"{round(total_minutes)} minutes"
    elif total_minutes < 1440:
        readable_time = f"{round(total_minutes / 60, 1)} hours"
    else:
        readable_time = f"{round(total_minutes / 1440, 1)} days"

    return {
        "expected_candles": round(candles, 2),
        "expected_hold_time": readable_time,
    }


def generate_signal():

    signals = []
    short_rejected_trend = 0
    short_rejected_regime = 0
    rejected_low_volatility = 0
    rejected_structure = 0
    rejected_liquidity = 0
    rejected_entry_timing = 0
    rejected_rr = 0
    rejected_mtf = 0
    rejected_cooldown = 0
    rejected_bos_pullback = 0
    adaptive_engine = get_adaptive_engine()

    # In-memory set tracking coins that already got a signal approved THIS scan cycle.
    # This is the real fix for the AVAXUSDT/TRXUSDT duplicate bug:
    # Both 5m and 15m scan in the SAME generate_signal() call.
    # When 5m approves AVAXUSDT, the 15m scan starts 0.2s later —
    # BEFORE trade_engine writes to any CSV. So file-based checks all fail.
    # This in-memory set updates instantly when a signal is approved.
    approved_this_scan: set = set()

    print(f"[CONFIG] MIN_RR set to: {MIN_RR}")
    print(f"[CONFIG] LONG min score: 95")
    print(f"[CONFIG] SHORT min score: 95")
    print(f"[CONFIG] Hard blocks active: HIGH_LUNAR, SATURN_HORA, NEUTRAL_HORA, ASIA_OPEN, NY_LATE, ASHWINI, DHANISHTA, BHARANI, MRIGASHIRA, PUNARVASU")

    for coin in COINS:
        coin = normalize_symbol(coin)

        for tf in TIMEFRAMES:

            try:
                print(f"Scanning {coin} {tf}")

                # ── IN-SCAN DUPLICATE GUARD ───────────────────────────────
                # Blocks same coin from firing on both 5m AND 15m in same scan.
                # File-based checks (signals_log, trades_log) are too slow —
                # trade_engine hasn't written yet when 15m scan starts.
                if coin in approved_this_scan:
                    print(f"[SCAN GUARD] {coin} already approved this scan cycle — skipping {tf}")
                    continue

                # ── POST-WIN COOLDOWN GUARD ───────────────────────────────
                # Blocks re-entry on a coin that recently won.
                # WIFUSDT won at 18:15 then bot re-entered twice at 19:00/19:15 — both losses.
                import time as _time_mod
                _win_ts = _recent_win_cooldown.get(coin)
                if _win_ts and (_time_mod.time() - _win_ts) < WIN_COOLDOWN_SECONDS:
                    _remaining = int(WIN_COOLDOWN_SECONDS - (_time_mod.time() - _win_ts))
                    print(f"[WIN COOLDOWN] {coin} recently won — {_remaining}s cooldown, skipping {tf}")
                    continue

                # ── OPEN TRADE GUARD ──────────────────────────────────────
                # Skip this coin entirely if a trade is already running on it.
                # Prevents double-entries on the same ticker.
                if _has_open_trade(coin):
                    print(f"[OPEN TRADE] {coin} already has an open position — skipping signal")
                    continue

                df = get_data(symbol=coin, interval=tf)
                if df is None or df.empty:
                    print(f"[API] Binance timeout — skipping {coin}")
                    continue

                df = add_indicators(df)
                regime = detect_market_regime(df)
                regime_name = str(regime.get("regime", "NORMAL")).upper()
                regime_score = regime.get("regime_score", 50)
                regime_note = str(regime.get("note", ""))
                liq = detect_liquidity_sweep(df)
                liquidity_event = detect_liquidity_event(df)["liquidity_event"]
                liquidity_zones = detect_liquidity_zones(df)
                sweep_context = detect_liquidity_sweep_context(df)
                liquidity_pools = detect_liquidity_pools(df)
                structure = detect_structure(df)
                vol_expansion = detect_volatility(df)
                df_1h = get_data(symbol=coin, interval="1h")
                if df_1h is None or df_1h.empty:
                    print(f"[API] Binance timeout — skipping {coin}")
                    continue
                df_1h = add_indicators(df_1h)

                df_4h = get_data(symbol=coin, interval="4h")
                if df_4h is None or df_4h.empty:
                    print(f"[API] Binance timeout — skipping {coin}")
                    continue
                df_4h = add_indicators(df_4h)

                trend_1h = get_trend(df_1h)
                trend_local = get_trend(df)
                trend_4h = get_trend(df_4h)

                rsi = df["rsi"].iloc[-1]
                macd = df["macd"].iloc[-1]
                price = df["close"].iloc[-1]
                atr = df["atr"].iloc[-1]
                volume = df["volume"].iloc[-1]
                avg_volume = df["volume"].mean()
                volume_spike = volume > avg_volume
                oi = get_open_interest(coin)
                if oi is None:
                    print(f"Skipping OI analysis for {coin}")
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
                    oi_context = {
                        "oi_change_5": 0.0,
                        "oi_change_20": 0.0,
                        "price_change_5": 0.0,
                        "price_change_20": 0.0,
                        "oi_bias": "neutral",
                        "oi_strength": "low",
                    }
                else:
                    oi_shift = detect_oi_shift(coin)
                    oi_context = detect_oi_momentum(coin)

                nakshatra, moon_phase, vol_factor = get_astrology()
                volatility = get_volatility_state(df, vol_factor)
                panchang = get_panchang()
                psy = get_nakshatra_psychology(nakshatra)
                tithi_text = explain_tithi(panchang["tithi"])
                planetary = calculate_planetary_bias()
                nak_cycle = get_nakshatra_market_bias()
                nakshatra_context = get_current_nakshatra_context()
                vedic_time = get_vedic_time_quality()
                vedic_block_status = get_vedic_trade_block_status()
                muhurta_context = get_muhurta_context()
                sun_transit = get_sun_transit_context()
                tithi_context = get_current_tithi_context()
                lunar_cycle = predict_lunar_volatility()
                hora_context = get_current_hora()

                psych = psychology_score(df)
                crowd = analyze_crowd_psychology(df)
                momentum_lookback = 5 if len(df) > 5 else 1
                momentum = price - df["close"].iloc[-momentum_lookback]
                momentum_threshold = max(atr * 0.25, 1e-9)

                # --- SCORING REBALANCE ---
                # 1. Get base technical score
                long_base = score_candle(df, "LONG")
                short_base = score_candle(df, "SHORT")

                # 2. Apply adaptive learning
                long_adaptive = apply_adaptive_weights(long_base["breakdown"], adaptive_engine)
                short_adaptive = apply_adaptive_weights(short_base["breakdown"], adaptive_engine)

                # 3. Set base scores
                score = long_adaptive["adjusted_score"]
                short_score = short_adaptive["adjusted_score"]

                # 4. Initialize bonus/penalty collectors
                tech_bonus_long, tech_bonus_short = 0, 0
                astro_bonus_long, astro_bonus_short = 0, 0
                astro_penalty_long, astro_penalty_short = 0, 0
                tech_reasons_long, tech_reasons_short = [], []
                astro_bonus_reasons_long, astro_bonus_reasons_short = [], []
                astro_penalty_reasons_long, astro_penalty_reasons_short = [], []

                # 5. Analyze Order Blocks & FVG for bonuses
                ob_long = analyze_ob_fvg(df, "LONG")
                ob_short = analyze_ob_fvg(df, "SHORT")

                # --- TECHNICAL BONUSES (65% weight) ---
                trends = [trend_local, trend_1h, trend_4h]
                if trends.count("UP") == 3:
                    tech_bonus_long += 15
                    tech_reasons_long.append("MTF 3/3 aligned")
                elif trends.count("UP") == 2:
                    tech_bonus_long += 8
                    tech_reasons_long.append("MTF 2/3 aligned")
                elif trends.count("UP") == 1:
                    astro_penalty_long -= 5
                    astro_penalty_reasons_long.append("MTF 1/3 aligned (-5)")
                elif trends.count("UP") == 0:
                    astro_penalty_long -= 15
                    astro_penalty_reasons_long.append("MTF 0/3 aligned (-15)")

                if trends.count("DOWN") == 3:
                    tech_bonus_short += 15
                    tech_reasons_short.append("MTF 3/3 aligned")
                elif trends.count("DOWN") == 2:
                    tech_bonus_short += 8
                    tech_reasons_short.append("MTF 2/3 aligned")
                elif trends.count("DOWN") == 1:
                    astro_penalty_short -= 5
                    astro_penalty_reasons_short.append("MTF 1/3 aligned (-5)")
                elif trends.count("DOWN") == 0:
                    astro_penalty_short -= 15
                    astro_penalty_reasons_short.append("MTF 0/3 aligned (-15)")

                if bool(liquidity_pools.get("sweep_lows")) or bool(sweep_context.get("swept_below")):
                    tech_bonus_long += 8
                    tech_reasons_long.append("Liquidity grab")
                if bool(liquidity_pools.get("sweep_highs")) or bool(sweep_context.get("swept_above")):
                    tech_bonus_short += 8
                    tech_reasons_short.append("Liquidity grab")

                if str(liquidity_event).strip() == "Stop Hunt":
                    tech_bonus_long += 6
                    tech_bonus_short += 6
                    tech_reasons_long.append("Stop hunt")
                    tech_reasons_short.append("Stop hunt")

                if bool(structure.get("bos_up")):
                    tech_bonus_long += 8
                    tech_reasons_long.append("BOS confirmed")
                if bool(structure.get("bos_down")):
                    tech_bonus_short += 8
                    tech_reasons_short.append("BOS confirmed")

                recent_volumes = df['volume'].tail(10).tolist()
                if volume > (pd.Series(recent_volumes).mean() * 1.5):
                    tech_bonus_long += 8
                    tech_bonus_short += 8
                    tech_reasons_long.append("Strong volume")
                    tech_reasons_short.append("Strong volume")

                vols = df['volume'].tail(3).tolist()
                if len(vols) == 3 and vols[2] > vols[1] > vols[0]:
                    tech_bonus_long += 5
                    tech_bonus_short += 5
                    tech_reasons_long.append("Volume acceleration")
                    tech_reasons_short.append("Volume acceleration")

                adx_col = df.get('adx')
                adx_val = float(adx_col.iloc[-1]) if adx_col is not None else 0.0
                if adx_val > 40:
                    tech_bonus_long += 8
                    tech_bonus_short += 8
                    tech_reasons_long.append("ADX > 40")
                    tech_reasons_short.append("ADX > 40")
                elif adx_val > 30:
                    tech_bonus_long += 5
                    tech_bonus_short += 5
                    tech_reasons_long.append("ADX > 30")
                    tech_reasons_short.append("ADX > 30")

                oi_bias_text = str(oi_context.get("oi_bias", "neutral")).lower()
                if oi_bias_text in {"bullish_continuation", "short_squeeze"}:
                    tech_bonus_long += 10
                    tech_reasons_long.append("OI confirms direction")
                if oi_bias_text in {"bearish_continuation", "longs_closing"}:
                    tech_bonus_short += 10
                    tech_reasons_short.append("OI confirms direction")

                if _detect_rsi_divergence(df, "LONG"):
                    tech_bonus_long += 6
                    tech_reasons_long.append("RSI Divergence")
                if _detect_rsi_divergence(df, "SHORT"):
                    tech_bonus_short += 6
                    tech_reasons_short.append("RSI Divergence")

                if _detect_vwap_reclaim(df, "LONG"):
                    tech_bonus_long += 5
                    tech_reasons_long.append("VWAP Reclaim")
                if _detect_vwap_reclaim(df, "SHORT"):
                    tech_bonus_short += 5
                    tech_reasons_short.append("VWAP Reclaim (failed)")

                # Psychological level detection — round numbers are real S/R in crypto
                # Called independently for both directions — no direction variable needed yet
                _psych_short = _detect_psychological_level(price, "SHORT")
                _psych_long  = _detect_psychological_level(price, "LONG")
                if _psych_short > 0:
                    tech_bonus_short += _psych_short
                    tech_reasons_short.append(f"Psychological level (round ${price:.4g})")
                if _psych_long < 0:
                    tech_bonus_long += abs(_psych_long)
                    tech_reasons_long.append(f"Psychological support (round ${price:.4g})")

                orderflow = analyze_orderflow(coin)
                if orderflow["bias"] == "BUYERS":
                    tech_bonus_long += 8
                    tech_reasons_long.append("Orderflow Buyers")
                elif orderflow["bias"] == "SELLERS":
                    tech_bonus_short += 8
                    tech_reasons_short.append("Orderflow Sellers")

                if ob_long['price_in_ob'] or ob_long['ob_score'] > 0:
                    tech_bonus_long += 10
                    tech_reasons_long.append("Order Block near")
                if ob_short['price_in_ob'] or ob_short['ob_score'] > 0:
                    tech_bonus_short += 10
                    tech_reasons_short.append("Order Block near")

                if ob_long['price_in_fvg'] or ob_long['fvg_score'] > 0:
                    tech_bonus_long += 8
                    tech_reasons_long.append("FVG near")
                if ob_short['price_in_fvg'] or ob_short['fvg_score'] > 0:
                    tech_bonus_short += 8
                    tech_reasons_short.append("FVG near")

                tech_bonus_long = min(tech_bonus_long, TECH_MAX_BONUS)
                tech_bonus_short = min(tech_bonus_short, TECH_MAX_BONUS)

                # --- ASTRO BONUSES (35% advisory) ---
                cosmic_bias = str(planetary.get("cosmic_bias", "NEUTRAL")).upper()
                if cosmic_bias in {"BULLISH", "BUY"}:
                    astro_bonus_long += 2
                    astro_bonus_reasons_long.append(f"Cosmic Bias: {cosmic_bias}")
                elif cosmic_bias in {"BEARISH", "SELL"}:
                    astro_bonus_short += 2
                    astro_bonus_reasons_short.append(f"Cosmic Bias: {cosmic_bias}")

                timing_quality = str(vedic_time.get("timing_quality", "")).upper()
                if timing_quality == "AVOID":
                    if vedic_time.get("current_period") == "GULIKA":
                        astro_penalty_long -= 5
                        astro_penalty_short -= 5
                        astro_penalty_reasons_long.append("Gulika period")
                        astro_penalty_reasons_short.append("Gulika period")
                elif timing_quality in {"GOOD", "FAVORABLE"}:
                    astro_bonus_long += 2
                    astro_bonus_short += 2
                    astro_bonus_reasons_long.append(f"Vedic Quality: {timing_quality}")
                    astro_bonus_reasons_short.append(f"Vedic Quality: {timing_quality}")

                hora_planet = str(hora_context.get("hora_planet", "NEUTRAL")).upper()
                if HORA_SCORING_ENABLED:
                    # FIX v2: Hora scoring is now regime-aware.
                    # Jupiter/Sun/Venus in a BEAR or RANGING market should NOT
                    # boost LONGs — the data showed all 3 wins were Jupiter Hora
                    # but so were many losses.  Now Jupiter only helps LONG in
                    # TRENDING_BULL or BREAKOUT; in other regimes it's neutral.
                    in_bull_regime = regime_name in {"TRENDING_BULL", "BREAKOUT"}
                    in_bear_regime = regime_name in {"TRENDING_BEAR", "RANGING", "SIDEWAYS"}

                    if hora_planet == "SUN":
                        if in_bull_regime:
                            astro_bonus_long += 6
                            astro_bonus_reasons_long.append("Sun Hora (bull regime)")
                        elif in_bear_regime:
                            astro_bonus_short += 3
                            astro_bonus_reasons_short.append("Sun Hora (bear/ranging — short favoured)")
                        else:
                            astro_bonus_long += 3
                            astro_bonus_reasons_long.append("Sun Hora")
                    elif hora_planet == "JUPITER":
                        if in_bull_regime:
                            astro_bonus_long += 8
                            astro_bonus_reasons_long.append("Jupiter Hora (bull regime)")
                        elif in_bear_regime:
                            # Jupiter in a bear/ranging market = false hope trap
                            astro_penalty_long -= 4
                            astro_penalty_reasons_long.append("Jupiter Hora in bear/ranging — LONG trap risk")
                            astro_bonus_short += 3
                            astro_bonus_reasons_short.append("Jupiter Hora (bear/ranging — short edge)")
                        else:
                            astro_bonus_long += 4
                            astro_bonus_reasons_long.append("Jupiter Hora (neutral regime)")
                    elif hora_planet == "VENUS":
                        if in_bull_regime:
                            astro_bonus_long += 6
                            astro_bonus_reasons_long.append("Venus Hora (bull regime)")
                        else:
                            astro_bonus_long += 2
                            astro_bonus_short += 2
                            astro_bonus_reasons_long.append("Venus Hora")
                            astro_bonus_reasons_short.append("Venus Hora")
                    elif hora_planet == "MARS":
                        astro_bonus_short += 6
                        astro_bonus_reasons_short.append("Mars Hora")
                        if in_bear_regime:
                            astro_bonus_short += 3
                            astro_bonus_reasons_short.append("Mars Hora + bear regime")
                    elif hora_planet == "SATURN":
                        astro_penalty_long -= 3
                        astro_bonus_short += 7
                        astro_penalty_reasons_long.append("Saturn Hora")
                        astro_bonus_reasons_short.append("Saturn Hora")
                    elif hora_planet == "MOON":
                        astro_penalty_long -= 1
                        astro_penalty_short -= 1
                        astro_penalty_reasons_long.append("Moon Hora")
                        astro_penalty_reasons_short.append("Moon Hora")
                    elif hora_planet == "MERCURY":
                        astro_penalty_long -= 2
                        astro_penalty_short -= 2
                        astro_penalty_reasons_long.append("Mercury Hora")
                        astro_penalty_reasons_short.append("Mercury Hora")

                sun_score_adjustment = 0
                sun_sign = str(sun_transit.get("sun_sign", "UNKNOWN")).upper()
                sun_sign_bias = str(sun_transit.get("sun_sign_bias", "NEUTRAL")).upper()
                sun_transit_effect = str(sun_transit.get("sun_transit_effect", "neutral"))
                if sun_sign in {"ARIES", "LEO", "SAGITTARIUS"}: # Fire
                    sun_score_adjustment = 4
                    astro_bonus_long += 4
                    astro_bonus_reasons_long.append(f"Sun in {sun_sign}")
                elif sun_sign in {"CAPRICORN", "TAURUS", "VIRGO"}: # Earth
                    sun_score_adjustment = 4
                    astro_bonus_short += 4
                    astro_bonus_reasons_short.append(f"Sun in {sun_sign}")
                elif sun_sign in {"GEMINI", "LIBRA", "AQUARIUS"}: # Air
                    sun_score_adjustment = -1
                    astro_penalty_long -= 1
                    astro_penalty_short -= 1
                    astro_penalty_reasons_long.append(f"Sun in {sun_sign}")
                    astro_penalty_reasons_short.append(f"Sun in {sun_sign}")
                elif sun_sign in {"CANCER", "SCORPIO", "PISCES"}: # Water
                    sun_score_adjustment = -1
                    astro_penalty_long -= 1
                    astro_penalty_short -= 1
                    astro_penalty_reasons_long.append(f"Sun in {sun_sign}")
                    astro_penalty_reasons_short.append(f"Sun in {sun_sign}")

                abhijit_active = bool(muhurta_context.get("abhijit_active", False))
                reversal_warning = bool(muhurta_context.get("reversal_warning", False))
                if MUHURTA_SCORING_ENABLED:
                    if abhijit_active:
                        astro_bonus_long += 10
                        astro_bonus_short += 10
                        astro_bonus_reasons_long.append("Abhijit Muhurta")
                        astro_bonus_reasons_short.append("Abhijit Muhurta")
                    if reversal_warning:
                        astro_penalty_long -= 2
                        astro_penalty_short -= 2
                        astro_penalty_reasons_long.append("Sandhya Kaal")
                        astro_penalty_reasons_short.append("Sandhya Kaal")

                nak_bias = str(nak_cycle.get("bias", "NEUTRAL")).upper()
                if nak_bias in {"BULLISH", "UP"}:
                    astro_bonus_long += 2
                    astro_bonus_reasons_long.append(f"Nakshatra Bias: {nak_bias}")
                elif nak_bias in {"BEARISH", "DOWN"}:
                    astro_bonus_short += 2
                    astro_bonus_reasons_short.append(f"Nakshatra Bias: {nak_bias}")
                if NAKSHATRA_DIRECTION_BLOCK_ENABLED:
                    if nak_bias == "DESTRUCTION":
                        astro_penalty_short -= 3
                        astro_penalty_reasons_short.append("Nakshatra: DESTRUCTION")
                    elif nak_bias == "VICTORY":
                        astro_bonus_short += 5
                        astro_bonus_reasons_short.append("Nakshatra: VICTORY")

                nakshatra_type = str(nakshatra_context.get("nakshatra_type", "OTHER")).upper()
                stop_hunt_risk = str(nakshatra_context.get("stop_hunt_risk", "NORMAL")).upper()
                signal_notes = []
                if sun_sign_bias in {"VOLATILITY_WARNING", "REVERSAL_WARNING"}:
                    signal_notes.append(f"Sun in {sun_sign}: {sun_transit_effect}")
                reversal_warning = reversal_warning or bool(sun_transit.get("reversal_warning", False))
                if NAKSHATRA_SCORING_ENABLED:
                    # FIX v2: Direction-aware nakshatra scoring.
                    # Dhruva in a ranging/sideways market is NEUTRAL — not +6 to both.
                    # This was the root cause of the LONG spam bug.
                    nak_adj_long,  nak_reason_long  = get_nakshatra_score_modifier(nakshatra, "LONG",  regime_name)
                    nak_adj_short, nak_reason_short = get_nakshatra_score_modifier(nakshatra, "SHORT", regime_name)
                    if nak_adj_long > 0:
                        astro_bonus_long += nak_adj_long
                        astro_bonus_reasons_long.append(nak_reason_long)
                    elif nak_adj_long < 0:
                        astro_penalty_long += nak_adj_long
                        astro_penalty_reasons_long.append(nak_reason_long)
                    if nak_adj_short > 0:
                        astro_bonus_short += nak_adj_short
                        astro_bonus_reasons_short.append(nak_reason_short)
                    elif nak_adj_short < 0:
                        astro_penalty_short += nak_adj_short
                        astro_penalty_reasons_short.append(nak_reason_short)

                lunar_volatility = str(lunar_cycle.get("lunar_volatility", "NORMAL")).upper()
                if LUNAR_VOLATILITY_GATE_ENABLED:
                    if lunar_volatility == "HIGH":
                        astro_penalty_short -= 5
                        astro_penalty_long -= 9 # 5 base + 4 for long
                        astro_penalty_reasons_short.append("High Lunar Volatility")
                        astro_penalty_reasons_long.append("High Lunar Volatility (Long)")
                    elif lunar_volatility == "LOW":
                        astro_bonus_long += 8
                        astro_bonus_short += 8
                        astro_bonus_reasons_long.append("Low Lunar Volatility")
                        astro_bonus_reasons_short.append("Low Lunar Volatility")

                # Note: astro bonus/penalty caps are applied later after ALL
                # session, nakshatra and macro adjustments are collected.

                vacuum = detect_liquidity_vacuum(df, liquidity_pools)
                if bool(vacuum.get("vacuum_active")):
                    if str(vacuum.get("vacuum_direction", "NONE")).upper() == "UP":
                        tech_bonus_long += 5
                        tech_reasons_long.append("Liquidity Vacuum Up")
                    elif str(vacuum.get("vacuum_direction", "NONE")).upper() == "DOWN":
                        tech_bonus_short += 5
                        tech_reasons_short.append("Liquidity Vacuum Down")

                # Smart short opportunity engine
                ema50 = float(df.get("ema50", df["close"]).iloc[-1])
                dist_from_ema50_pct = abs(price - ema50) / max(ema50, 1e-9) * 100.0
                overextended_short_setup = (
                    rsi > 75
                    and dist_from_ema50_pct > 2.0
                    and float(liquidity_pools.get("liquidity_above", price)) > float(price)
                )
                short_boost = 6 if overextended_short_setup else 0
                overext = {
                    "overextended": overextended_short_setup,
                    "distance_from_ema50_pct": round(dist_from_ema50_pct, 2),
                }
                tech_bonus_short += short_boost
                if short_boost > 0: tech_reasons_short.append("Overextended Setup")

                funding_rate = _get_funding_rate(coin)
                funding_divergence = _get_funding_divergence(df, funding_rate)

                pattern_signal = _detect_laf_lbf(df)
                signal_type = "TREND"
                forced_direction = None
                if pattern_signal.get("detected"):
                    signal_type = str(pattern_signal.get("signal_type", "TREND")).upper()
                    forced_direction = str(pattern_signal.get("direction", "")).upper() or None

                    # LAF/LBF GUARD: Pattern signals are powerful but must still
                    # respect bad session and regime conditions.
                    # Data shows NY_OPEN = 17% WR, SIDEWAYS = 25% WR.
                    # The LTCUSDT LAF trade that scored A-grade in NY_OPEN SIDEWAYS
                    # with R:R 1.56 is exactly why this guard is needed.
                    _bad_session = session_name in {"NY_OPEN", "ASIA_OPEN", "NY_LATE"}
                    _bad_regime  = regime_name in {"SIDEWAYS", "RANGING"}
                    if _bad_session and _bad_regime:
                        print(f"[LAF/LBF GUARD] {signal_type} pattern blocked — "
                              f"{session_name} + {regime_name} = too risky")
                        forced_direction = None
                        signal_type = "TREND"
                        pattern_signal = {"detected": False}
                    else:
                        if signal_type == "LAF":
                            tech_bonus_short += LAF_LBF_SCORE_BOOST
                            tech_reasons_short.append("LAF Pattern")
                        elif signal_type == "LBF":
                            tech_bonus_long += LAF_LBF_SCORE_BOOST
                            tech_reasons_long.append("LBF Pattern")


                # Preliminary direction using base scores only (final scores not yet calculated)
                if forced_direction in {"LONG", "SHORT"}:
                    direction = forced_direction
                else:
                    regime_prefers_short = regime_name in {"RANGING", "SIDEWAYS", "VOLATILE", "TRENDING_BEAR"}
                    if regime_prefers_short and short_score >= (score - 5):
                        direction = "SHORT"
                    elif score >= short_score:
                        direction = "LONG"
                    else:
                        direction = "SHORT"

                # Define session_name early so hard blocks can use it
                _early_threshold, session_name = get_session_threshold()

                # --- HARD BLOCKS ---
                if lunar_volatility == "HIGH":
                    print("[BLOCK] HIGH lunar — signal blocked")
                    continue  # hard block, no signal
                if hora_planet == "SATURN":
                    print("[BLOCK] Saturn hora — signal blocked")
                    continue  # hard block
                if hora_planet == "NEUTRAL":
                    print("[BLOCK] NEUTRAL hora (transition gap) — 0% win rate, signal blocked")
                    continue  # 0% win rate in London data — block all signals
                if session_name == "ASIA_OPEN":
                    print("[BLOCK] Asia Open — signal blocked")
                    continue  # no signal generated at all

                # FIX 2: Hard block NY_Late completely
                # Data: 14.3% WR across 14 trades — -15 penalty not enough, still fires
                # Same treatment as Asia Open — full hard block
                if session_name == "NY_LATE":
                    print("[BLOCK] NY Late (0-4 IST) — 14.3% WR, hard blocked")
                    continue

                # FIX 1: Hard block proven losing nakshatras
                # Ashwini:    0.0% WR (13 trades) — complete block
                # Bharani:   10.0% WR (10 trades) — complete block
                # Mrigashira: 15.0% WR (20 trades) — complete block
                # These have enough data (10+ trades each) to be statistically significant
                _nak_now = str(nakshatra_context.get("nakshatra_name", "")).strip()
                if _nak_now in {"Ashwini", "Bharani", "Mrigashira", "Punarvasu", "Dhanishta"}:
                    print(f"[BLOCK] {_nak_now} nakshatra — proven low WR, hard blocked")
                    # Win rates: Ashwini 0%, Dhanishta 5.3%, Bharani 10%,
                    # Punarvasu 12.5%, Mrigashira 15% — all 10+ trades of data
                    continue

                # FIX 3: Block LONG direction during bad nakshatras (Ardra also weak for LONG)
                # Even if Ardra fires in a good session, LONG during Ardra = 0% in new data
                # Only allow SHORT during Ardra — it has 29% WR overall but worse for LONG
                if _nak_now == "Ardra":
                    # Check direction early — we know direction from forced_direction or
                    # preliminary scoring. Use short_score vs score comparison as proxy.
                    # Safer: just add a strong LONG penalty here rather than full block
                    # (full block risks missing good Ardra SHORT setups)
                    pass  # Ardra handled via -12 penalty in scoring block below



                nakshatra_direction_block_reason = ""
                if NAKSHATRA_DIRECTION_BLOCK_ENABLED:
                    if nak_bias == "DESTRUCTION" and direction == "LONG":
                        nakshatra_direction_block_reason = (
                            "[NAKSHATRA BLOCK] DESTRUCTION phase — LONG trades historically 0% win rate"
                        )
                    elif (
                        nak_bias == "EXPANSION"
                        and direction == "LONG"
                        and regime_name != "TRENDING_BULL"
                    ):
                        nakshatra_direction_block_reason = (
                            "[NAKSHATRA BLOCK] EXPANSION phase without bull regime — LONG blocked"
                        )
                if nakshatra_direction_block_reason:
                    print(nakshatra_direction_block_reason)

                if ENTRY_CONFIRMATION_ENABLED:
                    momentum_confirmed = _momentum_alignment_score(df, direction)
                    current_volume = float(pd.to_numeric(df["volume"], errors="coerce").fillna(0.0).iloc[-1])
                    avg_recent_volume = float(pd.to_numeric(df["volume"], errors="coerce").fillna(0.0).tail(10).mean())

                    if not momentum_confirmed:
                        if direction == "LONG":
                            score = max(0, min(MAX_SCORE, score - 8))
                        else:
                            short_score = max(0, min(MAX_SCORE, short_score - 8))
                        signal_notes.append("Momentum not confirmed")

                    if current_volume < (avg_recent_volume * 0.8):
                        if direction == "LONG":
                            score = max(0, min(MAX_SCORE, score - 5))
                        else:
                            short_score = max(0, min(MAX_SCORE, short_score - 5))
                        signal_notes.append("Low volume entry warning")

                recent_window = df.tail(10)
                recent_low = float(recent_window["low"].min()) if not recent_window.empty else float(price)
                recent_high = float(recent_window["high"].max()) if not recent_window.empty else float(price)
                move_from_recent_low = ((price - recent_low) / max(recent_low, 1e-9)) * 100.0
                move_from_recent_high = ((recent_high - price) / max(recent_high, 1e-9)) * 100.0

                # --- ASTRO PENALTIES (35% advisory) ---
                if LATE_ENTRY_FILTER_ENABLED:
                    if move_from_recent_low > LATE_ENTRY_THRESHOLD_PCT:
                        astro_penalty_long -= 12
                        astro_penalty_reasons_long.append(f"Late entry > {LATE_ENTRY_THRESHOLD_PCT}%")
                    elif move_from_recent_low > (LATE_ENTRY_THRESHOLD_PCT - 0.5):
                        astro_penalty_long -= 6
                        astro_penalty_reasons_long.append("Possible late entry")

                    if move_from_recent_high > LATE_ENTRY_THRESHOLD_PCT:
                        astro_penalty_short -= 12
                        astro_penalty_reasons_short.append(f"Late entry > {LATE_ENTRY_THRESHOLD_PCT}%")
                    elif move_from_recent_high > (LATE_ENTRY_THRESHOLD_PCT - 0.5):
                        astro_penalty_short -= 6
                        astro_penalty_reasons_short.append("Possible late entry")

                crowd_phase = str(crowd.get("crowd_phase", "BALANCED")).upper()
                if crowd_phase == "CONSOLIDATION" and regime_name == "NORMAL":
                    astro_penalty_long -= 8
                    astro_penalty_reasons_long.append("Consolidation regime")

                if regime_name == "TRENDING_BEAR":
                    astro_penalty_long -= 10
                    astro_penalty_reasons_long.append("Bear trend")

                # Penalise LONG in ranging/sideways/volatile
                if regime_name in {"RANGING", "SIDEWAYS"}:
                    astro_penalty_long -= 12
                    astro_penalty_reasons_long.append(f"LONG in {regime_name} market — high risk")

                if regime_name == "VOLATILE":
                    astro_penalty_long -= 6
                    astro_penalty_reasons_long.append("Volatile market — LONG risky")

                # BUG FIX: Also penalise SHORT in SIDEWAYS/RANGING
                # Data shows SHORT in SIDEWAYS = 44% WR — below our 95 threshold target
                # Price chops both ways in sideways — shorts get stopped out as much as longs
                if regime_name in {"RANGING", "SIDEWAYS"}:
                    astro_penalty_short -= 10
                    astro_penalty_reasons_short.append(f"SHORT in {regime_name} market — choppy (-10)")

                if regime_name == "VOLATILE":
                    astro_penalty_short -= 5
                    astro_penalty_reasons_short.append("Volatile market — SHORT risky (-5)")

                # --- SESSION POWER ADJUSTMENT ---
                # Based on real win rate data from signals_log analysis:
                # London = 69% WR → +6 bonus both directions
                # Asia-London overlap = 51% WR → +3 bonus
                # NY Open = 20% WR → -10 penalty
                # Asia Open = 6% WR → -15 penalty
                # NY Late = 31% WR → -5 penalty (was neutral, but 0% in new data)
                _session_ist = get_ist_hour()
                if 14 <= _session_ist < 18:
                    # London session — best win rate, reward it
                    astro_bonus_long  += 6
                    astro_bonus_short += 6
                    astro_bonus_reasons_long.append("London session (+6)")
                    astro_bonus_reasons_short.append("London session (+6)")
                elif 10 <= _session_ist < 14:
                    # Asia-London overlap — good win rate
                    astro_bonus_long  += 3
                    astro_bonus_short += 3
                    astro_bonus_reasons_long.append("Asia-London session (+3)")
                    astro_bonus_reasons_short.append("Asia-London session (+3)")
                elif 18 <= _session_ist < 24:
                    # NY Open — 20% win rate, penalise hard
                    astro_penalty_long  -= 10
                    astro_penalty_short -= 10
                    astro_penalty_reasons_long.append("NY Open session (-10)")
                    astro_penalty_reasons_short.append("NY Open session (-10)")
                elif 4 <= _session_ist < 10:
                    # Asia Open — 6% win rate, near-block via penalty
                    astro_penalty_long  -= 15
                    astro_penalty_short -= 15
                    astro_penalty_reasons_long.append("Asia Open session (-15)")
                    astro_penalty_reasons_short.append("Asia Open session (-15)")
                else:
                    # NY Late (0-4 IST) — 16.7% win rate in new data, near-block
                    astro_penalty_long  -= 15
                    astro_penalty_short -= 15
                    astro_penalty_reasons_long.append("NY Late session (-15)")
                    astro_penalty_reasons_short.append("NY Late session (-15)")

                # --- NAKSHATRA WEAK PENALTIES ---
                # Note: Ashwini, Bharani, Mrigashira, Punarvasu, Dhanishta
                # are now HARD BLOCKED above — they never reach this point.
                # Penalties below are for nakshatras with partial edge only.
                _nak_name = str(nakshatra_context.get("nakshatra_name", "")).strip()
                if _nak_name == "Ardra":
                    # Ardra opposes LONG strongly — 0% LONG WR in new data
                    # SHORT during Ardra is acceptable (destructive energy = price falls)
                    astro_penalty_long  -= 20
                    astro_penalty_short -= 5
                    astro_penalty_reasons_long.append("Ardra nakshatra (-20, LONG 0% WR)")
                    astro_penalty_reasons_short.append("Ardra nakshatra (-5, caution)")
                elif _nak_name == "Uttara Bhadrapada":
                    astro_penalty_long  -= 6
                    astro_penalty_short -= 6
                    astro_penalty_reasons_long.append("Uttara Bhadrapada nakshatra (-6, low WR)")
                    astro_penalty_reasons_short.append("Uttara Bhadrapada nakshatra (-6, low WR)")
                elif _nak_name in {"Purva Ashadha", "Uttara Ashadha"}:
                    astro_penalty_long  -= 5
                    astro_penalty_short -= 5
                    astro_penalty_reasons_long.append(f"{_nak_name} nakshatra (-5, 0% WR)")
                    astro_penalty_reasons_short.append(f"{_nak_name} nakshatra (-5, 0% WR)")

                # --- MACRO CORRELATION SCORING ---
                # Oil, Gold, S&P500 via yfinance (free, no API key)
                # Cached for 15 mins so it doesn't slow down scanning
                try:
                    macro = get_macro_score_adjustment(direction)
                    if macro["long_bonus"] > 0:
                        astro_bonus_long += macro["long_bonus"]
                        astro_bonus_reasons_long.append(macro["reason_long"])
                    if macro["long_penalty"] < 0:
                        astro_penalty_long += macro["long_penalty"]
                        astro_penalty_reasons_long.append(macro["reason_long"])
                    if macro["short_bonus"] > 0:
                        astro_bonus_short += macro["short_bonus"]
                        astro_bonus_reasons_short.append(macro["reason_short"])
                    if macro["short_penalty"] < 0:
                        astro_penalty_short += macro["short_penalty"]
                        astro_penalty_reasons_short.append(macro["reason_short"])
                    if macro["data_fresh"]:
                        print(f"[MACRO] Oil:{macro['oil_chg']:+.1f}%({macro['oil_state']}) "
                              f"Gold:{macro['gold_chg']:+.1f}%({macro['gold_state']}) "
                              f"S&P:{macro['sp500_chg']:+.1f}%({macro['sp500_state']}) "
                              f"→ SHORT+{macro['short_bonus']} LONG+{macro['long_bonus']}")
                except Exception as macro_exc:
                    print(f"[MACRO] Engine error (non-critical): {macro_exc}")

                # --- FEAR & GREED INDEX SCORING ---
                # Free from alternative.me — no API key needed
                # Cached 1 hour. Contrarian: Extreme Greed = SHORT, Extreme Fear = LONG
                try:
                    fg = get_fg_score_adjustment()
                    if fg["long_bonus"] > 0:
                        astro_bonus_long += fg["long_bonus"]
                        astro_bonus_reasons_long.append(fg["reason"])
                    if fg["long_penalty"] < 0:
                        astro_penalty_long += fg["long_penalty"]
                        astro_penalty_reasons_long.append(fg["reason"])
                    if fg["short_bonus"] > 0:
                        astro_bonus_short += fg["short_bonus"]
                        astro_bonus_reasons_short.append(fg["reason"])
                    if fg["short_penalty"] < 0:
                        astro_penalty_short += fg["short_penalty"]
                        astro_penalty_reasons_short.append(fg["reason"])
                    if fg["data_fresh"]:
                        print(f"[F&G] {fg['fg_value']} ({fg['fg_class']}) "
                              f"→ SHORT+{fg['short_bonus']} LONG+{fg['long_bonus']}")
                except Exception as fg_exc:
                    print(f"[F&G] Engine error (non-critical): {fg_exc}")

                # --- WHALE FLOW SCORING ---
                # Tracks large aggressive trades (>$50k) on Binance Futures.
                # Uses /fapi/v1/aggTrades — free, no API key needed.
                # Cached 60s per symbol. Complements orderflow_engine (passive
                # order book) with actual executed whale trades (can't be faked).
                try:
                    whale = analyze_whale_flow(coin)
                    if whale["long_bonus"] > 0:
                        astro_bonus_long += whale["long_bonus"]
                        astro_bonus_reasons_long.append(whale["reason"])
                    if whale["long_penalty"] < 0:
                        astro_penalty_long += whale["long_penalty"]
                        astro_penalty_reasons_long.append(whale["reason"])
                    if whale["short_bonus"] > 0:
                        astro_bonus_short += whale["short_bonus"]
                        astro_bonus_reasons_short.append(whale["reason"])
                    if whale["short_penalty"] < 0:
                        astro_penalty_short += whale["short_penalty"]
                        astro_penalty_reasons_short.append(whale["reason"])
                    if whale["data_fresh"] and whale["whale_count"] > 0:
                        print(f"[WHALE] {coin} — {whale['whale_bias']} "
                              f"Buy${whale['whale_buy_usdt']/1000:.0f}k "
                              f"Sell${whale['whale_sell_usdt']/1000:.0f}k "
                              f"→ SHORT+{whale['short_bonus']} LONG+{whale['long_bonus']}")
                except Exception as whale_exc:
                    print(f"[WHALE] Engine error (non-critical): {whale_exc}")

                # --- APPLY CAPS (must be AFTER all penalties/bonuses are collected) ---
                # BUG FIX: cap was previously applied BEFORE session/nakshatra/macro
                # penalties, meaning those penalties had no ceiling protection.
                # Now cap applies once, after everything is collected.
                astro_bonus_long   = min(astro_bonus_long,   ASTRO_MAX_BONUS)
                astro_bonus_short  = min(astro_bonus_short,  ASTRO_MAX_BONUS)
                astro_penalty_long  = max(astro_penalty_long,  ASTRO_MAX_PENALTY)
                astro_penalty_short = max(astro_penalty_short, ASTRO_MAX_PENALTY)

                # --- FINAL SCORE CALCULATION ---
                final_long_score = score + tech_bonus_long + astro_bonus_long + astro_penalty_long
                final_short_score = short_score + tech_bonus_short + astro_bonus_short + astro_penalty_short

                final_long_score = max(0, min(MAX_SCORE, final_long_score))
                final_short_score = max(0, min(MAX_SCORE, final_short_score))

                # Re-determine direction using FINAL scores (not raw base scores)
                # Also add regime gate: in RANGING/SIDEWAYS/VOLATILE → prefer SHORT
                if forced_direction in {"LONG", "SHORT"}:
                    direction = forced_direction
                else:
                    regime_prefers_short = regime_name in {"RANGING", "SIDEWAYS", "VOLATILE", "TRENDING_BEAR"}
                    if regime_prefers_short and final_short_score >= (final_long_score - 5):
                        direction = "SHORT"
                    elif final_long_score >= final_short_score:
                        direction = "LONG"
                    else:
                        direction = "SHORT"

                signal_quality = "C"
                if WIN_PATTERN_BONUS_ENABLED and direction == "SHORT":
                    pattern_matches = 0
                    if direction == "SHORT":
                        pattern_matches += 1
                    if regime_name in {"TRENDING_BEAR", "NORMAL"}:
                        pattern_matches += 1
                    if lunar_volatility == "LOW":
                        pattern_matches += 1
                    if crowd_phase == "CONSOLIDATION":
                        pattern_matches += 1
                    if str(liquidity_event).strip() in {"", "None", "Liquidity Grab Up", "Stop Hunt"}:
                        pattern_matches += 1

                    if pattern_matches == 5:
                        astro_bonus_short += 12
                        astro_bonus_reasons_short.append("Win-Pattern A+")
                        signal_quality = "A+"
                    elif pattern_matches == 4:
                        astro_bonus_short += 6
                        astro_bonus_reasons_short.append("Win-Pattern A")
                        signal_quality = "A"
                    elif pattern_matches == 3:
                        astro_bonus_short += 3
                        astro_bonus_reasons_short.append("Win-Pattern B")
                        signal_quality = "B"

                # Recalculate final scores with win pattern bonus
                final_long_score = long_adaptive["adjusted_score"] + tech_bonus_long + astro_bonus_long + astro_penalty_long
                final_short_score = short_adaptive["adjusted_score"] + tech_bonus_short + astro_bonus_short + astro_penalty_short
                final_long_score = max(0, min(MAX_SCORE, final_long_score))
                final_short_score = max(0, min(MAX_SCORE, final_short_score))

                min_score_required, session_name = get_session_threshold()
                # FIX v2: Use adaptive, direction-aware thresholds.
                # LONG requires higher score in non-bull regimes.
                adaptive_threshold = adaptive_engine.get_score_threshold(regime_name, direction)
                min_score_required = max(min_score_required, adaptive_threshold)
                if direction == "LONG" and regime_name not in {"TRENDING_BULL"}:
                    # In non-bull regimes, longs need even higher conviction
                    min_score_required = max(min_score_required, 93)

                # UPGRADE: Revati + London = 92.6% win rate in real data.
                # Lower threshold to 90 for this proven golden window only.
                _nak_for_boost = str(nakshatra_context.get("nakshatra_name", "")).strip()
                if _nak_for_boost == "Revati" and session_name == "LONDON":
                    min_score_required = min(min_score_required, 90)
                    print("[REVATI+LONDON] Golden window — threshold lowered to 90")
                auto_trade_reason = ""

                # Preliminary confidence estimate for short_conditions check below
                confidence = calculate_confidence(final_long_score if direction == "LONG" else final_short_score)
                strength = str(confidence.get("strength", "NORMAL")).upper()

                short_conditions = (
                    macd < 0
                    and (trend_1h == "DOWN" or trend_4h == "DOWN")
                ) or overextended_short_setup
                if short_conditions:
                    print(f"SHORT setup detected for {coin} {tf}")

                    if trend_1h == "UP" and trend_4h == "UP":
                        short_rejected_trend += 1
                        print("Short rejected due to trend filter")

                    if regime_name in {"BULL", "BULLISH", "UPTREND", "TRENDING_UP", "TRENDING_BULL"}:
                        short_rejected_regime += 1
                        print("Short rejected due to regime")

                entry = price
                sl = smart_stop_loss(
                    df,
                    entry,
                    direction,
                    regime=regime_name,
                    lunar_volatility=lunar_volatility,
                    crowd_phase=crowd_phase,
                    nakshatra_type=nakshatra_type,
                    hora_planet=hora_planet,
                )
                tp_result = smart_take_profit(df, entry, sl, direction)
                if isinstance(tp_result, dict):
                    tp1_smart = tp_result.get("tp1", entry)
                    tp2_smart = tp_result.get("tp2", entry)
                    risk = abs(entry - sl)
                    rr_tp2 = abs(tp2_smart - entry) / max(risk, 1e-9)
                    rr_tp1 = abs(tp1_smart - entry) / max(risk, 1e-9)
                    if rr_tp2 >= MIN_RR:
                        tp = tp2_smart
                    elif rr_tp1 >= MIN_RR:
                        tp = tp1_smart
                    else:
                        tp = tp2_smart
                else:
                    tp = float(tp_result)

                tp1 = calculate_scalp_tp1(entry, direction, pct=0.003)
                tp2 = tp
                tp = tp2

                # UPGRADE: Apply timeframe TP cap — prevents 5m signals from
                # getting TPs that only make sense on 1h charts.
                # apply_timeframe_tp_cap() already existed but was never called.
                tp = apply_timeframe_tp_cap(entry, tp, direction, tf)

                # Store tp1_smart for outcome_engine breakeven trigger
                # (outcome_engine moves SL to breakeven when price hits tp1_smart)
                tp1_for_breakeven = tp1_smart if isinstance(tp_result, dict) else (
                    entry + abs(tp - entry) * 0.5 if direction == "LONG"
                    else entry - abs(tp - entry) * 0.5
                )
                if NAKSHATRA_SCORING_ENABLED and nakshatra_type == "DHRUVA":
                    tp_distance = abs(tp - entry) * 1.1
                    tp = entry + tp_distance if direction == "LONG" else entry - tp_distance
                original_spot_entry = entry
                original_spot_sl = sl
                original_spot_tp = tp
                basis_info = {
                    "basis_pct": 0.0,
                    "adjusted_entry": entry,
                    "adjusted_sl": sl,
                    "adjusted_tp": tp,
                    "blocked": False,
                    "warning": False,
                }
                if BASIS_ADJUSTMENT_ENABLED:
                    try:
                        basis_info = apply_basis_adjustment(coin, entry, sl, tp, direction)
                    except Exception as exc:
                        print(f"[BASIS] Adjustment fetch failed for {coin}: {exc}")

                if bool(basis_info.get("blocked")):
                    print(f"[BASIS BLOCK] Gap too large ({basis_info.get('basis_pct', 0.0):.3f}%) - skipping trade")
                    print("[TELEGRAM BLOCKED] Rejected signal — not sending")
                    _log_rejected_signal({
                        "signal_type": signal_type,
                        "signal_quality": "REJECTED",
                        "coin": coin,
                        "tf": tf,
                        "timeframe": tf,
                        "direction": direction,
                        "entry": entry,
                        "tp": tp,
                        "sl": sl,
                        "basis_pct": basis_info.get("basis_pct", 0.0),
                        "regime": regime_name,
                        "crowd_emotion": crowd["emotion"],
                        "crowd_phase": crowd_phase,
                        "nakshatra_name": nakshatra_context["nakshatra_name"],
                        "nakshatra_type": nakshatra_type,
                        "planetary_bias": planetary["cosmic_bias"],
                        "volatility_bias": planetary["volatility_bias"],
                        "sun_sign": sun_sign,
                        "sun_sign_bias": sun_sign_bias,
                        "sun_transit_effect": sun_transit_effect,
                        "vedic_time": vedic_time["current_period"],
                        "timing_quality": vedic_time["timing_quality"],
                        "vedic_block": bool(vedic_block_status.get("blocked")),
                        "tithi_group": tithi_context["tithi_group"],
                        "hora_planet": hora_planet,
                        "abhijit_active": abhijit_active,
                        "reversal_warning": reversal_warning,
                        "lunar_volatility": lunar_cycle["lunar_volatility"],
                        "lunar_psychology": lunar_cycle["lunar_psychology"],
                        "score": score if direction == "LONG" else short_score,
                    }, [f"basis block ({basis_info.get('basis_pct', 0.0):.3f}%)"])
                    continue
                if bool(basis_info.get("warning")):
                    print(f"[BASIS WARNING] Spot-Perp gap: {basis_info.get('basis_pct', 0.0):.3f}% - adjusting levels")
                    astro_penalty_long -= 3
                    astro_penalty_short -= 3
                    astro_penalty_reasons_long.append("Basis warning")

                entry = float(basis_info.get("adjusted_entry", entry))
                sl = float(basis_info.get("adjusted_sl", sl))
                tp = float(basis_info.get("adjusted_tp", tp))
                breakeven_sl = calculate_breakeven_sl(entry, direction)
                lvns = detect_low_volume_nodes(df)
                lvn_between_tp = find_lvn_between(entry, tp, direction, lvns)
                lvn_magnet_bonus = 8 if lvn_between_tp else 0
                if lvn_magnet_bonus > 0:
                    tech_bonus_long += lvn_magnet_bonus
                    tech_reasons_long.append("LVN Magnet")
                    if direction == "SHORT":
                        tech_bonus_short += lvn_magnet_bonus
                        tech_reasons_short.append("LVN Magnet")

                liquidation = detect_liquidation_zones(
                    open_interest=oi_context,
                    structure=structure,
                    liquidity_pools=liquidity_pools,
                    symbol=coin,
                    current_price=price,
                    volatility_pct=(atr / max(price, 1e-9)) * 100.0,
                )
                liq_tp_bonus = 0
                if direction == "LONG":
                    liq_target = float(liquidation.get("liq_zone_above", tp))
                    if abs(tp - liq_target) <= (atr * 0.5):
                        tech_bonus_long += 4
                        tech_reasons_long.append("TP aligns with Liq")
                else:
                    liq_target = float(liquidation.get("liq_zone_below", tp))
                    if abs(tp - liq_target) <= (atr * 0.5):
                        tech_bonus_short += 4
                        tech_reasons_short.append("TP aligns with Liq")

                # Recalculate final scores including all late bonuses (LVN magnet, liquidation TP, etc.)
                # Use the previously calculated final scores as the base (not raw adaptive score)
                # to avoid losing bonuses that were added before this point.
                final_long_score = long_adaptive["adjusted_score"] + tech_bonus_long + astro_bonus_long + astro_penalty_long
                final_short_score = short_adaptive["adjusted_score"] + tech_bonus_short + astro_bonus_short + astro_penalty_short
                final_long_score  = max(0, min(MAX_SCORE, final_long_score))
                final_short_score = max(0, min(MAX_SCORE, final_short_score))

                vyavhar_penalty = 0
                vyavhar_reasons = []
                vyavhar_block_reason = ""
                buy_volume = float(orderflow.get("buy_volume", 0.0))
                sell_volume = float(orderflow.get("sell_volume", 0.0))
                oi_change_now = float(oi_context.get("oi_change_5", oi_shift.get("oi_change", 0.0)) or 0.0)
                funding_rate_value = float(funding_rate or 0.0)
                long_sell_pressure = (
                    (sell_volume - buy_volume) / max(buy_volume, 1e-9)
                    if sell_volume > buy_volume else 0.0
                )
                short_buy_pressure = (
                    (buy_volume - sell_volume) / max(sell_volume, 1e-9)
                    if buy_volume > sell_volume else 0.0
                )

                if direction == "LONG":
                    if sell_volume > buy_volume:
                        vyavhar_penalty += 8
                        vyavhar_reasons.append("sell volume > buy volume")
                    if oi_change_now < 0:
                        vyavhar_penalty += 6
                        vyavhar_reasons.append("OI decreasing")
                    if funding_rate_value < 0:
                        vyavhar_penalty += 5
                        vyavhar_reasons.append("funding negative")
                else:
                    if buy_volume > sell_volume:
                        vyavhar_penalty += 8
                        vyavhar_reasons.append("buy volume > sell volume")
                    if oi_change_now > 0:
                        vyavhar_penalty += 6
                        vyavhar_reasons.append("OI increasing")
                    if funding_rate_value > 0:
                        vyavhar_penalty += 5
                        vyavhar_reasons.append("funding positive")

                if vyavhar_penalty > 0:
                    if direction == "LONG":
                        final_long_score -= 8
                        astro_penalty_reasons_long.append("Vyavhar contradiction")
                    else:
                        final_short_score -= 8
                        astro_penalty_reasons_short.append("Vyavhar contradiction")
                    print(f"[VYAVHAR] Behavior contradicts price direction: {', '.join(vyavhar_reasons)}")

                if vyavhar_block_reason:
                    print("[VYAVHAR] Behavior contradicts price direction")
                    signal_notes.append(vyavhar_block_reason)
                
                current_score = final_long_score if direction == "LONG" else final_short_score

                # --- SCORE TRANSPARENCY (printed after all adjustments) ---
                if direction == "LONG":
                    print(f"\n[SCORE BREAKDOWN] {coin} {tf} LONG")
                    print(f"  Base technical score: {long_adaptive['adjusted_score']:.2f} pts")
                    print(f"  Technical bonus:      +{tech_bonus_long} pts ({', '.join(tech_reasons_long)})")
                    print(f"  Astro bonus:          +{astro_bonus_long} pts ({', '.join(astro_bonus_reasons_long)})")
                    print(f"  Astro penalty:        {astro_penalty_long} pts ({', '.join(astro_penalty_reasons_long)})")
                    print(f"  Final score:          {current_score:.2f} pts (threshold: {min_score_required})")
                else:
                    print(f"\n[SCORE BREAKDOWN] {coin} {tf} SHORT")
                    print(f"  Base technical score: {short_adaptive['adjusted_score']:.2f} pts")
                    print(f"  Technical bonus:      +{tech_bonus_short} pts ({', '.join(tech_reasons_short)})")
                    print(f"  Astro bonus:          +{astro_bonus_short} pts ({', '.join(astro_bonus_reasons_short)})")
                    print(f"  Astro penalty:        {astro_penalty_short} pts ({', '.join(astro_penalty_reasons_short)})")
                    print(f"  Final score:          {current_score:.2f} pts (threshold: {min_score_required})")
                result_status = "APPROVED" if current_score >= min_score_required else "REJECTED (Score)"
                print(f"Result: {result_status}")

                # --- TIER DETERMINATION ---
                is_elite  = current_score >= 97           # top tier
                is_strong = 95 <= current_score < 97      # passes threshold, not elite
                is_normal = current_score < 95            # below threshold

                # Apply Tier 2 Entry Timing Penalty
                if is_strong and not entry_timing_ok:
                    original_score = current_score
                    penalty = 3
                    current_score -= penalty
                    print(f"[TIER 2] Entry timing penalty applied (-{penalty}). Score: {original_score} -> {current_score}")
                    if current_score < min_score_required:
                        print(f"[TIER 2] Score floored at threshold. Was {current_score}, now {min_score_required}")
                        current_score = min_score_required

                # Recalculate BOS pullback with Tier-based threshold
                bos_threshold = 0.30
                if is_elite: bos_threshold = 0.0
                elif is_strong: bos_threshold = 0.15
                
                if (direction == "LONG" and bool(structure.get("bos_up"))) or (direction == "SHORT" and bool(structure.get("bos_down"))):
                    bos_pullback = _get_bos_pullback_state(df, direction, threshold=bos_threshold)

                confidence = calculate_confidence(current_score)
                confidence_value = float(confidence.get("confidence", 0))
                rahu_override_applied = False
                rahu_override_eligible = _is_rahu_override_eligible(
                    current_score,
                    confidence_value,
                    regime_name,
                    vedic_time.get("current_period", "NORMAL"),
                )
                if rahu_override_eligible:
                    if direction == "LONG":
                        final_long_score -= 8
                    else:
                        final_short_score -= 8
                    rahu_override_applied = True
                    signal_notes.append("Rahu Kaal override active: -8 score penalty, size reduced 30%")
                    print("[RAHU-OVERRIDE] Score 98 extreme signal — Rahu Kaal penalty applied, trade allowed")
                
                duration_estimate = estimate_trade_duration(df, price, tp, tf)
                chart_path = create_chart(df, coin, tf, price, sl, tp)
                cleanup_chart_images()

                signal_key = f"{coin}_{tf}_{direction}"
                rr = _safe_rr(price, tp, sl)
                trends_list = [trend_local, trend_1h, trend_4h]
                aligned_count = trends_list.count("UP") if direction == "LONG" else trends_list.count("DOWN")

                structure_trend = str(structure.get("trend", "neutral")).lower()
                bullish_momentum = momentum > momentum_threshold
                bearish_momentum = momentum < -momentum_threshold
                structure_ok = (
                    (direction == "LONG" and bool(structure.get("bos_up")))
                    or (
                        direction == "LONG"
                        and bool(liquidity_pools.get("sweep_lows"))
                        and bullish_momentum
                    )
                    or (direction == "SHORT" and bool(structure.get("bos_down")))
                    or (
                        direction == "SHORT"
                        and bool(liquidity_pools.get("sweep_highs"))
                        and bearish_momentum
                    )
                    or signal_type in {"LAF", "LBF"}
                )
                distance_to_below = abs(price - float(liquidity_pools.get("liquidity_below", price)))
                distance_to_above = abs(float(liquidity_pools.get("liquidity_above", price)) - price)
                liquidity_ok = (
                    bool(liquidity_pools.get("sweep_lows")) or (distance_to_below < atr)
                ) if direction == "LONG" else (
                    bool(liquidity_pools.get("sweep_highs")) or (distance_to_above < atr)
                )
                volatility_ok = str(vol_expansion.get("state", "normal")).lower() != "compressed"
                entry_timing_ok = not _entry_timing_block(df, direction)
                bos_pullback = {"applicable": False, "pullback_met": True}
                if (direction == "LONG" and bool(structure.get("bos_up"))) or (
                    direction == "SHORT" and bool(structure.get("bos_down"))
                ):
                    bos_pullback = _get_bos_pullback_state(df, direction)

                ob_data = ob_long if direction == "LONG" else ob_short
                ob_msg = format_ob_fvg_for_telegram(ob_data, direction)
                current_vedic_period = str(vedic_time.get("current_period", "NORMAL")).upper()
                vedic_block_active = bool(vedic_block_status.get("blocked"))
                rahu_override_allowed = bool(rahu_override_applied and current_vedic_period == "RAHU KALAM")
                auto_trade_allowed = not (
                    (VEDIC_HARD_BLOCK_ENABLED and vedic_block_active and not rahu_override_allowed)
                    or (session_name == "ASIA_OPEN" and auto_trade_reason)
                )
                if stop_hunt_risk == "HIGH":
                    print("[NAKSHATRA] Tikshna active — tightening SL to 85%")

                signal_data = {
                    "status": "APPROVED",
                    "signal_type": signal_type,
                    "signal_quality": signal_quality,
                    "coin": coin,
                    "tf": tf,
                    "timeframe": tf,
                    "direction": direction,
                    "entry": entry,
                    "tp": tp,
                    "sl": sl,
                    "tp1_breakeven": round(float(tp1_for_breakeven), 8),
                    "basis_pct": basis_info.get("basis_pct", 0.0),
                    "basis_adjusted": bool(BASIS_ADJUSTMENT_ENABLED),
                    "spot_entry": original_spot_entry,
                    "spot_sl": original_spot_sl,
                    "spot_tp": original_spot_tp,
                    "expected_candles": duration_estimate["expected_candles"],
                    "expected_hold_time": duration_estimate["expected_hold_time"],
                    "score": score,
                    "confidence": confidence_value,
                    # Logging factors for adaptive learning
                    "ema_trend": long_base["breakdown"].get("ema_trend", 0) if direction == "LONG" else short_base["breakdown"].get("ema_trend", 0),
                    "macd": long_base["breakdown"].get("macd", 0) if direction == "LONG" else short_base["breakdown"].get("macd", 0),
                    "volume": long_base["breakdown"].get("volume", 0) if direction == "LONG" else short_base["breakdown"].get("volume", 0),
                    "adx": long_base["breakdown"].get("adx", 0) if direction == "LONG" else short_base["breakdown"].get("adx", 0),
                    "momentum": long_base["breakdown"].get("momentum", 0) if direction == "LONG" else short_base["breakdown"].get("momentum", 0),
                    "ob_fvg": ob_data["combined_score"],
                    "nakshatra": psy["name"],
                    "emotion": crowd["emotion"],
                    "crowd_emotion": crowd["emotion"],
                    "crowd_phase": crowd["crowd_phase"],
                    "volatility": volatility["state"],
                    "regime": regime_name,
                    "session": session_name,
                    "regime_score": regime_score,
                    "regime_note": regime_note,
                    "structure_trend": structure_trend,
                    "funding_rate": funding_rate,
                    "funding_divergence_reason": funding_divergence.get("reason"),
                    "funding_long_boost": funding_divergence.get("long_boost", 0),
                    "funding_short_boost": funding_divergence.get("short_boost", 0),
                    "structure_bos_up": bool(structure.get("bos_up")),
                    "structure_bos_down": bool(structure.get("bos_down")),
                    "laf_lbf_enabled": LAF_LBF_ENABLED,
                    "laf_lbf_detected": bool(pattern_signal.get("detected")),
                    "laf_lbf_key_level": pattern_signal.get("key_level"),
                    "laf_lbf_level_tests": pattern_signal.get("level_tests"),
                    "laf_lbf_breakout_volume": pattern_signal.get("breakout_volume"),
                    "laf_lbf_avg_volume": pattern_signal.get("avg_volume"),
                    "bos_pullback_threshold": BOS_PULLBACK_THRESHOLD,
                    "bos_pullback_required": bool(bos_pullback.get("applicable")),
                    "bos_pullback_met": bool(bos_pullback.get("pullback_met", True)),
                    "bos_breakout_high": bos_pullback.get("breakout_high"),
                    "bos_breakout_low": bos_pullback.get("breakout_low"),
                    "bos_pullback_trigger": bos_pullback.get("trigger_price"),
                    "structure_choch_up": bool(structure.get("choch_up")),
                    "structure_choch_down": bool(structure.get("choch_down")),
                    "market_structure": structure.get("market_structure", "RANGE"),
                    "liquidity_above": liquidity_zones.get("liquidity_above"),
                    "liquidity_below": liquidity_zones.get("liquidity_below"),
                    "liquidity_distance_pct": liquidity_zones.get("distance_to_liquidity"),
                    "liquidity_swept_below": bool(sweep_context.get("swept_below")),
                    "liquidity_swept_above": bool(sweep_context.get("swept_above")),
                    "pool_liquidity_above": liquidity_pools.get("liquidity_above"),
                    "pool_liquidity_below": liquidity_pools.get("liquidity_below"),
                    "pool_equal_highs": bool(liquidity_pools.get("equal_highs")),
                    "pool_equal_lows": bool(liquidity_pools.get("equal_lows")),
                    "pool_sweep_highs": bool(liquidity_pools.get("sweep_highs")),
                    "pool_sweep_lows": bool(liquidity_pools.get("sweep_lows")),
                    "distance_to_liquidity": liquidity_pools.get("distance_to_liquidity"),
                    "volatility_expansion": bool(vol_expansion.get("volatility_expansion")),
                    "volatility_expansion_state": vol_expansion.get("state"),
                    "risk_reward": round(rr, 2),
                    "lvn_near_entry": bool((long_base if direction == "LONG" else short_base).get("lvn_near_entry")),
                    "lvn_entry_price": (long_base if direction == "LONG" else short_base).get("lvn_entry_price"),
                    "lvn_between_entry_tp": bool(lvn_between_tp),
                    "lvn_tp_price": float(lvn_between_tp["mid"]) if lvn_between_tp else tp_result.get("lvn_target") if isinstance(tp_result, dict) else None,
                    "lvn_tp_used": bool(tp_result.get("lvn_tp_used")) if isinstance(tp_result, dict) else False,
                    "lvn_magnet_bonus": lvn_magnet_bonus,
                    "short_score": round(short_score, 2),
                    "overextended": bool(overext.get("overextended")),
                    "overextension_boost": short_boost,
                    "oi_change": oi_context.get("oi_change_5", oi_shift.get("oi_change", 0.0)),
                    "oi_change_20": oi_context.get("oi_change_20", 0.0),
                    "oi_bias": oi_context.get("oi_bias", oi_shift.get("bias", "neutral")),
                    "oi_strength": oi_context.get("oi_strength", "low"),
                    "oi_bonus": 10 if (direction == "LONG" and oi_bias_text in {"bullish_continuation", "short_squeeze"}) or (direction == "SHORT" and oi_bias_text in {"bearish_continuation", "longs_closing"}) else 0,
                    "liq_above": liquidation.get("liq_zone_above", liquidation.get("liq_above")),
                    "liq_below": liquidation.get("liq_zone_below", liquidation.get("liq_below")),
                    "liq_pressure": liquidation.get("liq_pressure", "neutral"),
                    "liquidation_tp_bonus": liq_tp_bonus,
                    "vacuum_direction": vacuum.get("vacuum_direction", "NONE"),
                    "vacuum_strength": vacuum.get("vacuum_strength", 0.0),
                    "vacuum_active": bool(vacuum.get("vacuum_active")),
                    "liquidity_event": liquidity_event,
                    "nakshatra_name": nakshatra_context["nakshatra_name"],
                    "nakshatra_type": nakshatra_type,
                    "planetary_bias": planetary["cosmic_bias"],
                    "volatility_bias": planetary["volatility_bias"],
                    "sun_sign": sun_sign,
                    "sun_sign_bias": sun_sign_bias,
                    "sun_transit_effect": sun_transit_effect,
                    "orderflow_buy_volume": orderflow["buy_volume"],
                    "orderflow_sell_volume": orderflow["sell_volume"],
                    "orderflow_bias": orderflow["bias"],
                    "orderflow_imbalance": orderflow["imbalance"],
                    "nakshatra_bias": nak_cycle["bias"],
                    "nakshatra_psychology": nak_cycle["psychology"],
                    "nakshatra_volatility": nak_cycle["volatility"],
                    "stop_hunt_risk": stop_hunt_risk,
                    "vedic_time": vedic_time["current_period"],
                    "timing_quality": vedic_time["timing_quality"],
                    "vedic_block": not auto_trade_allowed,
                    "auto_trade_allowed": auto_trade_allowed,
                    "auto_trade_reason": auto_trade_reason,
                    "rahu_override_applied": rahu_override_applied,
                    "rahu_override_eligible": rahu_override_eligible,
                    "vedic_block_until": vedic_block_status.get("active_until", ""),
                    "tithi_group": tithi_context["tithi_group"],
                    "hora_planet": hora_planet,
                    "abhijit_active": abhijit_active,
                    "reversal_warning": reversal_warning,
                    "lunar_volatility": lunar_cycle["lunar_volatility"],
                    "lunar_psychology": lunar_cycle["lunar_psychology"],
                    "insight": " | ".join(signal_notes),
                    "score": current_score,
                }
                message = f"""
🚀 BHRAMHA SIGNAL

💰 Coin: {coin}
⏱ Timeframe: {tf}

📈 Direction: {direction}

💵 Entry: {round(price,4)}
🛑 Stop Loss: {round(sl,4)}
🎯 Take Profit: {round(tp,4)}

⏳ Expected Trade Duration
Estimated Hold Time: {duration_estimate['expected_hold_time']}
Expected Candles: {duration_estimate['expected_candles']}

━━━━━━━━━━━━━━

📊 Score: {round(current_score,2)}
🔥 Strength: {confidence['strength']}
🎯 Confidence: {confidence['confidence']}%

📈 Win Probability: {confidence['probability']}
⚠ Risk Level: {confidence['risk']}

━━━━━━━━━━━━━━

📊 MARKET STRUCTURE

📈 Trend 1H: {trend_1h}
📈 Trend 4H: {trend_4h}

⚡ Volatility: {volatility['state']}
🐋 Liquidity Event: {liquidity_event}
📍 Market Regime: {regime_name} ({regime_score})
📝 Regime Note: {regime_note}
💧 Pool Sweep Highs: {liquidity_pools.get('sweep_highs')} | Sweep Lows: {liquidity_pools.get('sweep_lows')}
📏 Dist To Pool: {round(float(liquidity_pools.get('distance_to_liquidity', 0.0)), 4)}
🏗 Structure: {structure.get('market_structure', structure_trend.upper())} | BOS_UP: {structure.get('bos_up')} | BOS_DOWN: {structure.get('bos_down')}
⚖ Risk/Reward: {round(rr, 2)}

━━━━━━━━━━━━━━

📊 Institutional Analysis
{ob_msg}

━━━━━━━━━━━━━━

📊 Market Mechanics

Liquidity Above: {round(float(liquidity_pools.get('liquidity_above', 0.0)), 4)}
Liquidity Below: {round(float(liquidity_pools.get('liquidity_below', 0.0)), 4)}
Liquidation Zone Above: {round(float(liquidation.get('liq_zone_above', liquidation.get('liq_above', 0.0))), 4)}
Liquidation Zone Below: {round(float(liquidation.get('liq_zone_below', liquidation.get('liq_below', 0.0))), 4)}
Liquidation Pressure: {str(liquidation.get('liq_pressure', 'neutral')).upper()}

Open Interest Bias: {str(oi_context.get('oi_bias', oi_shift.get('bias', 'neutral'))).upper()}
Open Interest Strength: {str(oi_context.get('oi_strength', 'low')).upper()}
Liquidity Vacuum: {str(vacuum.get('vacuum_direction', 'NONE')).upper()} (Strength {vacuum.get('vacuum_strength', 0.0)})
Structure Break: {structure.get('market_structure', 'RANGE')}
Volatility Expansion: {str(vol_expansion.get('volatility_expansion', False)).upper()} ({str(vol_expansion.get('state', 'normal')).upper()})

━━━━━━━━━━━━━━

{"⚠ Overextension Detected\\nMarket likely exhausted.\\nReversal probability increased.\\n\\n━━━━━━━━━━━━━━\\n" if overext.get("overextended") else ""}

🧭 Smart Exit Engine
Stop Loss Placement: liquidity protected
Take Profit Target: liquidity zone

━━━━━━━━━━━━━━

📊 Order Flow

Buy Volume: {round(orderflow['buy_volume'], 2)}
Sell Volume: {round(orderflow['sell_volume'], 2)}
Order Flow Bias: {orderflow['bias']}

━━━━━━━━━━━━━━

🧠 Crowd Psychology
Emotion: {crowd['emotion']}
Phase: {crowd['crowd_phase']}

━━━━━━━━━━━━━━

🌍 Global Macro

{get_macro_summary()} | {get_fg_summary()}
{get_whale_summary(coin)}

━━━━━━━━━━━━━━

🪐 Planetary Alignment

Cosmic Bias: {planetary['cosmic_bias']}
Volatility Bias: {planetary['volatility_bias']}
Sun Sign: {sun_sign}
Sun Transit: {sun_transit_effect}
Sun Bias: {sun_sign_bias} ({sun_score_adjustment:+d})

━━━━━━━━━━━━━━

🕉 Vedic Time Window

Current Period: {vedic_time['current_period']}
Timing Quality: {vedic_time['timing_quality']}

━━━━━━━━━━━━━━

🌙 Lunar Volatility Cycle

Moon Phase: {round(lunar_cycle['moon_phase'],2)}
Tithi: {lunar_cycle['tithi']}
Volatility Forecast: {lunar_cycle['lunar_volatility']}

━━━━━━━━━━━━━━

🌙 Nakshatra Market Cycle

Nakshatra: {nak_cycle['nakshatra']}
Psychology: {nak_cycle['psychology']}
Bias: {nak_cycle['bias']}

━━━━━━━━━━━━━━

🌙 COSMIC TIMING

✨ Nakshatra: {psy['name']}
📜 Meaning: {psy['meaning']}

🌗 Tithi: {panchang['tithi']}
📜 Meaning: {tithi_text}

🌕 Moon Phase: {round(moon_phase,2)}

━━━━━━━━━━━━━━

🧠 BHRAMHA INSIGHT

This signal combines:

• Technical indicators
• Vedic time cycles
• Market psychology
• Liquidity behavior
• Volatility expansion

Trade with discipline.
"""
                strength_label = str(confidence.get("strength", strength)).upper()
                reject_reasons = []
                if vyavhar_block_reason:
                    reject_reasons.append(vyavhar_block_reason)
                if nakshatra_direction_block_reason:
                    reject_reasons.append(nakshatra_direction_block_reason)

                if current_score < min_score_required:
                    reject_reasons.append(f"score too low ({round(current_score, 2)} < {min_score_required})")
                if confidence_value < MIN_CONFIDENCE:
                    reject_reasons.append(f"confidence too low ({round(confidence_value, 2)})")
                if strength_levels.get(strength_label, 0) < MIN_STRENGTH:
                    reject_reasons.append(f"strength too low ({strength_label})")
                if rr < MIN_RR:
                    reject_reasons.append(f"risk reward too low ({round(rr, 2)})")
                    rejected_rr += 1
                
                # MTF validation is now handled by score penalties.

                if not structure_ok:
                    reject_reasons.append("against structure / no BOS+sweep confirmation")
                    rejected_structure += 1
                
                if not liquidity_ok:
                    if is_elite:
                        pass
                    elif is_strong:
                        print("[TIER 2] Pool rule warning (ignored)")
                    else:
                        reject_reasons.append("pool rule failed (no sweep and no <1 ATR liquidity distance)")
                        rejected_liquidity += 1

                if not volatility_ok:
                    reject_reasons.append("low volatility (compressed)")
                    rejected_low_volatility += 1
                
                if not entry_timing_ok:
                    if is_elite:
                        pass
                    elif is_strong:
                        pass # Already penalized above
                    else:
                        reject_reasons.append("entry timing extreme (RSI/VWAP/BB)")
                        rejected_entry_timing += 1

                if bool(bos_pullback.get("applicable")) and not bool(bos_pullback.get("pullback_met")):
                    # Only reject if strictly required (Normal Tier) or if Strong tier logic was used but failed 15%
                    # Since we recalculated bos_pullback with the correct threshold above, we just check pullback_met.
                    # However, for Elite, threshold was 0.0, so it always meets.
                    reject_reasons.append(f"BOS pullback not met (threshold {int(bos_threshold*100)}%)")
                    rejected_bos_pullback += 1

                if not reject_reasons and is_duplicate(signal_key):
                    reject_reasons.append("cooldown active")
                    rejected_cooldown += 1
                if not auto_trade_allowed:
                    active_until = str(vedic_block_status.get("active_until", "")).strip()
                    if current_vedic_period == "RAHU KALAM":
                        print("[VEDIC BLOCK] Rahu Kaal — trade blocked")
                        vedic_reason = "Rahu Kaal — trade blocked"
                    else:
                        vedic_reason = f"Vedic hard block active ({vedic_time['current_period']})"
                    if active_until:
                        vedic_reason = f"{vedic_reason} until {active_until}"
                    reject_reasons.append(vedic_reason)

                if reject_reasons:
                    print("Signal rejected:")
                    for reason in reject_reasons:
                        print(f"- {reason}")
                    print("[TELEGRAM BLOCKED] Rejected signal — not sending")
                    _log_rejected_signal(signal_data, reject_reasons)
                    continue

                signals.append({
                    "score": current_score,
                    "message": message,
                    "chart": chart_path,
                    "data": signal_data
                })
                # Mark coin as approved this scan (blocks other timeframes)
                approved_this_scan.add(coin)
                print(f"[SCAN GUARD] {coin} added to approved_this_scan — blocking other timeframes")
                # Note: win cooldown (_recent_win_cooldown) is updated by outcome_engine
                # when a trade actually closes as WIN, not here at signal approval

            except Exception as e:

                print("scan error", coin, tf, e)
            finally:
                time.sleep(0.2)

    signals = sorted(signals, key=lambda x: x["score"], reverse=True)

    top_signals = signals[:5]
    print(
        "Short rejection counts | "
        f"trend={short_rejected_trend}, "
        f"regime={short_rejected_regime}"
    )
    print(
        "Validation rejection counts | "
        f"volatility={rejected_low_volatility}, "
        f"structure={rejected_structure}, "
        f"liquidity={rejected_liquidity}, "
        f"entry_timing={rejected_entry_timing}, "
        f"bos_pullback={rejected_bos_pullback}, "
        f"rr={rejected_rr}, "
        f"mtf={rejected_mtf}, "
        f"cooldown={rejected_cooldown}"
    )

    return top_signals