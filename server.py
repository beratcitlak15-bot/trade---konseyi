from flask import Flask, request, jsonify
import requests
import os
from datetime import datetime, UTC

app = Flask(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

def send_telegram(msg: str) -> None:
    if not BOT_TOKEN or not CHAT_ID:
        print("Telegram env eksik: BOT_TOKEN veya CHAT_ID yok")
        return

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        resp = requests.post(
            url,
            json={
                "chat_id": CHAT_ID,
                "text": msg
            },
            timeout=15
        )
        print("Telegram status:", resp.status_code, resp.text)
    except Exception as e:
        print("Telegram hata:", e)

@app.route("/", methods=["GET"])
def home():
    return "Webhook aktif", 200

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = request.get_json(force=True)
        print("GELEN DATA:", data)

        event = str(data.get("event", "")).lower()

        # DXY bias/state geldiğinde Telegram'a gönderme
        if event == "dxy_state":
            print("DXY state geldi, Telegram'a gönderilmedi.")
            return jsonify({"status": "ignored_dxy_state"}), 200

        # Sadece gerçek signal event'ini Telegram'a gönder
        if event == "signal":
            symbol = data.get("symbol", "YOK")
            direction = data.get("direction", "YOK")
            entry = data.get("entry", "YOK")
            sl = data.get("sl", "YOK")
            tp = data.get("tp", "YOK")
            score = data.get("score", "")
            quality = data.get("quality", "")

            message = f"""🔥 YENİ SİNYAL GELDİ

📊 {symbol}
📈 Yön: {direction}

🎯 Entry: {entry}
🛑 SL: {sl}
💰 TP: {tp}

⭐ Score: {score}
💎 Quality: {quality}

⏰ {datetime.now(UTC).isoformat()}"""

            send_telegram(message)
            return jsonify({"status": "ok"}), 200

        print("Tanımsız event, ignore edildi:", event)
        return jsonify({"status": "ignored"}), 200

    except Exception as e:
        print("HATA:", e)
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)