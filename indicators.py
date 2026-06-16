from __future__ import annotations


def add_indicators(df):
    """Add core momentum/volatility indicators used by the signal stack."""
    import pandas as pd

    df = df.copy()
    close = pd.to_numeric(df["close"], errors="coerce")
    high = pd.to_numeric(df["high"], errors="coerce")
    low = pd.to_numeric(df["low"], errors="coerce")
    volume = pd.to_numeric(df["volume"], errors="coerce")

    try:
        import ta

        df["rsi"] = ta.momentum.rsi(close, window=14)

        macd = ta.trend.MACD(close)
        df["macd"] = macd.macd()

        df["ema50"] = ta.trend.ema_indicator(close, window=50)

        df["atr"] = ta.volatility.average_true_range(high, low, close)

        bb = ta.volatility.BollingerBands(close=close, window=20, window_dev=2)
        df["bb_upper"] = bb.bollinger_hband()
        df["bb_lower"] = bb.bollinger_lband()
        df["bb_mid"] = bb.bollinger_mavg()
    except Exception:
        # Fallback indicators to avoid hard dependency on third-party TA libs.
        delta = close.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)

        avg_gain = gain.rolling(14, min_periods=14).mean()
        avg_loss = loss.rolling(14, min_periods=14).mean()
        rs = avg_gain / avg_loss.replace(0, pd.NA)
        df["rsi"] = 100 - (100 / (1 + rs))

        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        df["macd"] = ema12 - ema26

        df["ema50"] = close.ewm(span=50, adjust=False).mean()

        prev_close = close.shift(1)
        tr = pd.concat(
            [(high - low), (high - prev_close).abs(), (low - prev_close).abs()],
            axis=1,
        ).max(axis=1)
        df["atr"] = tr.rolling(14, min_periods=14).mean()

        bb_mid = close.rolling(20, min_periods=20).mean()
        bb_std = close.rolling(20, min_periods=20).std(ddof=0)
        df["bb_upper"] = bb_mid + (2 * bb_std)
        df["bb_lower"] = bb_mid - (2 * bb_std)
        df["bb_mid"] = bb_mid

    # Session VWAP approximation from available window.
    typical_price = (high + low + close) / 3.0
    cum_tpv = (typical_price * volume).cumsum()
    cum_volume = volume.cumsum().replace(0, pd.NA)
    df["vwap"] = cum_tpv / cum_volume

    cols = ["rsi", "macd", "ema50", "atr", "bb_upper", "bb_lower", "bb_mid", "vwap"]
    df[cols] = df[cols].ffill().bfill()
    return df
