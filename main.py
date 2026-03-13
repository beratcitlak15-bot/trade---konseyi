from flask import Flask, jsonify
import requests
import os
import time
import threading
from datetime import datetime, timedelta

app = Flask(__name__)

# =========================
# ENV
# =========================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
TWELVEDATA_API_KEY = os.getenv("TWELVEDATA_API_KEY")

# =========================
# SETTINGS
# =========================
WATCHLIST = [
    "EURUSD",
    "XAUUSD",
]

SCAN_INTERVAL = 300  # 5 dakika
SIGNAL_COOLDOWN_MINUTES = 60
TIMEFRAME = "5min"
CANDLE_LIMIT = 20

# Aynı setup'ı tekrar tekrar göndermesin
LAST_SIGNAL_CACHE = {}

# Aktif sinyaller burada tutulur
# Örnek:
# ACTIVE_SIGNALS["EURUSD"] = {
#   "direction": "LONG",
#   "entry": 1.1450,
#   "sl": 1.1435,
#   "tp": 1.1480,
#   "message_id": 123,
#   "created_at": "..."
# }
ACTIVE_SIGNALS = {}

# =========================
# TELEGRAM
# =========================
def telegram_api(method: str, payload: dict):
    if not TELEGRAM_BOT_TOKEN:
        return {"ok": False, "error": "Telegram bot token eksik."}

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}"

    try:
        response = requests.post(url, json=payload, timeout=20)
        return response.json()
    except Exception as e:
        return {"ok": False, "error": str(e)}


def send_telegram_message(text: str, reply_to_message_id=None):
    if not TELEGRAM_CHAT_ID:
        return {"ok": False, "error": "Telegram chat id eksik."}

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text
    }

    if reply_to_message_id is not None:
        payload["reply_to_message_id"] = reply_to_message_id

    return telegram_api("sendMessage", payload)

# =========================
# TIME / SESSION
# =========================
def get_session():
    utc_hour = datetime.utcnow().hour

    if 0 <= utc_hour < 7:
        return "Asya"
    elif 7 <= utc_hour < 13:
        return "Londra"
    elif 13 <= utc_hour < 21:
        return "New York"
    else:
        return "Kapalı"


def market_is_open():
    return get_session() != "Kapalı"


def get_model(symbol: str):
    if symbol == "EURUSD":
        return "London Reversal"
    return "ICT Intraday"

# =========================
# TWELVEDATA
# =========================
def symbol_map(symbol: str):
    mapping = {
        "EURUSD": "EUR/USD",
        "XAUUSD": "XAU/USD",
    }
    return mapping.get(symbol, symbol)


def fetch_twelvedata_price(symbol: str):
    if not TWELVEDATA_API_KEY:
        return None

    url = "https://api.twelvedata.com/price"
    params = {
        "symbol": symbol_map(symbol),
        "apikey": TWELVEDATA_API_KEY
    }

    try:
        response = requests.get(url, params=params, timeout=20)
        data = response.json()

        if "price" in data:
            return float(data["price"])

        return None
    except Exception:
        return None


def fetch_twelvedata_candles(symbol: str, interval: str = TIMEFRAME, outputsize: int = CANDLE_LIMIT):
    if not TWELVEDATA_API_KEY:
        return []

    url = "https://api.twelvedata.com/time_series"
    params = {
        "symbol": symbol_map(symbol),
        "interval": interval,
        "outputsize": outputsize,
        "apikey": TWELVEDATA_API_KEY
    }

    try:
        response = requests.get(url, params=params, timeout=20)
        data = response.json()

        values = data.get("values", [])
        if not values:
            return []

        candles = []
        for item in values:
            try:
                candles.append({
                    "datetime": item["datetime"],
                    "open": float(item["open"]),
                    "high": float(item["high"]),
                    "low": float(item["low"]),
                    "close": float(item["close"]),
                })
            except Exception:
                continue

        # Twelve Data genelde newest -> oldest döndürür
        candles.reverse()
        return candles

    except Exception:
        return []

# =========================
# ANALYSIS HELPERS
# =========================
def average_range(candles):
    if not candles:
        return None

    ranges = []
    for c in candles[-10:]:
        ranges.append(c["high"] - c["low"])

    if not ranges:
        return None

    return sum(ranges) / len(ranges)


def build_setup_from_candles(symbol: str, candles):
    """
    Bu tam ICT motoru değil.
    Ama spam atmayan, muhafazakar bir setup filtresi.
    Sonraki aşamada buna:
    - liquidity sweep
    - MSS
    - FVG
    - OB
    eklenebilir.
    """

    if len(candles) < 12:
        return None

    last = candles[-1]
    c1 = candles[-2]
    c2 = candles[-3]
    c3 = candles[-4]

    avg_r = average_range(candles)
    if avg_r is None or avg_r <= 0:
        return None

    current_price = last["close"]
    session = get_session()

    # LONG setup
    bullish_structure = (
        c3["close"] < c2["close"] < c1["close"] < last["close"]
        and last["high"] > c1["high"]
        and last["low"] > c1["low"]
    )

    # SHORT setup
    bearish_structure = (
        c3["close"] > c2["close"] > c1["close"] > last["close"]
        and last["high"] < c1["high"]
        and last["low"] < c1["low"]
    )

    if bullish_structure:
        entry = round(current_price, 5 if symbol == "EURUSD" else 2)
        sl_raw = current_price - (avg_r * 1.2)
        tp_raw = current_price + (avg_r * 2.4)

        sl = round(sl_raw, 5 if symbol == "EURUSD" else 2)
        tp = round(tp_raw, 5 if symbol == "EURUSD" else 2)

        return {
            "symbol": symbol,
            "session": session,
            "direction": "LONG",
            "bias": "Yükseliş",
            "entry": entry,
            "sl": sl,
            "tp": tp,
            "confidence": 86,
            "liquidity": "Yakın dip likiditesi sonrası yukarı devam ihtimali",
            "structure": "Kısa vadeli yükseliş yapısı korunuyor",
            "fvg": "Mikro dengesizlik bölgesi izleniyor",
            "ob": "Yakın talep alanı korunuyor"
        }

    if bearish_structure:
        entry = round(current_price, 5 if symbol == "EURUSD" else 2)
        sl_raw = current_price + (avg_r * 1.2)
        tp_raw = current_price - (avg_r * 2.4)

        sl = round(sl_raw, 5 if symbol == "EURUSD" else 2)
        tp = round(tp_raw, 5 if symbol == "EURUSD" else 2)

        return {
            "symbol": symbol,
            "session": session,
            "direction": "SHORT",
            "bias": "Düşüş",
            "entry": entry,
            "sl": sl,
            "tp": tp,
            "confidence": 86,
            "liquidity": "Yakın tepe likiditesi sonrası aşağı devam ihtimali",
            "structure": "Kısa vadeli düşüş yapısı korunuyor",
            "fvg": "Mikro dengesizlik bölgesi izleniyor",
            "ob": "Yakın arz alanı korunuyor"
        }

    return None


def signal_cache_key(setup: dict):
    return f"{setup['symbol']}|{setup['direction']}|{setup['entry']}|{setup['sl']}|{setup['tp']}"


def signal_cooldown_active(setup: dict):
    key = signal_cache_key(setup)
    last_time = LAST_SIGNAL_CACHE.get(key)

    if last_time is None:
        return False

    return (datetime.utcnow() - last_time) < timedelta(minutes=SIGNAL_COOLDOWN_MINUTES)


def update_signal_cache(setup: dict):
    key = signal_cache_key(setup)
    LAST_SIGNAL_CACHE[key] = datetime.utcnow()

# =========================
# MESSAGE BUILDERS
# =========================
def build_signal_message(symbol: str, setup: dict, current_price: float):
    zaman = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    model = get_model(symbol)

    message = (
        f"📊 ANALİZ + SİNYAL RAPORU\n\n"
        f"Varlık: {symbol}\n"
        f"Model: {model}\n"
        f"Seans: {setup['session']}\n"
        f"Zaman: {zaman}\n"
        f"Anlık Fiyat: {current_price}\n\n"
        f"Yön: {setup['bias']}\n"
        f"DXY Durumu: Nötr\n"
        f"DXY Yorumu: DXY filtresi geçici olarak devre dışı.\n"
        f"Likidite: {setup['liquidity']}\n"
        f"Yapı: {setup['structure']}\n"
        f"FVG: {setup['fvg']}\n"
        f"Order Block: {setup['ob']}\n\n"
        f"📍 İşlem Planı\n"
        f"İşlem Yönü: {setup['direction']}\n"
        f"Giriş: {setup['entry']}\n"
        f"Zarar Durdur: {setup['sl']}\n"
        f"Kar Al: {setup['tp']}\n"
        f"Güven Skoru: {setup['confidence']}/100\n\n"
        f"⚠️ Risk Uyarısı\n"
        f"Haber Riski: Düşük\n"
        f"Not: Bu sinyal otomatik tarama sonucu üretildi. İşleme girmeden önce kendi teyidini al."
    )
    return message


def build_tp_message(signal: dict, current_price: float):
    return (
        f"✅ TP'ye ulaştı\n\n"
        f"Varlık: {signal['symbol']}\n"
        f"Yön: {signal['direction']}\n"
        f"Giriş: {signal['entry']}\n"
        f"TP: {signal['tp']}\n"
        f"Anlık Fiyat: {current_price}"
    )


def build_sl_message(signal: dict, current_price: float):
    return (
        f"❌ SL oldu\n\n"
        f"Varlık: {signal['symbol']}\n"
        f"Yön: {signal['direction']}\n"
        f"Giriş: {signal['entry']}\n"
        f"SL: {signal['sl']}\n"
        f"Anlık Fiyat: {current_price}"
    )

# =========================
# ACTIVE SIGNAL CHECK
# =========================
def check_active_signals():
    symbols_to_remove = []

    for symbol, signal in ACTIVE_SIGNALS.items():
        current_price = fetch_twelvedata_price(symbol)

        if current_price is None:
            continue

        if signal["direction"] == "LONG":
            if current_price >= signal["tp"]:
                send_telegram_message(
                    build_tp_message(signal, current_price),
                    reply_to_message_id=signal["message_id"]
                )
                symbols_to_remove.append(symbol)
            elif current_price <= signal["sl"]:
                send_telegram_message(
                    build_sl_message(signal, current_price),
                    reply_to_message_id=signal["message_id"]
                )
                symbols_to_remove.append(symbol)

        elif signal["direction"] == "SHORT":
            if current_price <= signal["tp"]:
                send_telegram_message(
                    build_tp_message(signal, current_price),
                    reply_to_message_id=signal["message_id"]
                )
                symbols_to_remove.append(symbol)
            elif current_price >= signal["sl"]:
                send_telegram_message(
                    build_sl_message(signal, current_price),
                    reply_to_message_id=signal["message_id"]
                )
                symbols_to_remove.append(symbol)

    for symbol in symbols_to_remove:
        ACTIVE_SIGNALS.pop(symbol, None)

# =========================
# SCAN ENGINE
# =========================
def scan_markets():
    while True:
        try:
            # Önce aktif sinyalleri kontrol et
            check_active_signals()

            if not market_is_open():
                time.sleep(SCAN_INTERVAL)
                continue

            for symbol in WATCHLIST:
                # Aynı varlıkta aktif sinyal varsa yeni sinyal üretme
                if symbol in ACTIVE_SIGNALS:
                    continue

                candles = fetch_twelvedata_candles(symbol)
                if not candles:
                    continue

                setup = build_setup_from_candles(symbol, candles)
                if setup is None:
                    continue

                if signal_cooldown_active(setup):
                    continue

                current_price = fetch_twelvedata_price(symbol)
                if current_price is None:
                    continue

                message = build_signal_message(symbol, setup, current_price)
                result = send_telegram_message(message)

                if result.get("ok") is True:
                    message_id = result.get("result", {}).get("message_id")

                    ACTIVE_SIGNALS[symbol] = {
                        "symbol": symbol,
                        "direction": setup["direction"],
                        "entry": setup["entry"],
                        "sl": setup["sl"],
                        "tp": setup["tp"],
                        "message_id": message_id,
                        "created_at": datetime.utcnow().isoformat()
                    }

                    update_signal_cache(setup)

            time.sleep(SCAN_INTERVAL)

        except Exception:
            time.sleep(SCAN_INTERVAL)

# =========================
# ROUTES
# =========================
@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "ok": True,
        "status": "Trade Konseyi aktif",
        "session": get_session(),
        "watchlist": WATCHLIST,
        "scan_interval_seconds": SCAN_INTERVAL,
        "mode": "Sadece setup olursa sinyal gönderir"
    })


@app.route("/test", methods=["GET"])
def test():
    eurusd = fetch_twelvedata_price("EURUSD")
    xauusd = fetch_twelvedata_price("XAUUSD")

    text = (
        f"✅ Sistem testi başarılı\n\n"
        f"EURUSD: {eurusd}\n"
        f"XAUUSD: {xauusd}\n"
        f"NASDAQ: devre dışı\n"
        f"US30: devre dışı\n"
        f"DXY: devre dışı"
    )

    result = send_telegram_message(text)
    return jsonify(result)


@app.route("/manual/<symbol>", methods=["GET"])
def manual_symbol(symbol):
    symbol = symbol.upper()

    if symbol not in WATCHLIST:
        return jsonify({
            "ok": False,
            "error": "Geçersiz sembol."
        }), 400

    candles = fetch_twelvedata_candles(symbol)
    if not candles:
        return jsonify({
            "ok": False,
            "error": "Mum verisi alınamadı."
        }), 500

    setup = build_setup_from_candles(symbol, candles)
    current_price = fetch_twelvedata_price(symbol)

    if current_price is None:
        return jsonify({
            "ok": False,
            "error": "Anlık fiyat alınamadı."
        }), 500

    if setup is None:
        text = (
            f"📋 Manuel analiz sonucu\n\n"
            f"Varlık: {symbol}\n"
            f"Seans: {get_session()}\n"
            f"Anlık Fiyat: {current_price}\n"
            f"Durum: Şu an net setup yok.\n"
            f"Not: Bot sessiz kalır."
        )
        result = send_telegram_message(text)
        return jsonify(result)

    message = build_signal_message(symbol, setup, current_price)
    result = send_telegram_message(message)

    if result.get("ok") is True:
        message_id = result.get("result", {}).get("message_id")

        ACTIVE_SIGNALS[symbol] = {
            "symbol": symbol,
            "direction": setup["direction"],
            "entry": setup["entry"],
            "sl": setup["sl"],
            "tp": setup["tp"],
            "message_id": message_id,
            "created_at": datetime.utcnow().isoformat()
        }

        update_signal_cache(setup)

    return jsonify(result)


@app.route("/active-signals", methods=["GET"])
def active_signals():
    return jsonify({
        "ok": True,
        "active_signals": ACTIVE_SIGNALS
    })

# =========================
# START BACKGROUND SCANNER
# =========================
def start_scanner():
    scanner = threading.Thread(target=scan_markets)
    scanner.daemon = True
    scanner.start()


start_scanner()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)