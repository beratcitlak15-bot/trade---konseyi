from flask import Flask, jsonify
import requests
import os

app = Flask(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
TWELVEDATA_API_KEY = os.getenv("TWELVEDATA_API_KEY")

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
# TWELVEDATA HELPERS
# =========================
def td_symbol_search(query: str):
    if not TWELVEDATA_API_KEY:
        return {"ok": False, "error": "TWELVEDATA_API_KEY eksik.", "results": []}

    url = "https://api.twelvedata.com/symbol_search"
    params = {
        "symbol": query,
        "apikey": TWELVEDATA_API_KEY
    }

    try:
        response = requests.get(url, params=params, timeout=25)
        data = response.json()

        # API bazen direkt liste, bazen data objesi döndürebilir
        if isinstance(data, list):
            raw_results = data
        else:
            raw_results = data.get("data", data.get("results", []))

        results = []
        for item in raw_results[:15]:
            results.append({
                "symbol": item.get("symbol"),
                "instrument_name": item.get("instrument_name"),
                "exchange": item.get("exchange"),
                "mic_code": item.get("mic_code"),
                "country": item.get("country"),
                "type": item.get("type"),
                "currency": item.get("currency"),
            })

        return {"ok": True, "results": results}

    except Exception as e:
        return {"ok": False, "error": str(e), "results": []}


def td_price(symbol: str):
    if not TWELVEDATA_API_KEY:
        return None

    url = "https://api.twelvedata.com/price"
    params = {
        "symbol": symbol,
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


def td_time_series(symbol: str, interval="5min", outputsize=5):
    if not TWELVEDATA_API_KEY:
        return {"ok": False, "error": "TWELVEDATA_API_KEY eksik.", "candles": []}

    url = "https://api.twelvedata.com/time_series"
    params = {
        "symbol": symbol,
        "interval": interval,
        "outputsize": outputsize,
        "apikey": TWELVEDATA_API_KEY
    }

    try:
        response = requests.get(url, params=params, timeout=25)
        data = response.json()

        values = data.get("values", [])
        candles = []

        for item in values:
            try:
                candles.append({
                    "datetime": item["datetime"],
                    "open": float(item["open"]),
                    "high": float(item["high"]),
                    "low": float(item["low"]),
                    "close": float(item["close"]),
                })
            except Exception:
                continue

        return {"ok": True, "candles": candles}
    except Exception as e:
        return {"ok": False, "error": str(e), "candles": []}

# =========================
# REPORT BUILDERS
# =========================
def build_search_report(query: str, results: list):
    lines = [f"🔎 Sembol arama sonucu: {query}", ""]

    if not results:
        lines.append("Sonuç bulunamadı.")
        return "\n".join(lines)

    for i, item in enumerate(results[:10], start=1):
        lines.append(f"{i}) {item.get('symbol')}")
        lines.append(f"İsim: {item.get('instrument_name')}")
        lines.append(f"Borsa: {item.get('exchange')}")
        lines.append(f"Tür: {item.get('type')}")
        lines.append(f"Ülke: {item.get('country')}")
        lines.append("")

    return "\n".join(lines)


def build_validate_report(symbol: str, price, candles_result):
    lines = [f"🧪 Sembol doğrulama: {symbol}", ""]

    lines.append(f"Fiyat: {price}")

    if candles_result.get("ok") and candles_result.get("candles"):
        lines.append(f"5dk mum verisi: VAR ({len(candles_result['candles'])} adet)")
        last = candles_result["candles"][0]
        lines.append(f"İlk mum zamanı: {last['datetime']}")
    else:
        lines.append("5dk mum verisi: YOK")

    return "\n".join(lines)

# =========================
# ROUTES
# =========================
@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "ok": True,
        "status": "Trade Konseyi sembol keşif motoru aktif"
    })


@app.route("/test", methods=["GET"])
def test():
    text = (
        "✅ Sembol keşif motoru aktif\n\n"
        "Kullanılacak endpointler:\n"
        "/search/nasdaq\n"
        "/search/dow\n"
        "/search/dollar\n"
        "/search/us30\n"
        "/search/dxy\n"
        "/search/nq\n"
        "/search/ym\n"
        "/search/dx\n"
        "/validate/SYMBOL"
    )

    result = send_telegram_message(text)
    return jsonify(result)


@app.route("/search/<query>", methods=["GET"])
def search_symbol(query):
    result = td_symbol_search(query)

    if not result["ok"]:
        return jsonify(result), 500

    report = build_search_report(query, result["results"])
    telegram_result = send_telegram_message(report)

    return jsonify({
        "ok": True,
        "query": query,
        "results": result["results"],
        "telegram_result": telegram_result
    })


@app.route("/validate/<path:symbol>", methods=["GET"])
def validate_symbol(symbol):
    price = td_price(symbol)
    candles_result = td_time_series(symbol, interval="5min", outputsize=5)

    report = build_validate_report(symbol, price, candles_result)
    telegram_result = send_telegram_message(report)

    return jsonify({
        "ok": True,
        "symbol": symbol,
        "price": price,
        "candles_result": candles_result,
        "telegram_result": telegram_result
    })


@app.route("/quick-discover", methods=["GET"])
def quick_discover():
    queries = ["nasdaq", "dow", "dxy", "dollar", "us30", "nq", "ym", "dx"]
    output = {}

    for q in queries:
        result = td_symbol_search(q)
        output[q] = result["results"][:5] if result["ok"] else []

    return jsonify({
        "ok": True,
        "queries": output
    })

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)