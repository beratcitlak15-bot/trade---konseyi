from flask import Flask, request, jsonify
import json
import os
from datetime import datetime

app = Flask(__name__)

STATE_FILE = "tradingview_state.json"


def default_state():
    return {
        "updated_at": None,
        "dxy_bias": "Yok",
        "index_smt": "Yok",
        "us100": {},
        "sp500": {}
    }


def load_state():
    if not os.path.exists(STATE_FILE):
        return default_state()

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, dict):
                return default_state()
            return {
                "updated_at": data.get("updated_at"),
                "dxy_bias": data.get("dxy_bias", "Yok"),
                "index_smt": data.get("index_smt", "Yok"),
                "us100": data.get("us100", {}),
                "sp500": data.get("sp500", {}),
            }
    except Exception:
        return default_state()


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


@app.route("/", methods=["GET"])
def home():
    return "Webhook aktif", 200


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200

@app.route("/webhook", methods=["POST"])
def webhook():
    import json

    raw_data = request.data
    print("RAW DATA:", raw_data)

    try:
        data = request.get_json(force=True)
    except Exception:
        data = json.loads(raw_data)

    print("PARSED DATA:", data)

    state = load_state()
    state["updated_at"] = datetime.utcnow().isoformat()

    symbol = str(data.get("symbol", "")).upper()
    event = str(data.get("event", "")).lower()

    if symbol == "DXY" or event == "dxy_state":
        state["dxy_bias"] = data.get("dxy_bias", state["dxy_bias"])
        save_state(state)
        print("DXY webhook geldi:", data)
        return jsonify({"status": "ok", "symbol": "DXY"}), 200

    payload = {
        "price": data.get("price"),
        "direction": data.get("direction", "YOK"),
        "entry": data.get("entry"),
        "sl": data.get("sl"),
        "tp": data.get("tp"),
        "score": data.get("score", 0),
        "quality": data.get("quality", "Yok"),
        "bias": data.get("bias", "Yok"),
        "h1_bias": data.get("h1_bias", "Yok"),
        "h4_bias": data.get("h4_bias", "Yok"),
        "w1_bias": data.get("w1_bias", "Yok"),
        "sweep": data.get("sweep", "Yok"),
        "mss": data.get("mss", "Yok"),
        "choch": data.get("choch", "Yok"),
        "displacement": data.get("displacement", "Yok"),
        "fvg": data.get("fvg", "Yok"),
        "pd": data.get("pd", "Yok"),
        "dxy_bias": data.get("dxy_bias", state["dxy_bias"]),
        "killzone": data.get("killzone", "Yok"),
        "ob_low": data.get("ob_low"),
        "ob_high": data.get("ob_high"),
        "reason": data.get("reason", f"TradingView {symbol} setup"),
    }

    if data.get("index_smt"):
        state["index_smt"] = data.get("index_smt")

    if symbol == "US100":
        payload["index_smt"] = state["index_smt"]
        state["us100"] = payload
        save_state(state)
        print("US100 webhook geldi:", data)
        return jsonify({"status": "ok", "symbol": "US100"}), 200

    if symbol == "SP500":
        payload["index_smt"] = state["index_smt"]
        state["sp500"] = payload
        save_state(state)
        print("SP500 webhook geldi:", data)
        return jsonify({"status": "ok", "symbol": "SP500"}), 200

    print("Bilinmeyen webhook payload:", data)
    return jsonify({"status": "ignored", "reason": "unknown symbol", "payload": data}), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)