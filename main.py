from flask import Flask, jsonify
import requests
import os
import time
import threading
import json
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
LTF_INTERVAL = "5min"
HTF_INTERVAL = "1h"
LTF_CANDLE_LIMIT = 120
HTF_CANDLE_LIMIT = 120
SIGNAL_COOLDOWN_MINUTES = 90
MIN_SIGNAL_CONFIDENCE = 85

LAST_SIGNAL_CACHE = {}
ACTIVE_SIGNALS_FILE = "active_signals.json"
ACTIVE_SIGNALS = {}

# =========================
# FILE STORAGE
# =========================
def load_active_signals():
    global ACTIVE_SIGNALS

    if not os.path.exists(ACTIVE_SIGNALS_FILE):
        ACTIVE_SIGNALS = {}
        save_active_signals()
        return

    try:
        with open(ACTIVE_SIGNALS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, dict):
            ACTIVE_SIGNALS = data
        else:
            ACTIVE_SIGNALS = {}
    except Exception:
        ACTIVE_SIGNALS = {}
        save_active_signals()


def save_active_signals():
    try:
        with open(ACTIVE_SIGNALS_FILE, "w", encoding="utf-8") as f:
            json.dump(ACTIVE_SIGNALS, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def set_active_signal(symbol: str, signal_data: dict):
    ACTIVE_SIGNALS[symbol] = signal_data
    save_active_signals()


def remove_active_signal(symbol: str):
    if symbol in ACTIVE_SIGNALS:
        ACTIVE_SIGNALS.pop(symbol, None)
        save_active_signals()

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
# SYMBOL MAP
# =========================
def symbol_map(symbol: str):
    mapping = {
        "EURUSD": "EUR/USD",
        "XAUUSD": "XAU/USD",
    }
    return mapping.get(symbol, symbol)

# =========================
# SESSION / KILLZONE
# =========================
def get_session_info():
    utc_hour = datetime.utcnow().hour

    if 0 <= utc_hour < 7:
        return {
            "session": "Asya",
            "killzone": False,
            "trade_window": False
        }
    elif 7 <= utc_hour < 10:
        return {
            "session": "Londra Killzone",
            "killzone": True,
            "trade_window": True
        }
    elif 10 <= utc_hour < 13:
        return {
            "session": "Londra",
            "killzone": False,
            "trade_window": True
        }
    elif 13 <= utc_hour < 16:
        return {
            "session": "London-NY Overlap",
            "killzone": True,
            "trade_window": True
        }
    elif 16 <= utc_hour < 18:
        return {
            "session": "New York Killzone",
            "killzone": True,
            "trade_window": True
        }
    elif 18 <= utc_hour < 21:
        return {
            "session": "New York",
            "killzone": False,
            "trade_window": True
        }
    else:
        return {
            "session": "Kapalı",
            "killzone": False,
            "trade_window": False
        }


def get_session():
    return get_session_info()["session"]


def market_is_open():
    return get_session_info()["session"] != "Kapalı"


def in_trade_window():
    return get_session_info()["trade_window"]


def get_model(symbol: str):
    if symbol == "EURUSD":
        return "ICT London Reversal Engine"
    return "ICT Intraday Engine"

# =========================
# TWELVEDATA
# =========================
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


def fetch_twelvedata_candles(symbol: str, interval: str, outputsize: int):
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

        candles.reverse()
        return candles
    except Exception:
        return []

# =========================
# HELPERS
# =========================
def round_price(symbol, value):
    if value is None:
        return None
    return round(value, 5 if symbol == "EURUSD" else 2)


def average_range(candles, lookback=14):
    if len(candles) < lookback:
        return None
    ranges = [(c["high"] - c["low"]) for c in candles[-lookback:]]
    if not ranges:
        return None
    return sum(ranges) / len(ranges)


def candle_body(candle):
    return abs(candle["close"] - candle["open"])


def is_bullish(candle):
    return candle["close"] > candle["open"]


def is_bearish(candle):
    return candle["close"] < candle["open"]


def recent_high(candles, lookback=20):
    if len(candles) < lookback:
        return None
    subset = candles[-lookback:]
    return max(c["high"] for c in subset)


def recent_low(candles, lookback=20):
    if len(candles) < lookback:
        return None
    subset = candles[-lookback:]
    return min(c["low"] for c in subset)


def find_swing_points(candles, left=2, right=2):
    swing_highs = []
    swing_lows = []

    for i in range(left, len(candles) - right):
        cur = candles[i]

        left_highs = [candles[j]["high"] for j in range(i - left, i)]
        right_highs = [candles[j]["high"] for j in range(i + 1, i + 1 + right)]
        if cur["high"] > max(left_highs) and cur["high"] > max(right_highs):
            swing_highs.append((i, cur["high"]))

        left_lows = [candles[j]["low"] for j in range(i - left, i)]
        right_lows = [candles[j]["low"] for j in range(i + 1, i + 1 + right)]
        if cur["low"] < min(left_lows) and cur["low"] < min(right_lows):
            swing_lows.append((i, cur["low"]))

    return swing_highs, swing_lows


def get_last_swing_high(candles):
    highs, _ = find_swing_points(candles)
    if not highs:
        return None
    return highs[-1]


def get_last_swing_low(candles):
    _, lows = find_swing_points(candles)
    if not lows:
        return None
    return lows[-1]

# =========================
# ICT LOGIC
# =========================
def detect_htf_bias(htf_candles):
    if len(htf_candles) < 30:
        return "Nötr"

    last = htf_candles[-1]["close"]
    high_20 = recent_high(htf_candles[:-1], 20)
    low_20 = recent_low(htf_candles[:-1], 20)

    if high_20 is None or low_20 is None:
        return "Nötr"

    if last > high_20:
        return "Yükseliş"

    if last < low_20:
        return "Düşüş"

    last5 = [c["close"] for c in htf_candles[-5:]]
    rising = all(last5[i] > last5[i - 1] for i in range(1, len(last5)))
    falling = all(last5[i] < last5[i - 1] for i in range(1, len(last5)))

    if rising:
        return "Yükseliş"
    if falling:
        return "Düşüş"

    return "Nötr"


def detect_liquidity_sweep(ltf_candles, symbol):
    if len(ltf_candles) < 30:
        return None

    last = ltf_candles[-1]
    prev = ltf_candles[:-1]

    last_high = get_last_swing_high(prev)
    last_low = get_last_swing_low(prev)

    if last_high:
        _, swing_high_value = last_high
        if last["high"] > swing_high_value and last["close"] < swing_high_value:
            return {
                "type": "bearish",
                "level": round_price(symbol, swing_high_value),
                "text": f"Üst likidite sweep alındı ({round_price(symbol, swing_high_value)})"
            }

    if last_low:
        _, swing_low_value = last_low
        if last["low"] < swing_low_value and last["close"] > swing_low_value:
            return {
                "type": "bullish",
                "level": round_price(symbol, swing_low_value),
                "text": f"Alt likidite sweep alındı ({round_price(symbol, swing_low_value)})"
            }

    return None


def detect_mss(ltf_candles, symbol):
    if len(ltf_candles) < 35:
        return None

    last = ltf_candles[-1]
    prev = ltf_candles[:-1]

    swing_high = get_last_swing_high(prev)
    swing_low = get_last_swing_low(prev)

    if swing_high and last["close"] > swing_high[1]:
        return {
            "type": "bullish",
            "level": round_price(symbol, swing_high[1]),
            "text": f"Bullish MSS / yapı kırılımı ({round_price(symbol, swing_high[1])})"
        }

    if swing_low and last["close"] < swing_low[1]:
        return {
            "type": "bearish",
            "level": round_price(symbol, swing_low[1]),
            "text": f"Bearish MSS / yapı kırılımı ({round_price(symbol, swing_low[1])})"
        }

    return None


def detect_fvg(ltf_candles, symbol):
    if len(ltf_candles) < 3:
        return None

    a = ltf_candles[-3]
    c = ltf_candles[-1]

    if a["high"] < c["low"]:
        return {
            "type": "bullish",
            "low": round_price(symbol, a["high"]),
            "high": round_price(symbol, c["low"]),
            "text": f"Bullish FVG mevcut ({round_price(symbol, a['high'])} - {round_price(symbol, c['low'])})"
        }

    if a["low"] > c["high"]:
        return {
            "type": "bearish",
            "low": round_price(symbol, c["high"]),
            "high": round_price(symbol, a["low"]),
            "text": f"Bearish FVG mevcut ({round_price(symbol, c['high'])} - {round_price(symbol, a['low'])})"
        }

    return None


def detect_displacement(ltf_candles):
    if len(ltf_candles) < 15:
        return None

    last = ltf_candles[-1]
    avg_r = average_range(ltf_candles[:-1], 12)
    if avg_r is None:
        return None

    last_range = last["high"] - last["low"]
    last_body = candle_body(last)

    active = last_range >= avg_r * 1.5 and last_body >= avg_r * 0.8

    return {
        "active": active,
        "range": last_range,
        "avg_range": avg_r
    }


def detect_premium_discount(ltf_candles, symbol):
    if len(ltf_candles) < 30:
        return None

    dealing_high = recent_high(ltf_candles, 30)
    dealing_low = recent_low(ltf_candles, 30)
    current = ltf_candles[-1]["close"]

    if dealing_high is None or dealing_low is None:
        return None

    eq = (dealing_high + dealing_low) / 2
    zone = "discount" if current < eq else "premium"

    return {
        "zone": zone,
        "high": round_price(symbol, dealing_high),
        "low": round_price(symbol, dealing_low),
        "equilibrium": round_price(symbol, eq),
        "text": "Discount bölgesinde" if zone == "discount" else "Premium bölgesinde"
    }


def detect_true_order_block(ltf_candles, direction, symbol):
    if len(ltf_candles) < 20:
        return None

    search_zone = ltf_candles[-12:-1]
    if not search_zone:
        return None

    if direction == "LONG":
        for c in reversed(search_zone):
            if is_bearish(c):
                ob_low = min(c["open"], c["close"], c["low"])
                ob_high = max(c["open"], c["close"], c["high"])
                return {
                    "type": "bullish",
                    "low": round_price(symbol, ob_low),
                    "high": round_price(symbol, ob_high),
                    "text": f"Bullish OB alanı ({round_price(symbol, ob_low)} - {round_price(symbol, ob_high)})"
                }

    if direction == "SHORT":
        for c in reversed(search_zone):
            if is_bullish(c):
                ob_low = min(c["open"], c["close"], c["low"])
                ob_high = max(c["open"], c["close"], c["high"])
                return {
                    "type": "bearish",
                    "low": round_price(symbol, ob_low),
                    "high": round_price(symbol, ob_high),
                    "text": f"Bearish OB alanı ({round_price(symbol, ob_low)} - {round_price(symbol, ob_high)})"
                }

    return None

# =========================
# ANALYSIS ENGINE
# =========================
def analyze_market(symbol: str):
    ltf_candles = fetch_twelvedata_candles(symbol, LTF_INTERVAL, LTF_CANDLE_LIMIT)
    htf_candles = fetch_twelvedata_candles(symbol, HTF_INTERVAL, HTF_CANDLE_LIMIT)

    if len(ltf_candles) < 40 or len(htf_candles) < 40:
        return None

    session_info = get_session_info()
    current_price = fetch_twelvedata_price(symbol)
    if current_price is None:
        return None

    current_price = round_price(symbol, current_price)

    htf_bias = detect_htf_bias(htf_candles)
    sweep = detect_liquidity_sweep(ltf_candles, symbol)
    mss = detect_mss(ltf_candles, symbol)
    fvg = detect_fvg(ltf_candles, symbol)
    displacement = detect_displacement(ltf_candles)
    pd = detect_premium_discount(ltf_candles, symbol)

    direction = "Bekle"
    confidence = 40

    if (
        session_info["trade_window"]
        and sweep
        and sweep["type"] == "bullish"
        and mss
        and mss["type"] == "bullish"
        and htf_bias == "Yükseliş"
        and displacement
        and displacement["active"] is True
        and pd
        and pd["zone"] == "discount"
        and (fvg is None or fvg["type"] == "bullish")
    ):
        direction = "LONG"
        confidence = 90

    elif (
        session_info["trade_window"]
        and sweep
        and sweep["type"] == "bearish"
        and mss
        and mss["type"] == "bearish"
        and htf_bias == "Düşüş"
        and displacement
        and displacement["active"] is True
        and pd
        and pd["zone"] == "premium"
        and (fvg is None or fvg["type"] == "bearish")
    ):
        direction = "SHORT"
        confidence = 90

    ob = None
    if direction == "LONG":
        ob = detect_true_order_block(ltf_candles, "LONG", symbol)
    elif direction == "SHORT":
        ob = detect_true_order_block(ltf_candles, "SHORT", symbol)

    avg_r = average_range(ltf_candles, 14)
    if avg_r is None:
        return None

    entry = current_price
    sl = "-"
    tp = "-"

    if direction == "LONG":
        sl = round_price(symbol, current_price - avg_r * 1.3)
        tp = round_price(symbol, current_price + avg_r * 2.6)

    elif direction == "SHORT":
        sl = round_price(symbol, current_price + avg_r * 1.3)
        tp = round_price(symbol, current_price - avg_r * 2.6)

    return {
        "symbol": symbol,
        "model": get_model(symbol),
        "session": session_info["session"],
        "killzone": session_info["killzone"],
        "trade_window": session_info["trade_window"],
        "current_price": current_price,
        "htf_bias": htf_bias,
        "liquidity": sweep["text"] if sweep else "Net liquidity sweep yok",
        "structure": mss["text"] if mss else "Net MSS yok",
        "fvg": fvg["text"] if fvg else "Belirgin FVG yok",
        "displacement": "Güçlü" if (displacement and displacement["active"]) else "Zayıf",
        "premium_discount": pd["text"] if pd else "PD alanı belirsiz",
        "order_block": ob["text"] if ob else "True order block net değil",
        "direction": direction,
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "confidence": confidence
    }

# =========================
# SIGNAL CACHE
# =========================
def signal_cache_key(analysis: dict):
    return f"{analysis['symbol']}|{analysis['direction']}|{analysis['entry']}|{analysis['sl']}|{analysis['tp']}"


def signal_cooldown_active(analysis: dict):
    key = signal_cache_key(analysis)
    last_time = LAST_SIGNAL_CACHE.get(key)

    if last_time is None:
        return False

    return (datetime.utcnow() - last_time) < timedelta(minutes=SIGNAL_COOLDOWN_MINUTES)


def update_signal_cache(analysis: dict):
    LAST_SIGNAL_CACHE[signal_cache_key(analysis)] = datetime.utcnow()

# =========================
# MESSAGES
# =========================
def build_signal_message(analysis: dict):
    zaman = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    kz_text = "Evet" if analysis["killzone"] else "Hayır"

    return (
        f"📊 ANALİZ + SİNYAL RAPORU\n\n"
        f"Varlık: {analysis['symbol']}\n"
        f"Model: {analysis['model']}\n"
        f"Seans: {analysis['session']}\n"
        f"Killzone: {kz_text}\n"
        f"Zaman: {zaman}\n"
        f"Anlık Fiyat: {analysis['current_price']}\n\n"
        f"HTF Bias: {analysis['htf_bias']}\n"
        f"Likidite Sweep: {analysis['liquidity']}\n"
        f"MSS / Yapı: {analysis['structure']}\n"
        f"FVG: {analysis['fvg']}\n"
        f"Displacement: {analysis['displacement']}\n"
        f"Premium / Discount: {analysis['premium_discount']}\n"
        f"True Order Block: {analysis['order_block']}\n\n"
        f"📍 İşlem Planı\n"
        f"İşlem Yönü: {analysis['direction']}\n"
        f"Giriş: {analysis['entry']}\n"
        f"Zarar Durdur: {analysis['sl']}\n"
        f"Kar Al: {analysis['tp']}\n"
        f"Güven Skoru: {analysis['confidence']}/100\n\n"
        f"⚠️ Risk Uyarısı\n"
        f"Haber Riski: Düşük\n"
        f"Not: Bu sinyal otomatik ICT mantığıyla üretilmiştir. İşleme girmeden önce son teyidini al."
    )


def build_no_setup_message(analysis: dict):
    return (
        f"📋 Manuel analiz sonucu\n\n"
        f"Varlık: {analysis['symbol']}\n"
        f"Seans: {analysis['session']}\n"
        f"Anlık Fiyat: {analysis['current_price']}\n"
        f"HTF Bias: {analysis['htf_bias']}\n"
        f"Likidite Sweep: {analysis['liquidity']}\n"
        f"MSS / Yapı: {analysis['structure']}\n"
        f"FVG: {analysis['fvg']}\n"
        f"Displacement: {analysis['displacement']}\n"
        f"Premium / Discount: {analysis['premium_discount']}\n"
        f"True Order Block: {analysis['order_block']}\n"
        f"Durum: Şu an net setup yok.\n"
        f"Not: Bot sessiz kalır."
    )


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
# TP / SL TRACKER
# =========================
def check_active_signals():
    symbols_to_remove = []

    for symbol, signal in ACTIVE_SIGNALS.items():
        current_price = fetch_twelvedata_price(symbol)
        if current_price is None:
            continue

        current_price = round_price(symbol, current_price)

        try:
            message_id = int(signal["message_id"]) if signal.get("message_id") is not None else None
        except Exception:
            message_id = None

        if signal["direction"] == "LONG":
            if current_price >= signal["tp"]:
                send_telegram_message(
                    build_tp_message(signal, current_price),
                    reply_to_message_id=message_id
                )
                symbols_to_remove.append(symbol)

            elif current_price <= signal["sl"]:
                send_telegram_message(
                    build_sl_message(signal, current_price),
                    reply_to_message_id=message_id
                )
                symbols_to_remove.append(symbol)

        elif signal["direction"] == "SHORT":
            if current_price <= signal["tp"]:
                send_telegram_message(
                    build_tp_message(signal, current_price),
                    reply_to_message_id=message_id
                )
                symbols_to_remove.append(symbol)

            elif current_price >= signal["sl"]:
                send_telegram_message(
                    build_sl_message(signal, current_price),
                    reply_to_message_id=message_id
                )
                symbols_to_remove.append(symbol)

    for symbol in symbols_to_remove:
        remove_active_signal(symbol)

# =========================
# SCAN ENGINE
# =========================
def scan_markets():
    while True:
        try:
            check_active_signals()

            if not market_is_open():
                time.sleep(SCAN_INTERVAL)
                continue

            for symbol in WATCHLIST:
                if symbol in ACTIVE_SIGNALS:
                    continue

                analysis = analyze_market(symbol)
                if not analysis:
                    continue

                if analysis["direction"] == "Bekle":
                    continue

                if analysis["confidence"] < MIN_SIGNAL_CONFIDENCE:
                    continue

                if signal_cooldown_active(analysis):
                    continue

                message = build_signal_message(analysis)
                result = send_telegram_message(message)

                if result.get("ok") is True:
                    message_id = result.get("result", {}).get("message_id")

                    set_active_signal(symbol, {
                        "symbol": symbol,
                        "direction": analysis["direction"],
                        "entry": analysis["entry"],
                        "sl": analysis["sl"],
                        "tp": analysis["tp"],
                        "message_id": message_id,
                        "created_at": datetime.utcnow().isoformat()
                    })

                    update_signal_cache(analysis)

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
        "mode": "Sadece güçlü ICT setup olursa sinyal gönderir"
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

    analysis = analyze_market(symbol)
    if not analysis:
        return jsonify({
            "ok": False,
            "error": "Analiz üretilemedi."
        }), 500

    if analysis["direction"] == "Bekle":
        result = send_telegram_message(build_no_setup_message(analysis))
        return jsonify(result)

    message = build_signal_message(analysis)
    result = send_telegram_message(message)

    if result.get("ok") is True:
        message_id = result.get("result", {}).get("message_id")

        set_active_signal(symbol, {
            "symbol": symbol,
            "direction": analysis["direction"],
            "entry": analysis["entry"],
            "sl": analysis["sl"],
            "tp": analysis["tp"],
            "message_id": message_id,
            "created_at": datetime.utcnow().isoformat()
        })

        update_signal_cache(analysis)

    return jsonify(result)


@app.route("/active-signals", methods=["GET"])
def active_signals():
    return jsonify({
        "ok": True,
        "active_signals": ACTIVE_SIGNALS
    })


@app.route("/reload-signals", methods=["GET"])
def reload_signals():
    load_active_signals()
    return jsonify({
        "ok": True,
        "message": "Active signals yeniden yüklendi.",
        "active_signals": ACTIVE_SIGNALS
    })

# =========================
# STARTUP
# =========================
def start_scanner():
    load_active_signals()
    scanner = threading.Thread(target=scan_markets)
    scanner.daemon = True
    scanner.start()


start_scanner()

if __name__ == "__main__":
    load_active_signals()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)