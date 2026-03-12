from flask import Flask, request, jsonify
import requests
import os
from datetime import datetime

app = Flask(__name__)

# =========================
# CONFIG
# =========================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

WATCHLIST = [
    "EURUSD",
    "GBPUSD",
    "XAUUSD",
    "XAGUSD",
    "OIL",
    "NASDAQ",
    "SP500",
]

EURUSD_MODEL = "London Reversal"
DEFAULT_MODEL = "ICT Intraday"

# =========================
# TELEGRAM MESSAGE
# =========================

def send_telegram_message(text):

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text
    }

    try:
        response = requests.post(url, json=payload, timeout=15)
        return response.json()
    except Exception as e:
        return {"error": str(e)}

# =========================
# NEWS FILTER (placeholder)
# =========================

def get_news_status(symbol):

    return {
        "status": "CLEAR",
        "comment": "No high impact news next 60 minutes"
    }

# =========================
# DXY FILTER (placeholder)
# =========================

def get_dxy_bias():

    return {
        "bias": "NEUTRAL",
        "comment": "DXY filter placeholder"
    }

# =========================
# QUALITY SCORE
# =========================

def calculate_quality_score(symbol):

    if symbol == "EURUSD":
        return 7.5
    else:
        return 6.8

# =========================
# BUILD MESSAGE
# =========================

def build_signal_message(symbol):

    model = EURUSD_MODEL if symbol == "EURUSD" else DEFAULT_MODEL

    news = get_news_status(symbol)
    dxy = get_dxy_bias()
    score = calculate_quality_score(symbol)

    time_now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    message = f"""
AI TRADE SIGNAL

Pair: {symbol}
Model: {model}
Timeframe: M5 / M15
Time: {time_now}

DXY Bias: {dxy['bias']}
DXY Note: {dxy['comment']}

News Status: {news['status']}
News Note: {news['comment']}

Setup Score: {score}/10

Watch for:
- liquidity sweep
- MSS
- FVG retest
- OB retest
"""

    return message

# =========================
# HOME
# =========================

@app.route("/")
def home():

    return jsonify({
        "status": "trade agent running"
    })

# =========================
# TEST TELEGRAM
# =========================

@app.route("/test")
def test():

    result = send_telegram_message("Trade agent online")

    return jsonify(result)

# =========================
# MANUAL SIGNAL
# =========================

@app.route("/signal", methods=["POST"])
def signal():

    data = request.get_json()

    symbol = data.get("symbol", "EURUSD").upper()

    if symbol not in WATCHLIST:
        return jsonify({
            "error": "symbol not supported"
        })

    message = build_signal_message(symbol)

    result = send_telegram_message(message)

    return jsonify(result)

# =========================
# WEBHOOK
# =========================

@app.route("/webhook", methods=["POST"])
def webhook():

    data = request.get_json()

    symbol = data.get("symbol", "EURUSD").upper()

    message = f"Webhook trigger received for {symbol}"

    result = send_telegram_message(message)

    return jsonify(result)

# =========================
# RUN SERVER
# =========================

if __name__ == "__main__":

    port = int(os.environ.get("PORT", 5000))

    app.run(host="0.0.0.0", port=port)
