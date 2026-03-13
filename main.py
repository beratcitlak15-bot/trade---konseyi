from flask import Flask, jsonify, request
import os
import time
import json
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

# Sadece bu kullanıcı botu kullanabilsin
ALLOWED_CHAT_IDS = [8463420441]

SCAN_INTERVAL_SECONDS = 30
MIN_CONFIDENCE = 80
SIGNALS_FILE = "active_signals.json"

# =========================
# TRADINGVIEW CONFIG
# =========================
MARKETS = {
    "EURUSD": {"symbol": "EURUSD", "exchange": "OANDA"},
    "GBPUSD": {"symbol": "GBPUSD", "exchange": "OANDA"},
    "USDJPY": {"symbol": "USDJPY", "exchange": "OANDA"},
    "XAUUSD": {"symbol": "XAUUSD", "exchange": "OANDA"},
    "XAGUSD": {"symbol": "XAGUSD", "exchange": "OANDA"},
    "NASDAQ": {"symbol": "US100", "exchange": "CAPITALCOM"},
    "US30": {"symbol": "US30", "exchange": "CAPITALCOM"},
    "SPX500": {"symbol": "US500", "exchange": "CAPITALCOM"},
    "DXY": {"symbol": "DXY", "exchange": "TVC"},
}

tv = None
ACTIVE_SIGNALS = {}
LAST_SIGNAL_KEYS = {}

# =========================
# TELEGRAM
# =========================
def telegram_api(method: str, payload: dict):
    if not TELEGRAM_BOT_TOKEN:
        return {"ok": False, "error": "TELEGRAM_BOT_TOKEN eksik"}

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}"
    try:
        r = requests.post(url, json=payload, timeout=20)
        return r.json()
    except Exception as e:
        return {"ok": False, "error": str(e)}


def send_telegram_message(text: str, reply_to_message_id=None):
    if not TELEGRAM_CHAT_ID:
        return {"ok": False, "error": "TELEGRAM_CHAT_ID eksik"}

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
# FILE HELPERS
# =========================
def load_active_signals():
    global ACTIVE_SIGNALS
    try:
        if os.path.exists(SIGNALS_FILE):
            with open(SIGNALS_FILE, "r", encoding="utf-8") as f:
                ACTIVE_SIGNALS = json.load(f)
        else:
            ACTIVE_SIGNALS = {}
    except Exception:
        ACTIVE_SIGNALS = {}


def save_active_signals():
    try:
        with open(SIGNALS_FILE, "w", encoding="utf-8") as f:
            json.dump(ACTIVE_SIGNALS, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

# =========================
# TV CONNECT
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
def get_hist(market_name: str, interval, n_bars=200):
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
        df = df.reset_index()
        return df
    except Exception:
        return None


def round_price(market: str, value):
    if value is None:
        return None
    if market in ["EURUSD", "GBPUSD", "USDJPY"]:
        return round(float(value), 5)
    return round(float(value), 2)


def get_last_close(df: pd.DataFrame):
    if df is None or df.empty:
        return None
    return float(df.iloc[-1]["close"])


def get_last_open(df: pd.DataFrame):
    if df is None or df.empty:
        return None
    return float(df.iloc[-1]["open"])


def get_last_high(df: pd.DataFrame):
    if df is None or df.empty:
        return None
    return float(df.iloc[-1]["high"])


def get_last_low(df: pd.DataFrame):
    if df is None or df.empty:
        return None
    return float(df.iloc[-1]["low"])


def candle_range(row):
    return float(row["high"] - row["low"])


def candle_body(row):
    return abs(float(row["close"] - row["open"]))


def avg_range(df: pd.DataFrame, lookback=20):
    if df is None or len(df) < lookback:
        return None
    sample = df.tail(lookback)
    return float((sample["high"] - sample["low"]).mean())


def swing_high(df: pd.DataFrame, lookback=20):
    if df is None or len(df) < lookback:
        return None
    return float(df.tail(lookback)["high"].max())


def swing_low(df: pd.DataFrame, lookback=20):
    if df is None or len(df) < lookback:
        return None
    return float(df.tail(lookback)["low"].min())

# =========================
# SESSION
# =========================
def current_session():
    utc_hour = datetime.utcnow().hour
    if 7 <= utc_hour < 12:
        return "Londra"
    if 12 <= utc_hour < 16:
        return "London-NY Overlap"
    if 16 <= utc_hour < 21:
        return "New York"
    if 0 <= utc_hour < 7:
        return "Asya"
    return "Geçiş"

# =========================
# ICT-LIKE ANALYSIS
# =========================
def get_htf_bias(df_1h: pd.DataFrame):
    if df_1h is None or len(df_1h) < 60:
        return "Nötr"

    ema20 = df_1h["close"].ewm(span=20).mean().iloc[-1]
    ema50 = df_1h["close"].ewm(span=50).mean().iloc[-1]
    close_now = float(df_1h.iloc[-1]["close"])

    recent_hi = swing_high(df_1h.iloc[:-1], 20)
    recent_lo = swing_low(df_1h.iloc[:-1], 20)

    if recent_hi is None or recent_lo is None:
        return "Nötr"

    if close_now > ema20 > ema50 and close_now >= recent_hi * 0.997:
        return "Yükseliş"

    if close_now < ema20 < ema50 and close_now <= recent_lo * 1.003:
        return "Düşüş"

    return "Nötr"


def detect_liquidity_sweep(df_5m: pd.DataFrame):
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


def detect_real_mss(df_5m: pd.DataFrame):
    if df_5m is None or len(df_5m) < 35:
        return "Yok"

    recent = df_5m.tail(12)
    prev = df_5m.iloc[:-12]

    prev_high = float(prev.tail(10)["high"].max())
    prev_low = float(prev.tail(10)["low"].min())

    recent_close = float(recent.iloc[-1]["close"])

    if recent_close > prev_high:
        return "Bullish MSS"

    if recent_close < prev_low:
        return "Bearish MSS"

    return "Yok"


def detect_displacement(df_5m: pd.DataFrame):
    if df_5m is None or len(df_5m) < 25:
        return "Zayıf"

    last = df_5m.iloc[-1]
    ar = avg_range(df_5m.iloc[:-1], 15)
    if ar is None:
        return "Zayıf"

    last_range = candle_range(last)
    last_body = candle_body(last)

    if last_range >= ar * 1.5 and last_body >= ar * 0.8:
        return "Güçlü"

    return "Zayıf"


def detect_fvg(df_5m: pd.DataFrame):
    if df_5m is None or len(df_5m) < 3:
        return "Yok"

    a = df_5m.iloc[-3]
    b = df_5m.iloc[-2]
    c = df_5m.iloc[-1]

    if float(a["high"]) < float(c["low"]) and float(b["close"]) > float(b["open"]):
        return "Bullish FVG"

    if float(a["low"]) > float(c["high"]) and float(b["close"]) < float(b["open"]):
        return "Bearish FVG"

    return "Yok"


def detect_pd_zone(df_5m: pd.DataFrame):
    if df_5m is None or len(df_5m) < 40:
        return "Belirsiz"

    hi = swing_high(df_5m, 40)
    lo = swing_low(df_5m, 40)
    if hi is None or lo is None:
        return "Belirsiz"

    eq = (hi + lo) / 2
    close_now = float(df_5m.iloc[-1]["close"])

    return "Discount" if close_now < eq else "Premium"


def detect_true_order_block(df_5m: pd.DataFrame):
    if df_5m is None or len(df_5m) < 12:
        return {"label": "Yok", "zone": None}

    recent = df_5m.tail(10).reset_index(drop=True)

    # Bullish OB: son güçlü yukarı displacement öncesindeki son bearish mum
    for i in range(len(recent) - 3, 1, -1):
        row = recent.iloc[i]
        nxt = recent.iloc[i + 1]

        if float(row["close"]) < float(row["open"]):
            if candle_range(nxt) > candle_range(row) * 1.2 and float(nxt["close"]) > float(row["high"]):
                return {
                    "label": "Bullish OB",
                    "zone": (
                        float(row["low"]),
                        float(row["high"])
                    )
                }

    # Bearish OB: son güçlü aşağı displacement öncesindeki son bullish mum
    for i in range(len(recent) - 3, 1, -1):
        row = recent.iloc[i]
        nxt = recent.iloc[i + 1]

        if float(row["close"]) > float(row["open"]):
            if candle_range(nxt) > candle_range(row) * 1.2 and float(nxt["close"]) < float(row["low"]):
                return {
                    "label": "Bearish OB",
                    "zone": (
                        float(row["low"]),
                        float(row["high"])
                    )
                }

    return {"label": "Yok", "zone": None}


def price_in_ob(price, ob_zone):
    if price is None or ob_zone is None:
        return False
    low_, high_ = ob_zone
    return low_ <= price <= high_

# =========================
# SCORING
# =========================
def analyze_market(market_name: str):
    df_5m = get_hist(market_name, Interval.in_5_minute, 220)
    df_1h = get_hist(market_name, Interval.in_1_hour, 220)

    if df_5m is None or df_1h is None:
        return None

    price = round_price(market_name, get_last_close(df_5m))
    htf_bias = get_htf_bias(df_1h)
    sweep = detect_liquidity_sweep(df_5m)
    mss = detect_real_mss(df_5m)
    displacement = detect_displacement(df_5m)
    fvg = detect_fvg(df_5m)
    pd_zone = detect_pd_zone(df_5m)
    ob = detect_true_order_block(df_5m)
    session = current_session()

    direction = "Bekle"
    confidence = 0

    # LONG şartları
    long_score = 0
    if htf_bias == "Yükseliş":
        long_score += 20
    if sweep == "Alt likidite sweep":
        long_score += 15
    if mss == "Bullish MSS":
        long_score += 20
    if displacement == "Güçlü":
        long_score += 15
    if fvg == "Bullish FVG":
        long_score += 10
    if pd_zone == "Discount":
        long_score += 10
    if ob["label"] == "Bullish OB" and price_in_ob(price, ob["zone"]):
        long_score += 15

    # SHORT şartları
    short_score = 0
    if htf_bias == "Düşüş":
        short_score += 20
    if sweep == "Üst likidite sweep":
        short_score += 15
    if mss == "Bearish MSS":
        short_score += 20
    if displacement == "Güçlü":
        short_score += 15
    if fvg == "Bearish FVG":
        short_score += 10
    if pd_zone == "Premium":
        short_score += 10
    if ob["label"] == "Bearish OB" and price_in_ob(price, ob["zone"]):
        short_score += 15

    if long_score >= MIN_CONFIDENCE and long_score > short_score:
        direction = "LONG"
        confidence = long_score
    elif short_score >= MIN_CONFIDENCE and short_score > long_score:
        direction = "SHORT"
        confidence = short_score
    else:
        direction = "Bekle"
        confidence = max(long_score, short_score)

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

    ob_text = "Yok"
    if ob["zone"]:
        ob_text = f"{ob['label']} ({round_price(market_name, ob['zone'][0])} - {round_price(market_name, ob['zone'][1])})"

    return {
        "market": market_name,
        "session": session,
        "price": price,
        "htf_bias": htf_bias,
        "sweep": sweep,
        "mss": mss,
        "displacement": displacement,
        "fvg": fvg,
        "pd_zone": pd_zone,
        "true_ob": ob_text,
        "direction": direction,
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "confidence": confidence
    }

# =========================
# MESSAGE BUILDERS
# =========================
def signal_key(a: dict):
    return f"{a['market']}|{a['direction']}|{a['entry']}|{a['sl']}|{a['tp']}"


def build_signal_text(a: dict):
    return (
        f"🚨 ANALİZ + SİNYAL RAPORU\n\n"
        f"Varlık: {a['market']}\n"
        f"Seans: {a['session']}\n"
        f"Anlık Fiyat: {a['price']}\n\n"
        f"HTF Bias: {a['htf_bias']}\n"
        f"Likidite Sweep: {a['sweep']}\n"
        f"MSS: {a['mss']}\n"
        f"FVG: {a['fvg']}\n"
        f"Premium/Discount: {a['pd_zone']}\n"
        f"Displacement: {a['displacement']}\n"
        f"True Order Block: {a['true_ob']}\n\n"
        f"İşlem Yönü: {a['direction']}\n"
        f"Giriş: {a['entry']}\n"
        f"Zarar Durdur: {a['sl']}\n"
        f"Kar Al: {a['tp']}\n"
        f"Güven Skoru: {a['confidence']}/100\n\n"
        f"⚠️ Risk Uyarısı\n"
        f"Not: Bu sinyal otomatik tarama sonucudur. İşleme girmeden önce kendi teyidini al."
    )


def build_manual_text(a: dict):
    return (
        f"📋 Manuel analiz sonucu\n\n"
        f"Varlık: {a['market']}\n"
        f"Seans: {a['session']}\n"
        f"Anlık Fiyat: {a['price']}\n"
        f"HTF Bias: {a['htf_bias']}\n"
        f"Likidite Sweep: {a['sweep']}\n"
        f"MSS: {a['mss']}\n"
        f"FVG: {a['fvg']}\n"
        f"Premium/Discount: {a['pd_zone']}\n"
        f"Displacement: {a['displacement']}\n"
        f"True Order Block: {a['true_ob']}\n"
        f"Durum: Şu an net setup yok.\n"
        f"Not: Bot sessiz kalır."
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
# ACTIVE SIGNAL TRACKER
# =========================
def check_active_signals():
    remove_list = []

    for market, sig in ACTIVE_SIGNALS.items():
        df_5m = get_hist(market, Interval.in_5_minute, 5)
        if df_5m is None:
            continue

        current_price = round_price(market, get_last_close(df_5m))
        if current_price is None:
            continue

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

    save_active_signals()

# =========================
# AUTO SCANNER
# =========================
def scanner_loop():
    while True:
        try:
            check_active_signals()

            for market in MARKETS.keys():
                if market in ACTIVE_SIGNALS:
                    continue

                a = analyze_market(market)
                if a is None:
                    continue

                if a["direction"] == "Bekle":
                    continue

                if a["confidence"] < MIN_CONFIDENCE:
                    continue

                key = signal_key(a)
                if LAST_SIGNAL_KEYS.get(market) == key:
                    continue

                result = send_telegram_message(build_signal_text(a))
                message_id = None
                if isinstance(result, dict):
                    message_id = result.get("result", {}).get("message_id")

                ACTIVE_SIGNALS[market] = {
                    "market": a["market"],
                    "direction": a["direction"],
                    "entry": a["entry"],
                    "sl": a["sl"],
                    "tp": a["tp"],
                    "message_id": message_id
                }
                LAST_SIGNAL_KEYS[market] = key
                save_active_signals()

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
        "status": "Trade Konseyi aktif",
        "markets": list(MARKETS.keys()),
        "scan_interval_seconds": SCAN_INTERVAL_SECONDS
    })


@app.route("/test", methods=["GET"])
def test():
    result = {}
    for market in MARKETS.keys():
        a = analyze_market(market)
        result[market] = a if a else {"error": "veri alınamadı"}
    return jsonify({"ok": True, "data": result})


@app.route("/manual/<market>", methods=["GET"])
def manual_market(market):
    market = market.upper()
    if market not in MARKETS:
        return jsonify({"ok": False, "error": "Geçersiz market"}), 400

    a = analyze_market(market)
    if a is None:
        return jsonify({"ok": False, "error": "Veri alınamadı"}), 500

    if a["direction"] == "Bekle":
        tg = send_telegram_message(build_manual_text(a))
    else:
        tg = send_telegram_message(build_signal_text(a))

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


@app.route("/status", methods=["GET"])
def status():
    session = current_session()
    text = (
        f"✅ Sistem durumu\n\n"
        f"Seans: {session}\n"
        f"Aktif sinyal sayısı: {len(ACTIVE_SIGNALS)}\n"
        f"İzlenen market sayısı: {len(MARKETS)}"
    )
    tg = send_telegram_message(text)
    return jsonify({"ok": True, "telegram_result": tg})


@app.route("/telegram-webhook", methods=["POST"])
def telegram_webhook():
    data = request.get_json(silent=True) or {}
    message = data.get("message", {})
    chat = message.get("chat", {})
    chat_id = chat.get("id")

    if not chat_id or not is_allowed_chat(chat_id):
        return jsonify({"ok": True})

    text = message.get("text", "").strip()

    if text == "/start":
        send_telegram_message("✅ Trade Konseyi aktif. Yetkili kullanıcı doğrulandı.")
    elif text == "/markets":
        send_telegram_message("İzlenen marketler:\n" + "\n".join(MARKETS.keys()))
    elif text == "/status":
        session = current_session()
        send_telegram_message(
            f"✅ Sistem durumu\n\n"
            f"Seans: {session}\n"
            f"Aktif sinyal sayısı: {len(ACTIVE_SIGNALS)}\n"
            f"İzlenen market sayısı: {len(MARKETS)}"
        )
    elif text.lower().startswith("/manual "):
        market = text.lower().replace("/manual ", "").upper()
        if market in MARKETS:
            a = analyze_market(market)
            if a:
                if a["direction"] == "Bekle":
                    send_telegram_message(build_manual_text(a))
                else:
                    send_telegram_message(build_signal_text(a))

    return jsonify({"ok": True})

# =========================
# START
# =========================
def start_background():
    load_active_signals()
    init_tv()
    t = threading.Thread(target=scanner_loop, daemon=True)
    t.start()


start_background()

if __name__ == "__main__":
    load_active_signals()
    init_tv()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)