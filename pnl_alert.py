import requests
import datetime

def send_tp_hit_alert(signal_data, current_price, mfe_percent):
    try:
        from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TRADE_LEVERAGE
        
        coin      = signal_data.get("coin", "")
        direction = signal_data.get("direction", "").upper()
        entry     = float(signal_data.get("entry", 0))
        tp        = float(signal_data.get("tp", 0))
        sl        = float(signal_data.get("sl", 0))
        quantity  = float(signal_data.get("quantity", 0))
        score     = signal_data.get("score", "-")
        session   = signal_data.get("session", "")
        signal_time = signal_data.get("time", "")
        
        # Duration
        try:
            start = datetime.datetime.fromisoformat(str(signal_time))
            mins = int((datetime.datetime.utcnow() - start).total_seconds() / 60)
            duration_str = f"{mins}m" if mins < 60 else f"{mins//60}h {mins%60}m"
        except:
            duration_str = "-"
        
        # PnL calculation
        if direction == "LONG":
            gross_pnl = (tp - entry) * quantity
        else:
            gross_pnl = (entry - tp) * quantity
        
        entry_fee = quantity * entry * 0.0005   # taker 0.05%
        exit_fee  = quantity * tp    * 0.0002   # maker 0.02%
        total_fee = entry_fee + exit_fee
        net_pnl   = gross_pnl - total_fee
        
        # PnL percent on capital used
        capital_used = (quantity * entry) / TRADE_LEVERAGE
        net_pnl_pct  = round((net_pnl / capital_used) * 100, 2) if capital_used else 0
        
        direction_emoji = "📈" if direction == "LONG" else "📉"
        
        msg = (
            f"🏆 BHRAMHA — TP HIT!\n\n"
            f"{direction_emoji} {coin} {direction} {TRADE_LEVERAGE}x\n"
            f"⏱ Duration: {duration_str} | Session: {session}\n\n"
            f"Entry:    {entry}\n"
            f"TP:       {tp}  ✅ HIT!\n"
            f"SL:       {sl}\n\n"
            f"━━━━━━━━━━━━━━\n\n"
            f"📊 PnL Breakdown\n"
            f"Gross PnL:   {'+' if gross_pnl>=0 else ''}{round(gross_pnl,3)} USDT\n"
            f"Entry Fee:   -{round(entry_fee,4)} USDT (taker 0.05%)\n"
            f"Exit Fee:    -{round(exit_fee,4)} USDT (maker 0.02%)\n"
            f"─────────────\n"
            f"🟢 Net PnL:  +{round(net_pnl,3)} USDT (+{net_pnl_pct}%)\n\n"
            f"⚡ Score: {score} | BHRAMHA WINS AGAIN!\n"
        )
        
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": msg
        }, timeout=10)
        print(f"TP hit alert sent for {coin}")
        
    except Exception as e:
        print(f"pnl_alert error: {e}")


def send_sl_hit_alert(signal_data, current_price):
    try:
        from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TRADE_LEVERAGE
        
        coin      = signal_data.get("coin", "")
        direction = signal_data.get("direction", "").upper()
        entry     = float(signal_data.get("entry", 0))
        tp        = float(signal_data.get("tp", 0))
        sl        = float(signal_data.get("sl", 0))
        quantity  = float(signal_data.get("quantity", 0))
        score     = signal_data.get("score", "-")
        session   = signal_data.get("session", "")
        signal_time = signal_data.get("time", "")
        
        # Duration
        try:
            start = datetime.datetime.fromisoformat(str(signal_time))
            mins = int((datetime.datetime.utcnow() - start).total_seconds() / 60)
            duration_str = f"{mins}m" if mins < 60 else f"{mins//60}h {mins%60}m"
        except:
            duration_str = "-"
        
        # PnL calculation at SL
        if direction == "LONG":
            gross_pnl = (sl - entry) * quantity
        else:
            gross_pnl = (entry - sl) * quantity
        
        entry_fee = quantity * entry * 0.0005
        exit_fee  = quantity * sl    * 0.0002
        total_fee = entry_fee + exit_fee
        net_pnl   = gross_pnl - total_fee
        
        capital_used = (quantity * entry) / TRADE_LEVERAGE
        net_pnl_pct  = round((net_pnl / capital_used) * 100, 2) if capital_used else 0
        
        direction_emoji = "📈" if direction == "LONG" else "📉"
        
        msg = (
            f"🛑 BHRAMHA — SL HIT\n\n"
            f"{direction_emoji} {coin} {direction} {TRADE_LEVERAGE}x\n"
            f"⏱ Duration: {duration_str} | Session: {session}\n\n"
            f"Entry:    {entry}\n"
            f"TP:       {tp}\n"
            f"SL:       {sl}  ❌ HIT\n\n"
            f"━━━━━━━━━━━━━━\n\n"
            f"📊 PnL Breakdown\n"
            f"Gross PnL:   {round(gross_pnl,3)} USDT\n"
            f"Entry Fee:   -{round(entry_fee,4)} USDT (taker 0.05%)\n"
            f"Exit Fee:    -{round(exit_fee,4)} USDT (maker 0.02%)\n"
            f"─────────────\n"
            f"🔴 Net PnL:  {round(net_pnl,3)} USDT ({net_pnl_pct}%)\n\n"
            f"⚡ Score was: {score} | Session: {session}\n"
            f"📖 BHRAMHA learns from every loss.\n"
        )
        
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": msg
        }, timeout=10)
        print(f"SL hit alert sent for {coin}")
        
    except Exception as e:
        print(f"pnl_alert error: {e}")