from flask import Flask, jsonify, request
import os
import time
import threading
from datetime import datetime
import requests
import pandas as pd

from tvDatafeed import TvDatafeed, Interval

app = Flask(__name__)

# =========================
# ENV
# =========================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
TV_USERNAME = os.getenv("TV_USERNAME")
TV_PASSWORD = os.getenv("TV_PASSWORD")

# Sadece bu kullanıcı/lar botu kullanabilsin
ALLOWED_CHAT_IDS = [8463420441]

SCAN_INTERVAL_SECONDS = 30
MIN_CONFIDENCE = 75

# Aynı setup'ı spamlamasın
LAST_SENT_KEYS = {}
ACTIVE_SIGNALS = {}

# =========================
# TRADINGVIEW SYMBOLS
# =========================
# Not:
# Bu semboller TradingView üzerinde yaygın kullanılan örneklerdir.
# Hesabına / bölgesel feed'ine göre bazıları çalışmayabilir.
# Gerekirse exchange kısmını sonra birlikte düzeltiriz.
MARKETS = {
    "EURUSD": {"symbol": "EURUSD", "exchange": "OANDA", "screener": "forex"},
    "GBPUSD": {"symbol": "GBPUSD", "exchange": "OANDA", "screener": "forex"},
    "USDJPY": {"symbol": "USDJPY", "exchange": "OANDA", "screener": "forex"},
    "XAUUSD": {"symbol": "XAUUSD", "exchange": "OANDA", "screener": "forex"},
    "XAGUSD": {"symbol": "XAGUSD", "exchange": "OANDA", "screener": "forex"},
    "NASDAQ": {"symbol": "US100", "exchange": "CAPITALCOM", "screener": "cfd"},
    "US30": {"symbol": "US30", "exchange": "CAPITALCOM", "screener": "cfd"},
    "SPX500": {"symbol": "US500", "exchange": "CAPITALCOM", "screener": "cfd"},
    "DXY": {"symbol": "DXY", "exchange": "TVC", "screener": "america"},
}

tv = None

# =========================
# TELEGRAM
# =========================
def telegram_api(method: str, payload: dict):
    if not TELEGRAM_BOT_TOKEN:
        return {"ok": False, "error": "TELEGRAM_BOT_TOKEN eksik."}

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}"
    try:
        response = requests.post(url, json=payload, timeout=20)
        return response.json()
    except Exception as e:
        return {"ok": False, "error": str(e)}


def send_telegram_message(text: str, reply_to_message_id=None):
    if not TELEGRAM_CHAT_ID:
        return {"ok": False, "error": "TELEGRAM_CHAT_ID eksik."}

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text
    }

    if reply_to_message_id:
        payload["reply_to_message_id"] = reply_to_message_id

    return telegram_api("sendMessage", payload)

# =========================
# SECURITY
# =========================
def is_allowed_chat(chat_id: int) -> bool:
    return chat_id in ALLOWED_CHAT_IDS

# =========================
# TRADINGVIEW CONNECT
# =========================
def init_tv():
    global tv
    try:
        if TV_USERNAME and TV_PASSWORD:
            tv = TvDatafeed(username=TV_USERNAME, password=TV_PASSWORD)
        else:
            tv = TvDatafeed()
        return True
    except Exception:
        tv = None
        return False

# =========================
# DATA HELPERS
# =========================
def get_hist(market_name: str, interval=Interval.in_5_minute, n_bars=120):
    if tv is None:
        return None

    cfg = MARKETS[market_name]

    try:
        df = tv.get_hist(
            symbol=cfg["symbol"],
            exchange=cfg["exchange"],
            interval=interval,
            n_bars=n_bars
        )
        if df is None or df.empty:
            return None
        return df.reset_index()
    except Exception:
        return None


def last_close(df: pd.DataFrame):
    if df is None or df.empty:
        return None
    return float(df.iloc[-1]["close"])


def round_price(market: str, value):
    if value is None:
        return None

    if market in ["EURUSD", "GBPUSD", "USDJPY"]:
        return round(value, 5)
    if market in ["XAUUSD", "XAGUSD"]:
        return round(value, 2)
    return round(value, 2)


def avg_range(df: pd.DataFrame, lookback=14):
    if df is None or len(df) < lookback:
        return None
    sample = df.tail(lookback)
    ranges = sample["high"] - sample["low"]
    return float(ranges.mean())


def recent_high(df: pd.DataFrame, lookback=20):
    if df is None or len(df) < lookback:
        return None
    return float(df.tail(lookback)["high"].max())


def recent_low(df: pd.DataFrame, lookback=20):
    if df is None or len(df) < lookback:
        return None
    return float(df.tail(lookback)["low"].min())

# =========================
# SIMPLE ICT-LIKE LOGIC
# =========================
def get_bias_from_htf(df_1h: pd.DataFrame):
    if df_1h is None or len(df_1h) < 30:
        return "Nötr"

    close_now = float(df_1h.iloc[-1]["close"])
    hi = recent_high(df_1h.iloc[:-1], 20)
    lo = recent_low(df_1h.iloc[:-1], 20)

    if hi is None or lo is None:
        return "Nötr"

    if close_now > hi:
        return "Yükseliş"
    if close_now < lo:
        return "Düşüş"
    return "Nötr"


def detect_displacement(df_5m: pd.DataFrame):
    if df_5m is None or len(df_5m) < 20:
        return False

    last = df_5m.iloc[-1]
    ar = avg_range(df_5m.iloc[:-1], 12)
    if ar is None:
        return False

    last_range = float(last["high"] - last["low"])
    body = abs(float(last["close"] - last["open"]))

    return last_range >= ar * 1.4 and body >= ar * 0.7


def detect_mss(df_5m: pd.DataFrame):
    if df_5m is None or len(df_5m) < 25:
        return "Yok"

    prev = df_5m.iloc[:-1]
    last = df_5m.iloc[-1]

    prev_high = float(prev.tail(10)["high"].max())
    prev_low = float(prev.tail(10)["low"].min())

    if float(last["close"]) > prev_high:
        return "Bullish MSS"
    if float(last["close"]) < prev_low:
        return "Bearish MSS"
    return "Yok"


def detect_pd(df_5m: pd.DataFrame):
    if df_5m is None or len(df_5m) < 30:
        return "Belirsiz"

    hi = recent_high(df_5m, 30)
    lo = recent_low(df_5m, 30)
    if hi is None or lo is None:
        return "Belirsiz"

    eq = (hi + lo) / 2
    close_now = float(df_5m.iloc[-1]["close"])

    return "Discount" if close_now < eq else "Premium"


def detect_fvg(df_5m: pd.DataFrame):
    if df_5m is None or len(df_5m) < 3:
        return "Yok"

    a = df_5m.iloc[-3]
    c = df_5m.iloc[-1]

    if float(a["high"]) < float(c["low"]):
        return "Bullish FVG"
    if float(a["low"]) > float(c["high"]):
        return "Bearish FVG"
    return "Yok"


def detect_sweep(df_5m: pd.DataFrame):
    if df_5m is None or len(df_5m) < 25:
        return "Yok"

    prev = df_5m.iloc[:-1]
    last = df_5m.iloc[-1]

    top = float(prev.tail(15)["high"].max())
    bottom = float(prev.tail(15)["low"].min())

    if float(last["high"]) > top and float(last["close"]) < top:
        return "Üst likidite sweep"
    if float(last["low"]) < bottom and float(last["close"]) > bottom:
        return "Alt likidite sweep"
    return "Yok"

# =========================
# ANALYSIS
# =========================
def analyze_market(market_name: str):
    df_5m = get_hist(market_name, Interval.in_5_minute, 120)
    df_1h = get_hist(market_name, Interval.in_1_hour, 120)

    if df_5m is None or df_1h is None:
        return None

    price = round_price(market_name, last_close(df_5m))
    bias = get_bias_from_htf(df_1h)
    displacement = detect_displacement(df_5m)
    mss = detect_mss(df_5m)
    pd_zone = detect_pd(df_5m)
    fvg = detect_fvg(df_5m)
    sweep = detect_sweep(df_5m)

    direction = "Bekle"
    confidence = 40

    if (
        bias == "Yükseliş"
        and displacement
        and mss == "Bullish MSS"
        and pd_zone == "Discount"
    ):
        direction = "LONG"
        confidence = 82
        if fvg == "Bullish FVG":
            confidence += 5
        if sweep == "Alt likidite sweep":
            confidence += 5

    elif (
        bias == "Düşüş"
        and displacement
        and mss == "Bearish MSS"
        and pd_zone == "Premium"
    ):
        direction = "SHORT"
        confidence = 82
        if fvg == "Bearish FVG":
            confidence += 5
        if sweep == "Üst likidite sweep":
            confidence += 5

    ar = avg_range(df_5m, 14)
    if ar is None or price is None:
        return None

    if direction == "LONG":
        entry = price
        sl = round_price(market_name, price - ar * 1.2)
        tp = round_price(market_name, price + ar * 2.4)
    elif direction == "SHORT":
        entry = price
        sl = round_price(market_name, price + ar * 1.2)
        tp = round_price(market_name, price - ar * 2.4)
    else:
        entry = price
        sl = "-"
        tp = "-"

    return {
        "market": market_name,
        "price": price,
        "bias": bias,
        "sweep": sweep,
        "mss": mss,
        "fvg": fvg,
        "pd_zone": pd_zone,
        "displacement": "Güçlü" if displacement else "Zayıf",
        "direction": direction,
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "confidence": min(confidence, 95),
    }

# =========================
# SIGNAL STATE
# =========================
def signal_key(analysis: dict):
    return f"{analysis['market']}|{analysis['direction']}|{analysis['entry']}|{analysis['sl']}|{analysis['tp']}"


def build_signal_text(a: dict):
    zaman = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

    return (
        f"🚨 TRADE KONSEYİ SİNYALİ\n\n"
        f"Varlık: {a['market']}\n"
        f"Zaman: {zaman}\n"
        f"Anlık Fiyat: {a['price']}\n\n"
        f"HTF Bias: {a['bias']}\n"
        f"Likidite Sweep: {a['sweep']}\n"
        f"MSS: {a['mss']}\n"
        f"FVG: {a['fvg']}\n"
        f"Premium/Discount: {a['pd_zone']}\n"
        f"Displacement: {a['displacement']}\n\n"
        f"İşlem Yönü: {a['direction']}\n"
        f"Giriş: {a['entry']}\n"
        f"SL: {a['sl']}\n"
        f"TP: {a['tp']}\n"
        f"Güven Skoru: {a['confidence']}/100\n\n"
        f"Not: Bu otomatik setup taramasıdır. Son onayı yine sen ver."
    )


def build_tp_text(sig: dict, current_price):
    return (
        f"✅ TP oldu\n\n"
        f"Varlık: {sig['market']}\n"
        f"Yön: {sig['direction']}\n"
        f"Giriş: {sig['entry']}\n"
        f"TP: {sig['tp']}\n"
        f"Anlık Fiyat: {current_price}"
    )


def build_sl_text(sig: dict, current_price):
    return (
        f"❌ SL oldu\n\n"
        f"Varlık: {sig['market']}\n"
        f"Yön: {sig['direction']}\n"
        f"Giriş: {sig['entry']}\n"
        f"SL: {sig['sl']}\n"
        f"Anlık Fiyat: {current_price}"
    )

# =========================
# TRACK ACTIVE SIGNALS
# =========================
def check_active_signals():
    remove_list = []

    for market, sig in ACTIVE_SIGNALS.items():
        df_5m = get_hist(market, Interval.in_5_minute, 5)
        if df_5m is None:
            continue

        current_price = round_price(market, float(df_5m.iloc[-1]["close"]))

        if sig["direction"] == "LONG":
            if current_price >= sig["tp"]:
                send_telegram_message(build_tp_text(sig, current_price), sig.get("message_id"))
                remove_list.append(market)
            elif current_price <= sig["sl"]:
                send_telegram_message(build_sl_text(sig, current_price), sig.get("message_id"))
                remove_list.append(market)

        elif sig["direction"] == "SHORT":
            if current_price <= sig["tp"]:
                send_telegram_message(build_tp_text(sig, current_price), sig.get("message_id"))
                remove_list.append(market)
            elif current_price >= sig["sl"]:
                send_telegram_message(build_sl_text(sig, current_price), sig.get("message_id"))
                remove_list.append(market)

    for market in remove_list:
        ACTIVE_SIGNALS.pop(market, None)

# =========================
# BACKGROUND SCANNER
# =========================
def scanner_loop():
    while True:
        try:
            check_active_signals()

            for market in MARKETS.keys():
                if market in ACTIVE_SIGNALS:
                    continue

                analysis = analyze_market(market)
                if analysis is None:
                    continue

                if analysis["direction"] == "Bekle":
                    continue

                if analysis["confidence"] < MIN_CONFIDENCE:
                    continue

                key = signal_key(analysis)
                last_sent = LAST_SENT_KEYS.get(market)

                if last_sent == key:
                    continue

                text = build_signal_text(analysis)
                result = send_telegram_message(text)

                message_id = None
                if isinstance(result, dict):
                    message_id = result.get("result", {}).get("message_id")

                ACTIVE_SIGNALS[market] = {
                    "market": market,
                    "direction": analysis["direction"],
                    "entry": analysis["entry"],
                    "sl": analysis["sl"],
                    "tp": analysis["tp"],
                    "message_id": message_id
                }
                LAST_SENT_KEYS[market] = key

            time.sleep(SCAN_INTERVAL_SECONDS)

        except Exception:
            time.sleep(SCAN_INTERVAL_SECONDS)

# =========================
# ROUTES
# =========================
@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "ok": True,
        "status": "Trade Konseyi TV motoru aktif",
        "markets": list(MARKETS.keys()),
        "scan_interval_seconds": SCAN_INTERVAL_SECONDS,
    })


@app.route("/test", methods=["GET"])
def test():
    result = {}
    for market in MARKETS.keys():
        a = analyze_market(market)
        result[market] = a if a else {"error": "veri alınamadı"}

    return jsonify({
        "ok": True,
        "data": result
    })


@app.route("/manual/<market>", methods=["GET"])
def manual_market(market):
    market = market.upper()
    if market not in MARKETS:
        return jsonify({"ok": False, "error": "Geçersiz market"}), 400

    a = analyze_market(market)
    if a is None:
        return jsonify({"ok": False, "error": "Veri alınamadı"}), 500

    if a["direction"] == "Bekle":
        text = (
            f"📋 Manuel analiz sonucu\n\n"
            f"Varlık: {a['market']}\n"
            f"Anlık Fiyat: {a['price']}\n"
            f"HTF Bias: {a['bias']}\n"
            f"Likidite Sweep: {a['sweep']}\n"
            f"MSS: {a['mss']}\n"
            f"FVG: {a['fvg']}\n"
            f"Premium/Discount: {a['pd_zone']}\n"
            f"Displacement: {a['displacement']}\n"
            f"Durum: Şu an net setup yok.\n"
            f"Not: Bot sessiz kalır."
        )
    else:
        text = build_signal_text(a)

    tg = send_telegram_message(text)
    return jsonify({
        "ok": True,
        "analysis": a,
        "telegram_result": tg
    })


@app.route("/active-signals", methods=["GET"])
def active_signals():
    return jsonify({
        "ok": True,
        "active_signals": ACTIVE_SIGNALS
    })


@app.route("/telegram-webhook", methods=["POST"])
def telegram_webhook():
    data = request.get_json(silent=True) or {}
    message = data.get("message", {})
    chat = message.get("chat", {})
    chat_id = chat.get("id")

    if not chat_id or not is_allowed_chat(chat_id):
        return jsonify({"ok": True})

    text = message.get("text", "").strip().lower()

    if text == "/start":
        send_telegram_message("✅ Trade Konseyi aktif. Yetkili kullanıcı doğrulandı.")
    elif text == "/markets":
        send_telegram_message("İzlenen marketler:\n" + "\n".join(MARKETS.keys()))
    elif text.startswith("/manual "):
        market = text.replace("/manual ", "").upper()
        if market in MARKETS:
            a = analyze_market(market)
            if a:
                if a["direction"] == "Bekle":
                    send_telegram_message(
                        f"📋 Manuel analiz sonucu\n\n"
                        f"Varlık: {a['market']}\n"
                        f"Anlık Fiyat: {a['price']}\n"
                        f"HTF Bias: {a['bias']}\n"
                        f"Likidite Sweep: {a['sweep']}\n"
                        f"MSS: {a['mss']}\n"
                        f"FVG: {a['fvg']}\n"
                        f"Premium/Discount: {a['pd_zone']}\n"
                        f"Displacement: {a['displacement']}\n"
                        f"Durum: Şu an net setup yok."
                    )
                else:
                    send_telegram_message(build_signal_text(a))
    return jsonify({"ok": True})

# =========================
# START
# =========================
def start_background():
    init_tv()
    t = threading.Thread(target=scanner_loop, daemon=True)
    t.start()

start_background()

if __name__ == "__main__":
    init_tv()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)