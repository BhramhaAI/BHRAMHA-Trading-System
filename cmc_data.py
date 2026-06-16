# -*- coding: utf-8 -*-
"""
cmc_data.py — CoinMarketCap data layer (the "CMC Skill" backbone).
==================================================================

BNB Hack Track 2 asks for a *CMC Skill*: a strategy authored on CoinMarketCap
market data. This module is BHRAMHA's CMC integration. It wraps the three CMC
endpoints available on the standard plan and exposes them as clean, typed,
cached helpers the strategy core consumes:

  * `get_quote(symbol)`            – live price, 24h volume, market cap, and the
                                     1h/24h/7d momentum that feed the momentum and
                                     sentiment layers.
  * `get_fear_greed()`             – CMC's native Fear & Greed index (replaces the
                                     old alternative.me dependency with a true CMC
                                     source — required for a *CMC* Skill).
  * `get_global_metrics()`         – BTC dominance + total market cap, used by the
                                     regime-detection layer.
  * `get_market_context(symbol)`   – one call bundling all of the above into the
                                     context dict the strategy core expects.

Design notes
------------
* The API key is read from the environment via `config.CMC_API_KEY` (never
  hardcoded). If the key is missing or a request fails, every function degrades
  gracefully to a neutral value so the strategy/backtester never crash — they
  simply lose the CMC overlay for that bar.
* Responses are cached for `CACHE_TTL` seconds. CMC's free plan has a monthly
  call credit, and the live bot polls on a loop, so caching matters.
* OHLCV history is intentionally NOT sourced here: CMC's historical OHLCV
  endpoint requires a paid plan (verified: HTTP 403, error 1006). Candles come
  from `binance_data.get_data` instead. CMC supplies the *context* overlay
  (sentiment, dominance, quotes) that differentiates the Skill.
"""

from __future__ import annotations

import time
from typing import Any

import requests

from config import CMC_API_KEY

BASE_URL = "https://pro-api.coinmarketcap.com"
CACHE_TTL = 300  # seconds
_REQUEST_TIMEOUT = 20

_cache: dict[str, tuple[float, Any]] = {}


def _headers() -> dict[str, str]:
    return {"X-CMC_PRO_API_KEY": CMC_API_KEY or "", "Accept": "application/json"}


def _get(path: str, params: dict | None = None, cache_key: str | None = None):
    """GET a CMC endpoint with TTL caching. Returns the parsed `data` block or
    None on any failure (missing key, network, plan restriction, bad status)."""
    if not CMC_API_KEY:
        return None

    key = cache_key or f"{path}:{sorted((params or {}).items())}"
    now = time.time()
    hit = _cache.get(key)
    if hit and (now - hit[0]) < CACHE_TTL:
        return hit[1]

    try:
        r = requests.get(BASE_URL + path, headers=_headers(),
                         params=params or {}, timeout=_REQUEST_TIMEOUT)
        payload = r.json()
        status = payload.get("status", {})
        # CMC returns error_code as int on most endpoints but as a string ("0")
        # on fear-and-greed — normalize before truth-testing.
        try:
            error_code = int(status.get("error_code") or 0)
        except (TypeError, ValueError):
            error_code = 0
        if r.status_code != 200 or error_code:
            print(f"[CMC] {path} error {r.status_code}/{error_code}: {status.get('error_message')}")
            return None
        data = payload.get("data")
        _cache[key] = (now, data)
        return data
    except Exception as exc:
        print(f"[CMC] request failed for {path}: {exc}")
        return None


# ──────────────────────────────────────────────────────────────────────────────
# Quotes
# ──────────────────────────────────────────────────────────────────────────────
def _base_asset(symbol: str) -> str:
    """'BTCUSDT' -> 'BTC'. CMC quotes use the base asset symbol."""
    s = symbol.upper()
    for quote in ("USDT", "USDC", "BUSD", "USD"):
        if s.endswith(quote) and len(s) > len(quote):
            return s[: -len(quote)]
    return s


def get_quote(symbol: str) -> dict:
    """
    Live CMC quote for `symbol` (accepts 'BTC' or 'BTCUSDT').

    Returns a flat dict of the USD quote, or an empty dict if unavailable.
    """
    asset = _base_asset(symbol)
    data = _get("/v1/cryptocurrency/quotes/latest", {"symbol": asset},
                cache_key=f"quote:{asset}")
    if not data or asset not in data:
        return {}
    usd = data[asset].get("quote", {}).get("USD", {})
    return {
        "symbol": asset,
        "price": usd.get("price"),
        "volume_24h": usd.get("volume_24h"),
        "volume_change_24h": usd.get("volume_change_24h"),
        "percent_change_1h": usd.get("percent_change_1h"),
        "percent_change_24h": usd.get("percent_change_24h"),
        "percent_change_7d": usd.get("percent_change_7d"),
        "market_cap": usd.get("market_cap"),
        "market_cap_dominance": usd.get("market_cap_dominance"),
    }


# ──────────────────────────────────────────────────────────────────────────────
# Fear & Greed (native CMC sentiment)
# ──────────────────────────────────────────────────────────────────────────────
def get_fear_greed() -> dict:
    """
    CMC Fear & Greed index. Returns {value: 0-100, classification: str}.

    Falls back to a neutral 50 / "Neutral" if the endpoint is unavailable so the
    sentiment layer always has a value.
    """
    data = _get("/v3/fear-and-greed/latest", cache_key="fng")
    if not data:
        return {"value": 50, "classification": "Neutral", "source": "fallback"}
    return {
        "value": int(data.get("value", 50)),
        "classification": data.get("value_classification", "Neutral"),
        "source": "cmc",
        "update_time": data.get("update_time"),
    }


# ──────────────────────────────────────────────────────────────────────────────
# Global metrics (regime)
# ──────────────────────────────────────────────────────────────────────────────
def get_global_metrics() -> dict:
    """BTC/ETH dominance and total market cap — inputs to regime detection."""
    data = _get("/v1/global-metrics/quotes/latest", cache_key="global")
    if not data:
        return {}
    usd = data.get("quote", {}).get("USD", {})
    return {
        "btc_dominance": data.get("btc_dominance"),
        "eth_dominance": data.get("eth_dominance"),
        "total_market_cap": usd.get("total_market_cap"),
        "total_volume_24h": usd.get("total_volume_24h"),
        "altcoin_market_cap": usd.get("altcoin_market_cap"),
    }


# ──────────────────────────────────────────────────────────────────────────────
# Bundled context
# ──────────────────────────────────────────────────────────────────────────────
def get_market_context(symbol: str) -> dict:
    """
    One call returning the full CMC overlay for `symbol`: quote + sentiment +
    global regime metrics. This is what the strategy core asks CMC for on the
    live path.
    """
    return {
        "quote": get_quote(symbol),
        "fear_greed": get_fear_greed(),
        "global": get_global_metrics(),
    }


if __name__ == "__main__":  # smoke test
    import json
    print(json.dumps(get_market_context("BTCUSDT"), indent=2, default=str))
