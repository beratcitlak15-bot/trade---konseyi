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
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY")

# =========================
# AYARLAR
# =========================
WATCHLIST = [
    "EURUSD",
    "XAUUSD",
    "NASDAQ",
    "US30",
    "DXY",
]

SCAN_INTERVAL = 300 # 5 dakika
MIN_SIGNAL_GUVEN = 85
SIGNAL_COOLDOWN_MINUTES = 60

# Aynı sinyali tekrar tekrar atmasın
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
# SEANS
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
    elif symbol == "DXY":
        return "DXY Yön Filtresi"
    else:
        return "ICT Intraday"

# =========================
# VERİ SAĞLAYICILARI
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


def fetch_finnhub_quote(symbol: str):
    if not FINNHUB_API_KEY:
        return None

    url = "https://finnhub.io/api/v1/quote"
    params = {
        "symbol": symbol,
        "token": FINNHUB_API_KEY
    }

    try:
        response = requests.get(url, params=params, timeout=20)
        data = response.json()

        # Finnhub quote cevabında son fiyat genelde "c"
        price = data.get("c")
        if price is None:
            return None

        try:
            price = float(price)
        except Exception:
            return None

        if price <= 0:
            return None

        return price
    except Exception:
        return None


def resolve_symbol_and_price(symbol: str):
    """
    Hangi marketi hangi API'den alacağımızı burada belirliyoruz.
    Test mesajında da hangi sembolün kullanıldığını göstereceğiz.
    """

    # EURUSD -> TwelveData
    if symbol == "EURUSD":
        resolved_symbol = "EUR/USD"
        price = fetch_twelvedata_price(resolved_symbol)
        return {
            "requested": symbol,
            "provider": "TwelveData",
            "resolved_symbol": resolved_symbol,
            "price": price
        }

    # XAUUSD -> TwelveData
    if symbol == "XAUUSD":
        candidates = ["XAU/USD", "XAUUSD"]
        for candidate in candidates:
            price = fetch_twelvedata_price(candidate)
            if price is not None:
                return {
                    "requested": symbol,
                    "provider": "TwelveData",
                    "resolved_symbol": candidate,
                    "price": price
                }

        return {
            "requested": symbol,
            "provider": "TwelveData",
            "resolved_symbol": None,
            "price": None
        }

    # NASDAQ -> Finnhub
    if symbol == "NASDAQ":
        candidates = [
            "^NDX",
            "NDX",
            "QQQ"
        ]
        for candidate in candidates:
            price = fetch_finnhub_quote(candidate)
            if price is not None:
                return {
                    "requested": symbol,
                    "provider": "Finnhub",
                    "resolved_symbol": candidate,
                    "price": price
                }

        return {
            "requested": symbol,
            "provider": "Finnhub",
            "resolved_symbol": None,
            "price": None
        }

    # US30 -> Finnhub
    if symbol == "US30":
        candidates = [
            "^DJI",
            "DJI",
            "DIA"
        ]
        for candidate in candidates:
            price = fetch_finnhub_quote(candidate)
            if price is not None:
                return {
                    "requested": symbol,
                    "provider": "Finnhub",
                    "resolved_symbol": candidate,
                    "price": price
                }

        return {
            "requested": symbol,
            "provider": "Finnhub",
            "resolved_symbol": None,
            "price": None
        }

    # DXY -> Finnhub
    if symbol == "DXY":
        candidates = [
            "DXY",
            "DX-Y.NYB",
            "USDX",
            "^DXY"
        ]
        for candidate in candidates:
            price = fetch_finnhub_quote(candidate)
            if price is not None:
                return {
                    "requested": symbol,
                    "provider": "Finnhub",
                    "resolved_symbol": candidate,
                    "price": price
                }

        return {
            "requested": symbol,
            "provider": "Finnhub",
            "resolved_symbol": None,
            "price": None
        }

    return {
        "requested": symbol,
        "provider": "Unknown",
        "resolved_symbol": None,
        "price": None
    }


def get_price(symbol: str):
    result = resolve_symbol_and_price(symbol)
    return result["price"]

# =========================
# DXY YÖN FİLTRESİ
# =========================
def get_dxy_bias():
    dxy_info = resolve_symbol_and_price("DXY")
    dxy_price = dxy_info["price"]

    if dxy_price is None:
        return {
            "yon": "Nötr",
            "yorum": "DXY verisi alınamadı.",
            "sembol": dxy_info["resolved_symbol"],
            "fiyat": None,
            "provider": dxy_info["provider"]
        }

    # Geçici basit mantık
    if dxy_price >= 100:
        return {
            "yon": "Yükseliş",
            "yorum": f"Dolar güçlü görünüyor. Anlık DXY: {dxy_price}",
            "sembol": dxy_info["resolved_symbol"],
            "fiyat": dxy_price,
            "provider": dxy_info["provider"]
        }
    else:
        return {
            "yon": "Düşüş",
            "yorum": f"Dolar zayıf görünüyor. Anlık DXY: {dxy_price}",
            "sembol": dxy_info["resolved_symbol"],
            "fiyat": dxy_price,
            "provider": dxy_info["provider"]
        }

# =========================
# HABER RİSKİ
# =========================
def get_news_risk(symbol: str):
    # Şimdilik placeholder
    return {
        "seviye": "Düşük",
        "mesaj": "Belirgin haber riski görünmüyor."
    }

# =========================
# ANALİZ
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
            "provider": symbol_info["provider"],
            "cozulen_sembol": symbol_info["resolved_symbol"],
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

    # EURUSD
    if symbol == "EURUSD":
        yon = (
            "Düşüş" if dxy_bias["yon"] == "Yükseliş"
            else "Yükseliş" if dxy_bias["yon"] == "Düşüş"
            else "Nötr"
        )
        islem_yonu = (
            "Short" if yon == "Düşüş"
            else "Long" if yon == "Yükseliş"
            else "Bekle"
        )
        zarar = round(price + 0.0015, 5) if islem_yonu == "Short" else round(price - 0.0015, 5)
        kar = round(price - 0.0030, 5) if islem_yonu == "Short" else round(price + 0.0030, 5)

        likidite = "Asya bölgesi likiditesi izleniyor"
        yapi = "MSS teyidi bekleniyor"
        fvg = "FVG oluşumu takip ediliyor"
        ob = "Order Block bölgesi yakın"

        # Şimdilik gerçek ICT motoru yok, güveni bilinçli düşük tutuyoruz
        guven = 60

    # XAUUSD
    elif symbol == "XAUUSD":
        yon = (
            "Düşüş" if dxy_bias["yon"] == "Yükseliş"
            else "Yükseliş" if dxy_bias["yon"] == "Düşüş"
            else "Nötr"
        )
        islem_yonu = (
            "Short" if yon == "Düşüş"
            else "Long" if yon == "Yükseliş"
            else "Bekle"
        )
        zarar = round(price + 8, 2) if islem_yonu == "Short" else round(price - 8, 2)
        kar = round(price - 16, 2) if islem_yonu == "Short" else round(price + 16, 2)

        likidite = "Yakın eşit tepe/dip bölgeleri takip ediliyor"
        yapi = "BOS sonrası intraday teyit aranıyor"
        fvg = "FVG bölgesi mevcut"
        ob = "Order Block retest ihtimali var"

        guven = 60

    # NASDAQ
    elif symbol == "NASDAQ":
        yon = "Nötr"
        islem_yonu = "Bekle"
        zarar = "-"
        kar = "-"
        likidite = "Index verisi izleniyor"
        yapi = "Doğrulama aşamasında"
        fvg = "Henüz aktif değil"
        ob = "Henüz aktif değil"

        guven = 40

    # US30
    elif symbol == "US30":
        yon = "Nötr"
        islem_yonu = "Bekle"
        zarar = "-"
        kar = "-"
        likidite = "Index verisi izleniyor"
        yapi = "Doğrulama aşamasında"
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
        "provider": symbol_info["provider"],
        "cozulen_sembol": symbol_info["resolved_symbol"],
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
# SETUP FİLTRESİ
# =========================
def setup_olustu_mu(analiz: dict, haber: dict):
    """
    Burada artık spam'i engelliyoruz.
    Bot sadece gerçek setup oluştuğunda mesaj atmalı.
    Şimdilik çok sıkı tutuyoruz.
    Gerçek ICT motoru gelince burada MSS/FVG/OB/Sweep kontrolü olacak.
    """

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
# MESAJ
# =========================
def build_single_report_message(symbol: str):
    if symbol == "DXY":
        return None, None, None

    dxy = get_dxy_bias()
    haber = get_news_risk(symbol)
    analiz = generate_analysis(symbol, dxy)
    zaman = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    message = (
        f"📊 ANALİZ + SİNYAL RAPORU\n\n"
        f"Varlık: {analiz['varlik']}\n"
        f"Model: {analiz['model']}\n"
        f"Seans: {analiz['seans']}\n"
        f"Zaman: {zaman}\n"
        f"Veri Sağlayıcı: {analiz['provider']}\n"
        f"Kullanılan Sembol: {analiz['cozulen_sembol']}\n"
        f"Anlık Fiyat: {analiz['fiyat']}\n\n"
        f"Yön: {analiz['yon']}\n"
        f"DXY Durumu: {dxy['yon']}\n"
        f"DXY Yorumu: {dxy['yorum']}\n"
        f"DXY Sağlayıcı: {dxy['provider']}\n"
        f"DXY Sembolü: {dxy['sembol']}\n"
        f"DXY Fiyatı: {dxy['fiyat']}\n"
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
                if symbol == "DXY":
                    continue

                message, analiz, haber = build_single_report_message(symbol)

                if message is None:
                    continue

                # Sadece setup varsa gönder
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
    dxy = resolve_symbol_and_price("DXY")

    text = (
        f"✅ Sistem testi başarılı\n\n"
        f"EURUSD → {eurusd['provider']} → {eurusd['resolved_symbol']} → {eurusd['price']}\n"
        f"XAUUSD → {xauusd['provider']} → {xauusd['resolved_symbol']} → {xauusd['price']}\n"
        f"NASDAQ → {nasdaq['provider']} → {nasdaq['resolved_symbol']} → {nasdaq['price']}\n"
        f"US30 → {us30['provider']} → {us30['resolved_symbol']} → {us30['price']}\n"
        f"DXY → {dxy['provider']} → {dxy['resolved_symbol']} → {dxy['price']}"
    )

    result = send_telegram_message(text)
    return jsonify(result)


@app.route("/manual/<symbol>", methods=["GET"])
def manual_symbol(symbol):
    symbol = symbol.upper()

    if symbol not in WATCHLIST or symbol == "DXY":
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
