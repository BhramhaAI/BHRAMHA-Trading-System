"""
BHRAMHA - Trade Engine
"""

import hashlib
import hmac
import math
import os
import time
from datetime import datetime, timezone

import pandas as pd
import requests

from binance_http import BINANCE_HTTP_TIMEOUT, session
from config import (
    AUTO_TRADE,
    BASIS_ADJUSTMENT_ENABLED,
    BINANCE_LIVE_API_KEY,
    BINANCE_LIVE_SECRET_KEY,
    BINANCE_TESTNET_API_KEY,
    BINANCE_TESTNET_SECRET_KEY,
    MAX_NOTIONAL,
    MIN_NOTIONAL,
    PORTFOLIO_STOP_PCT,
    RAHU_OVERRIDE_ENABLED,
    RAHU_OVERRIDE_MIN_SCORE,
    TRADE_CAPITAL,
    TRADE_LEVERAGE,
    TRADE_MODE,
    TRADE_SIZE,
    TITHI_SIZING_ENABLED,
    VEDIC_HARD_BLOCK_ENABLED,
)
from logger import log_signal
from telegram_bot import send_message
from vedic_time_engine import (
    get_current_nakshatra_context,
    get_current_tithi_context,
    get_muhurta_context,
    get_vedic_trade_block_status,
)
from hora_engine import get_current_hora
from smart_exit_engine import apply_basis_adjustment

# --- Constants ---
BASE_URL_LIVE    = "https://fapi.binance.com"
BASE_URL_TESTNET = "https://testnet.binancefuture.com"

if TRADE_MODE == "LIVE":
    BASE_URL   = BASE_URL_LIVE
    API_KEY    = BINANCE_LIVE_API_KEY
    SECRET_KEY = BINANCE_LIVE_SECRET_KEY
else:
    BASE_URL   = BASE_URL_TESTNET
    API_KEY    = BINANCE_TESTNET_API_KEY
    SECRET_KEY = BINANCE_TESTNET_SECRET_KEY

trading_halted      = False
last_api_call_time  = 0

balance_cache       = {"balance": None, "timestamp": 0}
positions_cache     = {"positions": None, "timestamp": 0}
exchange_info_cache = {"info": None, "timestamp": 0}

TRADES_LOG_CSV     = "trades_log.csv"
SIGNALS_LOG_CSV    = "signals_log.csv"
MAX_OPEN_TRADES    = int(TRADE_CAPITAL / TRADE_SIZE)
PORTFOLIO_SL_VALUE = TRADE_CAPITAL * PORTFOLIO_STOP_PCT
TRADE_LOG_COLUMNS = [
    "time", "coin", "direction", "entry", "tp1", "sl",
    "size", "leverage", "status", "order_id",
    "entry_rules_ok", "sl_rules_ok", "size_rules_ok",
]


# --- Helpers ---

def _sign(params):
    query = "&".join([f"{k}={v}" for k, v in params.items()])
    return hmac.new(
        SECRET_KEY.encode("utf-8"),
        query.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()


def _api_call(method, endpoint, params=None, signed=False):
    global last_api_call_time
    elapsed = time.time() - last_api_call_time
    if elapsed < 0.3:
        time.sleep(0.3 - elapsed)
    if params is None:
        params = {}
    if signed:
        params["timestamp"] = int(time.time() * 1000)
        params["signature"] = _sign(params)
    headers = {"X-MBX-APIKEY": API_KEY}
    url = f"{BASE_URL}{endpoint}"
    last_api_call_time = time.time()
    try:
        if method.upper() == "GET":
            response = session.get(url, headers=headers, params=params, timeout=BINANCE_HTTP_TIMEOUT)
        elif method.upper() == "POST":
            response = session.post(url, headers=headers, params=params, timeout=BINANCE_HTTP_TIMEOUT)
        elif method.upper() == "DELETE":
            response = session.delete(url, headers=headers, params=params, timeout=BINANCE_HTTP_TIMEOUT)
        else:
            raise ValueError(f"Unsupported HTTP method: {method}")
        if response.status_code in [429, 418]:
            print("Rate limit hit. Waiting 60s.")
            time.sleep(60)
            return _api_call(method, endpoint, params, signed)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.HTTPError as e:
        try:
            print(f"API call to {endpoint} failed: {e}")
            print(f"Binance error response: {e.response.json()}")
        except Exception:
            print(f"API call to {endpoint} failed: {e}")
        return None
    except Exception as e:
        print(f"Unexpected error during API call to {endpoint}: {e}")
        return None


def _get_exchange_info():
    now = time.time()
    if exchange_info_cache["info"] and (now - exchange_info_cache["timestamp"] < 3600):
        return exchange_info_cache["info"]
    info = _api_call("GET", "/fapi/v1/exchangeInfo")
    if info:
        exchange_info_cache["info"]      = info
        exchange_info_cache["timestamp"] = now
    return info


def _get_symbol_info(symbol):
    """Returns (qty_precision, price_precision, max_qty) for a symbol."""
    info = _get_exchange_info()
    if not info:
        return 0, 4, 99999999
    for s in info.get("symbols", []):
        if s["symbol"] == symbol:
            qty_precision   = int(s.get("quantityPrecision", 0))
            price_precision = int(s.get("pricePrecision", 4))
            max_qty = 99999999
            for f in s.get("filters", []):
                if f.get("filterType") == "LOT_SIZE":
                    max_qty = float(f.get("maxQty", 99999999))
            return qty_precision, price_precision, max_qty
    return 0, 4, 99999999


def _ensure_trades_log_exists():
    if not os.path.exists(TRADES_LOG_CSV):
        pd.DataFrame(columns=TRADE_LOG_COLUMNS).to_csv(TRADES_LOG_CSV, index=False)
        return

    try:
        df = pd.read_csv(TRADES_LOG_CSV)
    except Exception:
        pd.DataFrame(columns=TRADE_LOG_COLUMNS).to_csv(TRADES_LOG_CSV, index=False)
        return

    changed = False
    for col in TRADE_LOG_COLUMNS:
        if col not in df.columns:
            df[col] = ""
            changed = True
    if changed:
        df = df[TRADE_LOG_COLUMNS]
        df.to_csv(TRADES_LOG_CSV, index=False)


def _kartavya_yes_no(value):
    return "YES" if bool(value) else "NO"


def _print_kartavya_warning():
    print("[KARTAVYA WARNING] Rule violation attempted — blocked")


def _print_kartavya_discipline(entry_rules_ok, sl_rules_ok, size_rules_ok):
    print(f"[KARTAVYA] Duty performed. Entry followed rules: {_kartavya_yes_no(entry_rules_ok)}")
    print(f"[KARTAVYA] SL respected: {_kartavya_yes_no(sl_rules_ok)}")
    print(f"[KARTAVYA] Position size rules followed: {_kartavya_yes_no(size_rules_ok)}")


def _is_rahu_override_allowed(signal_data, vedic_block_status):
    current_period = str(vedic_block_status.get("current_period", "NORMAL")).upper()
    regime_name = str(signal_data.get("regime", "NORMAL")).upper()
    score_value = float(signal_data.get("score", 0) or 0)
    confidence_value = float(signal_data.get("confidence", 0) or 0)
    explicit_override = bool(signal_data.get("rahu_override_applied", False))
    explicit_eligible = bool(signal_data.get("rahu_override_eligible", False))
    return bool(
        RAHU_OVERRIDE_ENABLED
        and current_period == "RAHU KALAM"
        and regime_name in {"TRENDING_BULL", "TRENDING_BEAR"}
        and (
            explicit_eligible
            or (
                score_value >= float(max(RAHU_OVERRIDE_MIN_SCORE - 8, 0))
                and confidence_value >= float(max(RAHU_OVERRIDE_MIN_SCORE - 8, 0))
            )
        )
        and explicit_override
    )


def finalize_kartavya_log(symbol, direction, result):
    """Print and persist the latest discipline snapshot for a completed trade."""
    _ensure_trades_log_exists()
    try:
        df = pd.read_csv(TRADES_LOG_CSV)
        if df.empty:
            return

        symbol = str(symbol).upper().strip()
        direction = str(direction).upper().strip()
        mask = (
            df["coin"].fillna("").astype(str).str.upper().eq(symbol)
            & df["direction"].fillna("").astype(str).str.upper().eq(direction)
            & df["status"].fillna("").astype(str).str.upper().eq("OPEN")
        )
        if not mask.any():
            mask = (
                df["coin"].fillna("").astype(str).str.upper().eq(symbol)
                & df["direction"].fillna("").astype(str).str.upper().eq(direction)
            )
            if not mask.any():
                return

        idx = df[mask].index[-1]
        entry_rules_ok = str(df.at[idx, "entry_rules_ok"]).strip().upper() == "YES"
        sl_rules_ok = str(df.at[idx, "sl_rules_ok"]).strip().upper() == "YES"
        size_rules_ok = str(df.at[idx, "size_rules_ok"]).strip().upper() == "YES"
        _print_kartavya_discipline(entry_rules_ok, sl_rules_ok, size_rules_ok)
        df.at[idx, "status"] = str(result).upper().strip() or "CLOSED"
        df.to_csv(TRADES_LOG_CSV, index=False)
    except Exception as exc:
        print(f"[KARTAVYA] Unable to finalize discipline log for {symbol}: {exc}")


def _signal_row_exists(signal_data) -> bool:
    if not os.path.exists(SIGNALS_LOG_CSV):
        return False
    try:
        df = pd.read_csv(SIGNALS_LOG_CSV, on_bad_lines="skip")
        if df.empty:
            return False

        coin = str(signal_data.get("coin", "")).upper().strip()
        direction = str(signal_data.get("direction", "")).upper().strip()
        entry = float(signal_data.get("entry", 0) or 0)

        result_series = df.get("result", pd.Series(dtype=str)).fillna("").astype(str).str.upper()
        coin_series = df.get("coin", pd.Series(dtype=str)).fillna("").astype(str).str.upper()
        direction_series = df.get("direction", pd.Series(dtype=str)).fillna("").astype(str).str.upper()
        entry_series = pd.to_numeric(df.get("entry", pd.Series(dtype=float)), errors="coerce").fillna(0.0)

        mask = (
            result_series.eq("OPEN")
            & coin_series.eq(coin)
            & direction_series.eq(direction)
            & ((entry_series - entry).abs() <= max(abs(entry) * 0.002, 1e-6))
        )
        return bool(mask.any())
    except Exception:
        return False


def _ensure_signal_row_logged(signal_data, timeout_seconds: float = 5.0):
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if _signal_row_exists(signal_data):
            return
        time.sleep(0.5)

    recovery_payload = dict(signal_data)
    recovery_payload["result"] = "OPEN"
    try:
        log_signal(recovery_payload)
        print("[SYNC] Position logged to CSV after trade confirmation")
    except Exception as exc:
        print(f"[SYNC] Failed to write signal row after trade confirmation: {exc}")


# --- Public Functions ---

def get_account_balance():
    now = time.time()
    if balance_cache["balance"] is not None and (now - balance_cache["timestamp"] < 30):
        return balance_cache["balance"]
    try:
        balances = _api_call("GET", "/fapi/v2/balance", signed=True)
        if balances:
            for asset in balances:
                if asset["asset"] == "USDT":
                    bal = float(asset["balance"])
                    balance_cache["balance"]   = bal
                    balance_cache["timestamp"] = now
                    return bal
    except Exception as e:
        print(f"Failed to get balance: {e}")
    return balance_cache["balance"]


def get_open_positions():
    # Always fresh
    positions_cache["positions"] = None
    try:
        positions = _api_call("GET", "/fapi/v2/positionRisk", signed=True)
        if positions:
            open_pos = [p for p in positions if float(p.get("positionAmt", 0)) != 0]
            positions_cache["positions"] = open_pos
            positions_cache["timestamp"] = time.time()
            return open_pos
    except Exception as e:
        print(f"Failed to get positions: {e}")
    return []


def can_place_trade():
    if trading_halted:
        return False
    balance = get_account_balance()
    if balance is None or balance < TRADE_SIZE:
        return False
    open_positions = get_open_positions()
    if len(open_positions) >= MAX_OPEN_TRADES:
        return False
    return True


def close_all_positions():
    print("Closing all open positions...")
    open_positions = get_open_positions()
    if not open_positions:
        print("No open positions to close.")
        return
    for pos in open_positions:
        try:
            symbol   = pos["symbol"]
            quantity = abs(float(pos["positionAmt"]))
            side     = "BUY" if float(pos["positionAmt"]) < 0 else "SELL"
            result = _api_call("POST", "/fapi/v1/order", {
                "symbol":     symbol,
                "side":       side,
                "type":       "MARKET",
                "quantity":   str(quantity),
                "reduceOnly": "true",
            }, signed=True)
            print(f"Closed {quantity} of {symbol}" if result else f"Failed to close {symbol}")
        except Exception as e:
            print(f"Error closing {pos.get('symbol','?')}: {e}")


def check_portfolio_stop_loss():
    global trading_halted
    if trading_halted:
        return True
    balance = get_account_balance()
    if balance is not None and balance <= PORTFOLIO_SL_VALUE:
        trading_halted = True
        msg = (f"🚨 BHRAMHA PORTFOLIO STOP LOSS HIT\n"
               f"Balance: {balance:.2f} USDT\nTrading halted.")
        print(msg)
        send_message(msg)
        close_all_positions()
        return True
    return False


def place_trade(signal_data):
    """Places entry + TAKE_PROFIT_MARKET TP + STOP_MARKET SL."""
    if not AUTO_TRADE or trading_halted:
        return False

    hora_context = get_current_hora()
    tithi_context = get_current_tithi_context()
    nakshatra_context = get_current_nakshatra_context()
    vedic_block_status = get_vedic_trade_block_status()
    muhurta_context = get_muhurta_context()
    rahu_override_allowed = _is_rahu_override_allowed(signal_data, vedic_block_status)

    if VEDIC_HARD_BLOCK_ENABLED:
        if vedic_block_status.get("blocked"):
            if rahu_override_allowed and str(vedic_block_status.get("current_period", "")).upper() == "RAHU KALAM":
                print("[RAHU-OVERRIDE] Score 98 extreme signal — Rahu Kaal penalty applied, trade allowed")
            elif str(vedic_block_status.get("current_period", "")).upper() == "RAHU KALAM":
                print("[VEDIC BLOCK] Rahu Kaal — trade blocked")
                _print_kartavya_warning()
                return False
            else:
                print(vedic_block_status.get("message", "[VEDIC BLOCK] Trade blocked"))
                _print_kartavya_warning()
                return False

    try:
        symbol = signal_data["coin"]

        # Skip if already in a position for this coin
        open_positions = get_open_positions()
        if any(p.get("symbol") == symbol and float(p.get("positionAmt", 0)) != 0
               for p in open_positions):
            print(f"Skipping {symbol} — position already open")
            return False

        direction         = signal_data["direction"]
        entry             = float(signal_data["entry"])
        sl                = float(signal_data["sl"])
        tp_price          = float(signal_data["tp"])
        spot_entry        = float(signal_data.get("spot_entry", entry))
        spot_sl           = float(signal_data.get("spot_sl", sl))
        spot_tp           = float(signal_data.get("spot_tp", tp_price))
        tithi             = int(tithi_context.get("tithi", 0))
        tithi_group       = str(tithi_context.get("tithi_group", "UNKNOWN")).upper()

        if BASIS_ADJUSTMENT_ENABLED:
            try:
                basis_info = apply_basis_adjustment(symbol, spot_entry, spot_sl, spot_tp, direction)
                if basis_info.get("blocked"):
                    print(f"[BASIS BLOCK] Gap too large ({basis_info.get('basis_pct', 0.0):.3f}%) - skipping trade")
                    _print_kartavya_warning()
                    return False
                if basis_info.get("warning"):
                    print(f"[BASIS WARNING] Spot-Perp gap: {basis_info.get('basis_pct', 0.0):.3f}% - adjusting levels")
                entry = float(basis_info.get("adjusted_entry", entry))
                sl = float(basis_info.get("adjusted_sl", sl))
                tp_price = float(basis_info.get("adjusted_tp", tp_price))
            except Exception as exc:
                print(f"[BASIS] Execution-time adjustment failed for {symbol}: {exc}")

        qty_precision, price_precision, max_qty = _get_symbol_info(symbol)

        # Round TP/SL strictly to exchange price precision
        tp_rounded = round(tp_price, price_precision)
        sl_rounded = round(sl, price_precision)

        size_multiplier = 1.0
        sl_tighten_factor = 1.0
        if TITHI_SIZING_ENABLED:
            if tithi_group == "RIKTA":
                size_multiplier = 0.5
                print("[VEDIC] Rikta Tithi active — reducing position to 50%")
            elif tithi_group == "JAYA":
                size_multiplier = 1.1
            elif tithi_group == "PURNA":
                size_multiplier = 0.8

            if tithi == 30:
                size_multiplier = 0.5
                sl_tighten_factor = 0.8
                print("[VEDIC] Amavasya active — reducing position to 50% and tightening SL by 20%")
            elif tithi == 15:
                size_multiplier = 0.7
                sl_tighten_factor = 0.85
                print("[VEDIC] Purnima active — reducing position to 70% and tightening SL by 15%")

        if rahu_override_allowed:
            size_multiplier *= 0.7

        if sl_tighten_factor != 1.0:
            sl_distance = abs(entry - sl_rounded)
            tightened_distance = sl_distance * sl_tighten_factor
            if direction == "SHORT":
                sl_rounded = round(entry + tightened_distance, price_precision)
            else:
                sl_rounded = round(entry - tightened_distance, price_precision)

        # Validate TP/SL on correct side
        if direction == "SHORT":
            if tp_rounded >= entry:
                tp_rounded = round(entry * 0.995, price_precision)
            if sl_rounded <= entry:
                sl_rounded = round(entry * 1.005, price_precision)
        else:
            if tp_rounded <= entry:
                tp_rounded = round(entry * 1.005, price_precision)
            if sl_rounded >= entry:
                sl_rounded = round(entry * 0.995, price_precision)

        print(f"TP: {tp_rounded}, SL: {sl_rounded}, Entry: {entry}, Direction: {direction}")

        entry_rules_ok = bool(
            not vedic_block_status.get("blocked")
            and not any(
                p.get("symbol") == symbol and float(p.get("positionAmt", 0)) != 0
                for p in open_positions
            )
        )
        sl_rules_ok = bool(
            (direction == "LONG" and sl_rounded < entry)
            or (direction == "SHORT" and sl_rounded > entry)
        )

        # Set leverage
        lev_result = _api_call("POST", "/fapi/v1/leverage",
                               {"symbol": symbol, "leverage": TRADE_LEVERAGE}, signed=True)
        if lev_result:
            print(f"Leverage set: {lev_result}")
        time.sleep(0.3)

        # Apply Tithi sizing on top of the fixed-margin model, then clamp to hard notional caps.
        base_notional = TRADE_SIZE * TRADE_LEVERAGE
        position_notional = base_notional * size_multiplier
        position_notional = max(MIN_NOTIONAL, min(MAX_NOTIONAL, position_notional))

        quantity = round(position_notional / entry, qty_precision)
        if quantity > max_qty:
            quantity = round(max_qty, qty_precision)
        if quantity <= 0:
            print(f"Quantity is zero for {symbol}. Skipping.")
            return False

        notional        = round(quantity * entry, 2)
        if notional < MIN_NOTIONAL:
            print(f"Notional {notional} below MIN_NOTIONAL {MIN_NOTIONAL} for {symbol}. Skipping.")
            return False
        size_rules_ok   = bool(quantity > 0 and quantity <= max_qty and MIN_NOTIONAL <= notional <= MAX_NOTIONAL)
        sl_distance_pct = abs(entry - sl_rounded) / entry
        dollar_risk     = round(notional * sl_distance_pct, 2)
        effective_margin = round(notional / TRADE_LEVERAGE, 2)

        print(f"Tithi: {tithi} ({tithi_group}) | Effective margin: ${effective_margin} | Notional: ${notional} | "
              f"Qty: {quantity} | SL dist: {round(sl_distance_pct*100,3)}% | "
              f"Max loss if SL: ~${dollar_risk}")

        # ── 1. Market entry ────────────────────────────────────────────────
        side = "BUY" if direction == "LONG" else "SELL"
        entry_result = _api_call("POST", "/fapi/v1/order", {
            "symbol":   symbol,
            "side":     side,
            "type":     "MARKET",
            "quantity": str(quantity),
        }, signed=True)

        if not entry_result or "orderId" not in entry_result:
            print(f"Market order failed for {symbol}: {entry_result}")
            return False

        order_id   = entry_result["orderId"]
        close_side = "SELL" if direction == "LONG" else "BUY"
        print(f"Market order placed for {symbol}, ID: {order_id}")

        # ── Wait for position to register ──────────────────────────────────
        print("Waiting for position to register...")
        position_confirmed = False
        for attempt in range(15):
            time.sleep(0.2)
            fresh = get_open_positions()
            pos = next((p for p in fresh if p.get("symbol") == symbol), None)
            if pos and abs(float(pos.get("positionAmt", 0))) > 0:
                print(f"Position confirmed! Actual qty: {abs(float(pos['positionAmt']))} ✅")
                position_confirmed = True
                break

        if not position_confirmed:
            print("Position never registered — skipping TP/SL placement")
            return False

        # ── 2. TAKE_PROFIT_MARKET TP ───────────────────────────────────────
        tp_result = _api_call("POST", "/fapi/v1/order", {
            "symbol":        symbol,
            "side":          close_side,
            "type":          "TAKE_PROFIT_MARKET",
            "stopPrice":     str(tp_rounded),
            "closePosition": "true",
            "workingType":   "MARK_PRICE",
            "timeInForce":   "GTC",
        }, signed=True)

        if tp_result and "orderId" in tp_result:
            print(f"TP order placed for {symbol} at {tp_rounded} ✅")
        else:
            print(f"TP order FAILED for {symbol}: {tp_result}")
        time.sleep(0.2)

        # ── 3. STOP_MARKET SL ─────────────────────────────────────────────
        sl_result = _api_call("POST", "/fapi/v1/order", {
            "symbol":        symbol,
            "side":          close_side,
            "type":          "STOP_MARKET",
            "stopPrice":     str(sl_rounded),
            "closePosition": "true",
            "workingType":   "MARK_PRICE",
            "timeInForce":   "GTC",
        }, signed=True)

        if sl_result and "orderId" in sl_result:
            print(f"SL order placed for {symbol} at {sl_rounded} ?")
            sl_rules_ok = bool(sl_rules_ok and True)
        else:
            print(f"SL order FAILED for {symbol}: {sl_result}")
            sl_rules_ok = False

        # ?? 4. Log ????????????????????????????????????????????????????????
        _ensure_trades_log_exists()
        pd.DataFrame([{
            "time":      datetime.now(timezone.utc).isoformat(),
            "coin":      symbol,
            "direction": direction,
            "entry":     entry,
            "tp1":       tp_rounded,
            "sl":        sl_rounded,
            "size":      effective_margin,
            "leverage":  TRADE_LEVERAGE,
            "status":    "OPEN",
            "order_id":  order_id,
            "entry_rules_ok": _kartavya_yes_no(entry_rules_ok),
            "sl_rules_ok": _kartavya_yes_no(sl_rules_ok),
            "size_rules_ok": _kartavya_yes_no(size_rules_ok),
        }]).to_csv(TRADES_LOG_CSV, mode="a", header=False, index=False)
        _ensure_signal_row_logged(signal_data)

        return True

    except Exception as e:
        print(f"place_trade error for {signal_data.get('coin','?')}: {e}")
        return False


def get_trading_status():
    balance      = get_account_balance()
    positions    = get_open_positions()
    open_trades  = len(positions) if positions else 0
    total_risked = sum(float(p.get("initialMargin", 0)) for p in positions) if positions else 0
    return {
        "balance":        balance,
        "open_trades":    open_trades,
        "total_risked":   total_risked,
        "trading_halted": trading_halted,
        "pnl_today":      0,
    }
