import os
import time
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests
from flask import Flask, jsonify

app = Flask(__name__)

# =========================================================
# ENV
# =========================================================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
TWELVEDATA_API_KEY = os.getenv("TWELVEDATA_API_KEY", "").strip()
PORT = int(os.getenv("PORT", "10000"))

# =========================================================
# SETTINGS
# =========================================================
SCAN_INTERVAL_SECONDS = 600
TIMEFRAME = "15min"
OUTPUTSIZE = 60
MIN_SIGNAL_SCORE = 75
SIGNAL_COOLDOWN_MINUTES = 45

MARKETS = [
    "EUR/USD",
    "GBP/USD",
    "XAU/USD",
    "USD/JPY",
    "AUD/USD",
]

STATE: Dict[str, Any] = {
    "service_started_at": datetime.now(timezone.utc).isoformat(),
    "last_scan_at": None,
    "last_results": {},
    "scanner_started": False,
    "active_signals": {},
}

scan_lock = threading.Lock()
scanner_started_once = False

# =========================================================
# UTILS
# =========================================================
def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except Exception:
        return None


def minutes_since(iso_dt: Optional[str]) -> float:
    if not iso_dt:
        return 999999.0

    try:
        dt = datetime.fromisoformat(iso_dt)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - dt
        return delta.total_seconds() / 60.0
    except Exception:
        return 999999.0


def send_telegram_message(text: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram env eksik, mesaj gönderilmedi.")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
    }

    try:
        response = requests.post(url, json=payload, timeout=20)
        print(f"Telegram status: {response.status_code}")

        if response.status_code != 200:
            print(f"Telegram response: {response.text}")
            return False

        return True
    except Exception as e:
        print(f"Telegram gönderim hatası: {e}")
        return False


# =========================================================
# TWELVEDATA
# =========================================================
def fetch_twelvedata_series(symbol: str) -> Optional[Dict[str, Any]]:
    if not TWELVEDATA_API_KEY:
        print("TWELVEDATA_API_KEY yok.")
        return None

    url = "https://api.twelvedata.com/time_series"
    params = {
        "apikey": TWELVEDATA_API_KEY,
        "symbol": symbol,
        "interval": TIMEFRAME,
        "outputsize": OUTPUTSIZE,
        "format": "JSON",
    }

    try:
        response = requests.get(url, params=params, timeout=25)
        data = response.json()

        if response.status_code != 200:
            print(f"{symbol} HTTP hata: {response.status_code} - {response.text}")
            return None

        if data.get("status") == "error":
            print(f"{symbol} API hata: {data}")
            return None

        if "values" not in data or not data["values"]:
            print(f"{symbol} veri boş geldi.")
            return None

        return data

    except Exception as e:
        print(f"{symbol} fetch hatası: {e}")
        return None


# =========================================================
# ANALYSIS HELPERS
# =========================================================
def build_candles(raw: Dict[str, Any]) -> List[Dict[str, Any]]:
    values = raw.get("values", [])
    candles: List[Dict[str, Any]] = []

    for row in values:
        o = safe_float(row.get("open"))
        h = safe_float(row.get("high"))
        l = safe_float(row.get("low"))
        c = safe_float(row.get("close"))

        if None in (o, h, l, c):
            continue

        candles.append({
            "datetime": row.get("datetime"),
            "open": o,
            "high": h,
            "low": l,
            "close": c,
        })

    # TwelveData genelde en yeniyi üstte verir, analiz için terse çeviriyoruz
    candles.reverse()
    return candles


def detect_bias(candles: List[Dict[str, Any]]) -> str:
    if len(candles) < 20:
        return "Nötr"

    closes = [c["close"] for c in candles[-20:]]
    sma_5 = sum(closes[-5:]) / 5
    sma_20 = sum(closes) / 20

    if sma_5 > sma_20:
        return "Yükseliş"
    if sma_5 < sma_20:
        return "Düşüş"
    return "Nötr"


def detect_choch_like(candles: List[Dict[str, Any]]) -> str:
    if len(candles) < 4:
        return "Yok"

    _, b, _, d = candles[-4], candles[-3], candles[-2], candles[-1]

    if d["close"] > b["high"]:
        return "Bullish CHoCH"
    if d["close"] < b["low"]:
        return "Bearish CHoCH"
    return "Yok"


def detect_mss_like(candles: List[Dict[str, Any]]) -> str:
    if len(candles) < 6:
        return "Yok"

    recent_high = max(x["high"] for x in candles[-6:-1])
    recent_low = min(x["low"] for x in candles[-6:-1])
    last_close = candles[-1]["close"]

    if last_close > recent_high:
        return "Bullish MSS"
    if last_close < recent_low:
        return "Bearish MSS"
    return "Yok"


def detect_liquidity_sweep(candles: List[Dict[str, Any]]) -> str:
    if len(candles) < 6:
        return "Yok"

    prev_high = max(x["high"] for x in candles[-6:-1])
    prev_low = min(x["low"] for x in candles[-6:-1])
    last = candles[-1]

    if last["high"] > prev_high and last["close"] < prev_high:
        return "Üst likidite sweep"
    if last["low"] < prev_low and last["close"] > prev_low:
        return "Alt likidite sweep"
    return "Yok"


def detect_displacement(candles: List[Dict[str, Any]]) -> str:
    if len(candles) < 10:
        return "Zayıf"

    bodies = [abs(c["close"] - c["open"]) for c in candles[-10:-1]]
    avg_body = sum(bodies) / len(bodies) if bodies else 0
    last_body = abs(candles[-1]["close"] - candles[-1]["open"])

    if avg_body == 0:
        return "Zayıf"

    if last_body >= avg_body * 1.8:
        return "Güçlü"
    if last_body >= avg_body * 1.2:
        return "Orta"
    return "Zayıf"


def detect_premium_discount(candles: List[Dict[str, Any]]) -> str:
    if len(candles) < 20:
        return "Nötr"

    recent = candles[-20:]
    high_ = max(x["high"] for x in recent)
    low_ = min(x["low"] for x in recent)
    mid = (high_ + low_) / 2
    last_close = candles[-1]["close"]

    if last_close > mid:
        return "Premium"
    if last_close < mid:
        return "Discount"
    return "Nötr"


def detect_fvg(candles: List[Dict[str, Any]]) -> str:
    if len(candles) < 3:
        return "Yok"

    a, _, c = candles[-3], candles[-2], candles[-1]

    if c["low"] > a["high"]:
        return "Bullish FVG"
    if c["high"] < a["low"]:
        return "Bearish FVG"
    return "Yok"


def score_signal(
    bias: str,
    sweep: str,
    mss: str,
    choch: str,
    fvg: str,
    displacement: str,
    premium_discount: str,
) -> Dict[str, Any]:
    long_score = 0
    short_score = 0

    if bias == "Yükseliş":
        long_score += 20
    elif bias == "Düşüş":
        short_score += 20

    if sweep == "Alt likidite sweep":
        long_score += 20
    elif sweep == "Üst likidite sweep":
        short_score += 20

    if mss == "Bullish MSS":
        long_score += 20
    elif mss == "Bearish MSS":
        short_score += 20

    if choch == "Bullish CHoCH":
        long_score += 15
    elif choch == "Bearish CHoCH":
        short_score += 15

    if fvg == "Bullish FVG":
        long_score += 10
    elif fvg == "Bearish FVG":
        short_score += 10

    if displacement == "Güçlü":
        long_score += 10
        short_score += 10
    elif displacement == "Orta":
        long_score += 5
        short_score += 5

    if premium_discount == "Discount":
        long_score += 5
    elif premium_discount == "Premium":
        short_score += 5

    if long_score > short_score:
        direction = "LONG"
        score = long_score
    elif short_score > long_score:
        direction = "SHORT"
        score = short_score
    else:
        direction = "YOK"
        score = max(long_score, short_score)

    quality = "Yok"
    if score >= 85:
        quality = "A+"
    elif score >= 75:
        quality = "A"
    elif score >= 65:
        quality = "B"

    return {
        "direction": direction,
        "score": score,
        "quality": quality,
        "long_score": long_score,
        "short_score": short_score,
    }


def build_trade_levels(candles: List[Dict[str, Any]], direction: str) -> Dict[str, Optional[float]]:
    if len(candles) < 10 or direction not in ("LONG", "SHORT"):
        return {"entry": None, "sl": None, "tp": None}

    last = candles[-1]
    recent = candles[-10:]

    recent_high = max(x["high"] for x in recent)
    recent_low = min(x["low"] for x in recent)
    price = last["close"]

    if direction == "LONG":
        sl = recent_low
        risk = price - sl
        if risk <= 0:
            return {"entry": price, "sl": None, "tp": None}
        tp = price + risk * 2
        return {"entry": price, "sl": sl, "tp": tp}

    sl = recent_high
    risk = sl - price
    if risk <= 0:
        return {"entry": price, "sl": None, "tp": None}
    tp = price - risk * 2
    return {"entry": price, "sl": sl, "tp": tp}


def analyze_symbol(symbol: str) -> Optional[Dict[str, Any]]:
    raw = fetch_twelvedata_series(symbol)
    if not raw:
        return None

    candles = build_candles(raw)
    if len(candles) < 20:
        print(f"{symbol} yeterli candle yok.")
        return None

    last_price = candles[-1]["close"]
    bias = detect_bias(candles)
    sweep = detect_liquidity_sweep(candles)
    mss = detect_mss_like(candles)
    choch = detect_choch_like(candles)
    fvg = detect_fvg(candles)
    displacement = detect_displacement(candles)
    premium_discount = detect_premium_discount(candles)

    score_data = score_signal(
        bias=bias,
        sweep=sweep,
        mss=mss,
        choch=choch,
        fvg=fvg,
        displacement=displacement,
        premium_discount=premium_discount,
    )

    levels = build_trade_levels(candles, score_data["direction"])

    return {
        "symbol": symbol,
        "price": last_price,
        "htf_bias": bias,
        "liquidity_sweep": sweep,
        "mss": mss,
        "choch": choch,
        "fvg": fvg,
        "premium_discount": premium_discount,
        "displacement": displacement,
        "direction": score_data["direction"],
        "entry": levels["entry"],
        "sl": levels["sl"],
        "tp": levels["tp"],
        "score": score_data["score"],
        "quality": score_data["quality"],
        "long_score": score_data["long_score"],
        "short_score": score_data["short_score"],
        "last_candle_time": candles[-1]["datetime"],
    }

def format_signal_message(result: Dict[str, Any]) -> str:
    entry = f"{result['entry']:.5f}" if result["entry"] is not None else "Yok"
    sl = f"{result['sl']:.5f}" if result["sl"] is not None else "Yok"
    tp = f"{result['tp']:.5f}" if result["tp"] is not None else "Yok"

    return (
        "🚨 ANALİZ + SİNYAL RAPORU\n\n"
        f"Varlık: {result['symbol']}\n"
        f"Anlık Fiyat: {result['price']:.5f}\n\n"
        f"HTF Bias: {result['htf_bias']}\n"
        f"Likidite Sweep: {result['liquidity_sweep']}\n"
        f"MSS: {result['mss']}\n"
        f"CHoCH: {result['choch']}\n"
        f"FVG: {result['fvg']}\n"
        f"Premium/Discount: {result['premium_discount']}\n"
        f"Displacement: {result['displacement']}\n\n"
        f"İşlem Yönü: {result['direction']}\n"
        f"Giriş: {entry}\n"
        f"Zarar Durdur: {sl}\n"
        f"Kar Al: {tp}\n"
        f"Güven Skoru: {result['score']}/100\n"
        f"Sinyal Kalitesi: {result['quality']}\n"
        f"Long/Short Skor: {result['long_score']} / {result['short_score']}\n\n"
        f"Son Mum Zamanı: {result['last_candle_time']}"
    )


def should_send_signal(result: Dict[str, Any]) -> bool:
    symbol = result["symbol"]
    direction = result["direction"]
    score = result["score"]
    quality = result["quality"]

    if direction == "YOK":
        return False

    if score < MIN_SIGNAL_SCORE:
        return False

    if quality not in ("A", "A+"):
        return False

    active = STATE["active_signals"].get(symbol)
    if not active:
        return True

    same_direction = active.get("direction") == direction
    cooldown_ok = minutes_since(active.get("sent_at")) >= SIGNAL_COOLDOWN_MINUTES

    if same_direction and not cooldown_ok:
        return False

    return True


# =========================================================
# SCANNER
# =========================================================
def scan_once() -> Dict[str, Any]:
    with scan_lock:
        print("Tarama başladı...")
        STATE["last_scan_at"] = now_utc_iso()

        results: Dict[str, Any] = {}

        for symbol in MARKETS:
            try:
                result = analyze_symbol(symbol)

                if not result:
                    results[symbol] = {"ok": False, "error": "analysis_failed"}
                    print(f"{symbol} analiz üretilemedi.")
                    continue

                results[symbol] = {"ok": True, "result": result}
                STATE["last_results"][symbol] = result

                print(f"{symbol} -> yön: {result['direction']}, skor: {result['score']}")

                if should_send_signal(result):
                    msg = format_signal_message(result)
                    sent = send_telegram_message(msg)

                    if sent:
                        STATE["active_signals"][symbol] = {
                            "direction": result["direction"],
                            "score": result["score"],
                            "sent_at": now_utc_iso(),
                            "entry": result["entry"],
                            "sl": result["sl"],
                            "tp": result["tp"],
                        }
                        print(f"{symbol} sinyal gönderildi.")
                    else:
                        print(f"{symbol} sinyal gönderilemedi.")
                else:
                    print(f"{symbol} için sinyal yok.")

            except Exception as e:
                results[symbol] = {"ok": False, "error": str(e)}
                print(f"{symbol} scan hatası: {e}")

        print("Tarama bitti.")
        return results


def scanner_loop() -> None:
    # Uygulama ayağa kalktıktan sonra küçük gecikme
    time.sleep(5)

    while True:
        try:
            scan_once()
        except Exception as e:
            print(f"scanner_loop hata: {e}")

        time.sleep(SCAN_INTERVAL_SECONDS)


def start_background_scanner() -> None:
    global scanner_started_once

    if scanner_started_once:
        return

    scanner_started_once = True
    STATE["scanner_started"] = True

    t = threading.Thread(target=scanner_loop, daemon=True)
    t.start()
    print("Background scanner başlatıldı.")


# =========================================================
# ROUTES
# =========================================================
@app.route("/")
def home():
    return jsonify({
        "ok": True,
        "service": "trade-konseyi",
        "message": "Service is running",
        "scanner_started": STATE.get("scanner_started"),
        "last_scan_at": STATE.get("last_scan_at"),
        "markets": MARKETS,
    })


@app.route("/health")
def health():
    return jsonify({
        "ok": True,
        "scanner_started": STATE.get("scanner_started"),
        "last_scan_at": STATE.get("last_scan_at"),
        "watched_markets": len(MARKETS),
        "env": {
            "TWELVEDATA_API_KEY": bool(TWELVEDATA_API_KEY),
            "TELEGRAM_BOT_TOKEN": bool(TELEGRAM_BOT_TOKEN),
            "TELEGRAM_CHAT_ID": bool(TELEGRAM_CHAT_ID),
        }
    })


@app.route("/analyze/<path:symbol>")
def analyze_route(symbol: str):
    symbol = symbol.strip()
    result = analyze_symbol(symbol)

    if not result:
        return jsonify({
            "ok": False,
            "error": "analysis_failed",
            "symbol": symbol,
        }), 400

    return jsonify({
        "ok": True,
        "result": result,
    })


@app.route("/status")
def status():
    return jsonify({
        "ok": True,
        "service_started_at": STATE.get("service_started_at"),
        "scanner_started": STATE.get("scanner_started"),
        "last_scan_at": STATE.get("last_scan_at"),
        "markets": MARKETS,
        "last_results": STATE.get("last_results", {}),
        "active_signals": STATE.get("active_signals", {}),
    })


# =========================================================
# STARTUP
# =========================================================
print(f"TWELVEDATA_API_KEY dolu mu: {'evet' if bool(TWELVEDATA_API_KEY) else 'hayır'}")
print(f"TELEGRAM_BOT_TOKEN dolu mu: {'evet' if bool(TELEGRAM_BOT_TOKEN) else 'hayır'}")
print(f"TELEGRAM_CHAT_ID dolu mu: {'evet' if bool(TELEGRAM_CHAT_ID) else 'hayır'}")

start_background_scanner()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=False)