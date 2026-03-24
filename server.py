from flask import Flask, request, jsonify
import json
import os
from datetime import datetime, UTC

app = Flask(__name__)

STATE_FILE = "tradingview_state.json"

# =========================
# STATE LOAD / SAVE
# =========================

def load_state():
    if not os.path.exists(STATE_FILE):
        return {
            "updated_at": None,
            "dxy_bias": "Yok",
            "index_smt": "Yok",
            "us100": {},
            "sp500": {}
        }
    with open(STATE_FILE, "r") as f:
        return json.load(f)

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)

# =========================
# ROUTES
# =========================

@app.route("/", methods=["GET"])
def home():
    return "Webhook aktif", 200

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200

# =========================
# WEBHOOK
# =========================

@app.route("/webhook", methods=["POST"])
def webhook():
    print("------ WEBHOOK GELDİ ------")

    raw_data = request.data
    print("RAW DATA:", raw_data)

    try:
        data = request.get_json(force=True)
    except Exception:
        data = json.loads(raw_data)

    print("PARSED DATA:", data)

    state = load_state()
    print("STATE BEFORE:", state)

    # ZAMAN GÜNCELLE
    state["updated_at"] = datetime.now(UTC).isoformat()

    symbol = str(data.get("symbol", "")).upper()
    event = str(data.get("event", "")).lower()

    # =========================
    # DXY
    # =========================
    if symbol == "DXY" or event == "dxy_state":
        state["dxy_bias"] = data.get("dxy_bias", state["dxy_bias"])
        save_state(state)

        print("✅ DXY GÜNCELLENDİ")
        print("STATE AFTER:", state)

        return jsonify({"status": "ok", "type": "dxy"}), 200

    # =========================
    # US100 / US500 SIGNAL
    # =========================
    elif symbol in ["US100", "US500"] or event == "signal":

        signal_data = {
            "symbol": symbol,
            "direction": data.get("direction"),
            "price": data.get("price"),
            "entry": data.get("entry"),
            "sl": data.get("sl"),
            "tp": data.get("tp"),
            "score": data.get("score"),
            "quality": data.get("quality"),
            "time": state["updated_at"]
        }

        if symbol == "US100":
            state["us100"] = signal_data
        elif symbol == "US500":
            state["sp500"] = signal_data

        save_state(state)

        print("🔥 SIGNAL KAYDEDİLDİ:", signal_data)
        print("STATE AFTER:", state)

        return jsonify({"status": "ok", "type": "signal"}), 200

    # =========================
    # UNKNOWN
    # =========================
    else:
        print("⚠️ TANINMAYAN DATA:", data)
        return jsonify({"status": "ignored"}), 200