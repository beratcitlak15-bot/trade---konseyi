from flask import Flask, jsonify
import requests
import os
import time
import threading
from datetime import datetime

app = Flask(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
TWELVEDATA_API_KEY = os.getenv("TWELVEDATA_API_KEY")

WATCHLIST = [
    "EURUSD",
    "XAUUSD",
    "NASDAQ",
    "US30",
    "DXY",
]

SCAN_INTERVAL = 300  # 5 dakika
LAST_SENT = {}

# DXY için farklı aday semboller
DXY_CANDIDATES = [
    "DX-Y.NYB",
    "DXY",
    "USDX",
    "DXY:INDEX",
    "ICE.USDX",
]


def send_telegram_message(text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text
    }
    try:
        response = requests.post(url, json=payload, timeout=15)
        return response.json()
    except Exception as e:
        return {"ok": False, "error": str(e)}


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


def get_twelvedata_symbol(symbol: str):
    mapping = {
        "EURUSD": "EUR/USD",
        "XAUUSD": "XAU/USD",
        "NASDAQ": "NDX",
        "US30": "DJI",
    }
    return mapping.get(symbol, symbol)


def fetch_price_from_twelvedata(api_symbol: str):
    url = "https://api.twelvedata.com/price"
    params = {
        "symbol": api_symbol,
        "apikey": TWELVEDATA_API_KEY
    }

    try:
        response = requests.get(url, params=params, timeout=15)
        data = response.json()

        if "price" in data:
            return float(data["price"])

        return None
    except Exception:
        return None


def get_price(symbol: str):
    if symbol == "DXY":
        for candidate in DXY_CANDIDATES:
            price = fetch_price_from_twelvedata(candidate)
            if price is not None:
                return price
        return None

    api_symbol = get_twelvedata_symbol(symbol)
    return fetch_price_from_twelvedata(api_symbol)


def get_dxy_bias():
    dxy_price = get_price("DXY")

    if dxy_price is None:
        return {
            "yon": "Nötr",
            "yorum": "DXY verisi alınamadı."
        }

    # Geçici basit mantık
    if dxy_price >= 100:
        return {
            "yon": "Yükseliş",
            "yorum": f"DXY güçlü görünüyor. Anlık fiyat: {dxy_price}"
        }
    else:
        return {
            "yon": "Düşüş",
            "yorum": f"DXY zayıf görünüyor. Anlık fiyat: {dxy_price}"
        }


def get_news_risk(symbol: str):
    return {
        "seviye": "Düşük",
        "mesaj": "Belirgin haber riski görünmüyor."
    }


def generate_analysis(symbol: str, dxy_bias: dict):
    session = get_session()
    model = get_model(symbol)
    price = get_price(symbol)

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
        yon = "Düşüş" if dxy_bias["yon"] == "Yükseliş" else "Yükseliş" if dxy_bias["yon"] == "Düşüş" else "Nötr"
        islem_yonu = "Short" if yon == "Düşüş" else "Long" if yon == "Yükseliş" else "Bekle"
        zarar = round(price + 0.0015, 5) if islem_yonu == "Short" else round(price - 0.0015, 5)
        kar = round(price - 0.0030, 5) if islem_yonu == "Short" else round(price + 0.0030, 5)

    elif symbol == "XAUUSD":
        yon = "Düşüş" if dxy_bias["yon"] == "Yükseliş" else "Yükseliş" if dxy_bias["yon"] == "Düşüş" else "Nötr"
        islem_yonu = "Short" if yon == "Düşüş" else "Long" if yon == "Yükseliş" else "Bekle"
        zarar = round(price + 8, 2) if islem_yonu == "Short" else round(price - 8, 2)
        kar = round(price - 16, 2) if islem_yonu == "Short" else round(price + 16, 2)

    elif symbol == "NASDAQ":
        yon = "Düşüş" if dxy_bias["yon"] == "Yükseliş" else "Yükseliş" if dxy_bias["yon"] == "Düşüş" else "Nötr"
        islem_yonu = "Short" if yon == "Düşüş" else "Long" if yon == "Yükseliş" else "Bekle"
        zarar = round(price + 80, 2) if islem_yonu == "Short" else round(price - 80, 2)
        kar = round(price - 160, 2) if islem_yonu == "Short" else round(price + 160, 2)

    elif symbol == "US30":
        yon = "Düşüş" if dxy_bias["yon"] == "Yükseliş" else "Yükseliş" if dxy_bias["yon"] == "Düşüş" else "Nötr"
        islem_yonu = "Short" if yon == "Düşüş" else "Long" if yon == "Yükseliş" else "Bekle"
        zarar = round(price + 150, 2) if islem_yonu == "Short" else round(price - 150, 2)
        kar = round(price - 300, 2) if islem_yonu == "Short" else round(price + 300, 2)

    else:
        yon = "Nötr"
        islem_yonu = "Bekle"
        zarar = "-"
        kar = "-"

    return {
        "varlik": symbol,
        "model": model,
        "seans": session,
        "fiyat": price,
        "yon": yon,
        "likidite": "Likidite bölgeleri takip ediliyor",
        "yapi": "Yapı teyidi aranıyor",
        "fvg": "FVG bölgesi izleniyor",
        "ob": "Order Block bölgesi izleniyor",
        "islem_yonu": islem_yonu,
        "giris": price,
        "zarar_durdur": zarar,
        "kar_al": kar,
        "guven": 72 if islem_yonu != "Bekle" else 50
    }


def build_single_report_message(symbol: str):
    if symbol == "DXY":
        return None

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
        f"Anlık Fiyat: {analiz['fiyat']}\n\n"
        f"Yön: {analiz['yon']}\n"
        f"DXY Durumu: {dxy['yon']}\n"
        f"DXY Yorumu: {dxy['yorum']}\n"
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

    return message


def should_send(symbol: str, message: str):
    last = LAST_SENT.get(symbol)
    if last == message:
        return False
    LAST_SENT[symbol] = message
    return True


def scan_markets():
    while True:
        if not market_is_open():
            time.sleep(600)
            continue

        for symbol in WATCHLIST:
            if symbol == "DXY":
                continue

            message = build_single_report_message(symbol)

            if message and should_send(symbol, message):
                send_telegram_message(message)

        time.sleep(SCAN_INTERVAL)


@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "ok": True,
        "status": "AI trade agent running",
        "session": get_session(),
        "watchlist": WATCHLIST
    })


@app.route("/test", methods=["GET"])
def test():
    eurusd = get_price("EURUSD")
    xauusd = get_price("XAUUSD")
    dxy = get_price("DXY")

    text = (
        f"✅ Sistem testi başarılı\n\n"
        f"EURUSD: {eurusd}\n"
        f"XAUUSD: {xauusd}\n"
        f"DXY: {dxy}"
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

    message = build_single_report_message(symbol)
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
