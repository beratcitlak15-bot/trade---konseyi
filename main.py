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
SCAN_INTERVAL_SECONDS = 600  # 10 dakika
TIMEFRAME = "15min"
OUTPUTSIZE = 60

# Sadece A ve A+ gelsin
MIN_SIGNAL_SCORE = 75

# Aynı yönde sürekli spam atmasın
SIGNAL_COOLDOWN_MINUTES = 45

# Scanner durmuş sayılmadan önce heartbeat süresi
SCANNER_HEARTBEAT_STALE_MINUTES = 20
SCANNER_MONITOR_INTERVAL_SECONDS = 30

# İzlenen pariteler
MARKETS = [
    "EUR/USD",
    "GBP/USD",
    "XAU/USD",
    "USD/JPY",
    "AUD/USD",
]

# =========================================================
# GLOBAL STATE
# =========================================================
STATE: Dict[str, Any] = {
    "service_started_at": datetime.now(timezone.utc).isoformat(),
    "last_scan_at": None,
    "last_results": {},
    "active_signals": {},
    "scanner_started": False,
    "scanner_last_heartbeat": None,
    "scanner_restart_count": 0,
    "last_error": None,
}

scan_lock = threading.Lock()
scanner_state_lock = threading.Lock()
monitor_state_lock = threading.Lock()

scanner_thread: Optional[threading.Thread] = None
monitor_thread: Optional[threading.Thread] = None

http = requests.Session()

# =========================================================
# UTILS
# =========================================================
def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_utc_iso() -> str:
    return now_utc().isoformat()


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
        return (now_utc() - dt).total_seconds() / 60.0
    except Exception:
        return 999999.0


def mark_scanner_heartbeat() -> None:
    STATE["scanner_last_heartbeat"] = now_utc_iso()


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
        response = http.post(url, json=payload, timeout=20)
        print(f"Telegram status: {response.status_code}")
        if response.status_code != 200:
            print(f"Telegram response: {response.text}")
            return False
        return True
    except Exception as e:
        print(f"Telegram gönderim hatası: {e}")
        return False


def get_killzone_label() -> str:
    """
    Basit Türkiye saati bazlı killzone etiketi.
    Killzone zorunlu değildir, bonus puan verir.
    """
    hour = datetime.now().hour

    if 10 <= hour <= 12:
        return "London Killzone"
    if 15 <= hour <= 17:
        return "New York Killzone"
    return "Killzone Dışı"


def is_killzone_active() -> bool:
    return get_killzone_label() != "Killzone Dışı"


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
        response = http.get(url, params=params, timeout=25)
        data = response.json()

        if response.status_code != 200:
            print(f"{symbol} HTTP hata: {response.status_code} - {response.text}")
            return None

        if data.get("status") == "error":
            print(f"{symbol} API hata: {data}")
            return None

        if "values" not in data or not data["values"]:
            print(f"{symbol} veri boş.")
            return None

        return data

    except Exception as e:
        print(f"{symbol} fetch hatası: {e}")
        return None


# =========================================================
# ANALYSIS HELPERS
# =========================================================
def build_candles(raw: Dict[str, Any]) -> List[Dict[str, Any]]:
    candles: List[Dict[str, Any]] = []

    for row in raw.get("values", []):
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

    # TwelveData genelde en yeni mumu üstte döndürür
    candles.reverse()
    return candles


def detect_bias(candles: List[Dict[str, Any]]) -> str:
    if len(candles) < 20:
        return "Nötr"

    closes = [x["close"] for x in candles[-20:]]
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

    bodies = [abs(x["close"] - x["open"]) for x in candles[-10:-1]]
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


def detect_true_order_block(candles: List[Dict[str, Any]], direction: str) -> str:
    if len(candles) < 8:
        return "Yok"

    lookback = candles[-8:-1]

    if direction == "LONG":
        for candle in reversed(lookback):
            if candle["close"] < candle["open"]:
                return f"Bullish OB adayı ({candle['low']:.5f} - {candle['high']:.5f})"

    elif direction == "SHORT":
        for candle in reversed(lookback):
            if candle["close"] > candle["open"]:
                return f"Bearish OB adayı ({candle['low']:.5f} - {candle['high']:.5f})"

    

def get_recent_swing_high(candles: List[Dict[str, Any]], lookback: int = 6) -> Optional[float]:
    if len(candles) < lookback:
        return None
    return max(x["high"] for x in candles[-lookback:])


def get_recent_swing_low(candles: List[Dict[str, Any]], lookback: int = 6) -> Optional[float]:
    if len(candles) < lookback:
        return None
    return min(x["low"] for x in candles[-lookback:])


def detect_pair_smt(
    candles_a: List[Dict[str, Any]],
    candles_b: List[Dict[str, Any]],
    lookback: int = 6,
) -> str:
    """
    Basit SMT mantığı:
    - A yeni high yapıyor, B yapmıyorsa -> Bearish SMT
    - A yeni low yapıyor, B yapmıyorsa -> Bullish SMT
    """
    if len(candles_a) < lookback + 1 or len(candles_b) < lookback + 1:
        return "Yok"

    prev_high_a = max(x["high"] for x in candles_a[-(lookback + 1):-1])
    prev_low_a = min(x["low"] for x in candles_a[-(lookback + 1):-1])

    prev_high_b = max(x["high"] for x in candles_b[-(lookback + 1):-1])
    prev_low_b = min(x["low"] for x in candles_b[-(lookback + 1):-1])

    last_a = candles_a[-1]
    last_b = candles_b[-1]

    a_makes_higher_high = last_a["high"] > prev_high_a
    b_makes_higher_high = last_b["high"] > prev_high_b

    a_makes_lower_low = last_a["low"] < prev_low_a
    b_makes_lower_low = last_b["low"] < prev_low_b

    if a_makes_higher_high and not b_makes_higher_high:
        return "Bearish SMT"

    if a_makes_lower_low and not b_makes_lower_low:
        return "Bullish SMT"

    return "Yok"


def detect_smt_for_symbol(symbol: str, market_candles: Dict[str, List[Dict[str, Any]]]) -> str:
    """
    Öncelikli korelasyonlar:
    EUR/USD <-> GBP/USD
    EUR/USD <-> AUD/USD
    GBP/USD <-> AUD/USD
    """
    if symbol not in market_candles:
        return "Yok"

    pairs_map = {
        "EUR/USD": ["GBP/USD", "AUD/USD"],
        "GBP/USD": ["EUR/USD", "AUD/USD"],
        "AUD/USD": ["EUR/USD", "GBP/USD"],
    }

    peers = pairs_map.get(symbol, [])
    if not peers:
        return "Yok"

    base_candles = market_candles.get(symbol)
    if not base_candles:
        return "Yok"

    for peer in peers:
        peer_candles = market_candles.get(peer)
        if not peer_candles:
            continue

        smt = detect_smt_for_symbol(symbol, market_candles or {})
        if smt != "Yok":
            return smt

    return "Yok"


# =========================================================
# CORE SIGNAL LOGIC
# =========================================================
def has_required_long_conditions(
    sweep: str,
    mss: str,
    choch: str,
    displacement: str,
) -> bool:
    return (
        sweep == "Alt likidite sweep"
        and (mss == "Bullish MSS" or choch == "Bullish CHoCH")
        and displacement in ("Orta", "Güçlü")
    )


def has_required_short_conditions(
    sweep: str,
    mss: str,
    choch: str,
    displacement: str,
) -> bool:
    return (
        sweep == "Üst likidite sweep"
        and (mss == "Bearish MSS" or choch == "Bearish CHoCH")
        and displacement in ("Orta", "Güçlü")
    )


def score_signal(
    direction: str,
    bias: str,
    fvg: str,
    premium_discount: str,
    smt: str,
    killzone_active: bool,
    true_order_block: str,
) -> Dict[str, Any]:
    # Zorunlular tamamlandıysa taban puan
    score = 40
    quality = "Yok"

    if direction == "LONG":
        if bias == "Yükseliş":
            score += 20
        if fvg == "Bullish FVG":
            score += 15
        if premium_discount == "Discount":
            score += 15

    elif direction == "SHORT":
        if bias == "Düşüş":
            score += 20
        if fvg == "Bearish FVG":
            score += 15
        if premium_discount == "Premium":
            score += 15

    # Bonuslar
    if killzone_active:
        score += 10
    if smt != "Yok":
        score += 10
    if true_order_block != "Yok":
        score += 10

    if score >= 90:
        quality = "A+"
    elif score >= 75:
        quality = "A"
    elif score >= 65:
        quality = "B"

    return {
        "score": score,
        "quality": quality,
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


def analyze_symbol(
    symbol: str,
    raw_data_map: Optional[Dict[str, Dict[str, Any]]] = None,
    market_candles: Optional[Dict[str, List[Dict[str, Any]]]] = None,
) -> Optional[Dict[str, Any]]:
    raw = None

    if raw_data_map and symbol in raw_data_map:
        raw = raw_data_map[symbol]
    else:
        raw = fetch_twelvedata_series(symbol)

    if not raw:
        return None

    if market_candles and symbol in market_candles:
        candles = market_candles[symbol]
    else:
        candles = build_candles(raw)

    if len(candles) < 20:
        print(f"{symbol} için yeterli mum yok.")
        return None

    last_price = candles[-1]["close"]

    bias = detect_bias(candles)
    sweep = detect_liquidity_sweep(candles)
    mss = detect_mss_like(candles)
    choch = detect_choch_like(candles)
    displacement = detect_displacement(candles)
    fvg = detect_fvg(candles)
    premium_discount = detect_premium_discount(candles)

    killzone_label = get_killzone_label()
    killzone_active = is_killzone_active()
    smt = detect_smt_for_symbol(symbol, market_candles or {})

    direction = "YOK"

    if has_required_long_conditions(sweep, mss, choch, displacement):
        direction = "LONG"
    elif has_required_short_conditions(sweep, mss, choch, displacement):
        direction = "SHORT"
    else:
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
            "smt": smt,
            "killzone": killzone_label,
            "true_order_block": "Yok",
            "direction": "YOK",
            "entry": None,
            "sl": None,
            "tp": None,
            "score": 0,
            "quality": "Yok",
            "long_score": 0,
            "short_score": 0,
            "last_candle_time": candles[-1]["datetime"],
            "reason": "Zorunlu filtreler sağlanmadı",
        }

    true_order_block = detect_true_order_block(candles, direction)
    levels = build_trade_levels(candles, direction)

    scored = score_signal(
        direction=direction,
        bias=bias,
        fvg=fvg,
        premium_discount=premium_discount,
        smt=smt,
        killzone_active=killzone_active,
        true_order_block=true_order_block,
    )

    long_score = scored["score"] if direction == "LONG" else 0
    short_score = scored["score"] if direction == "SHORT" else 0

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
        "smt": smt,
        "killzone": killzone_label,
        "true_order_block": true_order_block,
        "direction": direction,
        "entry": levels["entry"],
        "sl": levels["sl"],
        "tp": levels["tp"],
        "score": scored["score"],
        "quality": scored["quality"],
        "long_score": long_score,
        "short_score": short_score,
        "last_candle_time": candles[-1]["datetime"],
        "reason": "Zorunlu filtreler sağlandı",
    }


def format_signal_message(result: Dict[str, Any]) -> str:
    entry = f"{result['entry']:.5f}" if result["entry"] is not None else "Yok"
    sl = f"{result['sl']:.5f}" if result["sl"] is not None else "Yok"
    tp = f"{result['tp']:.5f}" if result["tp"] is not None else "Yok"

    return (
        "🚨 ANALİZ + SİNYAL RAPORU\n\n"
        f"Varlık: {result['symbol']}\n"
        f"Anlık Fiyat: {result['price']:.5f}\n"
        f"Killzone: {result['killzone']}\n\n"
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
        f"Giriş: {entry}\n"
        f"Zarar Durdur: {sl}\n"
        f"Kar Al: {tp}\n"
        f"Güven Skoru: {result['score']}/100\n"
        f"Sinyal Kalitesi: {result['quality']}\n"
        f"Long/Short Skor: {result['long_score']} / {result['short_score']}\n"
        f"Son Mum Zamanı: {result['last_candle_time']}\n"
        f"Not: {result.get('reason', 'Yok')}"
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
        mark_scanner_heartbeat()

        results: Dict[str, Any] = {}

        raw_data_map: Dict[str, Dict[str, Any]] = {}
        market_candles: Dict[str, List[Dict[str, Any]]] = {}

        for s in MARKETS:
            try:
                raw = fetch_twelvedata_series(s)
                if raw:
                    raw_data_map[s] = raw
                    market_candles[s] = build_candles(raw)
                else:
                    print(f"{s} için raw data alınamadı.")
            except Exception as e:
                print(f"{s} preload hatası: {e}")

        for symbol in MARKETS:
            try:
                result = analyze_symbol(
                    symbol,
                    raw_data_map=raw_data_map,
                    market_candles=market_candles,
                )

                if not result:
                    results[symbol] = {"ok": False, "error": "analysis_failed"}
                    print(f"{symbol} analiz üretilemedi.")
                    continue

                results[symbol] = {"ok": True, "result": result}
                STATE["last_results"][symbol] = result

                print(
                    f"{symbol} -> yön: {result['direction']}, "
                    f"skor: {result['score']}, kalite: {result['quality']}"
                )

                if should_send_signal(result):
                    msg = format_signal_message(result)
                    sent = send_telegram_message(msg)

                    if sent:
                        STATE["active_signals"][symbol] = {
                            "direction": result["direction"],
                            "score": result["score"],
                            "quality": result["quality"],
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

                mark_scanner_heartbeat()

            except Exception as e:
                results[symbol] = {"ok": False, "error": str(e)}
                STATE["last_error"] = str(e)
                print(f"{symbol} scan hatası: {e}")

        print("Tarama bitti.")
        mark_scanner_heartbeat()
        return results


def scanner_loop() -> None:
    time.sleep(5)

    while True:
        try:
            mark_scanner_heartbeat()
            scan_once()
        except Exception as e:
            STATE["last_error"] = str(e)
            print(f"scanner_loop hata: {e}")

        mark_scanner_heartbeat()
        time.sleep(SCAN_INTERVAL_SECONDS)


# =========================================================
# SCANNER CONTROL
# =========================================================
def start_background_scanner() -> None:
    global scanner_thread

    with scanner_state_lock:
        if scanner_thread and scanner_thread.is_alive():
            STATE["scanner_started"] = True
            return

        scanner_thread = threading.Thread(target=scanner_loop, daemon=True)
        scanner_thread.start()
        STATE["scanner_started"] = True
        STATE["scanner_restart_count"] += 1
        mark_scanner_heartbeat()
        print("Background scanner başlatıldı.")


def ensure_scanner_running() -> None:
    global scanner_thread

    need_restart = False

    with scanner_state_lock:
        if scanner_thread is None or not scanner_thread.is_alive():
            need_restart = True

    if need_restart:
        print("Scanner durmuş veya başlamamış. Yeniden başlatılıyor...")
        start_background_scanner()
        return

    heartbeat_age = minutes_since(STATE.get("scanner_last_heartbeat"))
    if heartbeat_age > SCANNER_HEARTBEAT_STALE_MINUTES:
        print("Scanner heartbeat çok eski. Yeniden başlatılıyor...")
        start_background_scanner()


def monitor_loop() -> None:
    time.sleep(3)

    while True:
        try:
            ensure_scanner_running()
        except Exception as e:
            STATE["last_error"] = str(e)
            print(f"monitor_loop hata: {e}")

        time.sleep(SCANNER_MONITOR_INTERVAL_SECONDS)


def start_monitor_once() -> None:
    global monitor_thread

    with monitor_state_lock:
        if monitor_thread and monitor_thread.is_alive():
            return

        monitor_thread = threading.Thread(target=monitor_loop, daemon=True)
        monitor_thread.start()
        print("Scanner monitor başlatıldı.")


# =========================================================
# ROUTES
# =========================================================
@app.route("/")
def home():
    ensure_scanner_running()
    return jsonify(
        {
            "ok": True,
            "service": "trade-konseyi",
            "message": "Service is running",
            "scanner_started": STATE.get("scanner_started"),
            "scanner_restart_count": STATE.get("scanner_restart_count"),
            "scanner_last_heartbeat": STATE.get("scanner_last_heartbeat"),
            "last_scan_at": STATE.get("last_scan_at"),
            "markets": MARKETS,
        }
    )


@app.route("/health")
def health():
    ensure_scanner_running()
    return jsonify(
        {
            "ok": True,
            "scanner_started": STATE.get("scanner_started"),
            "scanner_restart_count": STATE.get("scanner_restart_count"),
            "scanner_last_heartbeat": STATE.get("scanner_last_heartbeat"),
            "last_scan_at": STATE.get("last_scan_at"),
            "watched_markets": len(MARKETS),
            "last_error": STATE.get("last_error"),
            "env": {
                "TWELVEDATA_API_KEY": bool(TWELVEDATA_API_KEY),
                "TELEGRAM_BOT_TOKEN": bool(TELEGRAM_BOT_TOKEN),
                "TELEGRAM_CHAT_ID": bool(TELEGRAM_CHAT_ID),
            },
        }
    )


@app.route("/analyze/<path:symbol>")
def analyze_route(symbol: str):
    ensure_scanner_running()
    symbol = symbol.strip()
    result = analyze_symbol(symbol)

    if not result:
        return jsonify(
            {
                "ok": False,
                "error": "analysis_failed",
                "symbol": symbol,
            }
        ), 400

    return jsonify(
        {
            "ok": True,
            "result": result,
        }
    )


@app.route("/status")
def status():
    ensure_scanner_running()
    return jsonify(
        {
            "ok": True,
            "service_started_at": STATE.get("service_started_at"),
            "scanner_started": STATE.get("scanner_started"),
            "scanner_restart_count": STATE.get("scanner_restart_count"),
            "scanner_last_heartbeat": STATE.get("scanner_last_heartbeat"),
            "last_scan_at": STATE.get("last_scan_at"),
            "last_error": STATE.get("last_error"),
            "markets": MARKETS,
            "last_results": STATE.get("last_results", {}),
            "active_signals": STATE.get("active_signals", {}),
        }
    )


# =========================================================
# STARTUP
# =========================================================
print(f"TWELVEDATA_API_KEY dolu mu: {'evet' if bool(TWELVEDATA_API_KEY) else 'hayır'}")
print(f"TELEGRAM_BOT_TOKEN dolu mu: {'evet' if bool(TELEGRAM_BOT_TOKEN) else 'hayır'}")
print(f"TELEGRAM_CHAT_ID dolu mu: {'evet' if bool(TELEGRAM_CHAT_ID) else 'hayır'}")

start_monitor_once()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=False)
