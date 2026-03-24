from flask import Flask, request, jsonify
import json
import os
from datetime import datetime

app = Flask(__name__)

STATE_FILE = "tradingview_state.json"

@app.route("/", methods=["GET"])
def home():
    return "Webhook aktif", 200

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json(silent=True) or {}

    state = {
        "updated_at": datetime.utcnow().isoformat(),
        "dxy_bias": data.get("dxy_bias", "Yok"),
        "index_smt": data.get("index_smt", "Yok"),
        "us100": data.get("us100", {}),
        "sp500": data.get("sp500", {}),
    }

    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)

    print("Webhook geldi:", state)

    return jsonify({"status": "ok"}), 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)