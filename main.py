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
MIN_SIGNAL_SCORE = 68
MIN_SIGNAL_SCORE_OUTSIDE_KILLZONE = 75  # Killzone dışı sinyaller için daha yüksek eşik
CACHE_DIR = ".mtf_cache"
BOT_STATE_FILE = "bot_state.json"

FOREX_PAIRS = [
    {"name": "EUR/USD", "symbol": "EUR/USD", "group": "forex"},
    {"name": "GBP/USD", "symbol": "GBP/USD", "group": "forex"},
    {"name": "USD/JPY", "symbol": "USD/JPY", "group": "forex"},
    {"name": "AUD/USD", "symbol": "AUD/USD", "group": "forex"},
    {"name": "USD/CHF", "symbol": "USD/CHF", "group": "forex"},
    {"name": "USD/CAD", "symbol": "USD/CAD", "group": "forex"},
    {"name": "EUR/AUD", "symbol": "EUR/AUD", "group": "forex"},
]

TF_RULES = {
    "5min":  {"ttl": 300,   "outputsize": 240},
    "15min": {"ttl": 300,   "outputsize": 240},
    "1h":    {"ttl": 1800,  "outputsize": 240},
    "4h":    {"ttl": 3600,  "outputsize": 240},
    "1week": {"ttl": 86400, "outputsize": 240},
}

# =========================================================
# TIMING
# =========================================================
SIGNAL_COOLDOWN_SECONDS        = 60 * 60
MAX_ACTIVE_SIGNAL_AGE_SECONDS  = 60 * 60 * 24
MIN_SETUP_CONFIRM_CYCLES       = 1
PENDING_SETUP_MAX_AGE_SECONDS  = 60 * 60 * 6

# =========================================================
# ENTRY / EXIT FILTERS
# =========================================================
MAX_BARS_AFTER_MITIGATION          = 6
ENTRY_DISTANCE_MAX_MULTIPLIER      = 1.50
TP_PROGRESS_BLOCK_THRESHOLD        = 0.65
MIN_RR_REQUIRED                    = 1.4
MIN_TP_DISTANCE_5M_MULTIPLIER      = 1.4
MIN_SL_DISTANCE_5M_MULTIPLIER      = 0.6
MIN_REMAINING_TP_DISTANCE_MULT     = 1.2

# =========================================================
# OTE
# =========================================================
OTE_MIN_LEVEL          = 0.62
OTE_MAX_LEVEL          = 0.79
ENABLE_OTE_RETRACEMENT = True
REQUIRE_KILLZONE_FOR_OTE   = True
REQUIRE_STRUCTURE_FOR_OTE  = True

# =========================================================
# IFVG
# =========================================================
ENABLE_IFVG_RECLAIM            = True
IFVG_LOOKBACK_BARS             = 20
REQUIRE_STRUCTURE_FOR_IFVG     = True
REQUIRE_KILLZONE_OR_BIAS_IFVG  = True
IFVG_MIN_CONFLUENCE_SCORE      = 2

# =========================================================
# SNIPER OB
# =========================================================
REQUIRE_SWEEP_FOR_SNIPER         = True
REQUIRE_STRUCTURE_FOR_SNIPER     = True
REQUIRE_MIN_DISPLACEMENT_SNIPER  = True

# =========================================================
# LONDON REVERSAL  (gelişmiş)
# =========================================================
ENABLE_LONDON_REVERSAL                   = True
LONDON_OPEN_HOUR_UTC                     = 6
LONDON_CLOSE_HOUR_UTC                    = 10
LONDON_REVERSAL_LOOKBACK_5M              = 24   # 120 dakika
LONDON_ASIA_BUFFER_RATIO                 = 0.0003  # Asia H/L tolerans
REQUIRE_ASIA_SWEEP_FOR_LONDON            = True
REQUIRE_STRUCTURE_FOR_LONDON             = True
REQUIRE_DISPLACEMENT_FOR_LONDON          = True
LONDON_MIN_SWEEP_PIPS                    = 3    # min sweep büyüklüğü (pip)
LONDON_REVERSAL_CONFIRM_CANDLES          = 2    # sweep sonrası kaç bar reversal beklenir
LONDON_REVERSAL_MAX_BARS_AFTER_SWEEP     = 6    # sweep çok eskimiş olmamalı
LONDON_BIAS_HARD_BLOCK                   = True  # bias tam ters ise engelle

# =========================================================
# SMT
# =========================================================
SMT_RELATIONS: Dict[str, List[Tuple[str, str]]] = {
    "EUR/USD": [("GBP/USD", "positive"), ("USD/CHF", "inverse")],
    "GBP/USD": [("EUR/USD", "positive"), ("USD/CHF", "inverse")],
    "USD/JPY": [("USD/CHF", "positive")],
    "AUD/USD": [("EUR/AUD", "inverse"), ("USD/CAD", "inverse")],
    "USD/CHF": [("USD/JPY", "positive"), ("EUR/USD", "inverse"), ("GBP/USD", "inverse")],
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


def round_price(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    return float(f"{value:.5f}")


def pips_distance(a: float, b: float, symbol: str = "") -> float:
    """JPY çiftleri için 2 ondalık, diğerleri için 5 ondalık pip hesabı."""
    if "JPY" in symbol:
        return abs(a - b) * 100
    return abs(a - b) * 10000


# =========================================================
# TELEGRAM
# =========================================================
def send_telegram_message(text: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram env eksik, mesaj gönderilmedi.")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text}
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


# =========================================================
# SESSION / KILLZONE
# =========================================================
def get_session_name(dt: Optional[datetime]) -> str:
    if not dt:
        return "Unknown"
    h = dt.hour
    if 0 <= h < 6:
        return "Asia"
    if 6 <= h < 12:
        return "London"
    if 12 <= h < 17:
        return "New York"
    return "Off Session"


def get_killzone_label(dt: Optional[datetime] = None) -> str:
    x = dt or now_utc()
    h = x.hour
    if LONDON_OPEN_HOUR_UTC <= h < LONDON_CLOSE_HOUR_UTC:
        return "London Killzone"
    if 12 <= h < 16:
        return "New York Killzone"
    return "Killzone Dışı"


def is_killzone_active(dt: Optional[datetime] = None) -> bool:
    return get_killzone_label(dt) != "Killzone Dışı"


def is_london_session(dt: Optional[datetime] = None) -> bool:
    x = dt or now_utc()
    return LONDON_OPEN_HOUR_UTC <= x.hour < LONDON_CLOSE_HOUR_UTC


def is_weekend_market_closed(dt: Optional[datetime] = None) -> bool:
    x = dt or now_utc()
    day  = x.weekday()
    hour = x.hour
    if day == 4 and hour >= 22:
        return True
    if day == 5:
        return True
    if day == 6 and hour < 22:
        return True
    return False


# =========================================================
# FILE / CACHE
# =========================================================
def ensure_cache_dir() -> None:
    os.makedirs(CACHE_DIR, exist_ok=True)


def symbol_to_filename(symbol: str) -> str:
    return symbol.replace("/", "_").replace(":", "_").replace(" ", "_").replace(".", "_").replace("-", "_")


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
# STATE
# =========================================================
def load_bot_state() -> Dict[str, Any]:
    data = load_json_file(BOT_STATE_FILE)
    if not data:
        return {"signals": {}, "active_signals": {}, "performance": {}, "pending_setups": {}}
    for key in ("signals", "active_signals", "performance", "pending_setups"):
        if key not in data or not isinstance(data[key], dict):
            data[key] = {}
    return data


def save_bot_state(data: Dict[str, Any]) -> None:
    save_json_file(BOT_STATE_FILE, data)


def get_symbol_perf_bucket(state: Dict[str, Any], symbol: str) -> Dict[str, Any]:
    bucket = state["performance"].get(symbol)
    if not bucket:
        bucket = {"wins": 0, "losses": 0, "total": 0, "last_outcome": None, "win_rate": None}
        state["performance"][symbol] = bucket
    return bucket


def record_signal_outcome(state: Dict[str, Any], symbol: str, outcome: str) -> None:
    bucket = get_symbol_perf_bucket(state, symbol)
    if outcome == "TP":
        bucket["wins"] += 1
    elif outcome == "SL":
        bucket["losses"] += 1
    bucket["total"] = bucket["wins"] + bucket["losses"]
    bucket["last_outcome"] = outcome
    if bucket["total"] > 0:
        bucket["win_rate"] = round((bucket["wins"] / bucket["total"]) * 100, 2)
    else:
        bucket["win_rate"] = None


def get_performance_adjustment(state: Dict[str, Any], symbol: str) -> Tuple[int, List[str]]:
    bucket = get_symbol_perf_bucket(state, symbol)
    total    = bucket.get("total", 0)
    win_rate = bucket.get("win_rate")
    notes: List[str] = []
    adjustment = 0
    if total >= 8 and isinstance(win_rate, (int, float)):
        if win_rate >= 65:
            adjustment += 5
            notes.append(f"Symbol win-rate güçlü ({win_rate:.0f}%)")
        elif win_rate >= 50:
            adjustment += 2
            notes.append(f"Symbol win-rate orta ({win_rate:.0f}%)")
        elif win_rate < 35:
            adjustment -= 8
            notes.append(f"Symbol win-rate zayıf ({win_rate:.0f}%)")
    return adjustment, notes


# =========================================================
# TWELVEDATA FETCH
# =========================================================
def fetch_twelvedata_series(symbol: str, interval: str, outputsize: int) -> Optional[Dict[str, Any]]:
    if not TWELVEDATA_API_KEY:
        print("TWELVEDATA_API_KEY yok.")
        return None
    url = "https://api.twelvedata.com/time_series"
    params = {
        "apikey":     TWELVEDATA_API_KEY,
        "symbol":     symbol,
        "interval":   interval,
        "outputsize": outputsize,
        "format":     "JSON",
    }
    try:
        response = http.get(url, params=params, timeout=25)
        data = response.json()
        if response.status_code != 200:
            print(f"{symbol} {interval} HTTP hata: {response.status_code}")
            return None
        if data.get("status") == "error":
            print(f"{symbol} {interval} API hata: {data.get('message', '')}")
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
    rule       = TF_RULES[interval]
    ttl        = rule["ttl"]
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
        print(f"{symbol} {interval} -> stale cache kullanıldı")
        return fallback
    return None


# =========================================================
# CANDLES
# =========================================================
def build_candles(raw: Dict[str, Any]) -> List[Dict[str, Any]]:
    candles: List[Dict[str, Any]] = []
    for row in raw.get("values", []):
        o  = safe_float(row.get("open"))
        h  = safe_float(row.get("high"))
        l  = safe_float(row.get("low"))
        c  = safe_float(row.get("close"))
        dt = parse_candle_dt(row.get("datetime"))
        if None in (o, h, l, c):
            continue
        candles.append({"datetime": row.get("datetime"), "dt": dt, "open": o, "high": h, "low": l, "close": c})
    candles.reverse()
    return candles


def candle_body(c: Dict[str, Any]) -> float:
    return abs(c["close"] - c["open"])


def candle_range(c: Dict[str, Any]) -> float:
    return c["high"] - c["low"]


def is_bullish(c: Dict[str, Any]) -> bool:
    return c["close"] > c["open"]


def is_bearish(c: Dict[str, Any]) -> bool:
    return c["close"] < c["open"]


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
# SWINGS / PIVOTS
# =========================================================
def is_pivot_high(candles: List[Dict[str, Any]], i: int, left: int = 2, right: int = 2) -> bool:
    if i - left < 0 or i + right >= len(candles):
        return False
    current = candles[i]["high"]
    for j in range(i - left, i + right + 1):
        if j == i:
            continue
        if candles[j]["high"] >= current:
            return False
    return True


def is_pivot_low(candles: List[Dict[str, Any]], i: int, left: int = 2, right: int = 2) -> bool:
    if i - left < 0 or i + right >= len(candles):
        return False
    current = candles[i]["low"]
    for j in range(i - left, i + right + 1):
        if j == i:
            continue
        if candles[j]["low"] <= current:
            return False
    return True


def collect_swings(candles: List[Dict[str, Any]], left: int = 2, right: int = 2) -> Dict[str, List[Dict[str, Any]]]:
    highs: List[Dict[str, Any]] = []
    lows:  List[Dict[str, Any]] = []
    for i in range(len(candles)):
        if is_pivot_high(candles, i, left=left, right=right):
            highs.append({"index": i, "price": candles[i]["high"], "datetime": candles[i]["datetime"]})
        if is_pivot_low(candles, i, left=left, right=right):
            lows.append({"index": i, "price": candles[i]["low"], "datetime": candles[i]["datetime"]})
    return {"highs": highs, "lows": lows}


def last_two(items: List[Dict[str, Any]]) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    if len(items) >= 2:
        return items[-2], items[-1]
    if len(items) == 1:
        return None, items[-1]
    return None, None


def approx_equal(a: float, b: float, tolerance_ratio: float = 0.0008) -> bool:
    if a == 0 or b == 0:
        return False
    return abs(a - b) / max(abs(a), abs(b)) <= tolerance_ratio


def equal_highs_exists(candles: List[Dict[str, Any]], tolerance_ratio: float = 0.0008) -> Optional[float]:
    swings = collect_swings(candles, left=2, right=2)["highs"]
    if len(swings) < 2:
        return None
    recent = swings[-6:]
    for i in range(len(recent)):
        for j in range(i + 1, len(recent)):
            if approx_equal(recent[i]["price"], recent[j]["price"], tolerance_ratio):
                return max(recent[i]["price"], recent[j]["price"])
    return None


def equal_lows_exists(candles: List[Dict[str, Any]], tolerance_ratio: float = 0.0008) -> Optional[float]:
    swings = collect_swings(candles, left=2, right=2)["lows"]
    if len(swings) < 2:
        return None
    recent = swings[-6:]
    for i in range(len(recent)):
        for j in range(i + 1, len(recent)):
            if approx_equal(recent[i]["price"], recent[j]["price"], tolerance_ratio):
                return min(recent[i]["price"], recent[j]["price"])
    return None


# =========================================================
# CONTEXT HELPERS
# =========================================================
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
        "pdl": min(x["low"]  for x in prev_day),
    }


def get_recent_session_box(candles: List[Dict[str, Any]], session_name: str) -> Optional[Dict[str, Any]]:
    session_candles = [c for c in candles if get_session_name(c.get("dt")) == session_name]
    if len(session_candles) < 3:
        return None
    recent = session_candles[-24:] if len(session_candles) > 24 else session_candles
    return {
        "high":  max(x["high"] for x in recent),
        "low":   min(x["low"]  for x in recent),
        "start": recent[0]["datetime"],
        "end":   recent[-1]["datetime"],
    }


def get_recent_asia_range(candles_5m: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    asia = [c for c in candles_5m if get_session_name(c.get("dt")) == "Asia"]
    if len(asia) < 6:
        return None
    recent = asia[-72:] if len(asia) > 72 else asia
    return {
        "high":  max(x["high"] for x in recent),
        "low":   min(x["low"]  for x in recent),
        "start": recent[0]["datetime"],
        "end":   recent[-1]["datetime"],
        "count": len(recent),
    }


# =========================================================
# STRUCTURE
# =========================================================
def detect_displacement(candles: List[Dict[str, Any]]) -> str:
    if len(candles) < 12:
        return "Yok"
    last        = candles[-1]
    avg_body_   = average_body(candles, 10)
    avg_range_  = average_range(candles, 10)
    if avg_body_ == 0 or avg_range_ == 0:
        return "Yok"
    body_ratio  = candle_body(last) / avg_body_
    range_ratio = candle_range(last) / avg_range_
    if body_ratio >= 2.0 and range_ratio >= 1.5:
        return "Güçlü"
    if body_ratio >= 1.4 and range_ratio >= 1.2:
        return "Orta"
    return "Zayıf"


def detect_htf_bias(candles: List[Dict[str, Any]]) -> str:
    swings = collect_swings(candles, left=3, right=3)
    h1, h2 = last_two(swings["highs"])
    l1, l2 = last_two(swings["lows"])
    if not h2 or not l2:
        return "Nötr"
    if h1 and l1 and h2["price"] > h1["price"] and l2["price"] > l1["price"]:
        return "Yükseliş"
    if h1 and l1 and h2["price"] < h1["price"] and l2["price"] < l1["price"]:
        return "Düşüş"
    return "Nötr"


def detect_mss_choch(candles: List[Dict[str, Any]]) -> Tuple[str, str]:
    swings = collect_swings(candles, left=2, right=2)
    h1, h2 = last_two(swings["highs"])
    l1, l2 = last_two(swings["lows"])
    if not h2 or not l2:
        return "Yok", "Yok"
    last_close = candles[-1]["close"]
    mss   = "Yok"
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


def detect_liquidity_sweep(candles: List[Dict[str, Any]]) -> str:
    if len(candles) < 8:
        return "Yok"
    last      = candles[-1]
    recent    = candles[-8:-1]
    prev_high = max(x["high"] for x in recent)
    prev_low  = min(x["low"]  for x in recent)
    if last["high"] > prev_high and last["close"] < prev_high:
        return "Üst likidite sweep"
    if last["low"] < prev_low and last["close"] > prev_low:
        return "Alt likidite sweep"
    return "Yok"


def detect_fvg(candles: List[Dict[str, Any]]) -> str:
    if len(candles) < 3:
        return "Yok"
    a, _, c = candles[-3], candles[-2], candles[-1]
    if c["low"] > a["high"]:
        return "Bullish FVG"
    if c["high"] < a["low"]:
        return "Bearish FVG"
    return "Yok"


def detect_pd(candles: List[Dict[str, Any]]) -> str:
    if len(candles) < 20:
        return "Nötr"
    recent = candles[-20:]
    high_  = max(x["high"] for x in recent)
    low_   = min(x["low"]  for x in recent)
    mid    = (high_ + low_) / 2
    price  = candles[-1]["close"]
    if price > mid:
        return "Premium"
    if price < mid:
        return "Discount"
    return "Nötr"


# =========================================================
# FVG ZONE
# =========================================================
def find_recent_fvg_zone(candles: List[Dict[str, Any]], lookback: int = IFVG_LOOKBACK_BARS) -> Optional[Dict[str, Any]]:
    if len(candles) < 3:
        return None
    start = max(2, len(candles) - lookback)
    for i in range(len(candles) - 1, start - 1, -1):
        a = candles[i - 2]
        c = candles[i]
        if c["low"] > a["high"]:
            return {"type": "bullish", "low": a["high"], "high": c["low"], "index": i, "datetime": c["datetime"]}
        if c["high"] < a["low"]:
            return {"type": "bearish", "low": c["high"], "high": a["low"], "index": i, "datetime": c["datetime"]}
    return None


def detect_ifvg_reclaim(candles_15m: List[Dict[str, Any]]) -> Dict[str, Any]:
    if len(candles_15m) < 8:
        return {"valid": False, "direction": "YOK", "reason": "15m veri yetersiz"}
    zone = find_recent_fvg_zone(candles_15m, IFVG_LOOKBACK_BARS)
    if not zone:
        return {"valid": False, "direction": "YOK", "reason": "Yakın FVG bulunamadı"}
    last = candles_15m[-1]
    if zone["type"] == "bearish" and last["close"] > zone["high"]:
        return {"valid": True, "direction": "LONG",  "reason": "Bearish FVG reclaim edildi",      "zone_low": zone["low"], "zone_high": zone["high"]}
    if zone["type"] == "bullish" and last["close"] < zone["low"]:
        return {"valid": True, "direction": "SHORT", "reason": "Bullish FVG breakdown/reclaim oldu", "zone_low": zone["low"], "zone_high": zone["high"]}
    return {"valid": False, "direction": "YOK", "reason": "IFVG reclaim yok"}


def score_ifvg_confluence(
    direction: str, bias: str, killzone_active: bool,
    displacement: str, mss: str, choch: str,
) -> int:
    score = 0
    if direction == "LONG"  and bias == "Yükseliş": score += 1
    if direction == "SHORT" and bias == "Düşüş":    score += 1
    if killzone_active:                              score += 1
    if displacement in ("Orta", "Güçlü"):            score += 1
    if direction == "LONG"  and (mss == "Bullish MSS" or choch == "Bullish CHoCH"): score += 1
    if direction == "SHORT" and (mss == "Bearish MSS" or choch == "Bearish CHoCH"): score += 1
    return score


# =========================================================
# OTE
# =========================================================
def get_recent_dealing_range(candles: List[Dict[str, Any]], lookback: int = 20) -> Optional[Dict[str, float]]:
    if len(candles) < lookback:
        return None
    recent = candles[-lookback:]
    high_  = max(x["high"] for x in recent)
    low_   = min(x["low"]  for x in recent)
    if high_ <= low_:
        return None
    return {"high": high_, "low": low_}


def get_ote_zone(range_high: float, range_low: float, direction: str) -> Dict[str, float]:
    full_range = range_high - range_low
    if direction == "LONG":
        ote_high = range_high - (full_range * OTE_MIN_LEVEL)
        ote_low  = range_high - (full_range * OTE_MAX_LEVEL)
        return {"low": min(ote_low, ote_high), "high": max(ote_low, ote_high)}
    if direction == "SHORT":
        ote_low  = range_low + (full_range * OTE_MIN_LEVEL)
        ote_high = range_low + (full_range * OTE_MAX_LEVEL)
        return {"low": min(ote_low, ote_high), "high": max(ote_low, ote_high)}
    return {"low": 0.0, "high": 0.0}


def detect_ote_retracement(candles_15m: List[Dict[str, Any]], direction: str) -> Dict[str, Any]:
    if len(candles_15m) < 20:
        return {"valid": False, "reason": "OTE için veri yetersiz"}
    dr = get_recent_dealing_range(candles_15m, 20)
    if not dr:
        return {"valid": False, "reason": "Dealing range bulunamadı"}
    zone  = get_ote_zone(dr["high"], dr["low"], direction)
    price = candles_15m[-1]["close"]
    if zone["low"] <= price <= zone["high"]:
        return {"valid": True, "reason": "Fiyat OTE bölgesinde", "zone_low": zone["low"], "zone_high": zone["high"]}
    return {"valid": False, "reason": "Fiyat OTE bölgesinde değil", "zone_low": zone["low"], "zone_high": zone["high"]}


# =========================================================
# ORDER BLOCK
# =========================================================
def detect_order_block(candles: List[Dict[str, Any]], direction: str) -> Optional[Dict[str, Any]]:
    if len(candles) < 14:
        return None
    avg_rng = average_range(candles[-12:], 12)
    if avg_rng == 0:
        return None
    lookback_start = max(0, len(candles) - 15)
    lookback_end   = len(candles) - 2
    for i in range(lookback_end, lookback_start - 1, -1):
        c      = candles[i]
        future = candles[i + 1:]
        if direction == "LONG" and is_bearish(c):
            broke = any(x["close"] > c["high"] for x in future)
            if not broke:
                continue
            disp_candle = max(future, key=lambda x: candle_body(x), default=None)
            if disp_candle and candle_body(disp_candle) < avg_rng * 0.35:
                continue
            return {"index": i, "datetime": c["datetime"], "low": c["low"], "high": c["high"]}
        if direction == "SHORT" and is_bullish(c):
            broke = any(x["close"] < c["low"] for x in future)
            if not broke:
                continue
            disp_candle = max(future, key=lambda x: candle_body(x), default=None)
            if disp_candle and candle_body(disp_candle) < avg_rng * 0.35:
                continue
            return {"index": i, "datetime": c["datetime"], "low": c["low"], "high": c["high"]}
    return None


# =========================================================
# MITIGATION / ENTRY TIMING
# =========================================================
def find_latest_ob_touch_index(candles_5m: List[Dict[str, Any]], ob: Dict[str, Any]) -> Optional[int]:
    for i in range(len(candles_5m) - 1, -1, -1):
        c = candles_5m[i]
        if c["low"] <= ob["high"] and c["high"] >= ob["low"]:
            return i
    return None


def is_rejection_candle(candle: Dict[str, Any], direction: str) -> bool:
    body  = candle_body(candle)
    rng   = candle_range(candle)
    if rng == 0:
        return False
    upper_wick   = candle["high"] - max(candle["open"], candle["close"])
    lower_wick   = min(candle["open"], candle["close"]) - candle["low"]
    body_ratio   = body / rng
    if direction == "LONG":
        bullish_close    = candle["close"] > candle["open"]
        lower_rejection  = lower_wick >= body * 1.2 if body > 0 else False
        return bullish_close and (lower_rejection or body_ratio >= 0.25)
    if direction == "SHORT":
        bearish_close   = candle["close"] < candle["open"]
        upper_rejection = upper_wick >= body * 1.2 if body > 0 else False
        return bearish_close and (upper_rejection or body_ratio >= 0.25)
    return False


def assess_mitigation_quality(candles_5m: List[Dict[str, Any]], ob: Dict[str, Any], direction: str) -> Dict[str, Any]:
    if len(candles_5m) < 6:
        return {"valid": False, "reason": "5m veri yetersiz", "touch_index": None, "bars_after_touch": None, "quality": "Yok"}
    touch_index = find_latest_ob_touch_index(candles_5m, ob)
    if touch_index is None:
        return {"valid": False, "reason": "OB touch yok", "touch_index": None, "bars_after_touch": None, "quality": "Yok"}
    bars_after_touch = len(candles_5m) - 1 - touch_index
    last = candles_5m[-1]
    if bars_after_touch > MAX_BARS_AFTER_MITIGATION:
        return {"valid": False, "reason": "Mitigation eski kaldı", "touch_index": touch_index, "bars_after_touch": bars_after_touch, "quality": "Eski"}
    if not is_rejection_candle(last, direction):
        return {"valid": False, "reason": "Rejection confirmation yok", "touch_index": touch_index, "bars_after_touch": bars_after_touch, "quality": "Zayıf"}
    quality = "Güçlü" if bars_after_touch <= 1 else "Orta"
    return {"valid": True, "reason": "Taze mitigation + rejection onayı", "touch_index": touch_index, "bars_after_touch": bars_after_touch, "quality": quality}


def validate_entry_timing(
    current_price: float, entry: float, tp: float, sl: float,
    direction: str, candles_5m: List[Dict[str, Any]], touch_index: Optional[int],
) -> Dict[str, Any]:
    avg_rng = average_range(candles_5m[-10:], 10)
    if avg_rng == 0:
        return {"valid": False, "reason": "Average range sıfır"}
    entry_distance = abs(current_price - entry)
    if entry_distance > (avg_rng * ENTRY_DISTANCE_MAX_MULTIPLIER):
        return {"valid": False, "reason": "Fiyat entry'den fazla uzaklaştı", "entry_distance": entry_distance}
    total_path = abs(tp - entry)
    progressed = abs(current_price - entry)
    if total_path > 0 and (progressed / total_path) >= TP_PROGRESS_BLOCK_THRESHOLD:
        return {"valid": False, "reason": "Fiyat TP yolunda fazla ilerledi", "entry_distance": entry_distance}
    if touch_index is not None:
        bars_after_touch = len(candles_5m) - 1 - touch_index
        if bars_after_touch > MAX_BARS_AFTER_MITIGATION:
            return {"valid": False, "reason": "OB touch sonrası geç kalındı", "entry_distance": entry_distance}
    if direction == "LONG"  and current_price < sl:
        return {"valid": False, "reason": "Current price SL altında"}
    if direction == "SHORT" and current_price > sl:
        return {"valid": False, "reason": "Current price SL üstünde"}
    return {"valid": True, "reason": "Entry timing uygun", "entry_distance": entry_distance}


def validate_absolute_trade_distances(
    candles_5m: List[Dict[str, Any]], entry: float, sl: float, tp: float, current_price: float,
) -> Dict[str, Any]:
    avg_rng_5m = average_range(candles_5m[-12:], 12)
    if avg_rng_5m <= 0:
        return {"valid": False, "reason": "5m average range sıfır"}
    tp_distance         = abs(tp - entry)
    sl_distance         = abs(entry - sl)
    remaining_tp        = abs(tp - current_price)
    if tp_distance < (avg_rng_5m * MIN_TP_DISTANCE_5M_MULTIPLIER):
        return {"valid": False, "reason": "TP mesafesi çok kısa", "tp_distance": tp_distance, "sl_distance": sl_distance, "remaining_tp_distance": remaining_tp}
    if sl_distance < (avg_rng_5m * MIN_SL_DISTANCE_5M_MULTIPLIER):
        return {"valid": False, "reason": "SL mesafesi çok kısa", "tp_distance": tp_distance, "sl_distance": sl_distance, "remaining_tp_distance": remaining_tp}
    if remaining_tp < (avg_rng_5m * MIN_REMAINING_TP_DISTANCE_MULT):
        return {"valid": False, "reason": "Fiyat hedefe fazla yaklaşmış", "tp_distance": tp_distance, "sl_distance": sl_distance, "remaining_tp_distance": remaining_tp}
    return {"valid": True, "reason": "Mutlak mesafeler uygun", "tp_distance": tp_distance, "sl_distance": sl_distance, "remaining_tp_distance": remaining_tp}


# =========================================================
# SMT
# =========================================================
def sweep_flags(candles: List[Dict[str, Any]]) -> Dict[str, bool]:
    if len(candles) < 8:
        return {"high_sweep": False, "low_sweep": False}
    last      = candles[-1]
    recent    = candles[-8:-1]
    prev_high = max(x["high"] for x in recent)
    prev_low  = min(x["low"]  for x in recent)
    return {
        "high_sweep": last["high"] > prev_high and last["close"] < prev_high,
        "low_sweep":  last["low"]  < prev_low  and last["close"] > prev_low,
    }


def detect_forex_smt(market_name: str, mtf_map: Dict[str, Dict[str, List[Dict[str, Any]]]]) -> str:
    relations = SMT_RELATIONS.get(market_name, [])
    if not relations:
        return "Yok"
    target_15m = mtf_map.get(market_name, {}).get("15min", [])
    if len(target_15m) < 8:
        return "Yok"
    target_flags  = sweep_flags(target_15m)
    bullish_votes = 0
    bearish_votes = 0
    for other_name, relation_type in relations:
        other_15m = mtf_map.get(other_name, {}).get("15min", [])
        if len(other_15m) < 8:
            continue
        other_flags = sweep_flags(other_15m)
        if relation_type == "positive":
            if target_flags["low_sweep"]  and not other_flags["low_sweep"]:  bullish_votes += 1
            if target_flags["high_sweep"] and not other_flags["high_sweep"]: bearish_votes += 1
        else:
            if target_flags["low_sweep"]  and not other_flags["high_sweep"]: bullish_votes += 1
            if target_flags["high_sweep"] and not other_flags["low_sweep"]:  bearish_votes += 1
    if bullish_votes > bearish_votes and bullish_votes > 0:
        return "Bullish SMT"
    if bearish_votes > bullish_votes and bearish_votes > 0:
        return "Bearish SMT"
    return "Yok"


# =========================================================
# SIGNAL DIRECTION — PRIMARY SNIPER
# =========================================================
def determine_direction(sweep: str, mss: str, choch: str, displacement: str) -> str:
    if sweep == "Alt likidite sweep" and (mss == "Bullish MSS" or choch == "Bullish CHoCH") and displacement in ("Orta", "Güçlü"):
        return "LONG"
    if sweep == "Üst likidite sweep" and (mss == "Bearish MSS" or choch == "Bearish CHoCH") and displacement in ("Orta", "Güçlü"):
        return "SHORT"
    return "YOK"


def validate_sniper_model(direction: str, sweep: str, mss: str, choch: str, displacement: str) -> Dict[str, Any]:
    if direction == "YOK":
        return {"valid": False, "reason": "Sniper yön oluşmadı"}
    if REQUIRE_SWEEP_FOR_SNIPER and sweep == "Yok":
        return {"valid": False, "reason": "Sniper için sweep yok"}
    if REQUIRE_STRUCTURE_FOR_SNIPER and mss == "Yok" and choch == "Yok":
        return {"valid": False, "reason": "Sniper için MSS/CHoCH yok"}
    if REQUIRE_MIN_DISPLACEMENT_SNIPER and displacement == "Zayıf":
        return {"valid": False, "reason": "Sniper displacement zayıf"}
    return {"valid": True, "reason": "Sniper şartları uygun"}


# =========================================================
# LONDON REVERSAL — GELİŞMİŞ MODEL
# =========================================================
def find_sweep_candle_index(
    candles: List[Dict[str, Any]],
    level: float,
    direction: str,
    lookback: int,
) -> Optional[int]:
    """
    Asia high/low sweep yapan mumu bul.
    direction='SHORT' -> high sweep (yukarı geçip geri döndü)
    direction='LONG'  -> low  sweep (aşağı geçip geri döndü)
    """
    start = max(0, len(candles) - lookback)
    for i in range(len(candles) - 1, start - 1, -1):
        c = candles[i]
        if direction == "SHORT" and c["high"] > level and c["close"] < level:
            return i
        if direction == "LONG"  and c["low"]  < level and c["close"] > level:
            return i
    return None


def detect_london_reversal_structure(
    candles_5m: List[Dict[str, Any]],
    sweep_index: int,
    direction: str,
) -> Dict[str, Any]:
    """
    Sweep sonrası yapıyı kontrol et:
    - MSS veya CHoCH var mı?
    - Displacement var mı?
    - Kaç bar geçti?
    """
    if sweep_index is None:
        return {"valid": False, "reason": "Sweep index yok"}
    post_sweep = candles_5m[sweep_index:]
    if len(post_sweep) < 2:
        return {"valid": False, "reason": "Sweep sonrası veri yok"}
    bars_after_sweep = len(candles_5m) - 1 - sweep_index
    if bars_after_sweep > LONDON_REVERSAL_MAX_BARS_AFTER_SWEEP:
        return {"valid": False, "reason": f"Sweep çok eski ({bars_after_sweep} bar)"}
    mss_post, choch_post = detect_mss_choch(post_sweep)
    disp_post = detect_displacement(post_sweep)
    has_structure = False
    if direction == "SHORT" and (mss_post == "Bearish MSS" or choch_post == "Bearish CHoCH"):
        has_structure = True
    if direction == "LONG"  and (mss_post == "Bullish MSS" or choch_post == "Bullish CHoCH"):
        has_structure = True
    if REQUIRE_STRUCTURE_FOR_LONDON and not has_structure:
        return {"valid": False, "reason": "London reversal yapı teyidi yok", "bars_after_sweep": bars_after_sweep}
    if REQUIRE_DISPLACEMENT_FOR_LONDON and disp_post == "Zayıf":
        return {"valid": False, "reason": "London reversal displacement zayıf", "bars_after_sweep": bars_after_sweep}
    return {
        "valid":            True,
        "reason":           "London reversal yapı onaylandı",
        "bars_after_sweep": bars_after_sweep,
        "mss":              mss_post,
        "choch":            choch_post,
        "displacement":     disp_post,
    }


def detect_london_reversal(
    candles_5m:  List[Dict[str, Any]],
    candles_15m: List[Dict[str, Any]],
    bias:        str,
) -> Dict[str, Any]:
    if not ENABLE_LONDON_REVERSAL:
        return {"valid": False, "direction": "YOK", "reason": "London Reversal kapalı"}
    if len(candles_5m) < 30 or len(candles_15m) < 20:
        return {"valid": False, "direction": "YOK", "reason": "London Reversal için veri yetersiz"}
    if not is_london_session():
        return {"valid": False, "direction": "YOK", "reason": "London session aktif değil"}

    asia_box = get_recent_asia_range(candles_5m)
    if not asia_box:
        return {"valid": False, "direction": "YOK", "reason": "Asia range bulunamadı"}

    asia_high = asia_box["high"]
    asia_low  = asia_box["low"]
    asia_size = asia_high - asia_low

    # Asia range çok küçükse geçersiz (konsolidasyon değil)
    avg_rng = average_range(candles_5m[-20:], 20)
    if asia_size < avg_rng * 0.5:
        return {"valid": False, "direction": "YOK", "reason": "Asia range çok dar"}

    # Büyük resim bias kontrolü
    if LONDON_BIAS_HARD_BLOCK:
        if bias == "Yükseliş" and asia_high > 0:
            pass  # SHORT setup olabilir ama bias bloğu aşağıda kontrol ediliyor
        if bias == "Düşüş" and asia_low > 0:
            pass

    result_short: Optional[Dict[str, Any]] = None
    result_long:  Optional[Dict[str, Any]] = None

    # --- SHORT SETUP: Asia High sweep ---
    sweep_idx_short = find_sweep_candle_index(
        candles_5m, asia_high, "SHORT", LONDON_REVERSAL_LOOKBACK_5M
    )
    if sweep_idx_short is not None:
        sweep_candle = candles_5m[sweep_idx_short]
        sweep_size   = sweep_candle["high"] - asia_high
        # Minimum sweep büyüklüğü kontrolü
        if sweep_size >= (avg_rng * 0.3):
            if LONDON_BIAS_HARD_BLOCK and bias == "Yükseliş":
                pass  # Yükseliş bias'ta short engellendi
            else:
                struct = detect_london_reversal_structure(candles_5m, sweep_idx_short, "SHORT")
                if struct["valid"]:
                    result_short = {
                        "valid":            True,
                        "direction":        "SHORT",
                        "reason":           f"Asia high ({asia_high:.5f}) sweep sonrası London bearish reversal",
                        "sweep_level":      asia_high,
                        "sweep_size":       sweep_size,
                        "bars_after_sweep": struct.get("bars_after_sweep"),
                        "post_mss":         struct.get("mss", "Yok"),
                        "post_choch":       struct.get("choch", "Yok"),
                        "post_displacement":struct.get("displacement", "Zayıf"),
                    }

    # --- LONG SETUP: Asia Low sweep ---
    sweep_idx_long = find_sweep_candle_index(
        candles_5m, asia_low, "LONG", LONDON_REVERSAL_LOOKBACK_5M
    )
    if sweep_idx_long is not None:
        sweep_candle = candles_5m[sweep_idx_long]
        sweep_size   = asia_low - sweep_candle["low"]
        if sweep_size >= (avg_rng * 0.3):
            if LONDON_BIAS_HARD_BLOCK and bias == "Düşüş":
                pass  # Düşüş bias'ta long engellendi
            else:
                struct = detect_london_reversal_structure(candles_5m, sweep_idx_long, "LONG")
                if struct["valid"]:
                    result_long = {
                        "valid":            True,
                        "direction":        "LONG",
                        "reason":           f"Asia low ({asia_low:.5f}) sweep sonrası London bullish reversal",
                        "sweep_level":      asia_low,
                        "sweep_size":       sweep_size,
                        "bars_after_sweep": struct.get("bars_after_sweep"),
                        "post_mss":         struct.get("mss", "Yok"),
                        "post_choch":       struct.get("choch", "Yok"),
                        "post_displacement":struct.get("displacement", "Zayıf"),
                    }

    # En taze sweep'i seç
    if result_short and result_long:
        # İkisi de varsa bars_after_sweep'i daha küçük olanı seç
        if (result_short.get("bars_after_sweep") or 99) <= (result_long.get("bars_after_sweep") or 99):
            return result_short
        return result_long
    if result_short:
        return result_short
    if result_long:
        return result_long

    return {"valid": False, "direction": "YOK", "reason": "London reversal koşulu oluşmadı"}


# =========================================================
# ALTERNATIVE MODELS
# =========================================================
def detect_alternative_model(
    candles_15m: List[Dict[str, Any]],
    candles_5m:  List[Dict[str, Any]],
    bias:        str,
    displacement:str,
    mss:         str,
    choch:       str,
) -> Dict[str, Any]:
    if len(candles_15m) < 20 or len(candles_5m) < 10:
        return {"valid": False, "model": "YOK", "direction": "YOK", "reason": "Veri yetersiz"}

    # MODEL 1: London Reversal (öncelikli)
    london = detect_london_reversal(candles_5m=candles_5m, candles_15m=candles_15m, bias=bias)
    if london["valid"]:
        return {
            "valid":     True,
            "model":     "LONDON_REVERSAL",
            "direction": london["direction"],
            "reason":    london["reason"],
            "extra":     london,
        }

    # MODEL 2: IFVG Reclaim
    if ENABLE_IFVG_RECLAIM:
        ifvg = detect_ifvg_reclaim(candles_15m)
        if ifvg["valid"]:
            direction = ifvg["direction"]
            confluence = score_ifvg_confluence(
                direction=direction, bias=bias,
                killzone_active=is_killzone_active(),
                displacement=displacement, mss=mss, choch=choch,
            )
            if REQUIRE_STRUCTURE_FOR_IFVG:
                structure_ok = (
                    (direction == "LONG"  and (mss == "Bullish MSS" or choch == "Bullish CHoCH")) or
                    (direction == "SHORT" and (mss == "Bearish MSS" or choch == "Bearish CHoCH"))
                )
                if structure_ok and confluence >= IFVG_MIN_CONFLUENCE_SCORE:
                    return {
                        "valid":      True,
                        "model":      "IFVG_RECLAIM",
                        "direction":  direction,
                        "reason":     f"{ifvg['reason']} | confluence={confluence}",
                        "zone_low":   round_price(ifvg["zone_low"]),
                        "zone_high":  round_price(ifvg["zone_high"]),
                    }
            else:
                if confluence >= IFVG_MIN_CONFLUENCE_SCORE:
                    return {
                        "valid":      True,
                        "model":      "IFVG_RECLAIM",
                        "direction":  direction,
                        "reason":     f"{ifvg['reason']} | confluence={confluence}",
                        "zone_low":   round_price(ifvg["zone_low"]),
                        "zone_high":  round_price(ifvg["zone_high"]),
                    }

    # MODEL 3: OTE Retracement
    if ENABLE_OTE_RETRACEMENT and bias in ("Yükseliş", "Düşüş"):
        direction = "LONG" if bias == "Yükseliş" else "SHORT"
        ote = detect_ote_retracement(candles_15m, direction)
        if ote["valid"] and displacement in ("Orta", "Güçlü"):
            return {
                "valid":     True,
                "model":     "OTE_RETRACEMENT",
                "direction": direction,
                "reason":    ote["reason"],
                "zone_low":  round_price(ote["zone_low"]),
                "zone_high": round_price(ote["zone_high"]),
            }

    return {"valid": False, "model": "YOK", "direction": "YOK", "reason": "Alternatif model yok"}


# =========================================================
# SL / TP
# =========================================================
def build_structure_sl(candles_15m: List[Dict[str, Any]], ob: Dict[str, Any], direction: str) -> Optional[float]:
    avg_rng = average_range(candles_15m[-10:], 10)
    if avg_rng == 0:
        return None
    recent     = candles_15m[-8:]
    sweep_low  = min(x["low"]  for x in recent)
    sweep_high = max(x["high"] for x in recent)
    buffer_val = avg_rng * 0.15
    if direction == "LONG":
        anchor = min(ob["low"], sweep_low)
        return anchor - buffer_val
    if direction == "SHORT":
        anchor = max(ob["high"], sweep_high)
        return anchor + buffer_val
    return None


def find_directional_target(
    candles_15m: List[Dict[str, Any]],
    candles_5m:  List[Dict[str, Any]],
    direction:   str,
    entry:       float,
) -> Optional[float]:
    swings      = collect_swings(candles_15m, left=2, right=2)
    pd_levels   = get_previous_day_levels(candles_5m)
    session_nm  = "London" if get_session_name(candles_5m[-1].get("dt")) == "London" else "New York"
    session_box = get_recent_session_box(candles_5m, session_nm)
    candidates: List[float] = []
    if direction == "LONG":
        eqh = equal_highs_exists(candles_15m)
        candidates.extend([x["price"] for x in swings["highs"] if x["price"] > entry])
        if eqh and eqh > entry:                                      candidates.append(eqh)
        if pd_levels["pdh"] and pd_levels["pdh"] > entry:           candidates.append(pd_levels["pdh"])
        if session_box and session_box["high"] > entry:              candidates.append(session_box["high"])
        return min(candidates) if candidates else None
    if direction == "SHORT":
        eql = equal_lows_exists(candles_15m)
        candidates.extend([x["price"] for x in swings["lows"] if x["price"] < entry])
        if eql and eql < entry:                                      candidates.append(eql)
        if pd_levels["pdl"] and pd_levels["pdl"] < entry:           candidates.append(pd_levels["pdl"])
        if session_box and session_box["low"] < entry:               candidates.append(session_box["low"])
        return max(candidates) if candidates else None
    return None


def build_trade_levels(
    candles_15m: List[Dict[str, Any]],
    candles_5m:  List[Dict[str, Any]],
    direction:   str,
    ob:          Dict[str, Any],
) -> Dict[str, Optional[float]]:
    entry = ob["low"] if direction == "LONG" else ob["high"]
    sl    = build_structure_sl(candles_15m, ob, direction)
    if sl is None:
        return {"entry": None, "sl": None, "tp": None, "rr": None}
    risk = abs(entry - sl)
    if risk <= 0:
        return {"entry": None, "sl": None, "tp": None, "rr": None}
    structure_target = find_directional_target(candles_15m, candles_5m, direction, entry)
    min_rr_tp = (entry + risk * 2.0) if direction == "LONG" else (entry - risk * 2.0)
    if structure_target is None:
        tp = min_rr_tp
    else:
        tp = max(structure_target, min_rr_tp) if direction == "LONG" else min(structure_target, min_rr_tp)
    rr = abs(tp - entry) / risk if risk > 0 else None
    return {
        "entry": round_price(entry),
        "sl":    round_price(sl),
        "tp":    round_price(tp),
        "rr":    round(rr, 2) if rr is not None else None,
    }


# =========================================================
# READY SIGNAL CHECK
# =========================================================
def is_ready_trade_signal(result: Dict[str, Any], pending_info: Dict[str, Any]) -> Dict[str, Any]:
    if int(pending_info.get("seen_count", 0)) < MIN_SETUP_CONFIRM_CYCLES:
        return {"valid": False, "reason": "Setup henüz yeterince olgunlaşmadı"}
    setup_model = result.get("setup_model", "YOK")
    killzone    = result.get("killzone", "Killzone Dışı")
    mss         = result.get("mss", "Yok")
    choch       = result.get("choch", "Yok")
    displacement= result.get("displacement", "Yok")
    if setup_model == "OTE_RETRACEMENT":
        if REQUIRE_KILLZONE_FOR_OTE and killzone == "Killzone Dışı":
            return {"valid": False, "reason": "OTE killzone dışı"}
        if REQUIRE_STRUCTURE_FOR_OTE:
            has_structure = mss != "Yok" or choch != "Yok" or displacement == "Güçlü"
            if not has_structure:
                return {"valid": False, "reason": "OTE için yapı teyidi zayıf"}
    return {"valid": True, "reason": "Ready signal"}


# =========================================================
# SCORING
# =========================================================
def score_signal(
    direction:        str,
    bias:             str,
    h4_bias:          str,
    w1_bias:          str,
    fvg:              str,
    pd:               str,
    smt:              str,
    killzone_active:  bool,
    displacement:     str,
    mitigation_quality: str,
    rr:               Optional[float],
    mss:              str,
    choch:            str,
    perf_adjustment:  int,
    perf_notes:       List[str],
    setup_model:      str,
    london_extra:     Optional[Dict[str, Any]] = None,
) -> Tuple[int, str, List[str]]:
    score = 40
    notes: List[str] = []

    # --- Bias alignment ---
    if direction == "LONG":
        if bias    == "Yükseliş": score += 14; notes.append("Ana bias uyumlu")
        if h4_bias == "Yükseliş": score += 8;  notes.append("4H bias destekliyor")
        if w1_bias == "Yükseliş": score += 5;  notes.append("1W bias destekliyor")
        if fvg     == "Bullish FVG": score += 8; notes.append("Bullish FVG var")
        if pd      == "Discount":    score += 8; notes.append("Discount bölgesinde")
        if smt     == "Bullish SMT": score += 6; notes.append("Bullish SMT var")
        if mss     == "Bullish MSS": score += 8; notes.append("Bullish MSS var")
        elif choch == "Bullish CHoCH": score += 5; notes.append("Bullish CHoCH var")
    if direction == "SHORT":
        if bias    == "Düşüş":    score += 14; notes.append("Ana bias uyumlu")
        if h4_bias == "Düşüş":    score += 8;  notes.append("4H bias destekliyor")
        if w1_bias == "Düşüş":    score += 5;  notes.append("1W bias destekliyor")
        if fvg     == "Bearish FVG": score += 8; notes.append("Bearish FVG var")
        if pd      == "Premium":     score += 8; notes.append("Premium bölgesinde")
        if smt     == "Bearish SMT": score += 6; notes.append("Bearish SMT var")
        if mss     == "Bearish MSS": score += 8; notes.append("Bearish MSS var")
        elif choch == "Bearish CHoCH": score += 5; notes.append("Bearish CHoCH var")

    # --- Bias conflict ---
    if bias == "Yükseliş" and h4_bias == "Düşüş":
        score -= 8; notes.append("1H/4H conflict")
    if bias == "Düşüş" and h4_bias == "Yükseliş":
        score -= 8; notes.append("1H/4H conflict")

    # --- Session ---
    if killzone_active:
        score += 8; notes.append("Killzone aktif")

    # --- Displacement ---
    if displacement == "Güçlü":
        score += 8; notes.append("Güçlü displacement")
    elif displacement == "Orta":
        score += 4; notes.append("Orta displacement")
    else:
        score -= 8; notes.append("Displacement zayıf")

    # --- Mitigation ---
    if mitigation_quality == "Güçlü":
        score += 8; notes.append("Taze mitigation")
    elif mitigation_quality == "Orta":
        score += 4; notes.append("Mitigation orta kalite")
    else:
        score -= 8; notes.append("Mitigation zayıf")

    # --- RR ---
    if rr is not None:
        if rr >= 3.0:   score += 10; notes.append("RR mükemmel (3+)")
        elif rr >= 2.5: score += 8;  notes.append("RR güçlü")
        elif rr >= 2.0: score += 5;  notes.append("RR yeterli")
        elif rr < MIN_RR_REQUIRED: score -= 12; notes.append("RR zayıf")

    # --- Model bonus ---
    if setup_model == "LONDON_REVERSAL":
        score += 6; notes.append("London reversal modeli")
        if london_extra:
            if london_extra.get("post_displacement") in ("Orta", "Güçlü"):
                score += 4; notes.append("London post-sweep displacement güçlü")
            if london_extra.get("post_mss") not in ("Yok", None):
                score += 3; notes.append("London post-sweep MSS var")
    elif setup_model == "IFVG_RECLAIM":
        score += 2; notes.append("IFVG reclaim modeli")
    elif setup_model == "SNIPER_OB":
        score += 3; notes.append("Sniper OB modeli")

    # --- Performance ---
    score += perf_adjustment
    notes.extend(perf_notes)

    quality = "Yok"
    if score >= 88:
        quality = "A+"
    elif score >= 68:
        quality = "A"

    return score, quality, notes


# =========================================================
# SIGNAL KEY / STATE
# =========================================================
def build_signal_key(result: Dict[str, Any]) -> str:
    symbol = result["symbol"]
    direction = result["direction"]
    entry = f"{result['entry']:.5f}" if isinstance(result.get("entry"), (int, float)) else "na"
    sl    = f"{result['sl']:.5f}"    if isinstance(result.get("sl"),    (int, float)) else "na"
    tp    = f"{result['tp']:.5f}"    if isinstance(result.get("tp"),    (int, float)) else "na"
    return f"{symbol}|{direction}|{entry}|{sl}|{tp}"


def should_skip_repeated_signal(result: Dict[str, Any], state: Dict[str, Any]) -> bool:
    key    = build_signal_key(result)
    record = state["signals"].get(key)
    if not record:
        return False
    last_sent = record.get("last_sent_ts")
    if not isinstance(last_sent, int):
        return False
    return (now_ts() - last_sent) < SIGNAL_COOLDOWN_SECONDS


def mark_signal_sent(result: Dict[str, Any], state: Dict[str, Any]) -> None:
    key = build_signal_key(result)
    state["signals"][key] = {
        "last_sent_ts": now_ts(), "symbol": result["symbol"],
        "direction": result["direction"], "entry": result["entry"],
        "sl": result["sl"], "tp": result["tp"],
    }
    state["active_signals"][key] = {
        "created_ts": now_ts(), "symbol": result["symbol"],
        "direction": result["direction"], "entry": result["entry"],
        "sl": result["sl"], "tp": result["tp"], "status": "OPEN",
    }


def build_pending_setup_key(result: Dict[str, Any]) -> str:
    symbol   = result["symbol"]
    direction= result["direction"]
    model    = result.get("setup_model", "YOK")
    ob_low   = f"{result['ob_low']:.5f}"  if isinstance(result.get("ob_low"),  (int, float)) else "na"
    ob_high  = f"{result['ob_high']:.5f}" if isinstance(result.get("ob_high"), (int, float)) else "na"
    return f"{symbol}|{direction}|{model}|{ob_low}|{ob_high}"


def update_pending_setup(state: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
    key    = build_pending_setup_key(result)
    bucket = state["pending_setups"].get(key)
    if not bucket:
        bucket = {
            "first_seen_ts": now_ts(), "last_seen_ts": now_ts(), "seen_count": 1,
            "symbol": result["symbol"], "direction": result["direction"],
            "setup_model": result.get("setup_model", "YOK"),
        }
        state["pending_setups"][key] = bucket
        return bucket
    bucket["last_seen_ts"] = now_ts()
    bucket["seen_count"]   = int(bucket.get("seen_count", 0)) + 1
    return bucket


def cleanup_pending_setups(state: Dict[str, Any]) -> None:
    pending = state.get("pending_setups", {})
    if not isinstance(pending, dict):
        return
    keys_to_delete = [
        k for k, v in pending.items()
        if not isinstance(v.get("last_seen_ts"), int)
        or (now_ts() - v["last_seen_ts"]) > PENDING_SETUP_MAX_AGE_SECONDS
    ]
    for k in keys_to_delete:
        pending.pop(k, None)


def update_active_signals_outcomes(state: Dict[str, Any], mtf_map: Dict[str, Dict[str, List[Dict[str, Any]]]]) -> None:
    active = state.get("active_signals", {})
    if not isinstance(active, dict) or not active:
        return
    keys_to_delete: List[str] = []
    for key, record in active.items():
        if record.get("status") != "OPEN":
            keys_to_delete.append(key); continue
        symbol     = record.get("symbol")
        created_ts = record.get("created_ts")
        direction  = record.get("direction")
        sl         = safe_float(record.get("sl"))
        tp         = safe_float(record.get("tp"))
        if not symbol or not isinstance(created_ts, int) or sl is None or tp is None:
            keys_to_delete.append(key); continue
        if (now_ts() - created_ts) > MAX_ACTIVE_SIGNAL_AGE_SECONDS:
            keys_to_delete.append(key); continue
        candles_5m = mtf_map.get(symbol, {}).get("5min", [])
        if not candles_5m:
            continue
        relevant = [c for c in candles_5m if c.get("dt") and int(c["dt"].timestamp()) >= created_ts]
        if not relevant:
            continue
        outcome = None
        if direction == "LONG":
            for c in relevant:
                if c["low"]  <= sl: outcome = "SL"; break
                if c["high"] >= tp: outcome = "TP"; break
        elif direction == "SHORT":
            for c in relevant:
                if c["high"] >= sl: outcome = "SL"; break
                if c["low"]  <= tp: outcome = "TP"; break
        if outcome:
            record_signal_outcome(state, symbol, outcome)
            keys_to_delete.append(key)
    for k in keys_to_delete:
        state["active_signals"].pop(k, None)


# =========================================================
# DATA PREP
# =========================================================
def build_forex_mtf_map() -> Dict[str, Dict[str, List[Dict[str, Any]]]]:
    mtf_map: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
    for market in FOREX_PAIRS:
        name   = market["name"]
        symbol = market["symbol"]
        mtf_map[name] = {}
        for interval in ("5min", "15min", "1h", "4h", "1week"):
            raw = get_series_with_policy(symbol, interval)
            mtf_map[name][interval] = build_candles(raw) if raw else []
    return mtf_map


# =========================================================
# ANALYZE
# =========================================================
def analyze_forex_symbol(
    market_name: str,
    mtf_map:     Dict[str, Dict[str, List[Dict[str, Any]]]],
    state:       Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    tfs = mtf_map.get(market_name, {})
    candles_5m  = tfs.get("5min",  [])
    candles_15m = tfs.get("15min", [])
    candles_1h  = tfs.get("1h",    [])
    candles_4h  = tfs.get("4h",    [])
    candles_1w  = tfs.get("1week", [])

    if len(candles_5m) < 20 or len(candles_15m) < 30 or len(candles_1h) < 20 or len(candles_4h) < 10 or len(candles_1w) < 5:
        return None

    current_price = candles_5m[-1]["close"]

    h1_bias = detect_htf_bias(candles_1h)
    h4_bias = detect_htf_bias(candles_4h)
    w1_bias = detect_htf_bias(candles_1w)
    bias    = h1_bias if h1_bias != "Nötr" else (h4_bias if h4_bias != "Nötr" else w1_bias)

    mss, choch   = detect_mss_choch(candles_15m)
    sweep        = detect_liquidity_sweep(candles_15m)
    displacement = detect_displacement(candles_15m)
    fvg          = detect_fvg(candles_15m)
    pd           = detect_pd(candles_15m)

    # --- Primary Sniper ---
    primary_direction = determine_direction(sweep, mss, choch, displacement)
    setup_model       = "SNIPER_OB"
    direction         = primary_direction
    alt_model_info    = None
    london_extra: Optional[Dict[str, Any]] = None

    if direction != "YOK":
        sniper_check = validate_sniper_model(direction, sweep, mss, choch, displacement)
        if not sniper_check["valid"]:
            print(f"{market_name} -> sniper filtresinden kaldı: {sniper_check['reason']}")
            direction = "YOK"

    # --- Alternative Models ---
    if direction == "YOK":
        alt_model_info = detect_alternative_model(
            candles_15m=candles_15m,
            candles_5m=candles_5m,
            bias=bias,
            displacement=displacement,
            mss=mss,
            choch=choch,
        )
        if alt_model_info["valid"]:
            direction   = alt_model_info["direction"]
            setup_model = alt_model_info["model"]
            if setup_model == "LONDON_REVERSAL":
                london_extra = alt_model_info.get("extra")

    if direction == "YOK":
        return None

    # --- Order Block ---
    ob = detect_order_block(candles_15m, direction)
    if not ob:
        print(f"{market_name} -> OB bulunamadı")
        return None

    # --- Levels ---
    levels = build_trade_levels(candles_15m, candles_5m, direction, ob)
    if levels["entry"] is None or levels["sl"] is None or levels["tp"] is None:
        return None
    if levels["rr"] is None or levels["rr"] < MIN_RR_REQUIRED:
        print(f"{market_name} -> RR filtresinden kaldı ({levels['rr']})")
        return None

    # --- Mitigation ---
    mitigation = assess_mitigation_quality(candles_5m, ob, direction)
    if not mitigation["valid"]:
        print(f"{market_name} -> mitigation filtresinden kaldı: {mitigation['reason']}")
        return None

    # --- Entry Timing ---
    timing = validate_entry_timing(
        current_price=current_price, entry=levels["entry"], tp=levels["tp"],
        sl=levels["sl"], direction=direction, candles_5m=candles_5m, touch_index=mitigation["touch_index"],
    )
    if not timing["valid"]:
        print(f"{market_name} -> entry timing filtresinden kaldı: {timing['reason']}")
        return None

    # --- Distance Check ---
    distance_check = validate_absolute_trade_distances(
        candles_5m=candles_5m, entry=levels["entry"], sl=levels["sl"],
        tp=levels["tp"], current_price=current_price,
    )
    if not distance_check["valid"]:
        print(f"{market_name} -> mesafe filtresinden kaldı: {distance_check['reason']}")
        return None

    # --- SMT + Performance ---
    smt = detect_forex_smt(market_name, mtf_map)
    perf_adjustment, perf_notes = get_performance_adjustment(state, market_name)

    # --- Score ---
    score, quality, score_notes = score_signal(
        direction=direction, bias=bias, h4_bias=h4_bias, w1_bias=w1_bias,
        fvg=fvg, pd=pd, smt=smt, killzone_active=is_killzone_active(),
        displacement=displacement, mitigation_quality=mitigation["quality"],
        rr=levels["rr"], mss=mss, choch=choch,
        perf_adjustment=perf_adjustment, perf_notes=perf_notes,
        setup_model=setup_model, london_extra=london_extra,
    )

    # --- Trade Management Levels ---
    risk       = abs(levels["entry"] - levels["sl"])
    partial_tp = round_price(levels["entry"] + risk)       if direction == "LONG" else round_price(levels["entry"] - risk)
    tp1        = levels["tp"]   # Structure hedefi (2R+)
    tp2        = round_price(levels["entry"] + risk * 3.0) if direction == "LONG" else round_price(levels["entry"] - risk * 3.0)
    be_trigger   = partial_tp
    invalidation = levels["sl"]
    entry_distance = timing.get("entry_distance")

    reason_map = {
        "SNIPER_OB":       "Sniper OB entry aktif",
        "LONDON_REVERSAL": "London reversal setup hazır",
        "IFVG_RECLAIM":    "IFVG reclaim onaylandı",
        "OTE_RETRACEMENT": "OTE retracement hazır",
    }

    return {
        "symbol":         market_name,
        "price":          round_price(current_price),
        "direction":      direction,
        "entry":          levels["entry"],
        "sl":             levels["sl"],
        "tp":             tp1,
        "tp2":            tp2,
        "rr":             levels["rr"],
        "score":          score,
        "quality":        quality,
        "bias":           bias,
        "h1_bias":        h1_bias,
        "h4_bias":        h4_bias,
        "w1_bias":        w1_bias,
        "sweep":          sweep,
        "mss":            mss,
        "choch":          choch,
        "displacement":   displacement,
        "fvg":            fvg,
        "pd":             pd,
        "smt":            smt,
        "killzone":       get_killzone_label(),
        "ob_low":         round_price(ob["low"]),
        "ob_high":        round_price(ob["high"]),
        "ob_time":        ob["datetime"],
        "mitigation_quality": mitigation["quality"],
        "mitigation_reason":  mitigation["reason"],
        "bars_after_touch":   mitigation["bars_after_touch"],
        "entry_distance": round_price(entry_distance) if isinstance(entry_distance, float) else entry_distance,
        "tp_distance":    round_price(distance_check["tp_distance"])  if isinstance(distance_check.get("tp_distance"),  float) else distance_check.get("tp_distance"),
        "sl_distance":    round_price(distance_check["sl_distance"])  if isinstance(distance_check.get("sl_distance"),  float) else distance_check.get("sl_distance"),
        "remaining_tp":   round_price(distance_check["remaining_tp_distance"]) if isinstance(distance_check.get("remaining_tp_distance"), float) else distance_check.get("remaining_tp_distance"),
        "partial_tp":     partial_tp,
        "be_trigger":     be_trigger,
        "invalidation":   invalidation,
        "score_notes":    score_notes,
        "setup_model":    setup_model,
        "model_reason":   alt_model_info["reason"] if alt_model_info else "Primary sniper model",
        "london_extra":   london_extra,
        "reason":         reason_map.get(setup_model, "Setup aktif") if quality in ("A", "A+") else "Setup var ama kalite filtresi yetersiz",
    }


# =========================================================
# MESSAGE FORMAT
# =========================================================
def fmt(value: Any, decimals: int = 5) -> str:
    if isinstance(value, (int, float)):
        return f"{value:.{decimals}f}"
    return str(value) if value is not None else "Yok"


def format_signal_message(r: Dict[str, Any]) -> str:
    london_lines = ""
    le = r.get("london_extra")
    if le and r.get("setup_model") == "LONDON_REVERSAL":
        london_lines = (
            f"\nLondon Sweep Level: {fmt(le.get('sweep_level'))}"
            f"\nPost-Sweep MSS: {le.get('post_mss', 'Yok')}"
            f"\nPost-Sweep CHoCH: {le.get('post_choch', 'Yok')}"
            f"\nPost-Sweep Displacement: {le.get('post_displacement', 'Yok')}"
            f"\nBars After Sweep: {le.get('bars_after_sweep', 'Yok')}"
        )

    score_notes = ", ".join(r.get("score_notes", [])[:6]) if isinstance(r.get("score_notes"), list) else "Yok"

    parts = [
        "🔥 ELITE SNIPER ICT SIGNAL",
        "",
        f"📌 {r['symbol']} | {r['direction']}",
        "",
        f"💰 Price:  {fmt(r.get('price'))}",
        f"🎯 Entry:  {fmt(r.get('entry'))}",
        f"🛑 SL:     {fmt(r.get('sl'))}",
        f"✅ TP1:    {fmt(r.get('tp'))}",
        f"🚀 TP2:    {fmt(r.get('tp2'))}",
        f"📊 RR:     {r.get('rr', 'Yok')}",
        "",
        f"⭐ Score: {r['score']} ({r['quality']})",
        "",
        "─── CONTEXT ───",
        f"Bias:        {r.get('bias', 'Yok')}",
        f"1H Bias:     {r.get('h1_bias', 'Yok')}",
        f"4H Bias:     {r.get('h4_bias', 'Yok')}",
        f"1W Bias:     {r.get('w1_bias', 'Yok')}",
        f"Sweep:       {r.get('sweep', 'Yok')}",
        f"MSS:         {r.get('mss', 'Yok')}",
        f"CHoCH:       {r.get('choch', 'Yok')}",
        f"Displacement:{r.get('displacement', 'Yok')}",
        f"FVG:         {r.get('fvg', 'Yok')}",
        f"PD:          {r.get('pd', 'Yok')}",
        f"SMT:         {r.get('smt', 'Yok')}",
        f"Session:     {r.get('killzone', 'Yok')}",
        "",
        "─── MODEL ───",
        f"Setup Model: {r.get('setup_model', 'YOK')}",
        f"Model Reason:{r.get('model_reason', 'Yok')}",
        london_lines,
        "",
        "─── ENTRY ───",
        f"OB Zone:          {fmt(r.get('ob_low'))} - {fmt(r.get('ob_high'))}",
        f"Mitigation:       {r.get('mitigation_quality', 'Yok')}",
        f"Bars After Touch: {r.get('bars_after_touch', 'Yok')}",
        f"Distance to Entry:{fmt(r.get('entry_distance'))}",
        f"TP Distance:      {fmt(r.get('tp_distance'))}",
        f"SL Distance:      {fmt(r.get('sl_distance'))}",
        f"Remaining TP:     {fmt(r.get('remaining_tp'))}",
        "",
        "─── MANAGEMENT ───",
        f"Partial TP (1R):  {fmt(r.get('partial_tp'))}",
        f"Move to BE after: {fmt(r.get('be_trigger'))}",
        f"Full TP (2R+):    {fmt(r.get('tp'))}",
        f"Invalidation:     {fmt(r.get('invalidation'))}",
        "",
        f"📝 Notes: {score_notes}",
        f"📌 Reason: {r.get('reason', 'Yok')}",
    ]
    return "\n".join(p for p in parts)


# =========================================================
# RUN
# =========================================================
def run_scan() -> int:
    print("=" * 60)
    print(f"ELITE SNIPER SCAN START -> {now_str()}")
    print(f"TwelveData key: {'✓' if TWELVEDATA_API_KEY else '✗'}")
    print(f"Telegram token: {'✓' if TELEGRAM_BOT_TOKEN else '✗'}")
    print(f"Telegram chat:  {'✓' if TELEGRAM_CHAT_ID  else '✗'}")

    if is_weekend_market_closed():
        print("Hafta sonu market kapalı.")
        print(f"ELITE SNIPER SCAN END -> {now_str()} | sinyal: 0")
        print("=" * 60)
        return 0

    mtf_map = build_forex_mtf_map()
    state   = load_bot_state()

    update_active_signals_outcomes(state, mtf_map)
    cleanup_pending_setups(state)

    total_signals = 0

    for market in FOREX_PAIRS:
        name   = market["name"]
        result = analyze_forex_symbol(name, mtf_map, state)

        if not result:
            print(f"{name} -> setup yok")
            continue

        print(
            f"{name} -> yön:{result['direction']} skor:{result['score']} "
            f"kalite:{result['quality']} rr:{result.get('rr')} model:{result.get('setup_model')}"
        )

        # Killzone dışı sinyaller için daha yüksek skor eşiği
        killzone_active_now = result.get("killzone", "Killzone Dışı") != "Killzone Dışı"
        min_score_required  = MIN_SIGNAL_SCORE if killzone_active_now else MIN_SIGNAL_SCORE_OUTSIDE_KILLZONE

        if result["score"] < min_score_required or result["quality"] not in ("A", "A+"):
            print(f"{name} -> kalite filtresini geçemedi (skor:{result['score']}, esik:{min_score_required}, killzone:{killzone_active_now})")
            continue

        pending_info = update_pending_setup(state, result)
        ready_check  = is_ready_trade_signal(result, pending_info)
        if not ready_check["valid"]:
            print(f"{name} -> beklemede: {ready_check['reason']}")
            continue

        if should_skip_repeated_signal(result, state):
            print(f"{name} -> cooldown içinde, tekrar gönderilmedi")
            continue

        msg  = format_signal_message(result)
        sent = send_telegram_message(msg)

        if sent:
            total_signals += 1
            mark_signal_sent(result, state)
            print(f"{name} -> ✅ SINYAL GÖNDERİLDİ")
        else:
            print(f"{name} -> ❌ sinyal gönderilemedi")

    save_bot_state(state)
    print(f"ELITE SNIPER SCAN END -> {now_str()} | toplam sinyal: {total_signals}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(run_scan())
