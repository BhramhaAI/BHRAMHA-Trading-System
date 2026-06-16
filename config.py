"""
BHRAMHA Config (FIXED v2)
=========================
Key changes:
- TECH_MAX_BONUS added (was missing from original, causing ImportError potential)
- MIN_RR raised to 1.8 (was 1.5) — filters low quality setups
- ASTRO_MAX_PENALTY relaxed to -30 (was -25) — allows stronger astro filters to work
- CONSOLIDATION_LONG_BLOCK_ENABLED = True — stops LONG entries in consolidation
- SHORT_ONLY_IN_BEAR_ENABLED = False — kept OFF, we handle this in signal_engine now
- TIMEFRAMES updated to ["5m", "15m"] for better signal quality
- WARNING: Rotate your API keys — they were visible in compiled bytecode.
"""

# ──────────────────────────────────────────────────────────────────────────────
# SECRETS — loaded from environment / .env, never hardcoded.
# Copy .env.example to .env and fill in your own keys. .env is gitignored.
# ──────────────────────────────────────────────────────────────────────────────
import os as _os


def _load_dotenv():
    """Minimal zero-dependency .env loader. Populates os.environ without
    overwriting variables already set in the real environment."""
    here = _os.path.dirname(_os.path.abspath(__file__))
    env_path = _os.path.join(here, ".env")
    if not _os.path.exists(env_path):
        return
    try:
        with open(env_path, "r", encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if key and key not in _os.environ:
                    _os.environ[key] = val
    except Exception as exc:  # pragma: no cover - config must never hard-crash
        print(f"[config] .env load skipped: {exc}")


_load_dotenv()


def _env(name: str, default: str = "") -> str:
    return _os.environ.get(name, default)


# Exchange (Binance) — only needed for the LIVE execution path (Track 1).
# Track 2 (the CMC Skill + backtester) does not require these.
BINANCE_LIVE_API_KEY    = _env("BINANCE_LIVE_API_KEY")
BINANCE_LIVE_SECRET_KEY = _env("BINANCE_LIVE_SECRET_KEY")
BINANCE_TESTNET_API_KEY    = _env("BINANCE_TESTNET_API_KEY")
BINANCE_TESTNET_SECRET_KEY = _env("BINANCE_TESTNET_SECRET_KEY")

# Telegram alerts (optional)
TELEGRAM_BOT_TOKEN = _env("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = _env("TELEGRAM_CHAT_ID")

# Track 2 data + LLM
CMC_API_KEY  = _env("CMC_API_KEY")   # CoinMarketCap (powers the CMC Skill)
GROQ_API_KEY = _env("GROQ_API_KEY")  # Groq LLM (authors/explains the strategy)

# ── Trading mode ──────────────────────────────────────────────────────────────
AUTO_TRADE  = True
TRADE_MODE  = "TESTNET"   # "LIVE" or "TESTNET"

# ── Capital & sizing ──────────────────────────────────────────────────────────
TRADE_CAPITAL  = 220
TRADE_SIZE     = 11
TRADE_LEVERAGE = 20
MIN_NOTIONAL   = 110
MAX_NOTIONAL   = 220
PORTFOLIO_STOP_PCT = 0.5   # 50%

# ── Signal quality gates ──────────────────────────────────────────────────────
MIN_RR = 1.8                    # was 1.5 — raised to improve R:R quality
MIN_CANDLE_AGE_MINUTES = 2
MOMENTUM_CANDLES_REQUIRED = 2
ENTRY_CONFIRMATION_ENABLED = True
LATE_ENTRY_FILTER_ENABLED  = True
LATE_ENTRY_THRESHOLD_PCT   = 2.0

# ── Vedic / astro filters ─────────────────────────────────────────────────────
VEDIC_HARD_BLOCK_ENABLED         = True
RAHU_OVERRIDE_ENABLED            = True
RAHU_OVERRIDE_MIN_SCORE          = 98
NAKSHATRA_DIRECTION_BLOCK_ENABLED = True
NAKSHATRA_SCORING_ENABLED         = True
HORA_SCORING_ENABLED              = True
MUHURTA_SCORING_ENABLED           = True
TITHI_SIZING_ENABLED              = True
LUNAR_VOLATILITY_GATE_ENABLED     = True
LUNAR_LONG_BLOCK_ENABLED          = False

# ── Regime / direction filters ────────────────────────────────────────────────
CONSOLIDATION_LONG_BLOCK_ENABLED = True   # FIX v2: was False — now blocks LONG in consolidation
SHORT_ONLY_IN_BEAR_ENABLED       = False  # handled dynamically in signal_engine now
VYAVHAR_HARD_BLOCK_ENABLED       = False
VYAVHAR_BLOCK_THRESHOLD          = 0.20
SESSION_SCORE_GATE_ENABLED       = True

# ── Score caps ────────────────────────────────────────────────────────────────
# TECH_MAX_BONUS: max points that technical bonuses can add on top of base score
TECH_MAX_BONUS   = 65    # technical layer gets 65% weight
ASTRO_MAX_BONUS  = 55    # raised: session(+6) + nakshatra + macro(+20) all live here
ASTRO_MAX_PENALTY = -50  # raised: NY Open(-10)+Asia Open(-15)+nakshatra(-8)+macro(-20)=-53 raw
ASTRO_ADVISORY_MODE = True

# ── Exit / SL settings ────────────────────────────────────────────────────────
DYNAMIC_SL_ENABLED       = True
WIN_PATTERN_BONUS_ENABLED = True
BASIS_ADJUSTMENT_ENABLED  = True
MAX_BASIS_PCT             = 0.8
BASIS_SL_BUFFER           = 0.002

# ── API timeouts ──────────────────────────────────────────────────────────────
BINANCE_API_CONNECT_TIMEOUT  = 10
BINANCE_API_READ_TIMEOUT     = 30
BINANCE_API_TIMEOUT          = (BINANCE_API_CONNECT_TIMEOUT, BINANCE_API_READ_TIMEOUT)
BINANCE_HTTP_TIMEOUT         = BINANCE_API_TIMEOUT
BINANCE_API_MAX_RETRIES      = 3
BINANCE_API_RETRY_DELAY_SECONDS = 3

# ── Scan intervals ────────────────────────────────────────────────────────────
SCAN_INTERVAL       = 300
SCALP_SCAN_INTERVAL = 180

# ── Coins ─────────────────────────────────────────────────────────────────────
COINS = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
    "ADAUSDT", "DOGEUSDT", "TRXUSDT", "AVAXUSDT", "DOTUSDT",
    "POLUSDT", "LINKUSDT", "LTCUSDT", "ATOMUSDT", "ETCUSDT",
    "XLMUSDT", "APTUSDT", "OPUSDT", "ARBUSDT", "NEARUSDT",
]

# ── Timeframes ────────────────────────────────────────────────────────────────
# FIX v2: Added 15m — 5m alone is too noisy; 15m filters out bad setups.
TIMEFRAMES       = ["5m", "15m"]
SCALP_TIMEFRAMES = ["1m", "5m"]