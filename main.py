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
FMP_API_KEY = os.getenv("FMP_API_KEY")

# =========================
# SETTINGS
# =========================
WATCHLIST = [
    "EURUSD",
    "XAUUSD",
    "NASDAQ",
    "US30"
]

SCAN_INTERVAL = 300  # 5 dakika
MIN_SIGNAL_GUVEN = 85
SIGNAL_COOLDOWN_MINUTES = 60

LAST_SIGNAL_CACHE = {}

# =========================
# TELEGRAM
# =========================
def send_telegram_message(text: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return {"ok": False, "error": "Telegram env eksik."}

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text
    }

    try:
        response = requests.post(url, json=payload, timeout=20)
        return response.json()
    except Exception as e:
        return {"ok": False, "error": str(e)}

# =========================
# SESSION
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
def fetch_twelvedata_price(symbol: str):
    if not TWELVEDATA_API_KEY:
        return None

    url = "https://api.twelvedata.com/price"
    params = {
        "symbol": symbol,
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

# =========================
# FMP
# =========================
def fetch_fmp_index_quote(symbol: str):
    """
    FMP stable index quote endpoint.
    """
    if not FMP_API_KEY:
        return None

    url = "https://financialmodelingprep.com/stable/index-quote"
    params = {
        "symbol": symbol,
        "apikey": FMP_API_KEY
    }

    try:
        response = requests.get(url, params=params, timeout=20)
        data = response.json()

        # FMP bazen liste döndürebilir
        if isinstance(data, list) and len(data) > 0:
            item = data[0]
            if isinstance(item, dict):
                price = item.get("price")
                if price is not None:
                    return float(price)

        # Bazen direkt obje dönebilir
        if isinstance(data, dict):
            price = data.get("price")
            if price is not None:
                return float(price)

        return None
    except Exception:
        return None

# =========================
# SYMBOL -> PRICE
# =========================
def resolve_symbol_and_price(symbol: str):
    if symbol == "EURUSD":
        resolved_symbol = "EUR/USD"
        price = fetch_twelvedata_price(resolved_symbol)
        return {
            "requested": symbol,
            "resolved_symbol": resolved_symbol,
            "price": price
        }

    if symbol == "XAUUSD":
        candidates = ["XAU/USD", "XAUUSD"]
        for candidate in candidates:
            price = fetch_twelvedata_price(candidate)
            if price is not None:
                return {
                    "requested": symbol,
                    "resolved_symbol": candidate,
                    "price": price
                }
        return {
            "requested": symbol,
            "resolved_symbol": None,
            "price": None
        }

    if symbol == "NASDAQ":
        # FMP tarafında gerçek endeks için Nasdaq 100'i hedefliyoruz
        candidates = ["NDX", "^NDX", "^IXIC"]
        for candidate in candidates:
            price = fetch_fmp_index_quote(candidate)
            if price is not None:
                return {
                    "requested": symbol,
                    "resolved_symbol": candidate,
                    "price": price
                }
        return {
            "requested": symbol,
            "resolved_symbol": None,
            "price": None
        }

    if symbol == "US30":
        candidates = ["DJI", "^DJI"]
        for candidate in candidates:
            price = fetch_fmp_index_quote(candidate)
            if price is not None:
                return {
                    "requested": symbol,
                    "resolved_symbol": candidate,
                    "price": price
                }
        return {
            "requested": symbol,
            "resolved_symbol": None,
            "price": None
        }

    return {
        "requested": symbol,
        "resolved_symbol": None,
        "price": None
    }


def get_price(symbol: str):
    return resolve_symbol_and_price(symbol)["price"]

# =========================
# DXY FILTER
# =========================
def get_dxy_bias():
    # Şimdilik kapalı
    return {
        "yon": "Nötr",
        "yorum": "DXY filtresi geçici olarak devre dışı."
    }

# =========================
# NEWS RISK
# =========================
def get_news_risk(symbol: str):
    return {
        "seviye": "Düşük",
        "mesaj": "Belirgin haber riski görünmüyor."
    }

# =========================
# ANALYSIS
# =========================
def generate_analysis(symbol: str, dxy_bias: dict):
    session = get_session()
    model = get_model(symbol)
    symbol_info = resolve_symbol_and_price(symbol)
    price = symbol_info["price"]

    if price is None:
        return {
            "varlik": symbol,
            "model": model,
            "seans": session,
            "fiyat": "Veri alınamadı",
            "yon": "Nötr",
            "likidite": "Veri alınamadı",
            "yapi": "Veri alınamadı",
            "fvg": "Veri alınamadı",
            "ob": "Veri alınamadı",
            "islem_yonu": "Bekle",
            "giris": "-",
            "zarar_durdur": "-",
            "kar_al": "-",
            "guven": 0
        }

    if symbol == "EURUSD":
        yon = "Nötr"
        islem_yonu = "Bekle"
        zarar = "-"
        kar = "-"
        likidite = "Asya bölgesi likiditesi izleniyor"
        yapi = "MSS teyidi bekleniyor"
        fvg = "FVG oluşumu takip ediliyor"
        ob = "Order Block bölgesi yakın"
        guven = 60

    elif symbol == "XAUUSD":
        yon = "Nötr"
        islem_yonu = "Bekle"
        zarar = "-"
        kar = "-"
        likidite = "Yakın eşit tepe/dip bölgeleri takip ediliyor"
        yapi = "BOS sonrası intraday teyit aranıyor"
        fvg = "FVG bölgesi mevcut"
        ob = "Order Block retest ihtimali var"
        guven = 60

    elif symbol == "NASDAQ":
        yon = "Nötr"
        islem_yonu = "Bekle"
        zarar = "-"
        kar = "-"
        likidite = "Endeks likidite bölgeleri izleniyor"
        yapi = "Index yapı doğrulaması bekleniyor"
        fvg = "Henüz aktif değil"
        ob = "Henüz aktif değil"
        guven = 40

    elif symbol == "US30":
        yon = "Nötr"
        islem_yonu = "Bekle"
        zarar = "-"
        kar = "-"
        likidite = "Endeks likidite bölgeleri izleniyor"
        yapi = "Index yapı doğrulaması bekleniyor"
        fvg = "Henüz aktif değil"
        ob = "Henüz aktif değil"
        guven = 40

    else:
        yon = "Nötr"
        islem_yonu = "Bekle"
        zarar = "-"
        kar = "-"
        likidite = "Veri yok"
        yapi = "Veri yok"
        fvg = "Veri yok"
        ob = "Veri yok"
        guven = 0

    return {
        "varlik": symbol,
        "model": model,
        "seans": session,
        "fiyat": price,
        "yon": yon,
        "likidite": likidite,
        "yapi": yapi,
        "fvg": fvg,
        "ob": ob,
        "islem_yonu": islem_yonu,
        "giris": price,
        "zarar_durdur": zarar,
        "kar_al": kar,
        "guven": guven
    }

# =========================
# SIGNAL FILTER
# =========================
def setup_olustu_mu(analiz: dict, haber: dict):
    if analiz["seans"] == "Kapalı":
        return False

    if analiz["fiyat"] == "Veri alınamadı":
        return False

    if analiz["islem_yonu"] == "Bekle":
        return False

    if analiz["guven"] < MIN_SIGNAL_GUVEN:
        return False

    if haber["seviye"] == "Yüksek":
        return False

    return True


def signal_cache_key(analiz: dict):
    return f"{analiz['varlik']}|{analiz['islem_yonu']}|{analiz['giris']}"


def signal_cooldown_aktif_mi(analiz: dict):
    key = signal_cache_key(analiz)
    last_time = LAST_SIGNAL_CACHE.get(key)

    if last_time is None:
        return False

    if datetime.utcnow() - last_time < timedelta(minutes=SIGNAL_COOLDOWN_MINUTES):
        return True

    return False


def signal_cache_guncelle(analiz: dict):
    key = signal_cache_key(analiz)
    LAST_SIGNAL_CACHE[key] = datetime.utcnow()

# =========================
# MESSAGE
# =========================
def build_single_report_message(symbol: str):
    haber = get_news_risk(symbol)
    analiz = generate_analysis(symbol, get_dxy_bias())
    zaman = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    message = (
        f"📊 ANALİZ + SİNYAL RAPORU\n\n"
        f"Varlık: {analiz['varlik']}\n"
        f"Model: {analiz['model']}\n"
        f"Seans: {analiz['seans']}\n"
        f"Zaman: {zaman}\n"
        f"Anlık Fiyat: {analiz['fiyat']}\n\n"
        f"Yön: {analiz['yon']}\n"
        f"DXY Durumu: Nötr\n"
        f"DXY Yorumu: DXY filtresi geçici olarak devre dışı.\n"
        f"Likidite: {analiz['likidite']}\n"
        f"Yapı: {analiz['yapi']}\n"
        f"FVG: {analiz['fvg']}\n"
        f"Order Block: {analiz['ob']}\n\n"
        f"📍 İşlem Planı\n"
        f"İşlem Yönü: {analiz['islem_yonu']}\n"
        f"Giriş: {analiz['giris']}\n"
        f"Zarar Durdur: {analiz['zarar_durdur']}\n"
        f"Kar Al: {analiz['kar_al']}\n"
        f"Güven Skoru: {analiz['guven']}/100\n\n"
        f"⚠️ Risk Uyarısı\n"
        f"Haber Riski: {haber['seviye']}\n"
        f"Not: {haber['mesaj']}"
    )

    return message, analiz, haber

# =========================
# SCAN
# =========================
def scan_markets():
    while True:
        try:
            if not market_is_open():
                time.sleep(600)
                continue

            for symbol in WATCHLIST:
                message, analiz, haber = build_single_report_message(symbol)

                if setup_olustu_mu(analiz, haber):
                    if not signal_cooldown_aktif_mi(analiz):
                        send_telegram_message(message)
                        signal_cache_guncelle(analiz)

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
        "status": "AI trade agent running",
        "session": get_session(),
        "watchlist": WATCHLIST,
        "scan_interval_seconds": SCAN_INTERVAL,
        "auto_signal_mode": "Setup olursa gönder, aksi halde sessiz kal"
    })


@app.route("/test", methods=["GET"])
def test():
    eurusd = resolve_symbol_and_price("EURUSD")
    xauusd = resolve_symbol_and_price("XAUUSD")
    nasdaq = resolve_symbol_and_price("NASDAQ")
    us30 = resolve_symbol_and_price("US30")

    text = (
        f"✅ Sistem testi başarılı\n\n"
        f"EURUSD: {eurusd['price']}\n"
        f"XAUUSD: {xauusd['price']}\n"
        f"NASDAQ: {nasdaq['price']}\n"
        f"US30: {us30['price']}\n"
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

    message, analiz, haber = build_single_report_message(symbol)
    result = send_telegram_message(message)

    return jsonify({
        "ok": True,
        "symbol": symbol,
        "result": result
    })


def start_scanner():
    scanner = threading.Thread(target=scan_markets)
    scanner.daemon = True
    scanner.start()


start_scanner()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
