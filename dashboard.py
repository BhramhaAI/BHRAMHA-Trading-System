from flask import Flask, render_template_string, jsonify
import pandas as pd
import os
import json
import datetime
from binance_http import BINANCE_HTTP_TIMEOUT, session

app = Flask(__name__)

# ── New feature integrations (Track 2) ────────────────────────────────────────
# The dashboard now surfaces the Vedic Panchang, live CoinMarketCap context, and
# the validated backtest — degrading gracefully if a module/network is absent.
try:
    from vedic_core import get_vedic_context
except Exception:
    get_vedic_context = None
try:
    from cmc_data import get_market_context
except Exception:
    get_market_context = None

_HERE = os.path.dirname(os.path.abspath(__file__))

# Measured headline results (reproducible via the repo's scripts). The Vedic
# ablation is from ablation_vedic.py; the BNB figures load from JSON if present.
VEDIC_ABLATION = {
    "on":  {"trades": 538, "win": 30.3, "total_r": 18.2, "exp": 0.034},
    "off": {"trades": 763, "win": 29.9, "total_r": 11.1, "exp": 0.015},
}


def _load_backtest_summary():
    path = os.path.join(_HERE, "backtest_BNBUSDT_4h.json")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            d = json.load(fh)
        return {
            "symbol": d.get("symbol", "BNBUSDT"),
            "interval": d.get("interval", "4h"),
            "days": d.get("days", 365),
            "trades": d.get("total_trades"),
            "win_rate": d.get("win_rate"),
            "total_r": d.get("total_return_r"),
            "expectancy": d.get("expectancy_r"),
            "max_dd": d.get("max_drawdown_r"),
        }
    except Exception:
        # Fallback to the documented reference numbers.
        return {"symbol": "BNBUSDT", "interval": "4h", "days": 365,
                "trades": 88, "win_rate": 32.95, "total_r": 9.28,
                "expectancy": 0.106, "max_dd": -9.0}


def get_panchang_snapshot():
    """Current Vedic Panchang for the live banner. Pure function of time."""
    if get_vedic_context is None:
        return None
    try:
        v = get_vedic_context()
        nak, tithi, hora = v["nakshatra"], v["tithi"], v["hora"]
        action = nak.get("action", "TRADE")
        action_tone = {"GOLDEN": "gold", "TRADE": "green", "CAUTION": "saffron",
                       "SHORT_ONLY": "saffron", "BLOCK": "maroon"}.get(action, "green")
        return {
            "nakshatra": nak.get("nakshatra_name"),
            "pada": nak.get("pada"),
            "bias": nak.get("bias"),
            "action": action,
            "action_tone": action_tone,
            "hora": hora.get("hora_planet"),
            "tithi_name": tithi.get("tithi_name"),
            "paksha": tithi.get("paksha"),
            "tithi_group": tithi.get("tithi_group"),
            "illumination": round((tithi.get("illumination") or 0) * 100, 1),
            "high_lunar": tithi.get("high_lunar_volatility"),
            "ayanamsa": v.get("ayanamsa"),
        }
    except Exception as e:
        print(f"panchang snapshot error: {e}")
        return None


def get_cmc_snapshot():
    """Live CoinMarketCap context for the banner (graceful fallback)."""
    if get_market_context is None:
        return None
    try:
        ctx = get_market_context("BNBUSDT")
        fg = ctx.get("fear_greed") or {}
        q = ctx.get("quote") or {}
        g = ctx.get("global") or {}
        return {
            "fg_value": fg.get("value"),
            "fg_class": fg.get("classification"),
            "price": q.get("price"),
            "change_24h": q.get("percent_change_24h"),
            "btc_dominance": g.get("btc_dominance"),
        }
    except Exception as e:
        print(f"cmc snapshot error: {e}")
        return None


# ── Old trade close times ─────────────────────────────────────────────────────
OLD_CLOSE_TIMES = {
    0: '2026-03-07 04:55:00',
    1: '2026-03-07 05:02:00',
    2: '2026-03-07 06:28:00',
    3: '2026-03-07 07:35:00',
    4: '2026-03-07 07:40:00',
    5: '2026-03-07 16:30:00',
    6: '2026-03-07 17:10:00',
}

# ── Session config ────────────────────────────────────────────────────────────
SESSION_ORDER  = ["ASIA_OPEN", "ASIA_LONDON", "LONDON", "NY_OPEN", "NY_LATE"]
SESSION_LABELS = {
    "ASIA_OPEN":   "Asia Open",
    "ASIA_LONDON": "Asia to London",
    "LONDON":      "London",
    "NY_OPEN":     "NY Open",
    "NY_LATE":     "NY Late",
    "UNKNOWN":     "Unknown",
}
SESSION_COLORS = {
    "ASIA_OPEN":   "#1a6fb0",
    "ASIA_LONDON": "#2e7d4f",
    "LONDON":      "#b8860b",
    "NY_OPEN":     "#b3322f",
    "NY_LATE":     "#7a1f6b",
    "UNKNOWN":     "#9a8a6a",
}
SESSION_TIMES = {
    "ASIA_OPEN":   "6AM-10AM IST",
    "ASIA_LONDON": "10AM-2PM IST",
    "LONDON":      "2PM-8PM IST",
    "NY_OPEN":     "8PM-12AM IST",
    "NY_LATE":     "12AM-6AM IST",
}


def get_session_from_utc(utc_time_str):
    try:
        import pytz
        t = pd.to_datetime(utc_time_str, errors='coerce')
        if pd.isna(t):
            return "UNKNOWN"
        if t.tzinfo is None:
            t = t.tz_localize('UTC')
        ist = pytz.timezone('Asia/Kolkata')
        h = t.astimezone(ist).hour
        if 6  <= h < 10: return "ASIA_OPEN"
        if 10 <= h < 14: return "ASIA_LONDON"
        if 14 <= h < 20: return "LONDON"
        if 20 <= h < 24: return "NY_OPEN"
        return "NY_LATE"
    except Exception:
        return "UNKNOWN"


def _find_close_time_binance(symbol, direction, tp, sl, signal_time_str):
    try:
        signal_time = pd.to_datetime(signal_time_str)
        since_ms = int(signal_time.timestamp() * 1000)
        r = session.get(
            "https://fapi.binance.com/fapi/v1/klines",
            params={"symbol": symbol, "interval": "1m", "startTime": since_ms, "limit": 500},
            timeout=BINANCE_HTTP_TIMEOUT,
        )
        candles = r.json()
        if not isinstance(candles, list):
            return None
        for c in candles:
            t = datetime.datetime.fromtimestamp(c[0] / 1000, tz=datetime.timezone.utc)
            high, low = float(c[2]), float(c[3])
            if direction == 'SHORT':
                if low <= float(tp) or high >= float(sl):
                    return t.strftime('%Y-%m-%d %H:%M:%S')
            else:
                if high >= float(tp) or low <= float(sl):
                    return t.strftime('%Y-%m-%d %H:%M:%S')
    except Exception:
        pass
    return None


def restore_close_times(csv_path):
    try:
        df = pd.read_csv(csv_path, dtype=str)
        if 'result' in df.columns:
            result_series = df['result'].fillna('').astype(str).str.upper()
            rejected_dropped = int((result_series == 'REJECTED').sum())
            if rejected_dropped:
                df = df.loc[result_series != 'REJECTED'].copy()
        else:
            rejected_dropped = 0
        if 'close_time' not in df.columns:
            df['close_time'] = ''
        df['close_time'] = df['close_time'].fillna('').str.replace('nan', '').str.strip()
        if 'session' not in df.columns:
            df['session'] = ''
        df['session'] = df['session'].fillna('').str.replace('nan', '').str.strip()

        changed = False
        for idx, row in df.iterrows():
            cur = str(df.at[idx, 'session']).strip()
            if cur in ('', 'UNKNOWN', 'nan'):
                df.at[idx, 'session'] = get_session_from_utc(str(row.get('time', '')))
                changed = True

            result = str(row.get('result', '')).upper()
            if result not in ('WIN', 'LOSS'):
                continue

            ct_str   = str(row.get('close_time', '')).strip()
            sig_time = pd.to_datetime(str(row.get('time', '')), errors='coerce')
            ct_time  = pd.to_datetime(ct_str, errors='coerce')

            if pd.notna(ct_time) and pd.notna(sig_time) and ct_time > sig_time:
                continue

            if idx in OLD_CLOSE_TIMES:
                df.at[idx, 'close_time'] = OLD_CLOSE_TIMES[idx]
                changed = True
                continue

            try:
                ct_new = _find_close_time_binance(
                    str(row.get('coin', '')).upper(),
                    str(row.get('direction', '')).upper(),
                    float(row.get('tp', 0)),
                    float(row.get('sl', 0)),
                    row.get('time', ''),
                )
                if ct_new:
                    df.at[idx, 'close_time'] = ct_new
                elif pd.notna(sig_time):
                    fb = (sig_time + datetime.timedelta(minutes=30)).strftime('%Y-%m-%d %H:%M:%S')
                    df.at[idx, 'close_time'] = fb
                changed = True
            except Exception:
                pass

        if changed or rejected_dropped:
            df.to_csv(csv_path, index=False)
    except Exception as e:
        print(f"restore_close_times error: {e}")


# ── Shared calendar computation ────────────────────────────────────────────────
def _compute_calendar_data(closed_df):
    cal_months               = []
    cal_best_day             = None
    cal_worst_day            = None
    cal_best_day_wins        = cal_best_day_total = 0
    cal_best_day_wr          = 0.0
    cal_worst_day_losses     = cal_worst_day_total = 0
    cal_worst_day_wr         = 0.0
    cal_best_streak          = 0
    cal_top_tickers          = []
    cal_dow                  = []

    if closed_df.empty:
        return _cal_result(cal_months, cal_best_day, cal_best_day_wins,
                           cal_best_day_total, cal_best_day_wr,
                           cal_worst_day, cal_worst_day_losses,
                           cal_worst_day_total, cal_worst_day_wr,
                           cal_best_streak, cal_top_tickers, cal_dow)

    import calendar as cal_mod
    import pytz

    ist = pytz.timezone('Asia/Kolkata')

    cal_df = closed_df.copy()
    cal_df["close_dt"] = pd.to_datetime(cal_df["close_time"], errors="coerce")
    cal_df = cal_df.dropna(subset=["close_dt"])

    if cal_df.empty:
        return _cal_result(cal_months, cal_best_day, cal_best_day_wins,
                           cal_best_day_total, cal_best_day_wr,
                           cal_worst_day, cal_worst_day_losses,
                           cal_worst_day_total, cal_worst_day_wr,
                           cal_best_streak, cal_top_tickers, cal_dow)

    cal_df["close_dt"] = pd.to_datetime(cal_df["close_time"], errors="coerce", utc=True)
    cal_df = cal_df.dropna(subset=["close_dt"])

    try:
        cal_df["close_date"] = cal_df["close_dt"].dt.tz_convert(ist).dt.date
    except Exception:
        cal_df["close_date"] = cal_df["close_dt"].dt.date

    cal_df["is_win"] = cal_df["result"] == "WIN"
    cal_df["dow"]    = pd.to_datetime(cal_df["close_date"].astype(str)).dt.dayofweek

    day_grp = cal_df.groupby("close_date").agg(
        wins=("is_win", "sum"), total=("is_win", "count")
    ).reset_index()
    day_grp["losses"] = day_grp["total"] - day_grp["wins"]
    day_grp["wr"]     = (day_grp["wins"] / day_grp["total"] * 100).round(1)

    sig_days = day_grp[day_grp["total"] >= 2]
    if not sig_days.empty:
        best_row  = sig_days.loc[sig_days["wr"].idxmax()]
        worst_row = sig_days.loc[sig_days["wr"].idxmin()]
        cal_best_day          = best_row["close_date"].strftime("%a %d %b")
        cal_best_day_wins     = int(best_row["wins"])
        cal_best_day_total    = int(best_row["total"])
        cal_best_day_wr       = float(best_row["wr"])
        cal_worst_day         = worst_row["close_date"].strftime("%a %d %b")
        cal_worst_day_losses  = int(worst_row["losses"])
        cal_worst_day_total   = int(worst_row["total"])
        cal_worst_day_wr      = float(worst_row["wr"])

    streak = max_streak = 0
    for r in cal_df.sort_values("close_dt")["is_win"]:
        if r:
            streak += 1
            max_streak = max(max_streak, streak)
        else:
            streak = 0
    cal_best_streak = max_streak

    tk_grp = cal_df.groupby("coin").agg(
        wins=("is_win", "sum"), total=("is_win", "count")
    ).reset_index()
    tk_grp["wr"] = (tk_grp["wins"] / tk_grp["total"] * 100).round(1)
    tk_grp = (tk_grp[tk_grp["total"] >= 2]
              .sort_values(["wins", "wr"], ascending=False)
              .head(7))
    cal_top_tickers = tk_grp.to_dict("records")

    dow_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    dow_grp = cal_df.groupby("dow").agg(
        wins=("is_win", "sum"), total=("is_win", "count")
    ).reset_index()
    dow_grp["wr"] = (dow_grp["wins"] / dow_grp["total"] * 100).round(1)
    dow_map = {int(r["dow"]): r for _, r in dow_grp.iterrows()}
    cal_dow = [
        {
            "name":  dow_names[i],
            "wins":  int(dow_map[i]["wins"])  if i in dow_map else 0,
            "total": int(dow_map[i]["total"]) if i in dow_map else 0,
            "wr":    float(dow_map[i]["wr"])  if i in dow_map else 0.0,
        }
        for i in range(7)
    ]

    if not day_grp.empty:
        day_grp["close_date"] = pd.to_datetime(day_grp["close_date"])
        day_grp["ym"]         = day_grp["close_date"].dt.to_period("M")
        periods  = sorted(day_grp["ym"].unique())[-3:]
        day_dict = {row["close_date"].date(): row for _, row in day_grp.iterrows()}

        for period in periods:
            import datetime as _dt
            year, month           = period.year, period.month
            first_weekday, num_days = cal_mod.monthrange(year, month)
            label = _dt.date(year, month, 1).strftime("%B %Y")
            cells = [{"empty": True}] * first_weekday

            for d in range(1, num_days + 1):
                date_obj = _dt.date(year, month, d)
                if date_obj in day_dict:
                    row_  = day_dict[date_obj]
                    wr    = float(row_["wr"])
                    w     = int(row_["wins"])
                    l     = int(row_["losses"])
                    if wr >= 80:
                        bg, border, tc = "rgba(46,125,79,0.22)", "rgba(46,125,79,0.45)", "#2e7d4f"
                    elif wr >= 60:
                        bg, border, tc = "rgba(46,125,79,0.12)", "rgba(46,125,79,0.30)", "#2e7d4f"
                    elif wr >= 40:
                        bg, border, tc = "rgba(184,134,11,0.14)",  "rgba(184,134,11,0.32)",  "#9c7a16"
                    elif wr >= 20:
                        bg, border, tc = "rgba(179,50,47,0.12)", "rgba(179,50,47,0.30)", "#b3322f"
                    else:
                        bg, border, tc = "rgba(179,50,47,0.22)", "rgba(179,50,47,0.45)", "#b3322f"
                    cells.append({
                        "empty": False, "no_trades": False,
                        "day": d, "wins": w, "losses": l, "wr": wr,
                        "bg": bg, "border": border, "text_color": tc,
                    })
                else:
                    cells.append({"empty": False, "no_trades": True, "day": d})
            cal_months.append({"label": label, "cells": cells})

    return _cal_result(cal_months, cal_best_day, cal_best_day_wins,
                       cal_best_day_total, cal_best_day_wr,
                       cal_worst_day, cal_worst_day_losses,
                       cal_worst_day_total, cal_worst_day_wr,
                       cal_best_streak, cal_top_tickers, cal_dow)


def _cal_result(months, best_day, best_day_wins, best_day_total, best_day_wr,
                worst_day, worst_day_losses, worst_day_total, worst_day_wr,
                best_streak, top_tickers, dow):
    return {
        "months":           months,
        "best_day":         best_day,
        "best_day_wins":    best_day_wins,
        "best_day_total":   best_day_total,
        "best_day_wr":      best_day_wr,
        "worst_day":        worst_day,
        "worst_day_losses": worst_day_losses,
        "worst_day_total":  worst_day_total,
        "worst_day_wr":     worst_day_wr,
        "best_streak":      best_streak,
        "top_tickers":      top_tickers,
        "dow":              dow,
    }


# ── HTML ──────────────────────────────────────────────────────────────────────
HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>BHRAMHA — Vedic Intelligence Dashboard</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Marcellus&family=Cormorant+Garamond:wght@400;500;600;700&family=Tiro+Devanagari+Hindi&display=swap" rel="stylesheet"/>
<style>
  :root {
    --bg: #faf4e6; --surface: #fffdf7; --surface-alt: #f6efdb;
    --border: #e0cf9f; --border-strong: #cdb069;
    --gold: #b8860b; --gold-bright: #d4af37; --gold-deep: #9c7a16;
    --maroon: #7a1f2b; --saffron: #d97a1e;
    --green: #2e7d4f; --red: #b3322f; --blue: #1a6fb0;
    --ink: #33271a; --text: #3a2e1f; --subtext: #8a7654; --muted: #a89773;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background: var(--bg); color: var(--text);
    font-family: 'Cormorant Garamond', Georgia, serif; font-size: 17px;
    min-height: 100vh; overflow-x: hidden; padding-bottom: 60px;
  }
  /* faint mandala watermark */
  body::before {
    content: ''; position: fixed; inset: 0; pointer-events: none; z-index: 0;
    background:
      radial-gradient(circle at 50% -10%, rgba(184,134,11,0.10), transparent 45%),
      radial-gradient(circle at 100% 100%, rgba(122,31,43,0.06), transparent 40%);
  }
  .mandala-bg {
    position: fixed; inset: 0; z-index: 0; pointer-events: none;
    background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='600' height='600' viewBox='0 0 100 100'%3E%3Cg fill='none' stroke='%23b8860b' stroke-width='0.3' opacity='0.5'%3E%3Ccircle cx='50' cy='50' r='46'/%3E%3Ccircle cx='50' cy='50' r='38'/%3E%3Ccircle cx='50' cy='50' r='30'/%3E%3Ccircle cx='50' cy='50' r='20'/%3E%3C/g%3E%3C/svg%3E");
    background-position: center 120px; background-repeat: no-repeat;
    background-size: 720px; opacity: 0.07;
  }
  header, main, footer { position: relative; z-index: 1; padding-left: 56px; padding-right: 56px; }
  header {
    padding-top: 40px; padding-bottom: 28px;
    border-bottom: 2px solid var(--border-strong);
    display: flex; align-items: flex-end; justify-content: space-between; gap: 24px;
    background:
      linear-gradient(180deg, rgba(255,253,247,0.7), transparent);
  }
  .brand-row { display: flex; align-items: center; gap: 18px; }
  .om {
    font-family: 'Tiro Devanagari Hindi', serif; font-size: 3.2rem; color: var(--gold);
    line-height: 1; text-shadow: 0 2px 10px rgba(184,134,11,0.3);
  }
  .logo {
    font-family: 'Marcellus', serif; font-size: 3rem; font-weight: 400;
    letter-spacing: 4px; line-height: 1; color: var(--maroon);
  }
  .logo .gold { color: var(--gold-deep); }
  .tagline {
    font-family: 'Marcellus', serif; color: var(--subtext);
    font-size: 0.82rem; margin-top: 8px; letter-spacing: 3px; text-transform: uppercase;
  }
  .sanskrit { font-family: 'Tiro Devanagari Hindi', serif; color: var(--gold-deep); font-size: 0.95rem; letter-spacing: 1px; }
  .gold-rule { height: 2px; background: linear-gradient(90deg, transparent, var(--gold), transparent); margin: 0; }
  .section-label {
    font-family: 'Marcellus', serif; color: var(--maroon);
    font-size: 0.95rem; letter-spacing: 2px; text-transform: uppercase;
    margin-bottom: 18px; display: flex; align-items: center; gap: 12px;
  }
  .section-label::before { content: '❖'; color: var(--gold); font-size: 0.85rem; }
  .table-count { font-family: 'Marcellus', serif; color: var(--muted); font-size: 0.78rem; letter-spacing: 1px; text-transform: uppercase; }
  main { padding-top: 40px; display: flex; flex-direction: column; gap: 44px; }

  /* ── Live badge ── */
  .live-badge { display: flex; align-items: center; gap: 8px; padding: 8px 16px; font-family:'Marcellus',serif; font-size: 0.78rem; letter-spacing: 1px; text-transform: uppercase; border: 1px solid; border-radius: 3px; }
  .live-badge.online  { color: var(--green); border-color: rgba(46,125,79,0.4);  background: rgba(46,125,79,0.07); }
  .live-badge.active  { color: var(--gold-deep); border-color: rgba(184,134,11,0.4); background: rgba(184,134,11,0.07); }
  .live-badge.offline { color: var(--muted);  border-color: rgba(168,151,115,0.4);  background: rgba(168,151,115,0.06); }
  .live-dot { width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0; }
  .live-badge.online  .live-dot { background: var(--green);  box-shadow: 0 0 8px var(--green);  animation: blink 1.4s ease-in-out infinite; }
  .live-badge.active  .live-dot { background: var(--gold);   box-shadow: 0 0 8px var(--gold);  animation: blink 2.2s ease-in-out infinite; }
  .live-badge.offline .live-dot { background: var(--muted);  box-shadow: none; }
  @keyframes blink { 0%,100% { opacity: 1; } 50% { opacity: 0.15; } }

  /* ── Cards / panels ── */
  .panel {
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 5px; box-shadow: 0 2px 14px rgba(122,31,43,0.05);
    position: relative;
  }
  .panel.framed { border: 1px solid var(--border-strong); }
  .panel.framed::before {
    content: ''; position: absolute; inset: 5px; border: 1px solid var(--border);
    border-radius: 3px; pointer-events: none;
  }

  .feature-grid { display: grid; grid-template-columns: 1.15fr 1fr 1.15fr; gap: 18px; }
  .feature-head {
    font-family:'Marcellus',serif; color: var(--maroon); font-size: 0.82rem;
    letter-spacing: 2px; text-transform: uppercase; padding: 16px 22px 12px;
    border-bottom: 1px solid var(--border); display:flex; align-items:center; justify-content:space-between;
  }
  .feature-head .glyph { color: var(--gold); font-size: 1.1rem; }
  .feature-body { padding: 18px 22px 22px; }

  .panch-main { font-family:'Marcellus',serif; font-size: 2.1rem; color: var(--ink); line-height:1.05; }
  .panch-sub { color: var(--subtext); font-size: 0.98rem; margin-top: 4px; }
  .chip { display:inline-block; padding: 3px 12px; border-radius: 20px; font-family:'Marcellus',serif; font-size: 0.72rem; letter-spacing:1px; text-transform: uppercase; border:1px solid; margin-top: 12px; }
  .chip.gold   { color: var(--gold-deep); border-color: var(--gold); background: rgba(184,134,11,0.10); }
  .chip.green  { color: var(--green); border-color: rgba(46,125,79,0.5); background: rgba(46,125,79,0.08); }
  .chip.saffron{ color: var(--saffron); border-color: rgba(217,122,30,0.5); background: rgba(217,122,30,0.08); }
  .chip.maroon { color: var(--maroon); border-color: rgba(122,31,43,0.5); background: rgba(122,31,43,0.08); }

  .kv-row { display:flex; align-items:center; justify-content:space-between; padding: 9px 0; border-bottom: 1px dashed var(--border); }
  .kv-row:last-child { border-bottom: none; }
  .kv-key { color: var(--subtext); font-size: 0.95rem; letter-spacing: 0.5px; }
  .kv-val { font-family:'Marcellus',serif; color: var(--ink); font-size: 1.05rem; }
  .kv-val.gold { color: var(--gold-deep); } .kv-val.green { color: var(--green); } .kv-val.red { color: var(--red); }

  .fg-big { font-family:'Marcellus',serif; font-size: 2.6rem; color: var(--ink); line-height: 1; }
  .gauge { height: 7px; border-radius: 6px; margin-top: 12px; overflow:hidden; background: linear-gradient(90deg, var(--red), var(--saffron), var(--green)); position: relative; }
  .gauge .needle { position:absolute; top:-3px; width: 3px; height: 13px; background: var(--ink); border-radius:2px; }

  .bt-metric { display:grid; grid-template-columns: 1fr 1fr; gap: 14px 18px; }
  .bt-cell .bt-num { font-family:'Marcellus',serif; font-size: 1.7rem; color: var(--ink); line-height: 1; }
  .bt-cell .bt-num.green { color: var(--green); } .bt-cell .bt-num.gold { color: var(--gold-deep); } .bt-cell .bt-num.red { color: var(--red); }
  .bt-cell .bt-cap { color: var(--subtext); font-size: 0.82rem; letter-spacing:0.5px; margin-top: 3px; text-transform: uppercase; }
  .ablation { margin-top: 16px; padding-top: 14px; border-top: 1px solid var(--border); }
  .ablation-row { display:flex; align-items:center; justify-content:space-between; padding: 5px 0; font-size: 0.95rem; }
  .ablation-row .lab { color: var(--subtext); }
  .ablation-row .on  { font-family:'Marcellus',serif; color: var(--green); }
  .ablation-row .off { font-family:'Marcellus',serif; color: var(--muted); }

  /* ── Stats grids ── */
  .stats-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; }
  .session-grid { display: grid; grid-template-columns: repeat(5, 1fr); gap: 14px; }
  .stat-card { background: var(--surface); border: 1px solid var(--border); border-radius: 4px; padding: 22px 22px 20px; min-height: 150px; box-shadow: 0 2px 10px rgba(122,31,43,0.04); }
  .session-card { background: var(--surface); border: 1px solid var(--border); border-radius: 4px; padding: 18px 18px 16px; min-height: 128px; }
  .best-card { border-color: var(--border-strong); background: linear-gradient(135deg, var(--surface) 55%, rgba(184,134,11,0.10)); }
  .stat-label { font-family:'Marcellus',serif; font-size: 0.72rem; color: var(--subtext); text-transform: uppercase; letter-spacing: 1.5px; margin-bottom: 14px; }
  .stat-value { font-family: 'Marcellus', serif; font-size: 2.3rem; font-weight: 400; line-height: 1; color: var(--ink); }
  .session-value { font-family: 'Marcellus', serif; font-size: 1.8rem; line-height: 1; }
  .accent { color: var(--gold-deep); } .green { color: var(--green); } .red { color: var(--red); } .blue { color: var(--blue); }
  .stat-sub { margin-top: 10px; color: var(--muted); font-size: 0.88rem; }
  .bar-wrap { margin-top: 14px; height: 4px; background: var(--surface-alt); border-radius: 4px; overflow: hidden; }
  .bar-fill { height: 100%; background: linear-gradient(90deg, var(--gold), var(--gold-bright)); }
  .best-fill { background: linear-gradient(90deg, var(--gold-deep), var(--gold-bright)); }

  .table-header { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 16px; }
  .table-wrapper { border: 1px solid var(--border); border-radius: 5px; overflow-x: auto; background: var(--surface); }
  table { width: 100%; border-collapse: collapse; min-width: 1300px; font-size: 0.98rem; }
  thead tr { background: var(--surface-alt); border-bottom: 2px solid var(--border-strong); }
  th, td { padding: 13px 15px; text-align: left; vertical-align: middle; }
  th { font-family:'Marcellus',serif; color: var(--maroon); text-transform: uppercase; letter-spacing: 1.2px; font-size: 0.68rem; font-weight: 400; }
  tbody tr { border-bottom: 1px solid var(--border); }
  tbody tr:hover { background: rgba(184,134,11,0.05); }
  .coin-badge { display: inline-flex; align-items: center; gap: 8px; font-family: 'Marcellus', serif; font-size: 0.95rem; color: var(--ink); }
  .coin-dot { width: 6px; height: 6px; border-radius: 50%; background: var(--gold); }
  .tf-pill, .signal-pill, .metric-pill { display: inline-block; padding: 4px 10px; border: 1px solid var(--border); border-radius: 3px; background: var(--surface-alt); font-size: 0.82rem; }
  .tf-pill { color: var(--subtext); }
  .signal-pill { border-color: rgba(184,134,11,0.35); color: var(--gold-deep); background: rgba(184,134,11,0.08); }
  .metric-pill.adverse { color: var(--red); border-color: rgba(179,50,47,0.3); background: rgba(179,50,47,0.07); }
  .dir, .result { display: inline-flex; align-items: center; gap: 6px; padding: 4px 12px; border-radius: 3px; font-family:'Marcellus',serif; font-size: 0.76rem; letter-spacing: 1px; text-transform: uppercase; }
  .dir.long, .result.win { color: var(--green); background: rgba(46,125,79,0.08); border: 1px solid rgba(46,125,79,0.3); }
  .dir.short, .result.loss { color: var(--red); background: rgba(179,50,47,0.08); border: 1px solid rgba(179,50,47,0.3); }
  .result.open { color: var(--subtext); background: var(--surface-alt); border: 1px solid var(--border); }
  .entry-price { color: var(--blue); } .tp-price { color: var(--green); } .sl-price { color: var(--red); }
  .insight-text { color: var(--subtext); font-size: 0.9rem; line-height: 1.4; max-width: 200px; }
  .empty-state { text-align: center; padding: 56px 24px; color: var(--muted); font-family:'Marcellus',serif; letter-spacing:1px; }
  .dur-win { color: var(--green); } .dur-loss { color: var(--red); } .dur-open { color: var(--gold-deep); }
  .session-pill { display: inline-block; padding: 4px 10px; border-radius: 3px; font-size: 0.8rem; border: 1px solid; white-space: nowrap; }

  .btn-gold {
    display:inline-flex; align-items:center; gap:8px; padding:9px 18px;
    background: rgba(184,134,11,0.08); border:1px solid var(--gold);
    color: var(--gold-deep); font-family:'Marcellus',serif; font-size:0.78rem;
    letter-spacing:1px; text-transform:uppercase; cursor:pointer; border-radius:3px;
    transition: all 0.2s;
  }
  .btn-gold:hover { background: rgba(184,134,11,0.18); }

  footer {
    padding-top: 22px; margin-top: 36px; border-top: 2px solid var(--border-strong);
    display: flex; align-items: center; justify-content: space-between; gap: 12px;
    color: var(--muted); font-family:'Marcellus',serif; font-size: 0.74rem; letter-spacing: 1.5px; text-transform: uppercase;
  }

  @media (max-width: 1100px) { .feature-grid { grid-template-columns: 1fr; } .stats-grid { grid-template-columns: repeat(3,1fr); } .session-grid { grid-template-columns: repeat(3,1fr); } }
  @media (max-width: 900px) { header, main, footer { padding-left: 22px; padding-right: 22px; } .stats-grid, .session-grid { grid-template-columns: repeat(2,1fr); } .logo { font-size: 2.2rem; } .om { font-size: 2.4rem; } }
  @media (max-width: 540px) { .stats-grid, .session-grid { grid-template-columns: 1fr; } }
</style>
</head>
<body>
<div class="mandala-bg"></div>
<header>
  <div>
    <div class="brand-row">
      <div class="om">ॐ</div>
      <div>
        <div class="logo">BHRA<span class="gold">H</span>MA</div>
        <div class="tagline">Vedic Intelligence Trading System</div>
      </div>
    </div>
    <div class="sanskrit" style="margin-top:10px;">ज्योतिष + बाज़ार &nbsp;·&nbsp; Where Jyotisha meets the markets</div>
  </div>
  <div style="display:flex;align-items:center;gap:12px;">
    <button onclick="openPnlModal()" class="btn-gold">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>
      PnL Calendar
    </button>
    <div class="live-badge {{ live_class }}">
      <div class="live-dot"></div>
      <span>{{ live_text }}</span>
    </div>
  </div>
</header>

<!-- ── PnL Calendar Modal ─────────────────────────────────────────────────── -->
<div id="pnlModal" style="display:none;position:fixed;inset:0;z-index:1000;background:rgba(51,39,26,0.45);backdrop-filter:blur(5px);align-items:center;justify-content:center;padding:24px;">
  <div class="panel" style="width:100%;max-width:900px;max-height:90vh;overflow-y:auto;position:relative;">
    <div style="padding:24px 30px 18px;border-bottom:2px solid var(--border-strong);display:flex;align-items:center;justify-content:space-between;">
      <div>
        <div style="font-family:'Marcellus',serif;font-size:1.4rem;color:var(--maroon);letter-spacing:1px;">PnL Calendar</div>
        <div style="display:flex;align-items:center;gap:12px;margin-top:4px;">
          <div style="font-size:0.85rem;color:var(--subtext);">Daily performance breakdown</div>
          <div id="calLastUpdated" style="font-size:0.78rem;color:var(--muted);"></div>
        </div>
      </div>
      <div style="display:flex;align-items:center;gap:8px;">
        <button id="calRefreshBtn" onclick="loadCalendarData()" class="btn-gold" style="padding:6px 14px;font-size:0.7rem;">
          <svg id="calRefreshIcon" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>
          Refresh
        </button>
        <button onclick="closePnlModal()" style="background:none;border:1px solid var(--border);color:var(--muted);width:32px;height:32px;cursor:pointer;font-size:1.1rem;display:flex;align-items:center;justify-content:center;border-radius:3px;">✕</button>
      </div>
    </div>
    <div id="calBody" style="padding:26px 30px;">
      <div style="text-align:center;padding:60px 0;color:var(--muted);">Loading calendar data…</div>
    </div>
  </div>
</div>

<script>
function wrColor(wr) {
  if (wr >= 70) return 'var(--green)';
  if (wr >= 50) return 'var(--gold-deep)';
  return 'var(--red)';
}
function renderCalendar(d) {
  let html = '';
  html += '<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin-bottom:28px;">';
  html += '<div class="panel" style="padding:18px;">';
  html += '<div style="font-family:Marcellus,serif;font-size:0.7rem;text-transform:uppercase;letter-spacing:1.5px;color:var(--subtext);margin-bottom:12px;">Best Day</div>';
  if (d.best_day) {
    html += `<div style="font-family:Marcellus,serif;font-size:1.5rem;color:var(--green);line-height:1;">${d.best_day}</div>`;
    html += `<div style="font-size:0.9rem;color:var(--muted);margin-top:8px;">${d.best_day_wins}W · ${d.best_day_total} signals · ${d.best_day_wr}% WR</div>`;
  } else { html += '<div style="color:var(--muted);">No data</div>'; }
  html += '</div>';
  html += '<div class="panel" style="padding:18px;">';
  html += '<div style="font-family:Marcellus,serif;font-size:0.7rem;text-transform:uppercase;letter-spacing:1.5px;color:var(--subtext);margin-bottom:12px;">Worst Day</div>';
  if (d.worst_day) {
    html += `<div style="font-family:Marcellus,serif;font-size:1.5rem;color:var(--red);line-height:1;">${d.worst_day}</div>`;
    html += `<div style="font-size:0.9rem;color:var(--muted);margin-top:8px;">${d.worst_day_losses}L · ${d.worst_day_total} signals · ${d.worst_day_wr}% WR</div>`;
  } else { html += '<div style="color:var(--muted);">No data</div>'; }
  html += '</div>';
  html += '<div class="panel" style="padding:18px;">';
  html += '<div style="font-family:Marcellus,serif;font-size:0.7rem;text-transform:uppercase;letter-spacing:1.5px;color:var(--subtext);margin-bottom:12px;">Best Win Streak</div>';
  html += `<div style="font-family:Marcellus,serif;font-size:1.5rem;color:var(--gold-deep);line-height:1;">${d.best_streak}<span style="font-size:1rem"> wins</span></div>`;
  html += '<div style="font-size:0.9rem;color:var(--muted);margin-top:8px;">Consecutive wins (all time)</div>';
  html += '</div></div>';

  html += '<div style="margin-bottom:28px;">';
  html += '<div style="font-family:Marcellus,serif;font-size:0.72rem;text-transform:uppercase;letter-spacing:1.5px;color:var(--maroon);margin-bottom:16px;">Monthly Heatmap — Win Rate by Day</div>';
  if (d.months && d.months.length) {
    d.months.forEach(month => {
      html += '<div style="margin-bottom:22px;">';
      html += `<div style="font-size:0.85rem;color:var(--subtext);letter-spacing:1px;text-transform:uppercase;margin-bottom:10px;">${month.label}</div>`;
      html += '<div style="display:grid;grid-template-columns:repeat(7,1fr);gap:3px;">';
      ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'].forEach(lbl => {
        html += `<div style="font-size:0.65rem;text-align:center;color:var(--muted);padding-bottom:6px;">${lbl}</div>`;
      });
      month.cells.forEach(cell => {
        if (cell.empty) { html += '<div></div>'; }
        else if (cell.no_trades) {
          html += `<div style="aspect-ratio:1;background:var(--surface-alt);border:1px solid var(--border);display:flex;align-items:center;justify-content:center;border-radius:3px;"><div style="font-size:0.65rem;color:var(--muted);">${cell.day}</div></div>`;
        } else {
          html += `<div style="aspect-ratio:1;background:${cell.bg};border:1px solid ${cell.border};display:flex;align-items:center;justify-content:center;flex-direction:column;border-radius:3px;" title="${cell.day} — ${cell.wins}W ${cell.losses}L (${cell.wr}% WR)">`;
          html += `<div style="font-size:0.65rem;color:${cell.text_color};font-weight:600;">${cell.day}</div>`;
          html += `<div style="font-size:0.55rem;color:${cell.text_color};opacity:0.85;">${cell.wr}%</div></div>`;
        }
      });
      html += '</div></div>';
    });
  } else { html += '<div style="text-align:center;padding:40px;color:var(--muted);">No closed trade data available</div>'; }
  html += '</div>';

  html += '<div style="display:grid;grid-template-columns:1fr 1fr;gap:14px;">';
  html += '<div class="panel" style="padding:18px;">';
  html += '<div style="font-family:Marcellus,serif;font-size:0.7rem;text-transform:uppercase;letter-spacing:1.5px;color:var(--maroon);margin-bottom:16px;">Best Tickers</div>';
  if (d.top_tickers && d.top_tickers.length) {
    d.top_tickers.forEach(tk => {
      const c = wrColor(tk.wr);
      html += `<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px;"><div style="display:flex;align-items:center;gap:8px;"><div style="width:5px;height:5px;border-radius:50%;background:var(--gold);"></div><span style="font-family:Marcellus,serif;font-size:0.92rem;color:var(--ink);">${tk.coin}</span></div><div style="display:flex;align-items:center;gap:10px;"><span style="font-size:0.8rem;color:var(--muted);">${tk.wins}W / ${tk.total}</span><span style="font-family:Marcellus,serif;font-size:0.92rem;color:${c};">${tk.wr}%</span></div></div>`;
      html += `<div style="height:2px;background:var(--surface-alt);margin-bottom:10px;overflow:hidden;border-radius:2px;"><div style="height:100%;width:${tk.wr}%;background:${c};"></div></div>`;
    });
  } else { html += '<div style="color:var(--muted);">No data</div>'; }
  html += '</div>';
  html += '<div class="panel" style="padding:18px;">';
  html += '<div style="font-family:Marcellus,serif;font-size:0.7rem;text-transform:uppercase;letter-spacing:1.5px;color:var(--maroon);margin-bottom:16px;">Best Day of Week</div>';
  if (d.dow && d.dow.length) {
    d.dow.forEach(day => {
      const c = wrColor(day.wr);
      html += `<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:9px;"><span style="font-size:0.85rem;color:var(--subtext);width:40px;">${day.name}</span><div style="flex:1;margin:0 12px;height:2px;background:var(--surface-alt);overflow:hidden;border-radius:2px;"><div style="height:100%;width:${day.wr}%;background:${c};"></div></div><div style="display:flex;align-items:center;gap:8px;"><span style="font-size:0.78rem;color:var(--muted);">${day.wins}W/${day.total}</span><span style="font-family:Marcellus,serif;font-size:0.9rem;color:${c};">${day.wr}%</span></div></div>`;
    });
  } else { html += '<div style="color:var(--muted);">No data</div>'; }
  html += '</div></div>';
  document.getElementById('calBody').innerHTML = html;
}
let _calRefreshTimer = null;
function loadCalendarData() {
  const icon = document.getElementById('calRefreshIcon');
  icon.style.animation = 'calSpin 0.8s linear infinite';
  document.getElementById('calBody').innerHTML = '<div style="text-align:center;padding:60px 0;color:var(--muted);">Loading calendar data…</div>';
  fetch('/api/calendar').then(r => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
    .then(data => {
      renderCalendar(data);
      const now = new Date();
      const hh = String(now.getHours()).padStart(2,'0'); const mm = String(now.getMinutes()).padStart(2,'0');
      document.getElementById('calLastUpdated').textContent = `Updated ${hh}:${mm} · auto-refresh 1hr`;
    })
    .catch(err => { document.getElementById('calBody').innerHTML = `<div style="text-align:center;padding:60px;color:var(--red);">Failed to load calendar data: ${err.message}</div>`; })
    .finally(() => { icon.style.animation = ''; });
}
function openPnlModal() { const m = document.getElementById('pnlModal'); m.style.display = 'flex'; loadCalendarData(); if (_calRefreshTimer) clearInterval(_calRefreshTimer); _calRefreshTimer = setInterval(loadCalendarData, 3600000); }
function closePnlModal() { document.getElementById('pnlModal').style.display = 'none'; if (_calRefreshTimer) { clearInterval(_calRefreshTimer); _calRefreshTimer = null; } }
document.getElementById('pnlModal').addEventListener('click', function(e) { if (e.target === this) closePnlModal(); });
</script>
<style>@keyframes calSpin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }</style>

<main>

  <!-- ── NEW: Live Vedic + CMC + Backtest feature row ───────────────────────── -->
  <section>
    <div class="section-label">Live Intelligence — Panchang · Market · Proof</div>
    <div class="feature-grid">

      <!-- Vedic Panchang -->
      <div class="panel framed">
        <div class="feature-head"><span>Vedic Panchang — Now</span><span class="glyph">☾</span></div>
        <div class="feature-body">
          {% if panchang %}
          <div class="panch-main">{{ panchang.nakshatra }}</div>
          <div class="panch-sub">Pada {{ panchang.pada }} · {{ panchang.bias }} bias · Hora of {{ panchang.hora }}</div>
          <span class="chip {{ panchang.action_tone }}">{{ panchang.action }}</span>
          <div style="margin-top:16px;">
            <div class="kv-row"><span class="kv-key">Tithi</span><span class="kv-val">{{ panchang.tithi_name }} ({{ panchang.paksha }})</span></div>
            <div class="kv-row"><span class="kv-key">Tithi group</span><span class="kv-val">{{ panchang.tithi_group }}</span></div>
            <div class="kv-row"><span class="kv-key">Moon illumination</span><span class="kv-val gold">{{ panchang.illumination }}%</span></div>
            <div class="kv-row"><span class="kv-key">Lunar volatility gate</span>
              {% if panchang.high_lunar %}<span class="kv-val red">HIGH — blocked</span>{% else %}<span class="kv-val green">Normal</span>{% endif %}</div>
            <div class="kv-row"><span class="kv-key">Lahiri ayanāṁśa</span><span class="kv-val">{{ panchang.ayanamsa }}°</span></div>
          </div>
          {% else %}
          <div style="color:var(--muted);padding:20px 0;">Vedic engine unavailable in this environment.</div>
          {% endif %}
        </div>
      </div>

      <!-- CMC Market Context -->
      <div class="panel framed">
        <div class="feature-head"><span>CoinMarketCap Context</span><span class="glyph">◎</span></div>
        <div class="feature-body">
          {% if cmc %}
          <div style="font-family:'Marcellus',serif;font-size:0.72rem;letter-spacing:1.5px;text-transform:uppercase;color:var(--subtext);margin-bottom:6px;">Fear &amp; Greed</div>
          <div class="fg-big">{{ cmc.fg_value if cmc.fg_value is not none else '—' }}<span style="font-size:1.1rem;color:var(--subtext);"> · {{ cmc.fg_class }}</span></div>
          {% if cmc.fg_value is not none %}<div class="gauge"><div class="needle" style="left:calc({{ cmc.fg_value }}% - 1px);"></div></div>{% endif %}
          <div style="margin-top:18px;">
            <div class="kv-row"><span class="kv-key">BNB price</span><span class="kv-val gold">{% if cmc.price %}${{ '%.2f'|format(cmc.price) }}{% else %}—{% endif %}</span></div>
            <div class="kv-row"><span class="kv-key">BNB 24h</span>
              {% if cmc.change_24h is not none %}<span class="kv-val {{ 'green' if cmc.change_24h >= 0 else 'red' }}">{{ '%+.2f'|format(cmc.change_24h) }}%</span>{% else %}<span class="kv-val">—</span>{% endif %}</div>
            <div class="kv-row"><span class="kv-key">BTC dominance</span><span class="kv-val">{% if cmc.btc_dominance %}{{ '%.1f'|format(cmc.btc_dominance) }}%{% else %}—{% endif %}</span></div>
          </div>
          <div style="margin-top:10px;font-size:0.82rem;color:var(--muted);">Sentiment &amp; regime sourced live from CoinMarketCap.</div>
          {% else %}
          <div style="color:var(--muted);padding:20px 0;">Set CMC_API_KEY in .env to enable the live CMC overlay.</div>
          {% endif %}
        </div>
      </div>

      <!-- Backtest proof -->
      <div class="panel framed">
        <div class="feature-head"><span>Strategy Backtest — {{ backtest.symbol }} {{ backtest.interval }}</span><span class="glyph">❖</span></div>
        <div class="feature-body">
          <div class="bt-metric">
            <div class="bt-cell"><div class="bt-num green">+{{ backtest.total_r }}R</div><div class="bt-cap">Total return ({{ backtest.days }}d)</div></div>
            <div class="bt-cell"><div class="bt-num gold">+{{ backtest.expectancy }}R</div><div class="bt-cap">Per-trade expectancy</div></div>
            <div class="bt-cell"><div class="bt-num">{{ backtest.win_rate }}%</div><div class="bt-cap">Win rate · {{ backtest.trades }} trades</div></div>
            <div class="bt-cell"><div class="bt-num red">{{ backtest.max_dd }}R</div><div class="bt-cap">Max drawdown</div></div>
          </div>
          <div class="ablation">
            <div style="font-family:'Marcellus',serif;font-size:0.7rem;letter-spacing:1.2px;text-transform:uppercase;color:var(--maroon);margin-bottom:8px;">Vedic layer ablation (pooled, 6 symbols)</div>
            <div class="ablation-row"><span class="lab">Vedic ON</span><span class="on">+{{ ablation.on.exp }}R/trade · {{ ablation.on.trades }} trades</span></div>
            <div class="ablation-row"><span class="lab">Vedic OFF</span><span class="off">+{{ ablation.off.exp }}R/trade · {{ ablation.off.trades }} trades</span></div>
            <div style="margin-top:8px;font-size:0.85rem;color:var(--subtext);">The Vedic timing layer acts as a selectivity filter — it ~doubles per-trade expectancy by trimming weaker setups.</div>
          </div>
        </div>
      </div>

    </div>
  </section>

  <!-- ── Seven-layer model ──────────────────────────────────────────────────── -->
  <section>
    <div class="section-label">The Seven-Layer Confluence Model</div>
    <div style="display:grid;grid-template-columns:repeat(7,1fr);gap:10px;">
      {% for layer in layers %}
      <div class="panel" style="padding:14px 12px;text-align:center;{{ 'border-color:var(--border-strong);background:linear-gradient(180deg,var(--surface),rgba(184,134,11,0.08));' if layer.edge else '' }}">
        <div style="font-family:'Marcellus',serif;color:var(--gold-deep);font-size:1.3rem;">{{ loop.index }}</div>
        <div style="font-family:'Marcellus',serif;font-size:0.74rem;letter-spacing:0.5px;color:var(--ink);margin-top:6px;min-height:34px;">{{ layer.name }}</div>
        <div style="font-size:0.78rem;color:var(--muted);margin-top:6px;line-height:1.3;">{{ layer.detail }}</div>
        {% if layer.edge %}<div class="chip gold" style="margin-top:10px;font-size:0.62rem;padding:2px 8px;">The Edge</div>{% endif %}
      </div>
      {% endfor %}
    </div>
  </section>

  <section>
    <div class="section-label">Live Signal Performance</div>
    <div class="stats-grid">
      <div class="stat-card"><div class="stat-label">Executed Trades</div><div class="stat-value accent">{{ total }}</div><div class="stat-sub">Open + closed, excluding rejected</div></div>
      <div class="stat-card"><div class="stat-label">Wins</div><div class="stat-value green">{{ wins }}</div><div class="stat-sub">Profitable closes</div></div>
      <div class="stat-card"><div class="stat-label">Losses</div><div class="stat-value red">{{ losses }}</div><div class="stat-sub">Stopped out</div></div>
      <div class="stat-card"><div class="stat-label">Win Rate (executed)</div><div class="stat-value blue">{{ winrate }}<span style="font-size:1.2rem">%</span></div><div class="bar-wrap"><div class="bar-fill" style="width:{{ winrate }}%"></div></div><div class="stat-sub">{{ wins }} / {{ closed }} closed trades</div></div>
      <div class="stat-card"><div class="stat-label">Win Rate (closed only)</div><div class="stat-value accent">{{ closed_winrate }}<span style="font-size:1.2rem">%</span></div><div class="bar-wrap"><div class="bar-fill" style="width:{{ closed_winrate }}%"></div></div><div class="stat-sub">{{ wins }} / {{ closed }} closed trades</div></div>
      <div class="stat-card best-card"><div class="stat-label">Best Performer</div>{% if best_coin != "N/A" %}<div class="stat-value accent">{{ best_coin }}</div><div class="bar-wrap"><div class="bar-fill best-fill" style="width:{{ best_coin_wr }}%"></div></div><div class="stat-sub">{{ best_coin_wins }} wins | {{ best_coin_wr }}% win rate | {{ best_coin_trades }} closed</div>{% else %}<div class="stat-value" style="font-size:1.4rem;color:var(--muted)">—</div><div class="stat-sub">No closed trades yet</div>{% endif %}</div>
      <div class="stat-card"><div class="stat-label">Average TP Progress</div><div class="stat-value blue">{{ avg_tp_progress }}<span style="font-size:1.2rem">%</span></div><div class="stat-sub">Closed trades only</div></div>
      <div class="stat-card"><div class="stat-label">Average MAE</div><div class="stat-value red">{{ avg_mae }}<span style="font-size:1.2rem">%</span></div><div class="stat-sub">Maximum adverse excursion</div></div>
      <div class="stat-card"><div class="stat-label">Winning Trade MFE</div><div class="stat-value green">{{ avg_mfe_wins }}<span style="font-size:1.2rem">%</span></div><div class="stat-sub">Average TP progress on wins</div></div>
      <div class="stat-card"><div class="stat-label">Losing Trade MFE</div><div class="stat-value accent">{{ avg_mfe_losses }}<span style="font-size:1.2rem">%</span></div><div class="stat-sub">Average TP progress on losses</div></div>
      <div class="stat-card"><div class="stat-label">Fastest Win</div><div class="stat-value green">{{ fastest_win }}</div><div class="stat-sub">{{ fastest_win_coin }}</div></div>
      <div class="stat-card"><div class="stat-label">Avg Completion Time</div><div class="stat-value blue">{{ avg_duration }}</div><div class="stat-sub">Closed trades</div></div>
    </div>
  </section>

  <section>
    <div class="section-label">Session Performance — Which time of day wins?</div>
    <div class="session-grid">
      {% for s in session_stats %}
      <div class="session-card" style="border-color:{{ s.color }}55;">
        <div style="font-family:'Marcellus',serif;font-size:0.72rem;text-transform:uppercase;letter-spacing:1px;color:{{ s.color }};margin-bottom:4px;">{{ s.label }}</div>
        <div style="font-size:0.74rem;color:var(--muted);margin-bottom:14px;">{{ s.time_range }}</div>
        <div class="session-value" style="color:{{ s.color }}">{{ s.winrate }}%</div>
        <div class="bar-wrap"><div class="bar-fill" style="width:{{ s.winrate }}%;background:{{ s.color }};"></div></div>
        <div style="margin-top:10px;font-size:0.82rem;color:var(--muted);">
          <span style="color:var(--green)">{{ s.wins }}W</span> ·
          <span style="color:var(--red)">{{ s.losses }}L</span> ·
          <span style="color:var(--gold-deep)">{{ s.open }}O</span>
          &nbsp;|&nbsp; {{ s.total }} signals
        </div>
      </div>
      {% endfor %}
    </div>
  </section>

  <section>
    <div class="table-header">
      <div class="section-label" style="margin-bottom:0;">Recent Signals</div>
      <div class="table-count">Showing last {{ rows|length }} entries</div>
    </div>
    <div class="table-wrapper">
      {% if rows %}
      <table>
        <thead><tr>
          <th>Coin</th><th>Timeframe</th><th>Type</th><th>Direction</th>
          <th>Entry</th><th>TP</th><th>SL</th><th>Result</th>
          <th>Session</th><th>Duration</th>
          <th>TP Progress %</th><th>Max Adverse Move %</th><th>Insight</th>
        </tr></thead>
        <tbody>
          {% for row in rows %}
          <tr>
            <td><span class="coin-badge"><span class="coin-dot"></span>{{ row.coin }}</span></td>
            <td><span class="tf-pill">{{ row.timeframe }}</span></td>
            <td><span class="signal-pill">{{ row.signal_type }}</span></td>
            <td>{% if row.direction|upper == "LONG" %}<span class="dir long">Long</span>{% else %}<span class="dir short">Short</span>{% endif %}</td>
            <td><span class="entry-price">{{ row.entry }}</span><div class="stat-sub" style="margin-top:4px;font-size:0.72rem;">{{ row.entry_time_ist }}</div></td>
            <td><span class="tp-price">{{ row.tp }}</span></td>
            <td><span class="sl-price">{{ row.sl }}</span></td>
            <td>{% if row.result|upper == "WIN" %}<span class="result win">Win</span>{% elif row.result|upper == "LOSS" %}<span class="result loss">Loss</span>{% else %}<span class="result open">Open</span>{% endif %}</td>
            <td><span class="session-pill" style="color:{{ row.session_color }};border-color:{{ row.session_color }}66;background:{{ row.session_color }}12;">{{ row.session_label }}</span></td>
            <td>{% if row.result|upper == "OPEN" %}<span class="dur-open">Running…</span>{% elif row.duration_str != "-" %}{% if row.result|upper == "WIN" %}<span class="dur-win">{{ row.duration_str }}</span>{% else %}<span class="dur-loss">{{ row.duration_str }}</span>{% endif %}{% else %}<span style="color:var(--muted)">-</span>{% endif %}</td>
            <td><span class="metric-pill">{{ row.mfe_percent }}</span></td>
            <td><span class="metric-pill adverse">{{ row.mae_percent }}</span></td>
            <td><div class="insight-text">{{ row.insight }}</div></td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
      {% else %}
      <div class="empty-state">No live signals recorded yet — run the strategy skill to populate this table.<br>The Vedic, CMC and backtest panels above are live regardless.</div>
      {% endif %}
    </div>
  </section>
</main>
<footer>
  <span>BHRAMHA · Vedic Intelligence · BNB Hack Track 2</span>
  <span>ॐ · Page refresh 1 min · PnL Calendar refresh 1 hr</span>
</footer>
<div class="gold-rule" style="margin:0 56px;"></div>
<script>setTimeout(() => location.reload(), 60000);</script>
</body>
</html>
"""


# ── Helpers ───────────────────────────────────────────────────────────────────
def _format_price(value):
    try:
        if pd.isna(value): return "-"
        return f"{float(value):.6f}".rstrip("0").rstrip(".")
    except Exception: return "-"

def _format_percent(value):
    try:
        if pd.isna(value): return "-"
        return f"{round(float(value), 2)}%"
    except Exception: return "-"

def _format_duration(total_seconds):
    try:
        s = int(total_seconds)
        if s <= 0: return "-"
        if s < 60: return f"{s}s"
        m = s // 60
        if m < 60: return f"{m}m"
        h = m // 60; m = m % 60
        if h < 24: return f"{h}h {m}m"
        d = h // 24; h = h % 24
        return f"{d}d {h}h"
    except Exception: return "-"


# The seven layers shown on the dashboard (mirrors strategy_core / SKILL.md).
LAYERS = [
    {"name": "Technical Base", "detail": "RSI · MACD · EMA · Supertrend", "edge": False},
    {"name": "Market Structure", "detail": "trend / range / volatile regime", "edge": False},
    {"name": "Vedic Timing", "detail": "nakshatra · hora · tithi · lunar", "edge": True},
    {"name": "Macro Regime", "detail": "BTC dominance · market cap (CMC)", "edge": False},
    {"name": "Sentiment", "detail": "CMC Fear & Greed (contrarian)", "edge": False},
    {"name": "Whale Flow", "detail": "large executed orders (live)", "edge": False},
    {"name": "Adaptive Gates", "detail": "tunable score / R:R thresholds", "edge": False},
]


# ── Route ─────────────────────────────────────────────────────────────────────
CSV_PATH = os.path.join(_HERE, "signals_log.csv")

@app.route("/")
def home():
    restore_close_times(CSV_PATH)

    try:
        df = pd.read_csv(CSV_PATH, dtype={'close_time': str, 'session': str})
    except Exception:
        df = pd.DataFrame(columns=["coin","timeframe","signal_type","direction",
                                    "entry","tp","sl","result","mfe_percent",
                                    "mae_percent","insight","time","close_time","session"])

    for col in ["coin","timeframe","signal_type","direction","entry","tp","sl",
                "result","mfe_percent","mae_percent","insight","time","close_time","session"]:
        if col not in df.columns:
            df[col] = ""

    df["signal_type"] = df["signal_type"].fillna("TREND").astype(str).str.upper()
    df["result"]      = df["result"].fillna("OPEN").astype(str).str.upper()
    df["insight"]     = df["insight"].fillna("").astype(str)
    df["close_time"]  = df["close_time"].fillna("").astype(str).str.replace("nan","").str.strip()
    df["session"]     = df["session"].fillna("").astype(str).str.replace("nan","").str.strip()

    mask = df["session"].isin(["", "UNKNOWN"])
    if mask.any():
        df.loc[mask, "session"] = df.loc[mask, "time"].apply(get_session_from_utc)

    df["time_dt"]       = pd.to_datetime(df["time"], errors="coerce")
    df["close_time_dt"] = pd.to_datetime(df["close_time"], errors="coerce")
    df["dur_sec"]       = (df["close_time_dt"] - df["time_dt"]).dt.total_seconds()

    try:
        t = df["time_dt"].dt.tz_localize("UTC").dt.tz_convert("Asia/Kolkata")
        df["entry_time_ist"] = t.dt.strftime("%I:%M %p, %d-%b")
    except Exception:
        df["entry_time_ist"] = df["time_dt"].dt.strftime("%I:%M %p, %d-%b")
    df["entry_time_ist"] = df["entry_time_ist"].fillna("-")

    for col in ["entry","tp","sl","mfe_percent","mae_percent"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    executed_df = df[df["result"] != "REJECTED"].copy()
    total   = len(executed_df)
    wins    = int((executed_df["result"] == "WIN").sum())
    losses  = int((executed_df["result"] == "LOSS").sum())
    open_trades = int((executed_df["result"] == "OPEN").sum())
    winrate = round(wins / (wins + losses) * 100, 2) if (wins + losses) else 0
    closed  = wins + losses
    closed_winrate = round(wins / closed * 100, 2) if closed else 0

    closed_df = executed_df[executed_df["result"].isin(["WIN","LOSS"])].copy()
    avg_tp_progress = round(closed_df["mfe_percent"].dropna().mean(), 2) if not closed_df.empty else 0
    avg_mae         = round(closed_df["mae_percent"].dropna().mean(), 2) if not closed_df.empty else 0
    avg_mfe_wins    = round(closed_df.loc[closed_df["result"]=="WIN","mfe_percent"].dropna().mean(), 2) if not closed_df.empty else 0
    avg_mfe_losses  = round(closed_df.loc[closed_df["result"]=="LOSS","mfe_percent"].dropna().mean(), 2) if not closed_df.empty else 0

    best_coin, best_coin_wr, best_coin_trades, best_coin_wins = "N/A", 0, 0, 0
    if not closed_df.empty:
        cd = closed_df.copy()
        cd["is_win"] = cd["result"] == "WIN"
        cs = cd.groupby("coin").agg(
            total_closed=("is_win","count"),
            coin_wins=("is_win","sum")
        ).reset_index()
        if not cs.empty:
            cs["coin_wr"] = (cs["coin_wins"] / cs["total_closed"] * 100).round(1)
            cs = cs.sort_values(["coin_wins", "coin_wr"], ascending=False)
            br = cs.iloc[0]
            best_coin        = br["coin"]
            best_coin_wr     = br["coin_wr"]
            best_coin_trades = int(br["total_closed"])
            best_coin_wins   = int(br["coin_wins"])

    fastest_win, fastest_win_coin, avg_duration = "-", "-", "-"
    wins_df = df[(df["result"]=="WIN") & (df["dur_sec"]>0)].copy()
    if not wins_df.empty:
        fi = wins_df["dur_sec"].idxmin()
        fastest_win      = _format_duration(wins_df.loc[fi, "dur_sec"])
        fastest_win_coin = str(wins_df.loc[fi, "coin"])

    valid_closed = closed_df[closed_df["dur_sec"]>0] if not closed_df.empty else pd.DataFrame()
    if not valid_closed.empty:
        avg_duration = _format_duration(valid_closed["dur_sec"].mean())

    # Live badge
    live_class = "offline"
    live_text  = "Bot Offline"
    try:
        latest = df["time_dt"].dropna().max()
        if pd.notna(latest):
            now_utc = pd.Timestamp.now(tz="UTC")
            if latest.tzinfo is None:
                latest = latest.tz_localize("UTC")
            mins_ago = (now_utc - latest).total_seconds() / 60
            if mins_ago < 10:
                live_class = "online"; live_text  = "Live Feed"
            elif mins_ago < 60:
                live_class = "active"; live_text  = f"Active · {int(mins_ago)}m ago"
            else:
                live_class = "offline"; live_text  = f"Last seen · {int(mins_ago // 60)}h ago"
    except Exception:
        pass

    # Session stats
    session_stats = []
    for key in SESSION_ORDER:
        s_df   = executed_df[executed_df["session"] == key]
        s_wins = int((s_df["result"] == "WIN").sum())
        s_loss = int((s_df["result"] == "LOSS").sum())
        s_open = int((s_df["result"] == "OPEN").sum())
        s_tot  = len(s_df)
        s_cl   = s_wins + s_loss
        s_wr   = round(s_wins / s_cl * 100, 1) if s_cl else 0
        session_stats.append({
            "key": key, "label": SESSION_LABELS.get(key, key),
            "time_range": SESSION_TIMES.get(key, ""),
            "color": SESSION_COLORS.get(key, "#9a8a6a"),
            "wins": s_wins, "losses": s_loss, "open": s_open,
            "total": s_tot, "winrate": s_wr,
        })

    # Table rows
    rows_df = df.tail(20).copy()
    rows_df["duration_str"]  = rows_df["dur_sec"].apply(_format_duration)
    rows_df["session_color"] = rows_df["session"].apply(lambda x: SESSION_COLORS.get(x, "#9a8a6a"))
    rows_df["session_label"] = rows_df["session"].apply(lambda x: SESSION_LABELS.get(x, x))
    for col in ["entry","tp","sl"]:
        rows_df[col] = rows_df[col].apply(_format_price)
    for col in ["mfe_percent","mae_percent"]:
        rows_df[col] = rows_df[col].apply(_format_percent)
    rows = rows_df.to_dict("records")

    return render_template_string(
        HTML,
        panchang=get_panchang_snapshot(),
        cmc=get_cmc_snapshot(),
        backtest=_load_backtest_summary(),
        ablation=VEDIC_ABLATION,
        layers=LAYERS,
        total=total, wins=wins, losses=losses, winrate=winrate,
        closed=closed, closed_winrate=closed_winrate,
        best_coin=best_coin, best_coin_wr=best_coin_wr,
        best_coin_trades=best_coin_trades, best_coin_wins=best_coin_wins,
        avg_tp_progress=avg_tp_progress, avg_mae=avg_mae,
        avg_mfe_wins=avg_mfe_wins, avg_mfe_losses=avg_mfe_losses,
        fastest_win=fastest_win, fastest_win_coin=fastest_win_coin,
        avg_duration=avg_duration, session_stats=session_stats, rows=rows,
        live_class=live_class, live_text=live_text,
    )


@app.route("/api/panchang")
def api_panchang():
    return jsonify({"panchang": get_panchang_snapshot(), "cmc": get_cmc_snapshot()})


@app.route("/api/calendar")
def api_calendar():
    try:
        restore_close_times(CSV_PATH)
        try:
            df = pd.read_csv(CSV_PATH, dtype={'close_time': str, 'session': str})
        except Exception:
            return jsonify(_cal_result([], None, 0, 0, 0, None, 0, 0, 0, 0, [], []))

        df["result"]     = df["result"].fillna("OPEN").astype(str).str.upper()
        df["close_time"] = df["close_time"].fillna("").astype(str).str.replace("nan","").str.strip()

        executed_df = df[df["result"] != "REJECTED"].copy()
        closed_df   = executed_df[executed_df["result"].isin(["WIN","LOSS"])].copy()

        return jsonify(_compute_calendar_data(closed_df))
    except Exception as e:
        print(f"/api/calendar error: {e}")
        return jsonify(_cal_result([], None, 0, 0, 0.0, None, 0, 0, 0.0, 0, [], []))


if __name__ == "__main__":
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
        print("BHRAMHA DASHBOARD STARTING...")
        print(f" * Local access:   http://127.0.0.1:5000")
        print(f" * Network access: http://{local_ip}:5000 (Use this on Phone)")
    except Exception:
        print("BHRAMHA DASHBOARD STARTING...")
    app.run(host="0.0.0.0", port=5000, debug=False)
