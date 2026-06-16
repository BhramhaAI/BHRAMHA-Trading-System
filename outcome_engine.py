import os
import time
from datetime import datetime

import pandas as pd

from binance_data import get_data
from binance_http import BINANCE_HTTP_TIMEOUT, session
from resilience import safe_execute

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.path.join(BASE_DIR, "signals_log.csv")
TRACKING_COLUMNS = ["max_price_reached", "min_price_reached", "mfe_percent", "mae_percent", "insight", "close_time"]

# ── Software TP/SL threshold ──────────────────────────────────────────────────
# 0.96 = trigger market close when price reaches 96% of way from entry to TP or SL
TRIGGER_THRESHOLD = 0.96
NAKSHATRA_TIGHTENED_THRESHOLD = 0.85
SANDHYA_TIGHTENED_THRESHOLD = 0.88
CVD_EARLY_EXIT_ENABLED = True
CVD_TIGHTENED_THRESHOLD = 0.75
CVD_CACHE_TTL = 300

_cvd_cache = {}


def _latest_price(symbol: str) -> float:
    url = "https://api.binance.com/api/v3/ticker/price"
    response = session.get(url, params={"symbol": symbol}, timeout=BINANCE_HTTP_TIMEOUT)
    response.raise_for_status()
    return float(response.json()["price"])


def _latest_candle(symbol: str, timeframe: str):
    df = get_data(symbol, timeframe, limit=2)
    if df is None or df.empty:
        return None
    last = df.iloc[-1]
    return {
        "high":  float(last["high"]),
        "low":   float(last["low"]),
        "close": float(last["close"]),
    }


def _safe_float(value):
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    try:
        return float(value)
    except Exception:
        return None


def _round_percent(value: float) -> float:
    return round(max(value, 0.0) * 100.0, 2)


def _compute_excursions(direction: str, entry: float, tp: float, sl: float, max_price: float, min_price: float):
    direction = str(direction).upper().strip()
    if direction == "LONG":
        tp_distance = tp - entry
        sl_distance = entry - sl
        if tp_distance <= 0 or sl_distance <= 0:
            return None, None
        mfe = (max_price - entry) / tp_distance
        mae = (entry - min_price) / sl_distance
        return _round_percent(mfe), _round_percent(mae)
    if direction == "SHORT":
        tp_distance = entry - tp
        sl_distance = sl - entry
        if tp_distance <= 0 or sl_distance <= 0:
            return None, None
        mfe = (entry - min_price) / tp_distance
        mae = (max_price - entry) / sl_distance
        return _round_percent(mfe), _round_percent(mae)
    return None, None


def _build_insight(result: str, mfe_percent, mae_percent) -> str:
    if result == "LOSS" and mfe_percent is not None and mfe_percent > 60:
        return "Entry good, TP may be too ambitious"
    if result == "LOSS" and mae_percent is not None and mae_percent > 100:
        return "SL exceeded before close, risk may be too loose"
    if result == "WIN" and mae_percent is not None and mae_percent > 70:
        return "Target hit after deep adverse move"
    return ""


def get_price_range_since(symbol, since_time, interval="1m"):
    try:
        if pd.isna(since_time):
            return None, None
        since_ms = int(since_time.timestamp() * 1000)
        url = "https://fapi.binance.com/fapi/v1/klines"
        r = session.get(url, params={
            "symbol": symbol, "interval": interval,
            "startTime": since_ms, "limit": 1500
        }, timeout=BINANCE_HTTP_TIMEOUT)
        candles = r.json()
        if not candles or not isinstance(candles, list):
            return None, None
        highs = [float(c[2]) for c in candles]
        lows  = [float(c[3]) for c in candles]
        return max(highs), min(lows)
    except Exception as e:
        print(f"Price range check failed for {symbol}: {e}")
        return None, None


def _compute_trigger_prices(direction, entry, tp, sl, threshold=TRIGGER_THRESHOLD):
    """Returns (tp_trigger, sl_trigger) at threshold% of distance from entry."""
    if direction == "LONG":
        tp_trigger = entry + (tp - entry) * threshold
        sl_trigger = entry - (entry - sl) * threshold
    else:  # SHORT
        tp_trigger = entry - (entry - tp) * threshold
        sl_trigger = entry + (sl - entry) * threshold
    return tp_trigger, sl_trigger


def _get_cvd_divergence(symbol: str, direction: str):
    if not CVD_EARLY_EXIT_ENABLED:
        return False

    cache_key = (str(symbol).upper(), "5m")
    now = time.time()
    cached = _cvd_cache.get(cache_key)
    if cached and (now - float(cached.get("timestamp", 0))) < CVD_CACHE_TTL:
        return bool(cached.get(str(direction).upper().strip(), False))

    try:
        df = get_data(symbol, "5m", limit=20)
        if df is None or len(df) < 10:
            return False

        opens = pd.to_numeric(df["open"], errors="coerce")
        closes = pd.to_numeric(df["close"], errors="coerce")
        highs = pd.to_numeric(df["high"], errors="coerce")
        lows = pd.to_numeric(df["low"], errors="coerce")
        volumes = pd.to_numeric(df["volume"], errors="coerce").fillna(0.0)

        signed_delta = []
        for open_price, close_price, volume in zip(opens, closes, volumes):
            if float(close_price) > float(open_price):
                signed_delta.append(float(volume))
            elif float(close_price) < float(open_price):
                signed_delta.append(-float(volume))
            else:
                signed_delta.append(0.0)

        recent_cvd = sum(signed_delta[-5:])
        prior_cvd = sum(signed_delta[-10:-5])

        recent_highs = highs.tail(3).tolist()
        recent_lows = lows.tail(3).tolist()
        price_up = len(recent_highs) == 3 and recent_highs[0] < recent_highs[1] < recent_highs[2]
        price_down = len(recent_lows) == 3 and recent_lows[0] > recent_lows[1] > recent_lows[2]

        long_divergence = price_up and recent_cvd <= prior_cvd
        short_divergence = price_down and recent_cvd >= prior_cvd

        _cvd_cache[cache_key] = {
            "timestamp": now,
            "LONG": bool(long_divergence),
            "SHORT": bool(short_divergence),
        }
        return bool(_cvd_cache[cache_key].get(str(direction).upper().strip(), False))
    except Exception:
        return False


def _close_position_market(symbol: str, direction: str) -> bool:
    """Places a market close order using trade_engine's API caller."""
    try:
        from trade_engine import _api_call, get_open_positions

        positions = get_open_positions()
        pos = next((p for p in positions if p.get("symbol") == symbol), None)
        if pos is None:
            print(f"[SW-TPSL] No open position for {symbol} — may already be closed")
            return False

        qty = abs(float(pos.get("positionAmt", 0)))
        if qty == 0:
            print(f"[SW-TPSL] Position qty=0 for {symbol} — already closed")
            return False

        close_side = "SELL" if direction == "LONG" else "BUY"
        result = _api_call("POST", "/fapi/v1/order", {
            "symbol":     symbol,
            "side":       close_side,
            "type":       "MARKET",
            "quantity":   str(qty),
            "reduceOnly": "true",
        }, signed=True)

        if result and "orderId" in result:
            print(f"[SW-TPSL] ✅ Market close placed for {symbol} qty={qty} side={close_side}")
            return True
        else:
            print(f"[SW-TPSL] ❌ Market close FAILED for {symbol}: {result}")
            return False
    except Exception as e:
        print(f"[SW-TPSL] Exception closing {symbol}: {e}")
        return False


# ── Breakeven SL tracking ──────────────────────────────────────────────────────
# Stores which symbols have already had their SL moved to breakeven this session.
# Key = "{coin}_{direction}", value = True
_breakeven_moved: dict = {}

BREAKEVEN_TRIGGER_PCT = 0.60   # Move SL to breakeven when price reaches 60% of TP distance
SL_BREAKEVEN_BUFFER   = 0.001  # 0.1% buffer past entry so SL is slightly profitable, not just zero


def _move_sl_to_breakeven(symbol: str, direction: str, entry: float, sl: float,
                           price_precision: int = 4) -> bool:
    """
    Cancel the existing STOP_MARKET SL order and place a new one at breakeven.
    Called by check_trade_outcomes() when price reaches 60% of TP distance.

    Breakeven SL = entry + small buffer (so even if it triggers, we make a tiny profit
    instead of losing nothing — better than a loss on a trade that nearly reached TP).
    """
    try:
        from trade_engine import _api_call, get_open_positions

        # Verify position still open
        positions = get_open_positions()
        pos = next((p for p in positions if p.get("symbol") == symbol), None)
        if pos is None or abs(float(pos.get("positionAmt", 0))) == 0:
            print(f"[BREAKEVEN] {symbol} position no longer open — skip")
            return False

        direction = str(direction).upper()

        # Calculate breakeven price with small buffer
        if direction == "LONG":
            breakeven_sl = round(entry * (1 + SL_BREAKEVEN_BUFFER), price_precision)
        else:
            breakeven_sl = round(entry * (1 - SL_BREAKEVEN_BUFFER), price_precision)

        # Validate: breakeven SL must be on the correct side of entry
        if direction == "LONG" and breakeven_sl <= entry:
            breakeven_sl = round(entry * 1.0005, price_precision)
        elif direction == "SHORT" and breakeven_sl >= entry:
            breakeven_sl = round(entry * 0.9995, price_precision)

        # Step 1: Cancel ALL open STOP_MARKET orders for this symbol
        open_orders = _api_call("GET", "/fapi/v1/openOrders",
                                {"symbol": symbol}, signed=True)
        cancelled_any = False
        if open_orders:
            for order in open_orders:
                order_type = str(order.get("type", "")).upper()
                if order_type in {"STOP_MARKET", "STOP"}:
                    cancel_result = _api_call("DELETE", "/fapi/v1/order", {
                        "symbol":  symbol,
                        "orderId": order["orderId"],
                    }, signed=True)
                    if cancel_result:
                        print(f"[BREAKEVEN] Cancelled old SL order {order['orderId']} for {symbol}")
                        cancelled_any = True

        if not cancelled_any:
            print(f"[BREAKEVEN] No SL orders found to cancel for {symbol} — placing new one anyway")

        # Step 2: Place new STOP_MARKET at breakeven
        close_side = "SELL" if direction == "LONG" else "BUY"
        new_sl_result = _api_call("POST", "/fapi/v1/order", {
            "symbol":        symbol,
            "side":          close_side,
            "type":          "STOP_MARKET",
            "stopPrice":     str(breakeven_sl),
            "closePosition": "true",
            "workingType":   "MARK_PRICE",
            "timeInForce":   "GTC",
        }, signed=True)

        if new_sl_result and "orderId" in new_sl_result:
            print(f"[BREAKEVEN] ✅ {symbol} {direction} SL moved to breakeven {breakeven_sl} "
                  f"(was {sl:.4f}, entry {entry:.4f})")
            return True
        else:
            print(f"[BREAKEVEN] ❌ Failed to place breakeven SL for {symbol}: {new_sl_result}")
            return False

    except Exception as e:
        print(f"[BREAKEVEN] Exception for {symbol}: {e}")
        return False


def check_trade_outcomes():
    if not os.path.isfile(CSV_PATH):
        print("Outcome check skipped: signals_log.csv not found")
        return

    try:
        df = pd.read_csv(CSV_PATH, dtype={'close_time': str})
        if 'close_time' not in df.columns:
            df['close_time'] = ''
        df['close_time'] = df['close_time'].fillna('').astype(str).str.replace('nan', '')
    except Exception as e:
        print("Outcome check CSV read error:", e)
        return

    if df.empty:
        return

    df.columns = [str(c).lower().strip() for c in df.columns]
    rejected_mask = df.get("result", pd.Series(dtype=str)).fillna("").astype(str).str.upper().eq("REJECTED")
    rejected_dropped = int(rejected_mask.sum())
    if rejected_dropped:
        df = df.loc[~rejected_mask].copy()

    if "tf" in df.columns and "timeframe" not in df.columns:
        df["timeframe"] = df["tf"]

    required = [
        "time", "signal_type", "signal_quality", "coin", "timeframe", "direction",
        "entry", "tp", "sl", "basis_pct",
        "crowd_emotion", "crowd_phase", "regime", "liquidity_event",
        "nakshatra_name", "nakshatra_type", "nakshatra_bias", "nakshatra_psychology", "stop_hunt_risk", "planetary_bias",
        "volatility_bias", "sun_sign", "sun_sign_bias", "sun_transit_effect", "hora_planet", "tithi_group", "abhijit_active", "reversal_warning", "vedic_time", "timing_quality", "vedic_block",
        "lunar_volatility", "lunar_psychology", "pump_probability",
        "orderflow_pressure", "volume_accumulation", "oi_buildup",
        "result", "max_price_reached", "min_price_reached",
        "mfe_percent", "mae_percent", "insight", "close_time",
    ]

    defaults = {
        "result": "OPEN", "signal_type": "TREND", "signal_quality": "C",
        "crowd_emotion": "NEUTRAL", "crowd_phase": "BALANCED",
        "regime": "NORMAL", "liquidity_event": "None",
        "nakshatra_name": "UNKNOWN", "nakshatra_type": "OTHER", "nakshatra_bias": "NEUTRAL", "nakshatra_psychology": "balanced", "stop_hunt_risk": "NORMAL",
        "planetary_bias": "NEUTRAL", "volatility_bias": "NORMAL", "sun_sign": "UNKNOWN", "sun_sign_bias": "NEUTRAL", "sun_transit_effect": "neutral", "hora_planet": "NEUTRAL", "tithi_group": "UNKNOWN", "abhijit_active": False, "reversal_warning": False,
        "vedic_time": "NORMAL", "timing_quality": "NEUTRAL", "vedic_block": False,
        "lunar_volatility": "NORMAL",
        "lunar_psychology": "Balanced sentiment with moderate volatility.",
        "pump_probability": "LOW", "orderflow_pressure": "NEUTRAL",
        "volume_accumulation": False, "oi_buildup": False,
        "basis_pct": 0.0,
        "mfe_percent": "", "mae_percent": "", "close_time": "",
    }
    for col in required:
        if col not in df.columns:
            df[col] = defaults.get(col, "")

    df["result"] = df["result"].fillna("").astype(str).str.upper()
    df.loc[df["result"].isin(["", "NAN", "NONE"]), "result"] = "OPEN"
    open_rows = df["result"].isin(["", "OPEN"])
    if not bool(open_rows.any()) and not rejected_dropped:
        return

    updated_rows = set()
    candle_cache = {}
    price_cache  = {}
    cvd_cache    = {}

    for idx, row in df.iterrows():
        result_now = str(row.get("result", "")).upper().strip()
        if result_now not in {"", "OPEN"}:
            continue

        coin      = str(row.get("coin", "")).strip().upper()
        timeframe = str(row.get("timeframe", "")).strip()
        direction = str(row.get("direction", "")).upper().strip()
        if not coin or not timeframe or direction not in {"LONG", "SHORT"}:
            continue

        try:
            entry = float(row.get("entry"))
            tp    = float(row.get("tp"))
            sl    = float(row.get("sl"))
        except Exception:
            continue

        # ── Update candle high/low ─────────────────────────────────────────
        candle_key = (coin, timeframe)
        if candle_key not in candle_cache:
            candle_cache[candle_key] = safe_execute(
                lambda c=coin, tf=timeframe: _latest_candle(c, tf), retries=2, delay=1)
        candle = candle_cache[candle_key]
        if candle is not None:
            high = _safe_float(candle.get("high"))
            low  = _safe_float(candle.get("low"))
            stored_max = _safe_float(row.get("max_price_reached"))
            stored_min = _safe_float(row.get("min_price_reached"))
            if high is not None:
                new_max = max(high, stored_max if stored_max is not None else entry)
                if stored_max is None or new_max != stored_max:
                    df.at[idx, "max_price_reached"] = new_max
                    updated_rows.add(idx)
            if low is not None:
                new_min = min(low, stored_min if stored_min is not None else entry)
                if stored_min is None or new_min != stored_min:
                    df.at[idx, "min_price_reached"] = new_min
                    updated_rows.add(idx)

        # ── Live price ─────────────────────────────────────────────────────
        if coin not in price_cache:
            price_cache[coin] = safe_execute(
                lambda s=coin: _latest_price(s), retries=3, delay=2)
        price = price_cache[coin]
        if price is None:
            continue

        # ── Full price range since signal ──────────────────────────────────
        signal_time = pd.to_datetime(row.get("time", ""))
        max_price, min_price = get_price_range_since(coin, signal_time)

        if max_price is not None:
            stored_max = _safe_float(df.at[idx, "max_price_reached"])
            if stored_max is None or max_price > stored_max:
                df.at[idx, "max_price_reached"] = max_price
                updated_rows.add(idx)

        if min_price is not None:
            stored_min = _safe_float(df.at[idx, "min_price_reached"])
            if stored_min is None or min_price < stored_min:
                df.at[idx, "min_price_reached"] = min_price
                updated_rows.add(idx)

        # ── Always update live MFE/MAE for dashboard ───────────────────────
        calc_max = _safe_float(df.at[idx, "max_price_reached"]) or entry
        calc_min = _safe_float(df.at[idx, "min_price_reached"]) or entry
        cur_mfe, cur_mae = _compute_excursions(direction, entry, tp, sl, calc_max, calc_min)
        if cur_mfe is not None:
            df.at[idx, "mfe_percent"] = float(cur_mfe)
            updated_rows.add(idx)
        if cur_mae is not None:
            df.at[idx, "mae_percent"] = float(cur_mae)
            updated_rows.add(idx)

        # ── Software TP/SL — 96% trigger ──────────────────────────────────
        cvd_key = (coin, direction)
        if cvd_key not in cvd_cache:
            cvd_cache[cvd_key] = _get_cvd_divergence(coin, direction)
        cvd_divergence = bool(cvd_cache.get(cvd_key, False))
        trigger_threshold = CVD_TIGHTENED_THRESHOLD if cvd_divergence else TRIGGER_THRESHOLD
        stop_hunt_risk = str(row.get("stop_hunt_risk", "NORMAL")).upper().strip()
        if stop_hunt_risk == "HIGH":
            trigger_threshold = min(trigger_threshold, NAKSHATRA_TIGHTENED_THRESHOLD)
            print(f"[NAKSHATRA] Tikshna active for {coin} - tightening SL to 85%")
        reversal_warning = bool(row.get("reversal_warning", False))
        if reversal_warning:
            trigger_threshold = min(trigger_threshold, SANDHYA_TIGHTENED_THRESHOLD)
        if cvd_divergence:
            print(f"[CVD] Divergence detected for {coin} — tightening exit to 75%")

        # ── BREAKEVEN SL MOVE — when price hits 60% of TP ────────────────────
        # When price reaches 60% of TP distance, cancel the Binance SL order
        # and replace it with one at breakeven. This converts "Entry good, TP
        # too ambitious" losses into zero-loss trades without reducing the TP.
        # Tracked per symbol so it only fires once per trade.
        breakeven_key = f"{coin}_{direction}"
        if breakeven_key not in _breakeven_moved:
            # Use tp1_breakeven from CSV if signal_engine stored it, else 60% of TP
            tp1_breakeven = _safe_float(row.get("tp1_breakeven"))
            if tp1_breakeven is None:
                if direction == "LONG":
                    tp1_breakeven = entry + (tp - entry) * BREAKEVEN_TRIGGER_PCT
                else:
                    tp1_breakeven = entry - (entry - tp) * BREAKEVEN_TRIGGER_PCT

            be_triggered = False
            if direction == "LONG" and price is not None and price >= tp1_breakeven:
                be_triggered = True
            elif direction == "SHORT" and price is not None and price <= tp1_breakeven:
                be_triggered = True

            if be_triggered:
                print(f"[BREAKEVEN] {coin} {direction} reached 60% of TP "
                      f"(trigger={tp1_breakeven:.6f} price={price:.6f}) — moving SL to breakeven")
                _price_precision = 4
                try:
                    from trade_engine import _get_symbol_info
                    _, _price_precision, _ = _get_symbol_info(coin)
                except Exception:
                    pass
                success = _move_sl_to_breakeven(
                    coin, direction, entry, sl, _price_precision
                )
                if success:
                    _breakeven_moved[breakeven_key] = True
                    # Update SL in CSV so MFE/MAE continues to use correct value
                    df.at[idx, "sl"] = (
                        round(entry * (1 + SL_BREAKEVEN_BUFFER), _price_precision)
                        if direction == "LONG"
                        else round(entry * (1 - SL_BREAKEVEN_BUFFER), _price_precision)
                    )
                    updated_rows.add(idx)

        tp_trigger, sl_trigger = _compute_trigger_prices(
            direction, entry, tp, sl, threshold=trigger_threshold
        )

        new_result   = None
        triggered_by = None

        if direction == "LONG":
            # Check trigger prices against live price AND full range
            if price >= tp_trigger or (max_price is not None and max_price >= tp_trigger):
                new_result   = "WIN"
                triggered_by = "TP"
            elif price <= sl_trigger or (min_price is not None and min_price <= sl_trigger):
                new_result   = "LOSS"
                triggered_by = "SL"
        else:  # SHORT
            if price <= tp_trigger or (min_price is not None and min_price <= tp_trigger):
                new_result   = "WIN"
                triggered_by = "TP"
            elif price >= sl_trigger or (max_price is not None and max_price >= sl_trigger):
                new_result   = "LOSS"
                triggered_by = "SL"

        if new_result:
            print(f"[SW-TPSL] {coin} {direction} {triggered_by} triggered | "
                  f"price={price:.6f} tp_trig={tp_trigger:.6f} sl_trig={sl_trigger:.6f}")

            # ── Market close on testnet ────────────────────────────────────
            try:
                from config import AUTO_TRADE
                if AUTO_TRADE:
                    _close_position_market(coin, direction)
            except Exception as e:
                print(f"[SW-TPSL] AUTO_TRADE check error: {e}")

            # ── Final MFE/MAE ──────────────────────────────────────────────
            final_max = _safe_float(df.at[idx, "max_price_reached"]) or entry
            final_min = _safe_float(df.at[idx, "min_price_reached"]) or entry
            mfe_percent, mae_percent = _compute_excursions(
                direction, entry, tp, sl, final_max, final_min)

            # ── PnL alerts ─────────────────────────────────────────────────
            if new_result == "WIN":
                try:
                    from pnl_alert import send_tp_hit_alert
                    send_tp_hit_alert(df.loc[idx].to_dict(), price, mfe_percent)
                except Exception as e:
                    print(f"TP alert error: {e}")
                # Update post-win cooldown so bot doesn't re-enter same coin immediately
                try:
                    import time as _t
                    from signal_engine import _recent_win_cooldown
                    _recent_win_cooldown[coin] = _t.time()
                    print(f"[WIN COOLDOWN] {coin} win recorded — blocking re-entry for 400s")
                except Exception:
                    pass
            else:
                try:
                    from pnl_alert import send_sl_hit_alert
                    send_sl_hit_alert(df.loc[idx].to_dict(), price)
                except Exception as e:
                    print(f"SL alert error: {e}")

            try:
                from trade_engine import finalize_kartavya_log
                finalize_kartavya_log(coin, direction, new_result)
            except Exception as e:
                print(f"[KARTAVYA] finalize error: {e}")

            # ── Write result ───────────────────────────────────────────────
            df.at[idx, "result"]      = new_result
            df.at[idx, "close_time"]  = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
            df.at[idx, "mfe_percent"] = float(mfe_percent) if mfe_percent else 0.0
            df.at[idx, "mae_percent"] = float(mae_percent) if mae_percent else 0.0
            df["insight"] = df["insight"].astype(str)
            df.at[idx, "insight"] = str(_build_insight(new_result, mfe_percent, mae_percent))
            updated_rows.add(idx)

    if updated_rows or rejected_dropped:
        df.to_csv(CSV_PATH, index=False)
        print(f"Outcome check: updated {len(updated_rows)} trade(s)")
        if rejected_dropped:
            print(f"Outcome check: dropped {rejected_dropped} rejected row(s)")