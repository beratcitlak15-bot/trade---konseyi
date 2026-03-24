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

# TradingView verisi daha sonra webhook ile bu dosyaya yazılacak
TRADINGVIEW_STATE_FILE = os.getenv("TRADINGVIEW_STATE_FILE", "tradingview_state.json").strip()

# =========================================================
# SETTINGS
# =========================================================
MIN_SIGNAL_SCORE = 80
CACHE_DIR = ".mtf_cache"

# Sadece bunlar TwelveData'dan çekilecek
FOREX_METALS = [
    {"name": "EUR/USD", "symbol": "EUR/USD", "group": "forex"},
    {"name": "GBP/USD", "symbol": "GBP/USD", "group": "forex"},
    {"name": "XAU/USD", "symbol": "XAU/USD", "group": "metal"},
    {"name": "USD/JPY", "symbol": "USD/JPY", "group": "forex"},
    {"name": "AUD/USD", "symbol": "AUD/USD", "group": "forex"},
]

# Bunlar TradingView motorundan beslenecek
TRADINGVIEW_MARKETS = ["DXY", "US100", "SP500"]

# Çoklu timeframe veri politikası
# 5m + 15m = her 5 dk
# 1h = 30 dk
# 4h = 1 saat
# 1w = 1 gün
TF_RULES = {
    "5min": {"ttl": 300, "outputsize": 240},
    "15min": {"ttl": 300, "outputsize": 240},
    "1h": {"ttl": 1800, "outputsize": 240},
    "4h": {"ttl": 3600, "outputsize": 240},
    "1week": {"ttl": 86400, "outputsize": 240},
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
    wd = now.weekday()  # Mon=0 ... Sun=6
    hour = now.hour

    # Cumartesi tamamen kapalı
    if wd == 5:
        return False

    # Cuma UTC 21:00 sonrası kapalı
    if wd == 4 and hour >= 21:
        return False

    # Pazar UTC 21:00 öncesi kapalı
    if wd == 6 and hour < 21:
        return False

    return True


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
# TWELVEDATA FETCH (SADECE FOREX / METALS)
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
# TRADINGVIEW STATE PLACEHOLDER
# =========================================================
def get_tradingview_state() -> Dict[str, Any]:
    data = load_json_file(TRADINGVIEW_STATE_FILE)

    if not data:
        return {
            "updated_at": None,
            "dxy_bias": "Yok",
            "index_smt": "Yok",
            "us100": {},
            "sp500": {},
        }

    return {
        "updated_at": data.get("updated_at"),
        "dxy_bias": data.get("dxy_bias", "Yok"),
        "index_smt": data.get("index_smt", "Yok"),
        "us100": data.get("us100", {}),
        "sp500": data.get("sp500", {}),
    }

# =========================================================
# WEEKEND FILTER (EKLENTİ - SADECE BU EKLENDİ)
# =========================================================
def is_weekend_market_closed(dt: Optional[datetime] = None) -> bool:
    x = dt or now_utc()

    # Python weekday:
    # Monday = 0 ... Sunday = 6
    day = x.weekday()
    hour = x.hour

    # Cuma 22:00 sonrası → market kapanıyor
    if day == 4 and hour >= 22:
        return True

    # Cumartesi → tamamen kapalı
    if day == 5:
        return True

    # Pazar → 22:00'a kadar kapalı
    if day == 6 and hour < 22:
        return True

    return False


# =========================================================
# NO-CHASE FILTER (EKLENTİ - SENİN STRATEJİ)
# =========================================================
def is_no_chase(
    candles_5m: List[Dict[str, Any]],
    entry_price: float,
    direction: str,
) -> bool:
    """
    Fiyat entry'den çok uzaksa trade alma.
    """

    if len(candles_5m) < 1:
        return False

    current_price = candles_5m[-1]["close"]

    # tolerans: %0.15 (senin sniper stiline uygun)
    tolerance = entry_price * 0.0015

    if direction == "LONG":
        return current_price > (entry_price + tolerance)

    if direction == "SHORT":
        return current_price < (entry_price - tolerance)

    return False

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


def get_recent_session_box(
    candles: List[Dict[str, Any]],
    session_name: str,
) -> Optional[Dict[str, Any]]:
    session_candles = [c for c in candles if get_session_name(c.get("dt")) == session_name]

    if len(session_candles) < 3:
        return None

    recent = session_candles[-24:] if len(session_candles) > 24 else session_candles

    return {
        "high": max(x["high"] for x in recent),
        "low": min(x["low"] for x in recent),
        "start": recent[0]["datetime"],
        "end": recent[-1]["datetime"],
    }


def get_previous_day_levels(candles: List[Dict[str, Any]]) -> Dict[str, Optional[float]]:
    day_groups: Dict[str, List[Dict[str, Any]]] = {}

    for c in candles:
        dt = c.get("dt")
        if not dt:
            continue

        key = dt.strftime("%Y-%m-%d")
        day_groups.setdefault(key, []).append(c)

    all_days = sorted(day_groups.keys())

    if len(all_days) < 2:
        return {"pdh": None, "pdl": None}

    prev_day = day_groups[all_days[-2]]

    return {
        "pdh": max(x["high"] for x in prev_day),
        "pdl": min(x["low"] for x in prev_day),
    }


def approx_equal(a: float, b: float, tolerance_ratio: float = 0.0008) -> bool:
    if a == 0 or b == 0:
        return False

    return abs(a - b) / max(abs(a), abs(b)) <= tolerance_ratio


def equal_highs_exists(
    candles: List[Dict[str, Any]],
    tolerance_ratio: float = 0.0008,
) -> Optional[float]:
    swings = collect_swings(candles, left=2, right=2)["highs"]

    if len(swings) < 2:
        return None

    recent = swings[-6:]

    for i in range(len(recent)):
        for j in range(i + 1, len(recent)):
            a = recent[i]["price"]
            b = recent[j]["price"]

            if approx_equal(a, b, tolerance_ratio):
                return max(a, b)

    return None


def equal_lows_exists(
    candles: List[Dict[str, Any]],
    tolerance_ratio: float = 0.0008,
) -> Optional[float]:
    swings = collect_swings(candles, left=2, right=2)["lows"]

    if len(swings) < 2:
        return None

    recent = swings[-6:]

    for i in range(len(recent)):
        for j in range(i + 1, len(recent)):
            a = recent[i]["price"]
            b = recent[j]["price"]

            if approx_equal(a, b, tolerance_ratio):
                return min(a, b)

    return None


# =========================================================
# DISPLACEMENT (PRO LEVEL)
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
# HTF BIAS (ADVANCED STRUCTURE)
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
# MSS + CHoCH (REAL ICT LOGIC)
# =========================================================
def detect_mss_choch(candles: List[Dict[str, Any]], bias) -> Tuple[str, str]:
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

    if h2 and last_close > h2["price"]:
        mss = "Bullish MSS"

    if l2 and last_close < l2["price"]:
        mss = "Bearish MSS"

    if h1 and l2:
        if last_close > h1["price"]:
            choch = "Bullish CHoCH"

    if l1 and h2:
        if last_close < l1["price"]:
            choch = "Bearish CHoCH"

    return mss, choch


# =========================================================
# LIQUIDITY SWEEP (SMART)
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
# FVG (TRUE GAP)
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
# ORDER BLOCK (SNIPER CORE)
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
# SMT (ADVANCED)
# =========================================================
def detect_index_smt(tv_state: Dict[str, Any]) -> str:
    return tv_state.get("index_smt", "Yok")


def get_dxy_bias(tv_state: Dict[str, Any]) -> str:
    return tv_state.get("dxy_bias", "Yok")


# =========================================================
# SIGNAL FILTER (SNIPER ENTRY)
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
# SCORE (PRO FILTER)
# =========================================================
def score_signal(
    direction: str,
    bias: str,
    fvg: str,
    pd: str,
    smt: str,
    dxy_bias: str,
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
        if dxy_bias == "Düşüş":
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
        if dxy_bias == "Yükseliş":
            score += 10

    if killzone_active:
        score += 10

    quality = "Yok"

    if score >= 90:
        quality = "A+"
    elif score >= 80:
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
    if direction == "LONG":
        entry = ob["low"]
        sl = ob["low"] - (average_range(candles_15m[-10:], 10) * 0.25)
        risk = entry - sl
        tp = entry + (risk * 2.0)

        return {
            "entry": entry,
            "sl": sl,
            "tp": tp,
        }

    if direction == "SHORT":
        entry = ob["high"]
        sl = ob["high"] + (average_range(candles_15m[-10:], 10) * 0.25)
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
# NO CHASE FILTER
# =========================================================
def is_no_chase(candles: List[Dict[str, Any]], entry: float, direction: str) -> bool:
    if len(candles) < 3 or entry is None:
        return False

    last_price = candles[-1]["close"]
    avg_rng = average_range(candles[-10:], 10)

    if avg_rng == 0:
        return False

    distance = abs(last_price - entry)

    # fiyat çok uzaksa (geç kalınmış trade)
    if distance > avg_rng * 1.5:
        return True

    return false

# =========================================================
# FOREX / METAL ANALYZE ENGINE (SNIPER OB ENTRY)
# =========================================================
def analyze_forex_symbol(
    market_name: str,
    mtf_map: Dict[str, Dict[str, List[Dict[str, Any]]]],
    tv_state: Dict[str, Any],
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

    # HTF bias
    h1_bias = detect_htf_bias(candles_1h)
    h4_bias = detect_htf_bias(candles_4h)
    w1_bias = detect_htf_bias(candles_1w)

    # Ana context bias
    bias = h1_bias if h1_bias != "Nötr" else h4_bias
    if bias == "Nötr":
        bias = w1_bias

    # 15m setup
    mss, choch = detect_mss_choch(candles_15m, bias)
    sweep = detect_liquidity_sweep(candles_15m)
    displacement = detect_displacement(candles_15m)
    fvg = detect_fvg(candles_15m)
    pd = detect_pd(candles_15m)

    direction = determine_direction(sweep, mss, choch, displacement)
    if direction == "YOK":
        return None

    # OB bul
    ob = detect_order_block(candles_15m, direction)
    if not ob:
        return None

    # Setup var ama fiyat OB'ye dönmemişse trade yok
    if not is_ob_mitigated(candles_5m, ob, direction):
        print(f"{market_name} -> setup var ama OB mitigation yok")
        return None

    # OB bazlı levels üret
    levels = build_trade_levels_from_ob(candles_15m, direction, ob)
    if levels["entry"] is None or levels["sl"] is None or levels["tp"] is None:
        return None

    # =========================================================
    # OB ENTRY SAFETY CHECK
    # =========================================================
    if direction == "SHORT":
        if levels["entry"] != ob["high"]:
            print(f"{market_name} -> OB short entry hatalı, sinyal iptal")
            return None

    if direction == "LONG":
        if levels["entry"] != ob["low"]:
            print(f"{market_name} -> OB long entry hatalı, sinyal iptal")
            return None

    # No-chase filtresi
    if is_no_chase(candles_5m, levels["entry"], direction):
        print(f"{market_name} -> skip (no-chase)")
        return None

    # DXY sadece EURUSD ve XAUUSD için aktif
    dxy_bias = get_dxy_bias(tv_state) if market_name in ("EUR/USD", "XAU/USD") else "Yok"

    # Şimdilik forex SMT local kullanılmıyor
    smt = "Yok"

    score, quality = score_signal(
        direction=direction,
        bias=bias,
        fvg=fvg,
        pd=pd,
        smt=smt,
        dxy_bias=dxy_bias,
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
            "dxy_bias": dxy_bias,
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
        "dxy_bias": dxy_bias,
        "killzone": get_killzone_label(),
        "ob_low": ob["low"],
        "ob_high": ob["high"],
        "reason": "Sniper OB mitigation entry hazır",
    }


# =========================================================
# TRADINGVIEW INDEX ANALYZE ENGINE
# =========================================================
def analyze_tradingview_indices(tv_state: Dict[str, Any]) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []

    index_smt = detect_index_smt(tv_state)

    us100 = tv_state.get("us100", {})
    if us100:
        results.append(
            {
                "symbol": "US100",
                "price": us100.get("price"),
                "direction": us100.get("direction", "YOK"),
                "entry": us100.get("entry"),
                "sl": us100.get("sl"),
                "tp": us100.get("tp"),
                "score": us100.get("score", 0),
                "quality": us100.get("quality", "Yok"),
                "bias": us100.get("bias", "Yok"),
                "h1_bias": us100.get("h1_bias", "Yok"),
                "h4_bias": us100.get("h4_bias", "Yok"),
                "w1_bias": us100.get("w1_bias", "Yok"),
                "sweep": us100.get("sweep", "Yok"),
                "mss": us100.get("mss", "Yok"),
                "choch": us100.get("choch", "Yok"),
                "displacement": us100.get("displacement", "Yok"),
                "fvg": us100.get("fvg", "Yok"),
                "pd": us100.get("pd", "Yok"),
                "dxy_bias": "Yok",
                "killzone": get_killzone_label(),
                "ob_low": us100.get("ob_low"),
                "ob_high": us100.get("ob_high"),
                "reason": us100.get("reason", "TradingView US100 setup"),
                "index_smt": index_smt,
            }
        )

    sp500 = tv_state.get("sp500", {})
    if sp500:
        results.append(
            {
                "symbol": "SP500",
                "price": sp500.get("price"),
                "direction": sp500.get("direction", "YOK"),
                "entry": sp500.get("entry"),
                "sl": sp500.get("sl"),
                "tp": sp500.get("tp"),
                "score": sp500.get("score", 0),
                "quality": sp500.get("quality", "Yok"),
                "bias": sp500.get("bias", "Yok"),
                "h1_bias": sp500.get("h1_bias", "Yok"),
                "h4_bias": sp500.get("h4_bias", "Yok"),
                "w1_bias": sp500.get("w1_bias", "Yok"),
                "sweep": sp500.get("sweep", "Yok"),
                "mss": sp500.get("mss", "Yok"),
                "choch": sp500.get("choch", "Yok"),
                "displacement": sp500.get("displacement", "Yok"),
                "fvg": sp500.get("fvg", "Yok"),
                "pd": sp500.get("pd", "Yok"),
                "dxy_bias": "Yok",
                "killzone": get_killzone_label(),
                "ob_low": sp500.get("ob_low"),
                "ob_high": sp500.get("ob_high"),
                "reason": sp500.get("reason", "TradingView SP500 setup"),
                "index_smt": index_smt,
            }
        )

    return results


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
        f"DXY Bias: {r.get('dxy_bias', 'Yok')}",
        f"Session: {r.get('killzone', 'Yok')}",
        f"OB Zone: {ob_low} - {ob_high}",
    ]

    if "index_smt" in r:
        parts.append(f"Index SMT: {r.get('index_smt', 'Yok')}")

    parts.extend(["", f"Reason: {r.get('reason', 'Yok')}"])
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
    print(f"TradingView state file: {TRADINGVIEW_STATE_FILE}")

    if is_weekend_market_closed():
        print("Hafta sonu market kapalı. Analiz yapılmadı.")
        print(f"ELITE SNIPER SCAN END -> {now_str()} | toplam sinyal: 0")
        print("=" * 60)
        return 0

    mtf_map = build_forex_mtf_map()
    tv_state = get_tradingview_state()

    total_signals = 0

    for market in FOREX_METALS:
        name = market["name"]
        result = analyze_forex_symbol(name, mtf_map, tv_state)

        if not result:
            print(f"{name} -> setup yok")
            continue

        print(
            f"{name} -> yön: {result['direction']}, "
            f"skor: {result['score']}, kalite: {result['quality']}"
        )

        
# =========================================================
# RUN
# =========================================================
def run_scan() -> int:
    print("=" * 60)
    print(f"ELITE SNIPER SCAN START -> {now_str()}")

    print(f"TwelveData key var mı: {'evet' if bool(TWELVEDATA_API_KEY) else 'hayır'}")
    print(f"Telegram token var mı: {'evet' if bool(TELEGRAM_BOT_TOKEN) else 'hayır'}")
    print(f"Telegram chat id var mı: {'evet' if bool(TELEGRAM_CHAT_ID) else 'hayır'}")
    print(f"TradingView state file: {TRADINGVIEW_STATE_FILE}")

    if is_weekend_market_closed():
        print("Hafta sonu market kapalı. Analiz yapılmadı.")
        print(f"ELITE SNIPER SCAN END -> {now_str()} | toplam sinyal: 0")
        print("=" * 60)
        return 0

    mtf_map = build_forex_mtf_map()
    tv_state = get_tradingview_state()

    total_signals = 0

    for market in FOREX_METALS:
        name = market["name"]
        result = analyze_forex_symbol(name, mtf_map, tv_state)

        if not result:
            print(f"{name} -> setup yok")
            continue

        print(
            f"{name} -> yön: {result['direction']}, "
            f"skor: {result['score']}, kalite: {result['quality']}"
        )

        # =========================================================
        # FINAL OB ENTRY CHECK (EKLENMESİ GEREKEN)
        # =========================================================
        if result["direction"] == "SHORT":
            if result["entry"] != result["ob_high"]:
                print(f"{name} -> entry OB high ile uyuşmuyor, sinyal gönderilmedi")
                continue

        if result["direction"] == "LONG":
            if result["entry"] != result["ob_low"]:
                print(f"{name} -> entry OB low ile uyuşmuyor, sinyal gönderilmedi")
                continue

        # =========================================================
        # TELEGRAM
        # =========================================================
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

    tv_results = analyze_tradingview_indices(tv_state)

    for result in tv_results:
        name = result["symbol"]

        print(
            f"{name} -> yön: {result['direction']}, "
            f"skor: {result['score']}, kalite: {result['quality']}"
        )

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