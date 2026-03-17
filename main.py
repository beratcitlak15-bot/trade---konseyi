import json
import os
import threading
import time
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
SCAN_INTERVAL_SECONDS = 60
TIMEFRAME = "15min"
OUTPUTSIZE = 120
SIGNAL_COOLDOWN_MINUTES = 45
MIN_SIGNAL_SCORE = 75
STATE_FILE = "state.json"

MARKETS = [
    "EUR/USD",
    "GBP/USD",
    "XAU/USD",
    "USD/JPY",
    "AUD/USD",
    "DX",
]

# =========================================================
# GLOBALS
# =========================================================
STATE: Dict[str, Any] = {
    "active_signals": {},
    "last_scan_at": None,
    "service_started_at": datetime.now(timezone.utc).isoformat(),
}

scan_lock = threading.Lock()
http = requests.Session()

# =========================================================
# BASIC HELPERS
# =========================================================
def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_utc_iso() -> str:
    return now_utc().isoformat()


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")


def safe_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def minutes_since(iso_dt: Optional[str]) -> float:
    if not iso_dt:
        return 999999.0

    try:
        dt = datetime.fromisoformat(iso_dt)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (now_utc() - dt).total_seconds() / 60.0
    except Exception:
        return 999999.0


def get_current_session() -> str:
    """
    Basit session ismi.
    UTC bazlı yaklaşık dağılım.
    """
    hour = now_utc().hour

    if 6 <= hour < 10:
        return "Asya Kapanış / Londra Açılış"
    if 10 <= hour < 14:
        return "Londra"
    if 14 <= hour < 17:
        return "Londra Killzone"
    if 17 <= hour < 21:
        return "New York"
    return "Session Dışı"


def is_killzone() -> bool:
    """
    Basit killzone filtresi.
    UTC bazlı yaklaşık pencere.
    """
    hour = now_utc().hour
    return hour in (14, 15, 16)


# =========================================================
# STATE
# =========================================================
def load_state() -> None:
    global STATE

    if not os.path.exists(STATE_FILE):
        return

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, dict):
            STATE.update(data)
            log("STATE yüklendi.")
    except Exception as e:
        log(f"STATE load hatası: {e}")


def save_state() -> None:
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(STATE, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log(f"STATE save hatası: {e}")


# =========================================================
# TELEGRAM
# =========================================================
def send_telegram_message(text: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log("Telegram env eksik, mesaj gönderilmedi.")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
    }

    try:
        response = http.post(url, json=payload, timeout=20)
        if response.status_code == 200:
            return True

        log(f"Telegram hata: {response.status_code} - {response.text}")
        return False
    except requests.RequestException as e:
        log(f"Telegram gönderim hatası: {e}")
        return False


# =========================================================
# TWELVEDATA
# =========================================================
def fetch_twelvedata_series(
    symbol: str,
    interval: str = TIMEFRAME,
    outputsize: int = OUTPUTSIZE,
) -> Optional[Dict[str, Any]]:
    if not TWELVEDATA_API_KEY:
        log("TWELVEDATA_API_KEY yok.")
        return None

    url = "https://api.twelvedata.com/time_series"
    params = {
        "apikey": TWELVEDATA_API_KEY,
        "symbol": symbol,
        "interval": interval,
        "outputsize": outputsize,
        "format": "JSON",
    }

    try:
        response = http.get(url, params=params, timeout=25)
        data = response.json()

        if response.status_code != 200:
            log(f"{symbol} HTTP hata: {response.status_code} - {response.text}")
            return None

        if data.get("status") == "error":
            log(f"{symbol} API hata: {data}")
            return None

        if not data.get("values"):
            log(f"{symbol} veri boş geldi.")
            return None

        return data

    except requests.RequestException as e:
        log(f"{symbol} bağlantı hatası: {e}")
        return None
    except ValueError as e:
        log(f"{symbol} JSON parse hatası: {e}")
        return None
    except Exception as e:
        log(f"{symbol} fetch genel hata: {e}")
        return None


# =========================================================
# CANDLE PREP
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

        candles.append(
            {
                "datetime": row.get("datetime"),
                "open": o,
                "high": h,
                "low": l,
                "close": c,
            }
        )

    # TwelveData çoğunlukla yeni -> eski döndürür.
    candles.reverse()
    return candles


# =========================================================
# ANALYSIS HELPERS
# =========================================================
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


def detect_true_order_block(candles: List[Dict[str, Any]]) -> str:
    if len(candles) < 6:
        return "Yok"

    last = candles[-1]
    prev = candles[-2]

    if last["close"] > prev["high"]:
        for candle in reversed(candles[-6:-1]):
            if candle["close"] < candle["open"]:
                return f"Bearish OB ({candle['low']:.5f} - {candle['high']:.5f})"

    if last["close"] < prev["low"]:
        for candle in reversed(candles[-6:-1]):
            if candle["close"] > candle["open"]:
                return f"Bullish OB ({candle['low']:.5f} - {candle['high']:.5f})"

    return "Yok"


def detect_smt_placeholder(symbol: str) -> str:
    # İleride cross-market SMT eklenebilir.
    _ = symbol
    return "Yok"


# =========================================================
# SCORING
# =========================================================
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

    if score >= 85:
        quality = "A+"
    elif score >= 75:
        quality = "A"
    elif score >= 65:
        quality = "B"
    else:
        quality = "Yok"

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

    last_close = candles[-1]["close"]
    recent = candles[-10:]
    recent_high = max(x["high"] for x in recent)
    recent_low = min(x["low"] for x in recent)

    if direction == "LONG":
        sl = recent_low
        risk = last_close - sl
        if risk <= 0:
            return {"entry": last_close, "sl": None, "tp": None}
        tp = last_close + (risk * 2)
        return {"entry": last_close, "sl": sl, "tp": tp}

    sl = recent_high
    risk = sl - last_close
    if risk <= 0:
        return {"entry": last_close, "sl": None, "tp": None}
    tp = last_close - (risk * 2)
    return {"entry": last_close, "sl": sl, "tp": tp}


# =========================================================
# MAIN ANALYSIS
# =========================================================
def analyze_symbol(symbol: str) -> Optional[Dict[str, Any]]:
    raw = fetch_twelvedata_series(symbol)
    if not raw:
        return None

    candles = build_candles(raw)
    if len(candles) < 20:
        log(f"{symbol} yeterli candle yok.")
        return None

    last_price = candles[-1]["close"]

    bias = detect_bias(candles)
    sweep = detect_liquidity_sweep(candles)
    mss = detect_mss_like(candles)
    choch = detect_choch_like(candles)
    fvg = detect_fvg(candles)
    displacement = detect_displacement(candles)
    premium_discount = detect_premium_discount(candles)
    smt = detect_smt_placeholder(symbol)
    true_order_block = detect_true_order_block(candles)

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
        "session": get_current_session(),
        "killzone": "Evet" if is_killzone() else "Hayır",
        "price": last_price,
        "htf_bias": bias,
        "liquidity_sweep": sweep,
        "mss": mss,
        "choch": choch,
        "fvg": fvg,
        "premium_discount": premium_discount,
        "displacement": displacement,
        "smt": smt,
        "true_order_block": true_order_block,
        "direction": score_data["direction"],
        "entry": levels["entry"],
        "sl": levels["sl"],
        "tp": levels["tp"],
        "score": score_data["score"],
        "quality": score_data["quality"],
        "long_score": score_data["long_score"],
        "short_score": score_data["short_score"],
        "raw_status": raw.get("status", "unknown"),
        "last_candle_time": candles[-1]["datetime"],
    }


# =========================================================
# SIGNAL RULES
# =========================================================
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

    active = STATE.get("active_signals", {}).get(symbol)
    if not active:
        return True

    same_direction = active.get("direction") == direction
    cooldown_ok = minutes_since(active.get("sent_at")) >= SIGNAL_COOLDOWN_MINUTES

    if same_direction and not cooldown_ok:
        return False

    return True


def format_price(value: Optional[float]) -> str:
    if value is None:
        return "Yok"
    return f"{value:.5f}"


def format_signal_message(result: Dict[str, Any]) -> str:
    return (
        "🚨 ANALİZ + SİNYAL RAPORU\n\n"
        f"Varlık: {result['symbol']}\n"
        f"Seans: {result['session']}\n"
        f"Killzone: {result['killzone']}\n"
        f"Anlık Fiyat: {result['price']:.5f}\n\n"
        f"HTF Bias: {result['htf_bias']}\n"
        f"Likidite Sweep: {result['liquidity_sweep']}\n"
        f"MSS: {result['mss']}\n"
        f"CHoCH: {result['choch']}\n"
        f"FVG: {result['fvg']}\n"
        f"Premium/Discount: {result['premium_discount']}\n"
        f"Displacement: {result['displacement']}\n"
        f"SMT: {result['smt']}\n"
        f"True Order Block: {result['true_order_block']}\n\n"
        f"İşlem Yönü: {result['direction']}\n"
        f"Giriş: {format_price(result['entry'])}\n"
        f"Zarar Durdur: {format_price(result['sl'])}\n"
        f"Kar Al: {format_price(result['tp'])}\n"
        f"Güven Skoru: {result['score']}/100\n"
        f"Sinyal Kalitesi: {result['quality']}\n"
        f"Long/Short Skor: {result['long_score']} / {result['short_score']}\n\n"
        f"Son Mum Zamanı: {result['last_candle_time']}\n"
        "⚠️ Not: Bu sinyal otomatik tarama ile üretildi."
    )


# =========================================================
# SCANNER
# =========================================================
def scan_once() -> None:
    with scan_lock:
        log("Tarama başladı...")
        STATE["last_scan_at"] = now_utc_iso()

        for symbol in MARKETS:
            try:
                result = analyze_symbol(symbol)

                if not result:
                    log(f"{symbol} analiz üretilemedi.")
                    continue

                log(f"{symbol} -> yön: {result['direction']}, skor: {result['score']}")

                if should_send_signal(result):
                    message = format_signal_message(result)
                    sent = send_telegram_message(message)

                    if sent:
                        STATE["active_signals"][symbol] = {
                            "direction": result["direction"],
                            "score": result["score"],
                            "sent_at": now_utc_iso(),
                            "entry": result["entry"],
                            "sl": result["sl"],
                            "tp": result["tp"],
                        }
                        save_state()
                        log(f"{symbol} sinyal gönderildi.")
                    else:
                        log(f"{symbol} Telegram gönderimi başarısız.")
                else:
                    log(f"{symbol} için sinyal yok.")

            except Exception as e:
                log(f"{symbol} scan hatası: {e}")

        save_state()
        log("Tarama bitti.")


def scanner_loop() -> None:
    while True:
        try:
            scan_once()
        except Exception as e:
            log(f"scanner_loop genel hata: {e}")

        time.sleep(SCAN_INTERVAL_SECONDS)


# =========================================================
# FLASK ROUTES
# =========================================================
@app.route("/")
def home():
    return jsonify(
        {
            "ok": True,
            "service": "trade-konseyi",
            "message": "Service is running",
            "last_scan_at": STATE.get("last_scan_at"),
            "markets": MARKETS,
        }
    )


@app.route("/health")
def health():
    return jsonify(
        {
            "ok": True,
            "active_signals": STATE.get("active_signals", {}),
            "last_scan_at": STATE.get("last_scan_at"),
            "watched_markets": len(MARKETS),
        }
    )


@app.route("/scan-now")
def scan_now():
    try:
        scan_once()
        return jsonify({"ok": True, "message": "Manual scan completed"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/analyze/<path:symbol>")
def analyze_route(symbol: str):
    symbol = symbol.strip()
    result = analyze_symbol(symbol)

    if not result:
        return jsonify({"ok": False, "error": "analysis failed"}), 400

    return jsonify({"ok": True, "result": result})


# =========================================================
# STARTUP
# =========================================================
def validate_env() -> None:
    log(f"TWELVEDATA_API_KEY dolu mu: {'evet' if bool(TWELVEDATA_API_KEY) else 'hayır'}")
    log(f"TELEGRAM_BOT_TOKEN dolu mu: {'evet' if bool(TELEGRAM_BOT_TOKEN) else 'hayır'}")
    log(f"TELEGRAM_CHAT_ID dolu mu: {'evet' if bool(TELEGRAM_CHAT_ID) else 'hayır'}")


if __name__ == "__main__":
    load_state()
    validate_env()

    scanner_thread = threading.Thread(target=scanner_loop, daemon=True)
    scanner_thread.start()

    app.run(host="0.0.0.0", port=PORT, debug=False)