from flask import Flask, request, jsonify
import requests
from datetime import datetime

app = Flask(__name__)

# 🔥 TELEGRAM AYARLARI
BOT_TOKEN = "8626229562:AAFRph72ngdmySSHJ5L2iF97xFQhEuh6K3w"
CHAT_ID = "8463420441"

def send_telegram(msg):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, json={
            "chat_id": CHAT_ID,
            "text": msg
        })
    except Exception as e:
        print("Telegram hata:", e)

# ✅ TEST
@app.route("/", methods=["GET"])
def home():
    return "Webhook aktif", 200

# ✅ WEBHOOK
@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = request.get_json(force=True)
        print("GELEN DATA:", data)

        symbol = data.get("symbol", "YOK")
        direction = data.get("direction", "YOK")
        entry = data.get("entry", "YOK")
        sl = data.get("sl", "YOK")
        tp = data.get("tp", "YOK")
        score = data.get("score", "")
        quality = data.get("quality", "")

        message = f"""
🔥 YENİ SİNYAL GELDİ

📊 {symbol}
📈 Yön: {direction}

🎯 Entry: {entry}
🛑 SL: {sl}
💰 TP: {tp}

⭐ Score: {score}
💎 Quality: {quality}

⏰ {datetime.utcnow()}
"""

        send_telegram(message)

        return jsonify({"status": "ok"}), 200

    except Exception as e:
        print("HATA:", e)
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)