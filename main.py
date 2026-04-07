import json
import os
import time
from datetime import datetime, UTC
from typing import Any, Dict, List, Optional, Tuple

import requests

# =========================================================
# ENV
# =========================================================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
TWELVEDATA_API_KEY = os.getenv("TWELVEDATA_API_KEY", "").strip()

# =========================================================
# SETTINGS
# =========================================================
MIN_SIGNAL_SCORE = 75
CACHE_DIR = ".mtf_cache"

# SADECE TWELVEDATA'DAN ÇEKİLECEK PARİTELER
FOREX_METALS = [
    {"name": "EUR/USD", "symbol": "EUR/USD", "group": "forex"},
    {"name": "GBP/USD", "symbol": "GBP/USD", "group": "forex"},
    {"name": "XAU/USD", "symbol": "XAU/USD", "group": "metal"},
    {"name": "USD/JPY", "symbol": "USD/JPY", "group": "forex"},
    {"name": "AUD/USD", "symbol": "AUD/USD", "group": "forex"},
    {"name": "USD/CHF", "symbol": "USD/CHF", "group": "forex"},
    {"name": "USD/CAD", "symbol": "USD/CAD", "group": "forex"},
    {"name": "EUR/AUD", "symbol": "EUR/AUD", "group": "forex"},
]

# Çoklu timeframe veri politikası
TF_RULES = {
    "5min": {"ttl": 300, "outputsize": 240},
    "15min": {"ttl": 300, "outputsize": 240},
    "1h": {"ttl": 1800, "outputsize": 240},
    "4h": {"ttl": 3600, "outputsize": 240},
    "1week": {"ttl": 86400, "outputsize": 240},
}

# SMT ilişkileri
# positive = aynı yönde korelasyon beklenir
# inverse  = ters yönde korelasyon beklenir
SMT_RELATIONS: Dict[str, List[Tuple[str, str]]] = {
    "EUR/USD": [("GBP/USD", "positive"), ("USD/CHF", "inverse")],
    "GBP/USD": [("EUR/USD", "positive"), ("USD/CHF", "inverse")],
    "XAU/USD": [("EUR/USD", "positive"), ("GBP/USD", "positive"), ("USD/CHF", "inverse")],
    "USD/JPY": [("USD/CHF", "positive")],
    "AUD/USD": [("EUR/AUD", "inverse"), ("USD/CAD", "inverse")],
    "USD/CHF": [("USD/JPY", "positive"), ("EUR/USD", "inverse"), ("GBP/USD", "inverse"), ("XAU/USD", "inverse")],
    "USD/CAD": [("AUD/USD", "inverse")],
    "EUR/AUD": [("AUD/USD", "inverse")],
}

http = requests.Session()

# =========================================================
# UTILS
# =========================================================
def safe_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except Exception:
        return None


def now_utc() -> datetime:
    return datetime.now(UTC)


def now_ts() -> int:
    return int(time.time())


def now_str() -> str:
    return now_utc().strftime("%Y-%m-%d %H:%M:%S UTC")


def parse_candle_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None

    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=UTC)
        except Exception:
            pass

    return None


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


def get_killzone_label(dt: Optional[datetime] = None) -> str:
    x = dt or now_utc()
    hour = x.hour

    if 7 <= hour < 10:
        return "London Killzone"
    if 12 <= hour < 15:
        return "New York Killzone"
    return "Killzone Dışı"


def is_killzone_active(dt: Optional[datetime] = None) -> bool:
    return get_killzone_label(dt) != "Killzone Dışı"


# =========================================================
# MARKET OPEN FILTER
# =========================================================
def is_forex_market_open() -> bool:
    now = now_utc()
    wd = now.weekday()
    hour = now.hour

    if wd == 5:
        return False

    if wd == 4 and hour >= 21:
        return False

    if wd == 6 and hour < 21:
        return False

    return True


def is_weekend_market_closed(dt: Optional[datetime] = None) -> bool:
    x = dt or now_utc()
    day = x.weekday()
    hour = x.hour

    if day == 4 and hour >= 22:
        return True

    if day == 5:
        return True

    if day == 6 and hour < 22:
        return True

    return False


# =========================================================
# FILE / CACHE HELPERS
# =========================================================
def ensure_cache_dir() -> None:
    os.makedirs(CACHE_DIR, exist_ok=True)


def symbol_to_filename(symbol: str) -> str:
    return (
        symbol.replace("/", "_")
        .replace(":", "_")
        .replace(" ", "_")
        .replace(".", "_")
        .replace("-", "_")
    )


def cache_path(symbol: str, interval: str) -> str:
    ensure_cache_dir()
    return os.path.join(CACHE_DIR, f"{symbol_to_filename(symbol)}__{interval}.json")


def load_json_file(path: str) -> Optional[Dict[str, Any]]:
    if not os.path.exists(path):
        return None

    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"JSON okuma hatası ({path}): {e}")
        return None


def save_json_file(path: str, payload: Dict[str, Any]) -> None:
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f)
    except Exception as e:
        print(f"JSON yazma hatası ({path}): {e}")


def load_cached_payload(symbol: str, interval: str) -> Optional[Dict[str, Any]]:
    return load_json_file(cache_path(symbol, interval))


def save_cached_payload(symbol: str, interval: str, payload: Dict[str, Any]) -> None:
    save_json_file(cache_path(symbol, interval), payload)


def cache_is_fresh(symbol: str, interval: str, ttl: int) -> bool:
    payload = load_cached_payload(symbol, interval)
    if not payload:
        return False

    fetched_at = payload.get("_fetched_at")
    if not isinstance(fetched_at, int):
        return False

    return (now_ts() - fetched_at) < ttl


# =========================================================
# TWELVEDATA FETCH
# =========================================================
def fetch_twelvedata_series(symbol: str, interval: str, outputsize: int) -> Optional[Dict[str, Any]]:
    if not TWELVEDATA_API_KEY:
        print("TWELVEDATA_API_KEY yok.")
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
            print(f"{symbol} {interval} HTTP hata: {response.status_code} - {response.text}")
            return None

        if data.get("status") == "error":
            print(f"{symbol} {interval} API hata: {data}")
            return None

        if "values" not in data or not data["values"]:
            print(f"{symbol} {interval} veri boş.")
            return None

        data["_fetched_at"] = now_ts()
        return data

    except Exception as e:
        print(f"{symbol} {interval} fetch hatası: {e}")
        return None


def get_series_with_policy(symbol: str, interval: str) -> Optional[Dict[str, Any]]:
    rule = TF_RULES[interval]
    ttl = rule["ttl"]
    outputsize = rule["outputsize"]

    if cache_is_fresh(symbol, interval, ttl):
        payload = load_cached_payload(symbol, interval)
        if payload:
            return payload

    fresh = fetch_twelvedata_series(symbol, interval, outputsize)
    if fresh:
        save_cached_payload(symbol, interval, fresh)
        return fresh

    fallback = load_cached_payload(symbol, interval)
    if fallback:
        print(f"{symbol} {interval} -> stale cache fallback kullanıldı")
        return fallback

    return None


# =========================================================
# CANDLES
# =========================================================
def build_candles(raw: Dict[str, Any]) -> List[Dict[str, Any]]:
    values = raw.get("values", [])
    candles: List[Dict[str, Any]] = []

    for row in values:
        o = safe_float(row.get("open"))
        h = safe_float(row.get("high"))
        l = safe_float(row.get("low"))
        c = safe_float(row.get("close"))
        dt = parse_candle_dt(row.get("datetime"))

        if None in (o, h, l, c):
            continue

        candles.append(
            {
                "datetime": row.get("datetime"),
                "dt": dt,
                "open": o,
                "high": h,
                "low": l,
                "close": c,
            }
        )

    candles.reverse()
    return candles


def candle_body(c: Dict[str, Any]) -> float:
    return abs(c["close"] - c["open"])


def candle_range(c: Dict[str, Any]) -> float:
    return c["high"] - c["low"]


def average_body(candles: List[Dict[str, Any]], count: int = 10) -> float:
    sample = candles[-count:] if len(candles) >= count else candles
    if not sample:
        return 0.0
    return sum(candle_body(x) for x in sample) / len(sample)


def average_range(candles: List[Dict[str, Any]], count: int = 10) -> float:
    sample = candles[-count:] if len(candles) >= count else candles
    if not sample:
        return 0.0
    return sum(candle_range(x) for x in sample) / len(sample)


# =========================================================
# SWING / PIVOT ENGINE
# =========================================================
def is_pivot_high(
    candles: List[Dict[str, Any]],
    i: int,
    left: int = 2,
    right: int = 2,
) -> bool:
    if i - left < 0 or i + right >= len(candles):
        return False

    current = candles[i]["high"]

    for j in range(i - left, i + right + 1):
        if j == i:
            continue
        if candles[j]["high"] >= current:
            return False

    return True


def is_pivot_low(
    candles: List[Dict[str, Any]],
    i: int,
    left: int = 2,
    right: int = 2,
) -> bool:
    if i - left < 0 or i + right >= len(candles):
        return False

    current = candles[i]["low"]

    for j in range(i - left, i + right + 1):
        if j == i:
            continue
        if candles[j]["low"] <= current:
            return False

    return True


def collect_swings(
    candles: List[Dict[str, Any]],
    left: int = 2,
    right: int = 2,
) -> Dict[str, List[Dict[str, Any]]]:
    highs: List[Dict[str, Any]] = []
    lows: List[Dict[str, Any]] = []

    for i in range(len(candles)):
        if is_pivot_high(candles, i, left=left, right=right):
            highs.append(
                {
                    "index": i,
                    "price": candles[i]["high"],
                    "datetime": candles[i]["datetime"],
                }
            )

        if is_pivot_low(candles, i, left=left, right=right):
            lows.append(
                {
                    "index": i,
                    "price": candles[i]["low"],
                    "datetime": candles[i]["datetime"],
                }
            )

    return {"highs": highs, "lows": lows}


def last_two(items: List[Dict[str, Any]]) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    if len(items) >= 2:
        return items[-2], items[-1]
    if len(items) == 1:
        return None, items[-1]
    return None, None


# =========================================================
# SESSION / LIQUIDITY CONTEXT
# =========================================================
def get_session_name(dt: Optional[datetime]) -> str:
    if not dt:
        return "Unknown"

    hour = dt.hour

    if 0 <= hour < 6:
        return "Asia"
    if 6 <= hour < 12:
        return "London"
    if 12 <= hour < 17:
        return "New York"
    return "Off Session"


def approx_equal(a: float, b: float, tolerance_ratio: float = 0.0008) -> bool:
    if a == 0 or b == 0:
        return False

    return abs(a - b) / max(abs(a), abs(b)) <= tolerance_ratio


# =========================================================
# DISPLACEMENT
# =========================================================
def detect_displacement(candles: List[Dict[str, Any]]) -> str:
    if len(candles) < 12:
        return "Yok"

    last = candles[-1]
    avg_body = average_body(candles, 10)
    avg_range_val = average_range(candles, 10)

    body = candle_body(last)
    total_range = candle_range(last)

    if avg_body == 0 or avg_range_val == 0:
        return "Yok"

    body_ratio = body / avg_body
    range_ratio = total_range / avg_range_val

    if body_ratio >= 2.0 and range_ratio >= 1.5:
        return "Güçlü"

    if body_ratio >= 1.4 and range_ratio >= 1.2:
        return "Orta"

    return "Zayıf"


# =========================================================
# HTF BIAS
# =========================================================
def detect_htf_bias(candles: List[Dict[str, Any]]) -> str:
    swings = collect_swings(candles, left=3, right=3)

    highs = swings["highs"]
    lows = swings["lows"]

    h1, h2 = last_two(highs)
    l1, l2 = last_two(lows)

    if not h2 or not l2:
        return "Nötr"

    if h1 and l1:
        if h2["price"] > h1["price"] and l2["price"] > l1["price"]:
            return "Yükseliş"

    if h1 and l1:
        if h2["price"] < h1["price"] and l2["price"] < l1["price"]:
            return "Düşüş"

    return "Nötr"


# =========================================================
# MSS + CHoCH
# =========================================================
def detect_mss_choch(candles: List[Dict[str, Any]], bias: str) -> Tuple[str, str]:
    swings = collect_swings(candles, left=2, right=2)

    highs = swings["highs"]
    lows = swings["lows"]

    h1, h2 = last_two(highs)
    l1, l2 = last_two(lows)

    if not h2 or not l2:
        return "Yok", "Yok"

    last_close = candles[-1]["close"]

    mss = "Yok"
    choch = "Yok"

    if last_close > h2["price"]:
        mss = "Bullish MSS"

    if last_close < l2["price"]:
        mss = "Bearish MSS"

    if h1 and last_close > h1["price"]:
        choch = "Bullish CHoCH"

    if l1 and last_close < l1["price"]:
        choch = "Bearish CHoCH"

    return mss, choch


# =========================================================
# LIQUIDITY SWEEP
# =========================================================
def detect_liquidity_sweep(candles: List[Dict[str, Any]]) -> str:
    if len(candles) < 8:
        return "Yok"

    last = candles[-1]
    recent = candles[-8:-1]

    prev_high = max(x["high"] for x in recent)
    prev_low = min(x["low"] for x in recent)

    if last["high"] > prev_high and last["close"] < prev_high:
        return "Üst likidite sweep"

    if last["low"] < prev_low and last["close"] > prev_low:
        return "Alt likidite sweep"

    return "Yok"


# =========================================================
# FVG
# =========================================================
def detect_fvg(candles: List[Dict[str, Any]]) -> str:
    if len(candles) < 3:
        return "Yok"

    a, b, c = candles[-3], candles[-2], candles[-1]

    if c["low"] > a["high"]:
        return "Bullish FVG"

    if c["high"] < a["low"]:
        return "Bearish FVG"

    return "Yok"


# =========================================================
# PREMIUM / DISCOUNT
# =========================================================
def detect_pd(candles: List[Dict[str, Any]]) -> str:
    if len(candles) < 20:
        return "Nötr"

    recent = candles[-20:]

    high_ = max(x["high"] for x in recent)
    low_ = min(x["low"] for x in recent)

    mid = (high_ + low_) / 2
    price = candles[-1]["close"]

    if price > mid:
        return "Premium"
    if price < mid:
        return "Discount"

    return "Nötr"


# =========================================================
# ORDER BLOCK
# =========================================================
def detect_order_block(
    candles: List[Dict[str, Any]],
    direction: str,
) -> Optional[Dict[str, float]]:
    if len(candles) < 10:
        return None

    lookback = candles[-10:-1]

    if direction == "LONG":
        for c in reversed(lookback):
            if c["close"] < c["open"]:
                return {
                    "low": c["low"],
                    "high": c["high"],
                }

    if direction == "SHORT":
        for c in reversed(lookback):
            if c["close"] > c["open"]:
                return {
                    "low": c["low"],
                    "high": c["high"],
                }

    return None


def is_price_in_ob(price: float, ob: Dict[str, float]) -> bool:
    return ob["low"] <= price <= ob["high"]


# =========================================================
# PULLBACK ENGINE
# =========================================================
def is_pullback_valid(
    candles_5m: List[Dict[str, Any]],
    direction: str,
    ob: Dict[str, float],
) -> bool:
    if len(candles_5m) < 6:
        return False

    recent = candles_5m[-6:]

    ob_low = ob["low"]
    ob_high = ob["high"]

    touched = False

    for c in recent:
        if c["low"] <= ob_high and c["high"] >= ob_low:
            touched = True
            break

    if not touched:
        return False

    last = candles_5m[-1]

    if direction == "LONG":
        return last["close"] > last["open"]

    if direction == "SHORT":
        return last["close"] < last["open"]

    return False


# =========================================================
# SMT ENGINE (FOREX-ONLY GELİŞTİRİLMİŞ)
# =========================================================
def sweep_flags(candles: List[Dict[str, Any]]) -> Dict[str, bool]:
    if len(candles) < 8:
        return {"high_sweep": False, "low_sweep": False}

    last = candles[-1]
    recent = candles[-8:-1]

    prev_high = max(x["high"] for x in recent)
    prev_low = min(x["low"] for x in recent)

    return {
        "high_sweep": last["high"] > prev_high and last["close"] < prev_high,
        "low_sweep": last["low"] < prev_low and last["close"] > prev_low,
    }


def detect_forex_smt(
    market_name: str,
    mtf_map: Dict[str, Dict[str, List[Dict[str, Any]]]],
) -> str:
    relations = SMT_RELATIONS.get(market_name, [])
    if not relations:
        return "Yok"

    target_15m = mtf_map.get(market_name, {}).get("15min", [])
    if len(target_15m) < 8:
        return "Yok"

    target_flags = sweep_flags(target_15m)

    bullish_votes = 0
    bearish_votes = 0

    for other_name, relation_type in relations:
        other_15m = mtf_map.get(other_name, {}).get("15min", [])
        if len(other_15m) < 8:
            continue

        other_flags = sweep_flags(other_15m)

        if relation_type == "positive":
            # Aynı yönlü korelasyonda aynı tarafta sweep beklenir.
            # Biri sweep yapıp diğeri yapmıyorsa SMT.
            if target_flags["low_sweep"] and not other_flags["low_sweep"]:
                bullish_votes += 1

            if target_flags["high_sweep"] and not other_flags["high_sweep"]:
                bearish_votes += 1

        elif relation_type == "inverse":
            # Ters korelasyonda karşı tarafta cevap beklenir.
            # Target low sweep yapınca partner high sweep ile cevap vermiyorsa bullish divergence.
            if target_flags["low_sweep"] and not other_flags["high_sweep"]:
                bullish_votes += 1

            if target_flags["high_sweep"] and not other_flags["low_sweep"]:
                bearish_votes += 1

    if bullish_votes > bearish_votes and bullish_votes > 0:
        return "Bullish SMT"

    if bearish_votes > bullish_votes and bearish_votes > 0:
        return "Bearish SMT"

    return "Yok"


# =========================================================
# SIGNAL FILTER
# =========================================================
def determine_direction(
    sweep: str,
    mss: str,
    choch: str,
    displacement: str,
) -> str:
    if (
        sweep == "Alt likidite sweep"
        and (mss == "Bullish MSS" or choch == "Bullish CHoCH")
        and displacement in ("Orta", "Güçlü")
    ):
        return "LONG"

    if (
        sweep == "Üst likidite sweep"
        and (mss == "Bearish MSS" or choch == "Bearish CHoCH")
        and displacement in ("Orta", "Güçlü")
    ):
        return "SHORT"

    return "YOK"


# =========================================================
# SCORE
# =========================================================
def score_signal(
    direction: str,
    bias: str,
    fvg: str,
    pd: str,
    smt: str,
    killzone_active: bool,
) -> Tuple[int, str]:
    score = 50

    if direction == "LONG":
        if bias == "Yükseliş":
            score += 15
        if fvg == "Bullish FVG":
            score += 10
        if pd == "Discount":
            score += 10
        if smt == "Bullish SMT":
            score += 10

    if direction == "SHORT":
        if bias == "Düşüş":
            score += 15
        if fvg == "Bearish FVG":
            score += 10
        if pd == "Premium":
            score += 10
        if smt == "Bearish SMT":
            score += 10

    if killzone_active:
        score += 10

    quality = "Yok"

    if score >= 90:
        quality = "A+"
    elif score >= 75:
        quality = "A"

    return score, quality


# =========================================================
# DATA PREP
# =========================================================
def build_forex_mtf_map() -> Dict[str, Dict[str, List[Dict[str, Any]]]]:
    mtf_map: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}

    for market in FOREX_METALS:
        name = market["name"]
        symbol = market["symbol"]

        mtf_map[name] = {}

        for interval in ("5min", "15min", "1h", "4h", "1week"):
            raw = get_series_with_policy(symbol, interval)

            if raw:
                mtf_map[name][interval] = build_candles(raw)
            else:
                mtf_map[name][interval] = []

    return mtf_map


# =========================================================
# OB MITIGATION ENTRY ENGINE
# =========================================================
def build_trade_levels_from_ob(
    candles_15m: List[Dict[str, Any]],
    direction: str,
    ob: Dict[str, float],
) -> Dict[str, Optional[float]]:
    avg_rng = average_range(candles_15m[-10:], 10)

    if avg_rng == 0:
        return {"entry": None, "sl": None, "tp": None}

    if direction == "LONG":
        entry = ob["low"]
        sl = ob["low"] - (avg_rng * 0.25)
        risk = entry - sl
        tp = entry + (risk * 2.0)

        return {
            "entry": entry,
            "sl": sl,
            "tp": tp,
        }

    if direction == "SHORT":
        entry = ob["high"]
        sl = ob["high"] + (avg_rng * 0.25)
        risk = sl - entry
        tp = entry - (risk * 2.0)

        return {
            "entry": entry,
            "sl": sl,
            "tp": tp,
        }

    return {
        "entry": None,
        "sl": None,
        "tp": None,
    }


def is_ob_mitigated(
    candles_5m: List[Dict[str, Any]],
    ob: Dict[str, float],
    direction: str,
) -> bool:
    if len(candles_5m) < 3:
        return False

    last = candles_5m[-1]
    prev = candles_5m[-2]

    touched = (
        (last["low"] <= ob["high"] and last["high"] >= ob["low"])
        or (prev["low"] <= ob["high"] and prev["high"] >= ob["low"])
    )

    if not touched:
        return False

    if direction == "LONG":
        if last["close"] > last["open"] and last["close"] >= ob["low"]:
            return True

    if direction == "SHORT":
        if last["close"] < last["open"] and last["close"] <= ob["high"]:
            return True

    return False


# =========================================================
# NO-CHASE FILTER
# =========================================================
def is_no_chase(candles: List[Dict[str, Any]], entry: Optional[float], direction: str) -> bool:
    if len(candles) < 3 or entry is None:
        return False

    last_price = candles[-1]["close"]
    avg_rng = average_range(candles[-10:], 10)

    if avg_rng == 0:
        return False

    distance = abs(last_price - entry)

    if distance > avg_rng * 1.5:
        return True

    return False


# =========================================================
# FOREX / METAL ANALYZE ENGINE
# =========================================================
def analyze_forex_symbol(
    market_name: str,
    mtf_map: Dict[str, Dict[str, List[Dict[str, Any]]]],
) -> Optional[Dict[str, Any]]:
    tfs = mtf_map.get(market_name, {})

    candles_5m = tfs.get("5min", [])
    candles_15m = tfs.get("15min", [])
    candles_1h = tfs.get("1h", [])
    candles_4h = tfs.get("4h", [])
    candles_1w = tfs.get("1week", [])

    if (
        len(candles_5m) < 20
        or len(candles_15m) < 30
        or len(candles_1h) < 20
        or len(candles_4h) < 10
        or len(candles_1w) < 5
    ):
        return None

    current_price = candles_5m[-1]["close"]

    h1_bias = detect_htf_bias(candles_1h)
    h4_bias = detect_htf_bias(candles_4h)
    w1_bias = detect_htf_bias(candles_1w)

    bias = h1_bias if h1_bias != "Nötr" else h4_bias
    if bias == "Nötr":
        bias = w1_bias

    mss, choch = detect_mss_choch(candles_15m, bias)
    sweep = detect_liquidity_sweep(candles_15m)
    displacement = detect_displacement(candles_15m)
    fvg = detect_fvg(candles_15m)
    pd = detect_pd(candles_15m)

    direction = determine_direction(sweep, mss, choch, displacement)
    if direction == "YOK":
        return None

    ob = detect_order_block(candles_15m, direction)
    if not ob:
        return None

    if not is_ob_mitigated(candles_5m, ob, direction):
        print(f"{market_name} -> setup var ama OB mitigation yok")
        return None

    if not is_pullback_valid(candles_5m, direction, ob):
        print(f"{market_name} -> setup var ama valid pullback yok")
        return None

    levels = build_trade_levels_from_ob(candles_15m, direction, ob)
    if levels["entry"] is None or levels["sl"] is None or levels["tp"] is None:
        return None

    if direction == "SHORT":
        if levels["entry"] != ob["high"]:
            print(f"{market_name} -> OB short entry hatalı, sinyal iptal")
            return None

    if direction == "LONG":
        if levels["entry"] != ob["low"]:
            print(f"{market_name} -> OB long entry hatalı, sinyal iptal")
            return None

    if is_no_chase(candles_5m, levels["entry"], direction):
        print(f"{market_name} -> skip (no-chase)")
        return None

    smt = detect_forex_smt(market_name, mtf_map)

    score, quality = score_signal(
        direction=direction,
        bias=bias,
        fvg=fvg,
        pd=pd,
        smt=smt,
        killzone_active=is_killzone_active(),
    )

    if quality not in ("A", "A+"):
        return {
            "symbol": market_name,
            "price": current_price,
            "direction": direction,
            "entry": levels["entry"],
            "sl": levels["sl"],
            "tp": levels["tp"],
            "score": score,
            "quality": quality,
            "bias": bias,
            "h1_bias": h1_bias,
            "h4_bias": h4_bias,
            "w1_bias": w1_bias,
            "sweep": sweep,
            "mss": mss,
            "choch": choch,
            "displacement": displacement,
            "fvg": fvg,
            "pd": pd,
            "smt": smt,
            "killzone": get_killzone_label(),
            "ob_low": ob["low"],
            "ob_high": ob["high"],
            "reason": "OB mitigation geldi ama skor filtreyi geçemedi",
        }

    return {
        "symbol": market_name,
        "price": current_price,
        "direction": direction,
        "entry": levels["entry"],
        "sl": levels["sl"],
        "tp": levels["tp"],
        "score": score,
        "quality": quality,
        "bias": bias,
        "h1_bias": h1_bias,
        "h4_bias": h4_bias,
        "w1_bias": w1_bias,
        "sweep": sweep,
        "mss": mss,
        "choch": choch,
        "displacement": displacement,
        "fvg": fvg,
        "pd": pd,
        "smt": smt,
        "killzone": get_killzone_label(),
        "ob_low": ob["low"],
        "ob_high": ob["high"],
        "reason": "Sniper OB mitigation entry hazır",
    }


# =========================================================
# MESSAGE FORMAT
# =========================================================
def format_signal_message(r: Dict[str, Any]) -> str:
    entry = f"{r['entry']:.5f}" if isinstance(r.get("entry"), (int, float)) else str(r.get("entry", "Yok"))
    sl = f"{r['sl']:.5f}" if isinstance(r.get("sl"), (int, float)) else str(r.get("sl", "Yok"))
    tp = f"{r['tp']:.5f}" if isinstance(r.get("tp"), (int, float)) else str(r.get("tp", "Yok"))
    price = f"{r['price']:.5f}" if isinstance(r.get("price"), (int, float)) else str(r.get("price", "Yok"))
    ob_low = f"{r['ob_low']:.5f}" if isinstance(r.get("ob_low"), (int, float)) else "Yok"
    ob_high = f"{r['ob_high']:.5f}" if isinstance(r.get("ob_high"), (int, float)) else "Yok"

    parts = [
        "🔥 ELITE SNIPER ICT SIGNAL",
        "",
        f"{r['symbol']} | {r['direction']}",
        "",
        f"Price: {price}",
        f"Entry: {entry}",
        f"SL: {sl}",
        f"TP: {tp}",
        "",
        f"Score: {r['score']} ({r['quality']})",
        "",
        f"Bias: {r.get('bias', 'Yok')}",
        f"1H Bias: {r.get('h1_bias', 'Yok')}",
        f"4H Bias: {r.get('h4_bias', 'Yok')}",
        f"1W Bias: {r.get('w1_bias', 'Yok')}",
        f"Sweep: {r.get('sweep', 'Yok')}",
        f"MSS: {r.get('mss', 'Yok')}",
        f"CHoCH: {r.get('choch', 'Yok')}",
        f"Displacement: {r.get('displacement', 'Yok')}",
        f"FVG: {r.get('fvg', 'Yok')}",
        f"PD: {r.get('pd', 'Yok')}",
        f"SMT: {r.get('smt', 'Yok')}",
        f"Session: {r.get('killzone', 'Yok')}",
        f"OB Zone: {ob_low} - {ob_high}",
        "",
        f"Reason: {r.get('reason', 'Yok')}",
    ]
    return "\n".join(parts)


# =========================================================
# RUN
# =========================================================
def run_scan() -> int:
    print("=" * 60)
    print(f"ELITE SNIPER SCAN START -> {now_str()}")

    print(f"TwelveData key var mı: {'evet' if bool(TWELVEDATA_API_KEY) else 'hayır'}")
    print(f"Telegram token var mı: {'evet' if bool(TELEGRAM_BOT_TOKEN) else 'hayır'}")
    print(f"Telegram chat id var mı: {'evet' if bool(TELEGRAM_CHAT_ID) else 'hayır'}")

    if is_weekend_market_closed():
        print("Hafta sonu market kapalı. Analiz yapılmadı.")
        print(f"ELITE SNIPER SCAN END -> {now_str()} | toplam sinyal: 0")
        print("=" * 60)
        return 0

    mtf_map = build_forex_mtf_map()
    total_signals = 0

    for market in FOREX_METALS:
        name = market["name"]
        result = analyze_forex_symbol(name, mtf_map)

        if not result:
            print(f"{name} -> setup yok")
            continue

        print(
            f"{name} -> yön: {result['direction']}, "
            f"skor: {result['score']}, kalite: {result['quality']}"
        )

        if result["direction"] == "SHORT":
            if result["entry"] != result["ob_high"]:
                print(f"{name} -> entry OB high ile uyuşmuyor, sinyal gönderilmedi")
                continue

        if result["direction"] == "LONG":
            if result["entry"] != result["ob_low"]:
                print(f"{name} -> entry OB low ile uyuşmuyor, sinyal gönderilmedi")
                continue

        if result["score"] >= MIN_SIGNAL_SCORE and result["quality"] in ("A", "A+"):
            msg = format_signal_message(result)
            sent = send_telegram_message(msg)

            if sent:
                total_signals += 1
                print(f"{name} -> SIGNAL GÖNDERİLDİ")
            else:
                print(f"{name} -> sinyal gönderilemedi")
        else:
            print(f"{name} -> setup var ama filtreyi geçemedi")

    print(f"ELITE SNIPER SCAN END -> {now_str()} | toplam sinyal: {total_signals}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(run_scan())