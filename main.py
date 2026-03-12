from flask import Flask, jsonify
import requests
import os
import time
import threading
from datetime import datetime

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

def send_telegram_message(text):

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text
    }

    try:
        requests.post(url, json=payload)
    except Exception as e:
        print("Telegram error:", e)

def get_session():

    utc_hour = datetime.utcnow().hour

    if 0 <= utc_hour < 7:
        return "ASIA"

    if 7 <= utc_hour < 13:
        return "LONDON"

    if 13 <= utc_hour < 21:
        return "NEW YORK"

    return "CLOSED"

def get_dxy_bias():

    # placeholder
    return "NEUTRAL"

def analyze_symbol(symbol):

    if symbol == "DXY":
        return None

    bias = get_dxy_bias()

    now = datetime.utcnow().strftime("%H:%M")

    message = f"""
Market Scan

Pair: {symbol}
Time: {now} UTC

DXY Bias: {bias}

Status: Monitoring structure
Waiting for ICT setup
"""

    return message

def scan_markets():

    while True:

        session = get_session()

        if session == "CLOSED":
            time.sleep(600)
            continue

        print("Scanning markets...")

        for symbol in WATCHLIST:

            result = analyze_symbol(symbol)

            if result:
                send_telegram_message(result)

        time.sleep(SCAN_INTERVAL)

@app.route("/")
def home():

    return jsonify({
        "status": "AI trade agent running"
    })

@app.route("/test")
def test():

    send_telegram_message("AI trading agent active")

    return jsonify({"status": "message sent"})


def start_scanner():

    scanner = threading.Thread(target=scan_markets)
    scanner.daemon = True
    scanner.start()

start_scanner()

if __name__ == "__main__":

    port = int(os.environ.get("PORT", 5000))

    app.run(host="0.0.0.0", port=port)
