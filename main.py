from flask import Flask, jsonify
import requests
import os
import time
import threading
from datetime import datetime

app = Flask(__name__)

# =========================
# ENV
# =========================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
TWELVEDATA_API_KEY = os.getenv("TWELVEDATA_API_KEY")
ALPHAVANTAGE_API_KEY = os.getenv("ALPHAVANTAGE_API_KEY")

# =========================
# SETTINGS
# =========================
SCAN_INTERVAL = 30  # 30 saniye
WATCHLIST = ["EURUSD", "XAUUSD", "NASDAQ", "US30", "DXY"]

# Alpha Vantage için aday semboller
INDEX_CANDIDATES = {
    "NASDAQ": ["^NDX", "^IXIC", "QQQ"],
    "US30": ["^DJI", "DIA"],
    "DXY": ["DX-Y.NYB", "UUP"]
}

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


def send_telegram_message(text: str):
    if not TELEGRAM_CHAT_ID:
        return {"ok": False, "error": "Telegram chat id eksik."}

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text
    }

    return telegram_api("sendMessage", payload)

# =========================
# TWELVEDATA
# =========================
def fetch_twelvedata_price(symbol: str):
    if not TWELVEDATA_API_KEY:
        return None

    symbol_map = {
        "EURUSD": "EUR/USD",
        "XAUUSD": "XAU/USD"
    }

    real_symbol = symbol_map.get(symbol, symbol)

    url = "https://api.twelvedata.com/price"
    params = {
        "symbol": real_symbol,
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
# ALPHA VANTAGE
# =========================
def av_global_quote(symbol: str):
    if not ALPHAVANTAGE_API_KEY:
        return None

    url = "https://www.alphavantage.co/query"
    params = {
        "function": "GLOBAL_QUOTE",
        "symbol": symbol,
        "apikey": ALPHAVANTAGE_API_KEY
    }

    try:
        response = requests.get(url, params=params, timeout=25)
        data = response.json()

        quote = data.get("Global Quote", {})
        price = quote.get("05. price")

        if price is not None and str(price).strip() != "":
            return float(price)

        return None
    except Exception:
        return None


def av_symbol_search(query: str):
    if not ALPHAVANTAGE_API_KEY:
        return []

    url = "https://www.alphavantage.co/query"
    params = {
        "function": "SYMBOL_SEARCH",
        "keywords": query,
        "apikey": ALPHAVANTAGE_API_KEY
    }

    try:
        response = requests.get(url, params=params, timeout=25)
        data = response.json()
        matches = data.get("bestMatches", [])

        results = []
        for item in matches[:10]:
            results.append({
                "symbol": item.get("1. symbol"),
                "name": item.get("2. name"),
                "type": item.get("3. type"),
                "region": item.get("4. region"),
                "currency": item.get("8. currency")
            })

        return results
    except Exception:
        return []

# =========================
# MARKET FETCHERS
# =========================
def fetch_index_market(market_name: str):
    candidates = INDEX_CANDIDATES.get(market_name, [])
    tested = []

    for sym in candidates:
        price = av_global_quote(sym)
        tested.append({
            "symbol": sym,
            "price": price
        })
        if price is not None:
            return {
                "market": market_name,
                "symbol": sym,
                "price": price,
                "tested": tested
            }

    return {
        "market": market_name,
        "symbol": None,
        "price": None,
        "tested": tested
    }


def fetch_market_price(market: str):
    if market in ["EURUSD", "XAUUSD"]:
        return {
            "market": market,
            "symbol": market,
            "price": fetch_twelvedata_price(market)
        }

    if market in ["NASDAQ", "US30", "DXY"]:
        return fetch_index_market(market)

    return {
        "market": market,
        "symbol": None,
        "price": None
    }

# =========================
# ANALYSIS (şimdilik basit veri odaklı)
# =========================
def simple_bias(current_price, previous_price):
    if current_price is None or previous_price is None:
        return "Bilinmiyor"
    if current_price > previous_price:
        return "Yükseliş"
    if current_price < previous_price:
        return "Düşüş"
    return "Nötr"

# Önceki değerleri hafızada tut
LAST_PRICES = {}

def build_market_snapshot():
    lines = ["📡 TRADE KONSEYİ VERİ RAPORU", ""]

    for market in WATCHLIST:
        info = fetch_market_price(market)
        current_price = info.get("price")
        previous_price = LAST_PRICES.get(market)
        bias = simple_bias(current_price, previous_price)

        if current_price is not None:
            LAST_PRICES[market] = current_price

        used_symbol = info.get("symbol") if info.get("symbol") else "-"
        lines.append(f"{market}: {current_price}")
        lines.append(f"Sembol: {used_symbol}")
        lines.append(f"Kısa Yön: {bias}")
        lines.append("")

    lines.append(f"Zaman: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}")
    return "\n".join(lines)

# =========================
# BACKGROUND SCANNER
# =========================
def scanner_loop():
    while True:
        try:
            # Şimdilik otomatik mesaj atmıyor
            # sadece sistem çalışsın ve hafızada fiyat tutsun
            for market in WATCHLIST:
                info = fetch_market_price(market)
                if info.get("price") is not None:
                    LAST_PRICES[market] = info["price"]
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
        "status": "Trade Konseyi veri motoru aktif",
        "watchlist": WATCHLIST,
        "scan_interval_seconds": SCAN_INTERVAL
    })


@app.route("/test", methods=["GET"])
def test():
    eurusd = fetch_market_price("EURUSD")
    xauusd = fetch_market_price("XAUUSD")
    nasdaq = fetch_market_price("NASDAQ")
    us30 = fetch_market_price("US30")
    dxy = fetch_market_price("DXY")

    text = (
        f"✅ Sistem testi başarılı\n\n"
        f"EURUSD: {eurusd['price']}\n"
        f"XAUUSD: {xauusd['price']}\n"
        f"NASDAQ: {nasdaq['price']} ({nasdaq.get('symbol')})\n"
        f"US30: {us30['price']} ({us30.get('symbol')})\n"
        f"DXY: {dxy['price']} ({dxy.get('symbol')})"
    )

    result = send_telegram_message(text)
    return jsonify({
        "telegram_result": result,
        "data": {
            "EURUSD": eurusd,
            "XAUUSD": xauusd,
            "NASDAQ": nasdaq,
            "US30": us30,
            "DXY": dxy
        }
    })


@app.route("/test-all", methods=["GET"])
def test_all():
    text = build_market_snapshot()
    result = send_telegram_message(text)
    return jsonify({
        "ok": True,
        "telegram_result": result,
        "snapshot": text
    })


@app.route("/test-indices", methods=["GET"])
def test_indices():
    nasdaq = fetch_market_price("NASDAQ")
    us30 = fetch_market_price("US30")
    dxy = fetch_market_price("DXY")

    text = (
        f"📈 İndeks veri testi\n\n"
        f"NASDAQ: {nasdaq['price']} ({nasdaq.get('symbol')})\n"
        f"US30: {us30['price']} ({us30.get('symbol')})\n"
        f"DXY: {dxy['price']} ({dxy.get('symbol')})"
    )

    result = send_telegram_message(text)
    return jsonify({
        "ok": True,
        "telegram_result": result,
        "NASDAQ": nasdaq,
        "US30": us30,
        "DXY": dxy
    })


@app.route("/discover/<query>", methods=["GET"])
def discover(query):
    results = av_symbol_search(query)
    return jsonify({
        "ok": True,
        "query": query,
        "results": results
    })


@app.route("/manual/<market>", methods=["GET"])
def manual_market(market):
    market = market.upper()
    if market not in WATCHLIST:
        return jsonify({"ok": False, "error": "Geçersiz market"}), 400

    info = fetch_market_price(market)
    prev = LAST_PRICES.get(market)
    bias = simple_bias(info.get("price"), prev)

    if info.get("price") is not None:
        LAST_PRICES[market] = info["price"]

    text = (
        f"📋 Manuel veri sonucu\n\n"
        f"Market: {market}\n"
        f"Fiyat: {info.get('price')}\n"
        f"Kullanılan sembol: {info.get('symbol')}\n"
        f"Kısa yön: {bias}"
    )

    result = send_telegram_message(text)
    return jsonify({
        "ok": True,
        "market": market,
        "info": info,
        "telegram_result": result
    })

# =========================
# START
# =========================
def start_scanner():
    t = threading.Thread(target=scanner_loop)
    t.daemon = True
    t.start()


start_scanner()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)