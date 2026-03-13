from flask import Flask
import requests
import os

app = Flask(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

TWELVEDATA_API_KEY = os.getenv("TWELVEDATA_API_KEY")
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY")


def send_telegram(text):

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text
    }

    requests.post(url, json=payload)


# -------------------------
# TWELVEDATA
# -------------------------

def get_twelve_price(symbol):

    url = "https://api.twelvedata.com/price"

    params = {
        "symbol": symbol,
        "apikey": TWELVEDATA_API_KEY
    }

    r = requests.get(url, params=params)
    data = r.json()

    if "price" in data:
        return float(data["price"])

    return None


# -------------------------
# FINNHUB
# -------------------------

def get_finnhub_price(symbol):

    url = f"https://finnhub.io/api/v1/quote?symbol={symbol}&token={FINNHUB_API_KEY}"

    r = requests.get(url)
    data = r.json()

    if "c" in data:
        return data["c"]

    return None


# -------------------------
# TEST ENDPOINT
# -------------------------

@app.route("/test")
def test():

    eurusd = get_twelve_price("EUR/USD")
    xauusd = get_twelve_price("XAU/USD")

    nasdaq = get_finnhub_price("NDX")
    us30 = get_finnhub_price("DJI")
    dxy = get_finnhub_price("DXY")

    text = f"""
Sistem Testi

EURUSD → TwelveData → {eurusd}
XAUUSD → TwelveData → {xauusd}

NASDAQ → Finnhub → {nasdaq}
US30 → Finnhub → {us30}
DXY → Finnhub → {dxy}
"""

    send_telegram(text)

    return "Test gönderildi"


@app.route("/")
def home():
    return "Trade Konseyi aktif"


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
