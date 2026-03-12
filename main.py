from flask import Flask, jsonify
import requests
import os
import time
import threading
from datetime import datetime
import requests

app = Flask(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

WATCHLIST = [
    "EURUSD",
    "XAUUSD",
    "NASDAQ",
    "US30",
    "DXY"
]

SCAN_INTERVAL = 300  # 5 dakika

# Son gönderilen mesajları kontrol etmek için basit hafıza
LAST_SENT = {}

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

    # Yaklaşık seans ayrımı
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

def get_dxy_bias():
    """
    Şimdilik örnek yön filtresi.
    İleride gerçek H1/H4/Daily analiz eklenecek.
    """
    utc_minute = datetime.utcnow().minute

    if utc_minute < 20:
        return {
            "yon": "Yükseliş",
            "yorum": "Dolar güçlü görünüyor."
        }
    elif utc_minute < 40:
        return {
            "yon": "Düşüş",
            "yorum": "Dolar zayıf görünüyor."
        }
    else:
        return {
            "yon": "Nötr",
            "yorum": "DXY tarafında net yön yok."
        }

def get_news_risk(symbol: str):
    """
    Şimdilik placeholder.
    İleride gerçek ekonomik takvim bağlanacak.
    """
    utc_minute = datetime.utcnow().minute

    if utc_minute % 3 == 0:
        return {
            "seviye": "Orta",
            "mesaj": "Yakın zamanda haber oynaklığı olabilir."
        }
    return {
        "seviye": "Düşük",
        "mesaj": "Belirgin haber riski görünmüyor."
    }

def generate_mock_analysis(symbol: str, dxy_bias: dict):
    """
    Şimdilik iskelet analiz motoru.
    İleride gerçek sweep, MSS, BOS, FVG, OB, killzone,
    high timeframe bias ve London Reversal mantığı eklenecek.
    """
    session = get_session()
    model = get_model(symbol)

    if symbol == "EURUSD":
        yon = "Düşüş" if dxy_bias["yon"] == "Yükseliş" else "Yükseliş" if dxy_bias["yon"] == "Düşüş" else "Nötr"
        likidite = "Asya bölgesi likiditesi izleniyor"
        yapi = "MSS teyidi bekleniyor"
        fvg = "FVG oluşumu takip ediliyor"
        ob = "Order Block bölgesi yakın"
        islem_yonu = "Short" if yon == "Düşüş" else "Long" if yon == "Yükseliş" else "Bekle"
        giris = "1.08250"
        zarar_durdur = "1.08420"
        kar_al = "1.07890"
        guven = 74 if islem_yonu != "Bekle" else 58

    elif symbol == "XAUUSD":
        yon = "Düşüş" if dxy_bias["yon"] == "Yükseliş" else "Yükseliş" if dxy_bias["yon"] == "Düşüş" else "Nötr"
        likidite = "Yakın eşit tepe/dip bölgeleri takip ediliyor"
        yapi = "BOS sonrası intraday teyit aranıyor"
        fvg = "FVG bölgesi mevcut"
        ob = "Order Block retest ihtimali var"
        islem_yonu = "Short" if yon == "Düşüş" else "Long" if yon == "Yükseliş" else "Bekle"
        giris = "2921.40"
        zarar_durdur = "2928.10"
        kar_al = "2908.60"
        guven = 77 if islem_yonu != "Bekle" else 60

    elif symbol == "NASDAQ":
        yon = "Düşüş" if dxy_bias["yon"] == "Yükseliş" else "Yükseliş" if dxy_bias["yon"] == "Düşüş" else "Nötr"
        likidite = "Önceki seansın likidite bölgeleri takip ediliyor"
        yapi = "Intraday yapı değişimi izleniyor"
        fvg = "FVG bölgesi korunuyor"
        ob = "Order Block dönüş alanı mevcut"
        islem_yonu = "Short" if yon == "Düşüş" else "Long" if yon == "Yükseliş" else "Bekle"
        giris = "20850"
        zarar_durdur = "20935"
        kar_al = "20690"
        guven = 72 if islem_yonu != "Bekle" else 57

    elif symbol == "US30":
        yon = "Düşüş" if dxy_bias["yon"] == "Yükseliş" else "Yükseliş" if dxy_bias["yon"] == "Düşüş" else "Nötr"
        likidite = "Gün içi tepe/dip likiditesi takipte"
        yapi = "M15 yapısı izleniyor"
        fvg = "FVG henüz tam teyitli değil"
        ob = "Order Block bölgesine yaklaşım var"
        islem_yonu = "Short" if yon == "Düşüş" else "Long" if yon == "Yükseliş" else "Bekle"
        giris = "39280"
        zarar_durdur = "39440"
        kar_al = "38990"
        guven = 69 if islem_yonu != "Bekle" else 55

    else:
        return None

    return {
        "varlik": symbol,
        "model": model,
        "seans": session,
        "yon": yon,
        "likidite": likidite,
        "yapi": yapi,
        "fvg": fvg,
        "ob": ob,
        "islem_yonu": islem_yonu,
        "giris": giris,
        "zarar_durdur": zarar_durdur,
        "kar_al": kar_al,
        "guven": guven
    }

def build_single_report_message(symbol: str):
    if symbol == "DXY":
        return None

    dxy = get_dxy_bias()
    haber = get_news_risk(symbol)
    analiz = generate_mock_analysis(symbol, dxy)

    if analiz is None:
        return None

    zaman = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    message = (
        f"📊 ANALİZ + SİNYAL RAPORU\n\n"
        f"Varlık: {analiz['varlik']}\n"
        f"Model: {analiz['model']}\n"
        f"Seans: {analiz['seans']}\n"
        f"Zaman: {zaman}\n\n"
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
    """
    Aynı mesajı sürekli spamlamasın diye basit kontrol.
    """
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
        "watchlist": WATCHLIST,
        "scan_interval_seconds": SCAN_INTERVAL
    })

@app.route("/test", methods=["GET"])
def test():
    test_message = (
        "📊 ANALİZ + SİNYAL RAPORU\n\n"
        "Varlık: TEST\n"
        "Model: Sistem Kontrolü\n"
        "Seans: Test\n\n"
        "Yön: Nötr\n"
        "DXY Durumu: Nötr\n"
        "DXY Yorumu: Test bağlantısı başarılı.\n"
        "Likidite: Test\n"
        "Yapı: Test\n"
        "FVG: Test\n"
        "Order Block: Test\n\n"
        "📍 İşlem Planı\n"
        "İşlem Yönü: Bekle\n"
        "Giriş: -\n"
        "Zarar Durdur: -\n"
        "Kar Al: -\n"
        "Güven Skoru: 100/100\n\n"
        "⚠️ Risk Uyarısı\n"
        "Haber Riski: Düşük\n"
        "Not: Sistem çalışıyor."
    )
    result = send_telegram_message(test_message)
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
def get_price(symbol):
    api_key = os.getenv("TWELVEDATA_API_KEY")

    url = f"https://api.twelvedata.com/price?symbol={symbol}&apikey={api_key}"

    try:
        r = requests.get(url).json()
        return float(r["price"])
    except:
        return None
        price = get_price("EUR/USD")
print("EURUSD:", price)
