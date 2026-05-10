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
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID",   "").strip()
TWELVEDATA_API_KEY = os.getenv("TWELVEDATA_API_KEY",  "").strip()

# =========================================================
# SETTINGS
# =========================================================
MIN_SIGNAL_SCORE                  = 80   # Backtest: 68→80 daha iyi WR
MIN_SIGNAL_SCORE_OUTSIDE_KILLZONE = 82   # Killzone dışı daha sıkı
CACHE_DIR      = ".mtf_cache"
BOT_STATE_FILE = "bot_state.json"

# Sinyal üreten pariteler (EUR/USD sadece SMT için, EUR/JPY tamamen çıktı)
SIGNAL_PAIRS = [
    {"name": "GBP/USD", "symbol": "GBP/USD", "group": "forex"},
    {"name": "USD/JPY", "symbol": "USD/JPY", "group": "forex"},
    {"name": "AUD/USD", "symbol": "AUD/USD", "group": "forex"},
    {"name": "USD/CAD", "symbol": "USD/CAD", "group": "forex"},
    {"name": "USD/CHF", "symbol": "USD/CHF", "group": "forex"},
    {"name": "GBP/JPY", "symbol": "GBP/JPY", "group": "forex"},
    {"name": "EUR/AUD", "symbol": "EUR/AUD", "group": "forex"},
    {"name": "USD/NZD", "symbol": "NZD/USD", "group": "forex"},  # Ters sembol TwelveData'da
    {"name": "GBP/AUD", "symbol": "GBP/AUD", "group": "forex"},
]

# SMT için kullanılan pariteler (sinyal üretmez, sadece korelasyon)
SMT_ONLY_PAIRS = [
    {"name": "EUR/USD", "symbol": "EUR/USD", "group": "forex"},
]

# Tüm pariteler (veri çekmek için)
FOREX_PAIRS = SIGNAL_PAIRS + SMT_ONLY_PAIRS

TF_RULES = {
    "5min":  {"ttl": 180,   "outputsize": 240},
    "15min": {"ttl": 180,   "outputsize": 240},
    "1h":    {"ttl": 900,   "outputsize": 240},
    "4h":    {"ttl": 1800,  "outputsize": 240},
    "1week": {"ttl": 86400, "outputsize": 240},
}

# =========================================================
# TIMING
# =========================================================
SIGNAL_COOLDOWN_SECONDS       = 60 * 60
MAX_ACTIVE_SIGNAL_AGE_SECONDS = 60 * 60 * 24
MIN_SETUP_CONFIRM_CYCLES      = 1
PENDING_SETUP_MAX_AGE_SECONDS = 60 * 60 * 6

# =========================================================
# ENTRY / EXIT FILTERS
# =========================================================
MAX_BARS_AFTER_MITIGATION           = 5
ENTRY_DISTANCE_MAX_MULTIPLIER       = 1.20
TP_PROGRESS_BLOCK_THRESHOLD         = 0.65
MIN_RR_REQUIRED                     = 1.4
MIN_TP_DISTANCE_5M_MULTIPLIER       = 1.4
MIN_SL_DISTANCE_5M_MULTIPLIER       = 0.6
MIN_REMAINING_TP_DISTANCE_5M_MULTIPLIER = 1.2

# =========================================================
# OTE
# =========================================================
OTE_MIN_LEVEL         = 0.62
OTE_MAX_LEVEL         = 0.79
ENABLE_OTE_RETRACEMENT = True
REQUIRE_KILLZONE_FOR_OTE  = True
REQUIRE_STRUCTURE_FOR_OTE = True

# =========================================================
# IFVG
# =========================================================
ENABLE_IFVG_RECLAIM           = True
IFVG_LOOKBACK_BARS            = 20
REQUIRE_STRUCTURE_FOR_IFVG    = True
IFVG_USE_KILLZONE_BIAS_CONFLUENCE = True
IFVG_MIN_CONFLUENCE_SCORE     = 2

# =========================================================
# SNIPER OB
# =========================================================
REQUIRE_SWEEP_FOR_SNIPER        = True
REQUIRE_STRUCTURE_FOR_SNIPER    = True
REQUIRE_MIN_DISPLACEMENT_SNIPER = True

# =========================================================
# LONDON REVERSAL — KAPATILDI (backtest: %30 WR)
# =========================================================
ENABLE_LONDON_REVERSAL = False

# =========================================================
# NY OPEN DISPLACEMENT + MSS (Yeni Model 1)
# =========================================================
ENABLE_NY_OPEN_DISPLACEMENT    = True
NY_OPEN_HOUR_UTC               = 12   # NY açılışı
NY_OPEN_WINDOW_MINUTES         = 90   # İlk 90 dakika
NY_OPEN_MIN_DISPLACEMENT       = "Orta"  # "Orta" veya "Güçlü"
NY_OPEN_REQUIRE_MSS            = True
NY_OPEN_REQUIRE_HTF_BIAS       = True  # HTF bias zorunlu

# =========================================================
# BREAKER BLOCK (Yeni Model 2)
# =========================================================
ENABLE_BREAKER_BLOCK           = True
BREAKER_LOOKBACK_BARS          = 30
BREAKER_REQUIRE_SWEEP          = True
BREAKER_REQUIRE_STRUCTURE      = True
BREAKER_MIN_CONFLUENCE         = 2

# =========================================================
# INVERSION FVG (Yeni Model 3)
# =========================================================
ENABLE_INVERSION_FVG           = True
IFVG_INV_LOOKBACK_BARS         = 25
IFVG_INV_REQUIRE_STRUCTURE     = True
IFVG_INV_MIN_CONFLUENCE        = 2

# =========================================================
# SMT RELATIONS — Güncellenmiş (EUR/JPY çıkarıldı)
# =========================================================
SMT_RELATIONS: Dict[str, List[Tuple[str, str]]] = {
    "GBP/USD": [
        ("EUR/USD", "positive"),
        ("USD/CHF", "inverse"),
        ("GBP/JPY", "positive"),
        ("GBP/AUD", "positive"),
    ],
    "USD/JPY": [
        ("USD/CHF", "positive"),
        ("GBP/JPY", "positive"),
        ("EUR/USD", "inverse"),
    ],
    "AUD/USD": [
        ("EUR/AUD", "inverse"),
        ("USD/CAD", "inverse"),
        ("GBP/AUD", "inverse"),
    ],
    "USD/CHF": [
        ("USD/JPY", "positive"),
        ("EUR/USD", "inverse"),
        ("GBP/USD", "inverse"),
    ],
    "USD/CAD": [
        ("AUD/USD", "inverse"),
        ("GBP/USD", "inverse"),
    ],
    "GBP/JPY": [
        ("GBP/USD", "positive"),
        ("USD/JPY", "positive"),
    ],
    "EUR/AUD": [
        ("AUD/USD", "inverse"),
        ("GBP/AUD", "inverse"),
    ],
    "USD/NZD": [
        ("AUD/USD", "inverse"),
        ("USD/CAD", "positive"),
    ],
    "GBP/AUD": [
        ("GBP/USD", "positive"),
        ("AUD/USD", "inverse"),
        ("EUR/AUD", "positive"),
    ],
    # Sadece SMT referansı
    "EUR/USD": [
        ("GBP/USD", "positive"),
        ("USD/CHF", "inverse"),
    ],
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

def is_jpy_pair(symbol: str) -> bool:
    return "JPY" in symbol.upper()

def price_decimals(symbol: str) -> int:
    return 3 if is_jpy_pair(symbol) else 5

def pip_multiplier(symbol: str) -> float:
    return 0.01 if is_jpy_pair(symbol) else 0.0001

def to_pips(price_diff: Any, symbol: str) -> str:
    if not isinstance(price_diff, (int, float)):
        return "Yok"
    pip = pip_multiplier(symbol)
    return f"{price_diff / pip:.1f} pip"

def round_price(value: Optional[float], symbol: str = "") -> Optional[float]:
    if value is None:
        return None
    return float(f"{value:.{price_decimals(symbol)}f}")

def fmt_val(value: Any, symbol: str = "") -> str:
    """Fiyat formatlama — JPY: 3 ondalık, diğer: 5 ondalık."""
    if isinstance(value, (int, float)):
        return f"{value:.{price_decimals(symbol)}f}"
    return str(value) if value is not None else "Yok"

fmt_price = fmt_val

def fmt(value: Any, decimals: int = 2) -> str:
    """Genel sayı formatlama (fiyat dışı)."""
    if isinstance(value, (int, float)):
        return f"{value:.{decimals}f}"
    return str(value) if value is not None else "Yok"

def pips_distance(a: float, b: float, symbol: str = "") -> float:
    if "JPY" in symbol:
        return abs(a - b) * 100
    return abs(a - b) * 10000


# =========================================================
# TELEGRAM
# =========================================================
def send_telegram_message(text: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram env eksik.")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        response = http.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text}, timeout=20)
        if response.status_code != 200:
            print(f"Telegram hata: {response.text}")
            return False
        return True
    except Exception as e:
        print(f"Telegram hatası: {e}")
        return False


# =========================================================
# SESSION / KILLZONE
# =========================================================
def get_session_name(dt: Optional[datetime]) -> str:
    if not dt:
        return "Unknown"
    h = dt.hour
    if 0  <= h < 6:  return "Asia"
    if 6  <= h < 12: return "London"
    if 12 <= h < 17: return "New York"
    return "Off Session"

def get_killzone_label(dt: Optional[datetime] = None) -> str:
    x = dt or now_utc()
    h = x.hour
    if 6  <= h < 10: return "London Killzone"
    if 12 <= h < 16: return "New York Killzone"
    return "Killzone Dışı"

def is_killzone_active(dt: Optional[datetime] = None) -> bool:
    return get_killzone_label(dt) != "Killzone Dışı"

def is_ny_open_window(dt: Optional[datetime] = None) -> bool:
    """NY açılış penceresi: 12:00-13:30 UTC"""
    x = dt or now_utc()
    h, m = x.hour, x.minute
    total_min = h * 60 + m
    ny_start  = NY_OPEN_HOUR_UTC * 60
    ny_end    = ny_start + NY_OPEN_WINDOW_MINUTES
    return ny_start <= total_min < ny_end

def is_weekend_market_closed(dt: Optional[datetime] = None) -> bool:
    x = dt or now_utc()
    day, hour = x.weekday(), x.hour
    if day == 4 and hour >= 22: return True
    if day == 5:                return True
    if day == 6 and hour < 22:  return True
    return False


# =========================================================
# FILE / CACHE
# =========================================================
def ensure_cache_dir() -> None:
    os.makedirs(CACHE_DIR, exist_ok=True)

def symbol_to_filename(symbol: str) -> str:
    return symbol.replace("/","_").replace(":","_").replace(" ","_").replace(".","_").replace("-","_")

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
    if outcome == "TP": bucket["wins"] += 1
    elif outcome == "SL": bucket["losses"] += 1
    bucket["total"] = bucket["wins"] + bucket["losses"]
    bucket["last_outcome"] = outcome
    if bucket["total"] > 0:
        bucket["win_rate"] = round((bucket["wins"] / bucket["total"]) * 100, 2)

def get_performance_adjustment(state: Dict[str, Any], symbol: str) -> Tuple[int, List[str]]:
    bucket = get_symbol_perf_bucket(state, symbol)
    total    = bucket.get("total", 0)
    win_rate = bucket.get("win_rate")
    notes: List[str] = []
    adjustment = 0
    if total >= 8 and isinstance(win_rate, (int, float)):
        if win_rate >= 65:
            adjustment += 5;  notes.append(f"Symbol win-rate güçlü ({win_rate:.0f}%)")
        elif win_rate >= 50:
            adjustment += 2;  notes.append(f"Symbol win-rate orta ({win_rate:.0f}%)")
        elif win_rate < 35:
            adjustment -= 8;  notes.append(f"Symbol win-rate zayıf ({win_rate:.0f}%)")
    return adjustment, notes


# =========================================================
# TWELVEDATA FETCH
# =========================================================
def fetch_twelvedata_series(symbol: str, interval: str, outputsize: int) -> Optional[Dict[str, Any]]:
    if not TWELVEDATA_API_KEY:
        print("TWELVEDATA_API_KEY yok.")
        return None
    # USD/NZD → NZD/USD dönüşümü
    api_symbol = "NZD/USD" if symbol == "USD/NZD" else symbol
    url = "https://api.twelvedata.com/time_series"
    params = {"apikey": TWELVEDATA_API_KEY, "symbol": api_symbol,
              "interval": interval, "outputsize": outputsize, "format": "JSON"}
    try:
        response = http.get(url, params=params, timeout=25)
        data = response.json()
        if response.status_code != 200 or data.get("status") == "error":
            print(f"{symbol} {interval} API hata: {data.get('message','')}")
            return None
        if "values" not in data or not data["values"]:
            return None
        data["_fetched_at"] = now_ts()
        # USD/NZD için fiyatları ters çevir
        if symbol == "USD/NZD":
            for v in data["values"]:
                for k in ("open","high","low","close"):
                    try:
                        v[k] = str(round(1.0 / float(v[k]), 5))
                    except Exception:
                        pass
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
        candles.append({"datetime": row.get("datetime"), "dt": dt,
                        "open": o, "high": h, "low": l, "close": c})
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
    return sum(candle_body(x) for x in sample) / len(sample) if sample else 0.0

def average_range(candles: List[Dict[str, Any]], count: int = 10) -> float:
    sample = candles[-count:] if len(candles) >= count else candles
    return sum(candle_range(x) for x in sample) / len(sample) if sample else 0.0


# =========================================================
# SWINGS / PIVOTS
# =========================================================
def is_pivot_high(candles: List[Dict[str, Any]], i: int, left: int = 2, right: int = 2) -> bool:
    if i - left < 0 or i + right >= len(candles):
        return False
    current = candles[i]["high"]
    return all(candles[j]["high"] < current for j in range(i-left, i+right+1) if j != i)

def is_pivot_low(candles: List[Dict[str, Any]], i: int, left: int = 2, right: int = 2) -> bool:
    if i - left < 0 or i + right >= len(candles):
        return False
    current = candles[i]["low"]
    return all(candles[j]["low"] > current for j in range(i-left, i+right+1) if j != i)

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
    if len(items) >= 2: return items[-2], items[-1]
    if len(items) == 1: return None, items[-1]
    return None, None

def approx_equal(a: float, b: float, tolerance_ratio: float = 0.0008) -> bool:
    if a == 0 or b == 0: return False
    return abs(a - b) / max(abs(a), abs(b)) <= tolerance_ratio

def equal_highs_exists(candles: List[Dict[str, Any]], tolerance_ratio: float = 0.0008) -> Optional[float]:
    swings = collect_swings(candles, left=2, right=2)["highs"]
    if len(swings) < 2: return None
    recent = swings[-6:]
    for i in range(len(recent)):
        for j in range(i + 1, len(recent)):
            if approx_equal(recent[i]["price"], recent[j]["price"], tolerance_ratio):
                return max(recent[i]["price"], recent[j]["price"])
    return None

def equal_lows_exists(candles: List[Dict[str, Any]], tolerance_ratio: float = 0.0008) -> Optional[float]:
    swings = collect_swings(candles, left=2, right=2)["lows"]
    if len(swings) < 2: return None
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
        if not dt: continue
        key = dt.strftime("%Y-%m-%d")
        day_groups.setdefault(key, []).append(c)
    all_days = sorted(day_groups.keys())
    if len(all_days) < 2:
        return {"pdh": None, "pdl": None}
    prev_day = day_groups[all_days[-2]]
    return {"pdh": max(x["high"] for x in prev_day),
            "pdl": min(x["low"]  for x in prev_day)}

def get_recent_session_box(candles: List[Dict[str, Any]], session_name: str) -> Optional[Dict[str, Any]]:
    session_candles = [c for c in candles if get_session_name(c.get("dt")) == session_name]
    if len(session_candles) < 3: return None
    recent = session_candles[-24:] if len(session_candles) > 24 else session_candles
    return {"high": max(x["high"] for x in recent), "low": min(x["low"] for x in recent)}

def get_recent_asia_range(candles_5m: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    asia = [c for c in candles_5m if get_session_name(c.get("dt")) == "Asia"]
    if len(asia) < 6: return None
    recent = asia[-72:] if len(asia) > 72 else asia
    return {"high": max(x["high"] for x in recent), "low": min(x["low"] for x in recent)}


# =========================================================
# STRUCTURE DETECTION
# =========================================================
def detect_displacement(candles: List[Dict[str, Any]]) -> str:
    if len(candles) < 12: return "Yok"
    last       = candles[-1]
    avg_body_  = average_body(candles, 10)
    avg_range_ = average_range(candles, 10)
    if avg_body_ == 0 or avg_range_ == 0: return "Yok"
    body_ratio  = candle_body(last) / avg_body_
    range_ratio = candle_range(last) / avg_range_
    if body_ratio >= 2.0 and range_ratio >= 1.5: return "Güçlü"
    if body_ratio >= 1.4 and range_ratio >= 1.2: return "Orta"
    return "Zayıf"

def detect_htf_bias(candles: List[Dict[str, Any]]) -> str:
    swings = collect_swings(candles, left=3, right=3)
    h1, h2 = last_two(swings["highs"])
    l1, l2 = last_two(swings["lows"])
    if not h2 or not l2: return "Nötr"
    if h1 and l1 and h2["price"] > h1["price"] and l2["price"] > l1["price"]: return "Yükseliş"
    if h1 and l1 and h2["price"] < h1["price"] and l2["price"] < l1["price"]: return "Düşüş"
    return "Nötr"

def detect_mss_choch(candles: List[Dict[str, Any]]) -> Tuple[str, str]:
    swings = collect_swings(candles, left=2, right=2)
    h1, h2 = last_two(swings["highs"])
    l1, l2 = last_two(swings["lows"])
    if not h2 or not l2: return "Yok", "Yok"
    last_close = candles[-1]["close"]
    mss   = "Bullish MSS"  if last_close > h2["price"] else ("Bearish MSS"  if last_close < l2["price"] else "Yok")
    choch = "Bullish CHoCH" if h1 and last_close > h1["price"] else ("Bearish CHoCH" if l1 and last_close < l1["price"] else "Yok")
    return mss, choch

def detect_liquidity_sweep(candles: List[Dict[str, Any]]) -> str:
    if len(candles) < 8: return "Yok"
    last      = candles[-1]
    recent    = candles[-8:-1]
    prev_high = max(x["high"] for x in recent)
    prev_low  = min(x["low"]  for x in recent)
    if last["high"] > prev_high and last["close"] < prev_high: return "Üst likidite sweep"
    if last["low"]  < prev_low  and last["close"] > prev_low:  return "Alt likidite sweep"
    return "Yok"

def detect_fvg(candles: List[Dict[str, Any]]) -> str:
    if len(candles) < 3: return "Yok"
    a, _, c = candles[-3], candles[-2], candles[-1]
    if c["low"]  > a["high"]: return "Bullish FVG"
    if c["high"] < a["low"]:  return "Bearish FVG"
    return "Yok"

def detect_pd(candles: List[Dict[str, Any]]) -> str:
    if len(candles) < 20: return "Nötr"
    recent = candles[-20:]
    mid    = (max(x["high"] for x in recent) + min(x["low"] for x in recent)) / 2
    price  = candles[-1]["close"]
    if price > mid: return "Premium"
    if price < mid: return "Discount"
    return "Nötr"


# =========================================================
# FVG ZONE
# =========================================================
def find_recent_fvg_zone(candles: List[Dict[str, Any]], lookback: int = 20) -> Optional[Dict[str, Any]]:
    if len(candles) < 3: return None
    start = max(2, len(candles) - lookback)
    for i in range(len(candles) - 1, start - 1, -1):
        a = candles[i - 2]
        c = candles[i]
        if c["low"]  > a["high"]: return {"type": "bullish", "low": a["high"], "high": c["low"]}
        if c["high"] < a["low"]:  return {"type": "bearish", "low": c["high"], "high": a["low"]}
    return None

def detect_ifvg_reclaim(candles_15m: List[Dict[str, Any]]) -> Dict[str, Any]:
    if len(candles_15m) < 8:
        return {"valid": False, "direction": "YOK", "reason": "15m veri yetersiz"}
    zone = find_recent_fvg_zone(candles_15m, IFVG_LOOKBACK_BARS)
    if not zone:
        return {"valid": False, "direction": "YOK", "reason": "Yakın FVG bulunamadı"}
    last = candles_15m[-1]
    if zone["type"] == "bearish" and last["close"] > zone["high"]:
        return {"valid": True, "direction": "LONG",  "reason": "Bearish FVG reclaim",
                "zone_low": zone["low"], "zone_high": zone["high"]}
    if zone["type"] == "bullish" and last["close"] < zone["low"]:
        return {"valid": True, "direction": "SHORT", "reason": "Bullish FVG breakdown",
                "zone_low": zone["low"], "zone_high": zone["high"]}
    return {"valid": False, "direction": "YOK", "reason": "IFVG reclaim yok"}

def score_ifvg_confluence(direction: str, bias: str, killzone_active: bool,
                          displacement: str, mss: str, choch: str) -> int:
    score = 0
    if direction == "LONG"  and bias == "Yükseliş": score += 1
    if direction == "SHORT" and bias == "Düşüş":    score += 1
    if killzone_active:                              score += 1
    if displacement in ("Orta", "Güçlü"):           score += 1
    if direction == "LONG"  and (mss == "Bullish MSS" or choch == "Bullish CHoCH"): score += 1
    if direction == "SHORT" and (mss == "Bearish MSS" or choch == "Bearish CHoCH"): score += 1
    return score


# =========================================================
# OTE
# =========================================================
def detect_ote_retracement(candles_15m: List[Dict[str, Any]], direction: str) -> Dict[str, Any]:
    if len(candles_15m) < 20:
        return {"valid": False, "reason": "OTE için veri yetersiz"}
    recent = candles_15m[-20:]
    high_  = max(x["high"] for x in recent)
    low_   = min(x["low"]  for x in recent)
    if high_ <= low_:
        return {"valid": False, "reason": "Dealing range bulunamadı"}
    full_range = high_ - low_
    if direction == "LONG":
        zone = {"low": high_ - full_range * OTE_MAX_LEVEL, "high": high_ - full_range * OTE_MIN_LEVEL}
    else:
        zone = {"low": low_ + full_range * OTE_MIN_LEVEL, "high": low_ + full_range * OTE_MAX_LEVEL}
    price = candles_15m[-1]["close"]
    if zone["low"] <= price <= zone["high"]:
        return {"valid": True, "reason": "OTE bölgesinde", "zone_low": zone["low"], "zone_high": zone["high"]}
    return {"valid": False, "reason": "OTE bölgesinde değil"}


# =========================================================
# ORDER BLOCK
# =========================================================
def detect_order_block(candles: List[Dict[str, Any]], direction: str) -> Optional[Dict[str, Any]]:
    if len(candles) < 14: return None
    avg_rng = average_range(candles[-12:], 12)
    if avg_rng == 0: return None
    for i in range(len(candles)-2, max(0, len(candles)-15)-1, -1):
        c      = candles[i]
        future = candles[i + 1:]
        if direction == "LONG" and is_bearish(c):
            if not any(x["close"] > c["high"] for x in future): continue
            disp_c = max(future, key=lambda x: candle_body(x), default=None)
            if disp_c and candle_body(disp_c) < avg_rng * 0.35: continue
            return {"index": i, "datetime": c["datetime"], "low": c["low"], "high": c["high"]}
        if direction == "SHORT" and is_bullish(c):
            if not any(x["close"] < c["low"] for x in future): continue
            disp_c = max(future, key=lambda x: candle_body(x), default=None)
            if disp_c and candle_body(disp_c) < avg_rng * 0.35: continue
            return {"index": i, "datetime": c["datetime"], "low": c["low"], "high": c["high"]}
    return None


# =========================================================
# BREAKER BLOCK (Yeni Model 2)
# =========================================================
def detect_breaker_block(candles: List[Dict[str, Any]], direction: str) -> Optional[Dict[str, Any]]:
    """
    Breaker Block: Fiyat bir OB'yi kırdıktan sonra oluşan yapı.
    LONG için: Önce bearish OB kırılmış (aşağı), sonra fiyat bu bölgeye geri dönüyor.
    SHORT için: Önce bullish OB kırılmış (yukarı), sonra fiyat bu bölgeye geri dönüyor.
    """
    if len(candles) < BREAKER_LOOKBACK_BARS: return None
    avg_rng = average_range(candles[-12:], 12)
    if avg_rng == 0: return None
    recent = candles[-BREAKER_LOOKBACK_BARS:]
    current_price = candles[-1]["close"]

    if direction == "LONG":
        # Bullish Breaker: Eski bearish OB kırıldı (aşağı break), şimdi fiyat geri döndü
        for i in range(len(recent) - 3, 0, -1):
            ob_c = recent[i]
            if not is_bearish(ob_c): continue
            # Bu OB'nin altına kırılma var mı?
            broke_down = any(x["close"] < ob_c["low"] for x in recent[i+1:i+8] if i+8 <= len(recent))
            if not broke_down: continue
            # Şimdi fiyat bu OB bölgesine geri döndü mü?
            if ob_c["low"] <= current_price <= ob_c["high"] * 1.001:
                return {
                    "index":    i,
                    "datetime": ob_c["datetime"],
                    "low":      ob_c["low"],
                    "high":     ob_c["high"],
                    "type":     "bullish_breaker",
                }
    elif direction == "SHORT":
        # Bearish Breaker: Eski bullish OB kırıldı (yukarı break), şimdi fiyat geri döndü
        for i in range(len(recent) - 3, 0, -1):
            ob_c = recent[i]
            if not is_bullish(ob_c): continue
            broke_up = any(x["close"] > ob_c["high"] for x in recent[i+1:i+8] if i+8 <= len(recent))
            if not broke_up: continue
            if ob_c["low"] * 0.999 <= current_price <= ob_c["high"]:
                return {
                    "index":    i,
                    "datetime": ob_c["datetime"],
                    "low":      ob_c["low"],
                    "high":     ob_c["high"],
                    "type":     "bearish_breaker",
                }
    return None

def score_breaker_confluence(direction: str, bias: str, killzone_active: bool,
                              displacement: str, mss: str, choch: str) -> int:
    score = 0
    if direction == "LONG"  and bias == "Yükseliş": score += 1
    if direction == "SHORT" and bias == "Düşüş":    score += 1
    if killzone_active:                              score += 1
    if displacement in ("Orta", "Güçlü"):           score += 1
    if direction == "LONG"  and (mss == "Bullish MSS" or choch == "Bullish CHoCH"): score += 1
    if direction == "SHORT" and (mss == "Bearish MSS" or choch == "Bearish CHoCH"): score += 1
    return score


# =========================================================
# INVERSION FVG (Yeni Model 3)
# =========================================================
def detect_inversion_fvg(candles_15m: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Inversion FVG: Fiyat bir FVG bölgesini geçtikten sonra geri döner.
    Bullish IFVG: Fiyat Bullish FVG'yi aşağı kırdı → SHORT
    Bearish IFVG: Fiyat Bearish FVG'yi yukarı kırdı → LONG
    Normal IFVG Reclaim'den farkı: daha uzun vadeli zone, trend dönüşünde kullanılır.
    """
    if len(candles_15m) < 8:
        return {"valid": False, "direction": "YOK", "reason": "Veri yetersiz"}

    lookback = IFVG_INV_LOOKBACK_BARS
    start    = max(2, len(candles_15m) - lookback)
    last     = candles_15m[-1]

    for i in range(len(candles_15m) - 3, start - 1, -1):
        a = candles_15m[i - 2]
        b = candles_15m[i - 1]
        c = candles_15m[i]

        # Bullish FVG: c["low"] > a["high"] → fiyat artık bu zone'un altına düştü → SHORT
        if c["low"] > a["high"]:
            zone_low  = a["high"]
            zone_high = c["low"]
            # Fiyat bu zone'u aşağı kırdı mı?
            broke_below = any(x["close"] < zone_low for x in candles_15m[i+1:])
            if not broke_below: continue
            # Şimdi fiyat zone'a geri döndü mü?
            if zone_low * 0.999 <= last["close"] <= zone_high * 1.001:
                return {
                    "valid":      True,
                    "direction":  "SHORT",
                    "reason":     "Bullish FVG inversion → SHORT",
                    "zone_low":   zone_low,
                    "zone_high":  zone_high,
                }

        # Bearish FVG: c["high"] < a["low"] → fiyat zone'u yukarı kırdı → LONG
        if c["high"] < a["low"]:
            zone_low  = c["high"]
            zone_high = a["low"]
            broke_above = any(x["close"] > zone_high for x in candles_15m[i+1:])
            if not broke_above: continue
            if zone_low * 0.999 <= last["close"] <= zone_high * 1.001:
                return {
                    "valid":      True,
                    "direction":  "LONG",
                    "reason":     "Bearish FVG inversion → LONG",
                    "zone_low":   zone_low,
                    "zone_high":  zone_high,
                }

    return {"valid": False, "direction": "YOK", "reason": "Inversion FVG yok"}

def score_inv_fvg_confluence(direction: str, bias: str, killzone_active: bool,
                              displacement: str, mss: str, choch: str) -> int:
    return score_ifvg_confluence(direction, bias, killzone_active, displacement, mss, choch)


# =========================================================
# NY OPEN DISPLACEMENT + MSS (Yeni Model 1)
# =========================================================
def detect_ny_open_displacement(
    candles_5m:  List[Dict[str, Any]],
    candles_15m: List[Dict[str, Any]],
    bias:        str,
    mss:         str,
    choch:       str,
) -> Dict[str, Any]:
    """
    NY Open Displacement: 12:00-13:30 UTC arasında güçlü displacement + MSS/CHoCH.
    Trend yönünde entry arar. Bias zorunlu.
    """
    if not ENABLE_NY_OPEN_DISPLACEMENT:
        return {"valid": False, "direction": "YOK", "reason": "NY Open kapalı"}

    dt_now = candles_5m[-1].get("dt") if candles_5m else None
    if not is_ny_open_window(dt_now):
        return {"valid": False, "direction": "YOK", "reason": "NY open penceresi değil"}

    if len(candles_5m) < 20 or len(candles_15m) < 20:
        return {"valid": False, "direction": "YOK", "reason": "Veri yetersiz"}

    # HTF bias zorunlu
    if NY_OPEN_REQUIRE_HTF_BIAS and bias == "Nötr":
        return {"valid": False, "direction": "YOK", "reason": "NY Open: HTF bias nötr"}

    # Displacement kontrolü (5M'de)
    disp_5m = detect_displacement(candles_5m[-15:])
    if disp_5m == "Zayıf" or disp_5m == "Yok":
        return {"valid": False, "direction": "YOK", "reason": "NY Open: displacement zayıf"}

    # Yapı kontrolü (15M'de)
    if NY_OPEN_REQUIRE_MSS and mss == "Yok" and choch == "Yok":
        return {"valid": False, "direction": "YOK", "reason": "NY Open: MSS/CHoCH yok"}

    # Yön belirleme: bias ve yapı uyumu
    direction = "YOK"
    reason    = ""
    if bias == "Yükseliş" and (mss == "Bullish MSS" or choch == "Bullish CHoCH"):
        direction = "LONG"
        reason    = f"NY Open LONG | Disp:{disp_5m} | {mss or choch}"
    elif bias == "Düşüş" and (mss == "Bearish MSS" or choch == "Bearish CHoCH"):
        direction = "SHORT"
        reason    = f"NY Open SHORT | Disp:{disp_5m} | {mss or choch}"

    if direction == "YOK":
        return {"valid": False, "direction": "YOK", "reason": "NY Open: Bias/Yapı uyumsuz"}

    return {
        "valid":        True,
        "direction":    direction,
        "reason":       reason,
        "displacement": disp_5m,
    }


# =========================================================
# MITIGATION / ENTRY
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
    if rng == 0: return False
    upper_wick = candle["high"] - max(candle["open"], candle["close"])
    lower_wick = min(candle["open"], candle["close"]) - candle["low"]
    body_ratio = body / rng
    if direction == "LONG":
        return candle["close"] > candle["open"] and ((lower_wick >= body * 1.2 if body > 0 else False) or body_ratio >= 0.25)
    if direction == "SHORT":
        return candle["close"] < candle["open"] and ((upper_wick >= body * 1.2 if body > 0 else False) or body_ratio >= 0.25)
    return False

def assess_mitigation_quality(candles_5m: List[Dict[str, Any]], ob: Dict[str, Any],
                               direction: str) -> Dict[str, Any]:
    if len(candles_5m) < 6:
        return {"valid": False, "reason": "5m yetersiz", "touch_index": None, "bars_after_touch": None, "quality": "Yok"}
    touch_index = find_latest_ob_touch_index(candles_5m, ob)
    if touch_index is None:
        return {"valid": False, "reason": "OB touch yok", "touch_index": None, "bars_after_touch": None, "quality": "Yok"}
    bars_after_touch = len(candles_5m) - 1 - touch_index
    if bars_after_touch > MAX_BARS_AFTER_MITIGATION:
        return {"valid": False, "reason": "Mitigation eski", "touch_index": touch_index,
                "bars_after_touch": bars_after_touch, "quality": "Eski"}
    if not is_rejection_candle(candles_5m[-1], direction):
        return {"valid": False, "reason": "Rejection yok", "touch_index": touch_index,
                "bars_after_touch": bars_after_touch, "quality": "Zayıf"}
    quality = "Güçlü" if bars_after_touch <= 1 else "Orta"
    return {"valid": True, "reason": "Taze mitigation", "touch_index": touch_index,
            "bars_after_touch": bars_after_touch, "quality": quality}

def validate_entry_timing(current_price: float, entry: float, tp: float, sl: float,
                           direction: str, candles_5m: List[Dict[str, Any]],
                           touch_index: Optional[int]) -> Dict[str, Any]:
    avg_rng = average_range(candles_5m[-10:], 10)
    if avg_rng == 0:
        return {"valid": False, "reason": "Average range sıfır"}
    entry_distance = abs(current_price - entry)
    if entry_distance > avg_rng * ENTRY_DISTANCE_MAX_MULTIPLIER:
        return {"valid": False, "reason": "Fiyat entry'den uzak", "entry_distance": entry_distance}
    total_path = abs(tp - entry)
    if total_path > 0 and (abs(current_price - entry) / total_path) >= TP_PROGRESS_BLOCK_THRESHOLD:
        return {"valid": False, "reason": "TP yolunda fazla ilerledi", "entry_distance": entry_distance}
    if touch_index is not None:
        if (len(candles_5m) - 1 - touch_index) > MAX_BARS_AFTER_MITIGATION:
            return {"valid": False, "reason": "OB touch sonrası geç", "entry_distance": entry_distance}
    if direction == "LONG"  and current_price < sl:
        return {"valid": False, "reason": "Current price SL altında"}
    if direction == "SHORT" and current_price > sl:
        return {"valid": False, "reason": "Current price SL üstünde"}
    return {"valid": True, "reason": "Entry timing uygun", "entry_distance": entry_distance}

def validate_absolute_trade_distances(candles_5m: List[Dict[str, Any]], entry: float,
                                       sl: float, tp: float, current_price: float) -> Dict[str, Any]:
    avg_rng_5m = average_range(candles_5m[-12:], 12)
    if avg_rng_5m <= 0:
        return {"valid": False, "reason": "5m avg range sıfır"}
    tp_distance  = abs(tp    - entry)
    sl_distance  = abs(entry - sl)
    remaining_tp = abs(tp    - current_price)
    if tp_distance  < avg_rng_5m * MIN_TP_DISTANCE_5M_MULTIPLIER:
        return {"valid": False, "reason": "TP mesafesi kısa",
                "tp_distance": tp_distance, "sl_distance": sl_distance, "remaining_tp_distance": remaining_tp}
    if sl_distance  < avg_rng_5m * MIN_SL_DISTANCE_5M_MULTIPLIER:
        return {"valid": False, "reason": "SL mesafesi kısa",
                "tp_distance": tp_distance, "sl_distance": sl_distance, "remaining_tp_distance": remaining_tp}
    if remaining_tp < avg_rng_5m * MIN_REMAINING_TP_DISTANCE_5M_MULTIPLIER:
        return {"valid": False, "reason": "Hedefe fazla yaklaşılmış",
                "tp_distance": tp_distance, "sl_distance": sl_distance, "remaining_tp_distance": remaining_tp}
    return {"valid": True, "reason": "Mesafeler uygun",
            "tp_distance": tp_distance, "sl_distance": sl_distance, "remaining_tp_distance": remaining_tp}


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

def _sweep_size(candles: List[Dict[str, Any]]) -> Dict[str, float]:
    if len(candles) < 8:
        return {"high_size": 0.0, "low_size": 0.0}
    last      = candles[-1]
    recent    = candles[-8:-1]
    prev_high = max(x["high"] for x in recent)
    prev_low  = min(x["low"]  for x in recent)
    return {
        "high_size": max(0.0, last["high"] - prev_high) if last["high"] > prev_high else 0.0,
        "low_size":  max(0.0, prev_low - last["low"])   if last["low"]  < prev_low  else 0.0,
    }

def _smt_session_weight(dt: Optional[datetime]) -> float:
    session = get_session_name(dt)
    return {"London": 1.4, "New York": 1.3, "Asia": 0.7, "Off Session": 0.5}.get(session, 0.8)

def detect_forex_smt(market_name: str,
                     mtf_map: Dict[str, Dict[str, List[Dict[str, Any]]]]) -> Dict[str, Any]:
    _EMPTY = {"label": "Yok", "bullish_score": 0.0, "bearish_score": 0.0,
              "pairs_used": [], "session_weight": 0.0, "htf_bias": "Nötr"}
    relations = SMT_RELATIONS.get(market_name, [])
    if not relations: return _EMPTY
    target_15m = mtf_map.get(market_name, {}).get("15min", [])
    target_1h  = mtf_map.get(market_name, {}).get("1h",    [])
    if len(target_15m) < 8: return _EMPTY

    target_flags = sweep_flags(target_15m)
    target_sizes = _sweep_size(target_15m)
    avg_rng      = average_range(target_15m[-12:], 12)
    last_dt      = target_15m[-1].get("dt") if target_15m else None
    session_w    = _smt_session_weight(last_dt)
    min_sweep    = avg_rng * 0.15
    bullish_score = 0.0
    bearish_score = 0.0
    bullish_pairs: List[str] = []
    bearish_pairs: List[str] = []

    for other_name, relation_type in relations:
        other_15m = mtf_map.get(other_name, {}).get("15min", [])
        if len(other_15m) < 8: continue
        other_flags = sweep_flags(other_15m)
        other_avg   = average_range(other_15m[-12:], 12)

        if relation_type == "positive":
            if target_flags["low_sweep"] and not other_flags["low_sweep"] and target_sizes["low_size"] > min_sweep:
                w = 1.0
                if other_avg > 0: w += min(target_sizes["low_size"] / other_avg * 0.3, 0.5)
                bullish_score += w * session_w; bullish_pairs.append(other_name)
            if target_flags["high_sweep"] and not other_flags["high_sweep"] and target_sizes["high_size"] > min_sweep:
                w = 1.0
                if other_avg > 0: w += min(target_sizes["high_size"] / other_avg * 0.3, 0.5)
                bearish_score += w * session_w; bearish_pairs.append(other_name)
        else:  # inverse
            if target_flags["low_sweep"] and not other_flags["high_sweep"] and target_sizes["low_size"] > min_sweep:
                bullish_score += 1.0 * session_w; bullish_pairs.append(other_name)
            if target_flags["high_sweep"] and not other_flags["low_sweep"] and target_sizes["high_size"] > min_sweep:
                bearish_score += 1.0 * session_w; bearish_pairs.append(other_name)

    if len(target_1h) >= 8:
        h1_flags = sweep_flags(target_1h)
        if h1_flags["low_sweep"]  and bullish_score > 0: bullish_score *= 1.3
        if h1_flags["high_sweep"] and bearish_score > 0: bearish_score *= 1.3

    MIN_SCORE = 0.7 if session_w >= 1.3 else (0.9 if session_w >= 1.0 else 1.2)
    htf_bias  = detect_htf_bias(target_1h) if len(target_1h) >= 10 else "Nötr"
    if htf_bias == "Yükseliş" and bearish_score > bullish_score: MIN_SCORE *= 1.3
    if htf_bias == "Düşüş"    and bullish_score > bearish_score: MIN_SCORE *= 1.3

    if bullish_score > bearish_score and bullish_score >= MIN_SCORE:
        return {"label": "Bullish SMT", "bullish_score": round(bullish_score, 3),
                "bearish_score": round(bearish_score, 3), "pairs_used": bullish_pairs,
                "session_weight": round(session_w, 2), "htf_bias": htf_bias}
    if bearish_score > bullish_score and bearish_score >= MIN_SCORE:
        return {"label": "Bearish SMT", "bullish_score": round(bullish_score, 3),
                "bearish_score": round(bearish_score, 3), "pairs_used": bearish_pairs,
                "session_weight": round(session_w, 2), "htf_bias": htf_bias}
    return {"label": "Yok", "bullish_score": round(bullish_score, 3),
            "bearish_score": round(bearish_score, 3), "pairs_used": [],
            "session_weight": round(session_w, 2), "htf_bias": htf_bias}


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
    if direction == "YOK":                                    return {"valid": False, "reason": "Yön yok"}
    if REQUIRE_SWEEP_FOR_SNIPER and sweep == "Yok":           return {"valid": False, "reason": "Sweep yok"}
    if REQUIRE_STRUCTURE_FOR_SNIPER and mss == "Yok" and choch == "Yok":
                                                              return {"valid": False, "reason": "MSS/CHoCH yok"}
    if REQUIRE_MIN_DISPLACEMENT_SNIPER and displacement == "Zayıf":
                                                              return {"valid": False, "reason": "Displacement zayıf"}
    return {"valid": True, "reason": "Sniper şartları uygun"}


# =========================================================
# ALTERNATIVE MODELS — Güncellenmiş
# =========================================================
def detect_alternative_model(
    candles_15m: List[Dict[str, Any]],
    candles_5m:  List[Dict[str, Any]],
    bias:        str,
    displacement:str,
    mss:         str,
    choch:       str,
    market_name: str = "",
) -> Dict[str, Any]:
    if len(candles_15m) < 20 or len(candles_5m) < 10:
        return {"valid": False, "model": "YOK", "direction": "YOK", "reason": "Veri yetersiz"}

    dt_now = candles_5m[-1].get("dt") if candles_5m else None
    kz_now = is_killzone_active(dt_now)

    # MODEL 1: NY Open Displacement + MSS
    if ENABLE_NY_OPEN_DISPLACEMENT:
        ny = detect_ny_open_displacement(candles_5m, candles_15m, bias, mss, choch)
        if ny["valid"]:
            return {
                "valid":     True,
                "model":     "NY_OPEN_DISP",
                "direction": ny["direction"],
                "reason":    ny["reason"],
                "extra":     ny,
            }

    # MODEL 2: IFVG Reclaim
    if ENABLE_IFVG_RECLAIM:
        ifvg = detect_ifvg_reclaim(candles_15m)
        if ifvg["valid"]:
            direction  = ifvg["direction"]
            confluence = score_ifvg_confluence(direction, bias, kz_now, displacement, mss, choch)
            structure_ok = (
                (direction == "LONG"  and (mss == "Bullish MSS" or choch == "Bullish CHoCH")) or
                (direction == "SHORT" and (mss == "Bearish MSS" or choch == "Bearish CHoCH"))
            )
            # Bias zorunlu (backtest bulgusu: Nötr bias IFVG'de zayıf)
            bias_ok = (direction == "LONG" and bias == "Yükseliş") or (direction == "SHORT" and bias == "Düşüş")
            if structure_ok and (kz_now or bias_ok) and confluence >= IFVG_MIN_CONFLUENCE_SCORE and bias != "Nötr":
                return {
                    "valid":      True,
                    "model":      "IFVG_RECLAIM",
                    "direction":  direction,
                    "reason":     f"{ifvg['reason']} | confluence={confluence}",
                    "zone_low":   round_price(ifvg["zone_low"],  market_name),
                    "zone_high":  round_price(ifvg["zone_high"], market_name),
                }

    # MODEL 3: Breaker Block
    if ENABLE_BREAKER_BLOCK and bias != "Nötr":
        # BALANCED FILTER:
        # Sadece Zayıf displacement olan Breaker sinyallerini ele.
        # Orta ve Güçlü displacement sinyal üretmeye devam eder.
        if displacement != "Zayıf":
            bb_dir = "LONG" if bias == "Yükseliş" else "SHORT"
            bb     = detect_breaker_block(candles_15m, bb_dir)
            if bb:
                confluence = score_breaker_confluence(bb_dir, bias, kz_now, displacement, mss, choch)
                structure_ok = (
                    (bb_dir == "LONG"  and (mss == "Bullish MSS" or choch == "Bullish CHoCH")) or
                    (bb_dir == "SHORT" and (mss == "Bearish MSS" or choch == "Bearish CHoCH"))
                )
                if BREAKER_REQUIRE_STRUCTURE and not structure_ok:
                    pass  # Yapı yok, atla
                elif confluence >= BREAKER_MIN_CONFLUENCE:
                    return {
                        "valid":      True,
                        "model":      "BREAKER_BLOCK",
                        "direction":  bb_dir,
                        "reason":     f"Breaker Block | confluence={confluence} | {bb['type']}",
                        "zone_low":   round_price(bb["low"],  market_name),
                        "zone_high":  round_price(bb["high"], market_name),
                    }

    # MODEL 4: Inversion FVG
    if ENABLE_INVERSION_FVG:
        inv = detect_inversion_fvg(candles_15m)
        if inv["valid"]:
            direction  = inv["direction"]
            confluence = score_inv_fvg_confluence(direction, bias, kz_now, displacement, mss, choch)
            structure_ok = (
                (direction == "LONG"  and (mss == "Bullish MSS" or choch == "Bullish CHoCH")) or
                (direction == "SHORT" and (mss == "Bearish MSS" or choch == "Bearish CHoCH"))
            )
            bias_ok = (direction == "LONG" and bias == "Yükseliş") or (direction == "SHORT" and bias == "Düşüş")
            if structure_ok and bias_ok and confluence >= IFVG_INV_MIN_CONFLUENCE:
                return {
                    "valid":      True,
                    "model":      "INVERSION_FVG",
                    "direction":  direction,
                    "reason":     f"{inv['reason']} | confluence={confluence}",
                    "zone_low":   round_price(inv["zone_low"],  market_name),
                    "zone_high":  round_price(inv["zone_high"], market_name),
                }

    # MODEL 5: OTE Retracement
    if ENABLE_OTE_RETRACEMENT and bias in ("Yükseliş", "Düşüş"):
        direction = "LONG" if bias == "Yükseliş" else "SHORT"
        ote = detect_ote_retracement(candles_15m, direction)
        if ote["valid"] and displacement in ("Orta", "Güçlü"):
            return {
                "valid":     True,
                "model":     "OTE_RETRACEMENT",
                "direction": direction,
                "reason":    ote["reason"],
                "zone_low":  round_price(ote["zone_low"],  market_name),
                "zone_high": round_price(ote["zone_high"], market_name),
            }

    return {"valid": False, "model": "YOK", "direction": "YOK", "reason": "Alternatif model yok"}


# =========================================================
# SL / TP
# =========================================================
def build_structure_sl(candles_15m: List[Dict[str, Any]], ob: Dict[str, Any], direction: str) -> Optional[float]:
    avg_rng = average_range(candles_15m[-10:], 10)
    if avg_rng == 0: return None
    recent     = candles_15m[-8:]
    # Backtest bulgusuna göre buffer küçültüldü: 0.15 → 0.08
    buffer_val = avg_rng * 0.08
    if direction == "LONG":
        anchor = min(ob["low"], min(x["low"] for x in recent))
        return anchor - buffer_val
    if direction == "SHORT":
        anchor = max(ob["high"], max(x["high"] for x in recent))
        return anchor + buffer_val
    return None

def find_directional_target(candles_15m: List[Dict[str, Any]], candles_5m: List[Dict[str, Any]],
                             direction: str, entry: float) -> Optional[float]:
    swings      = collect_swings(candles_15m, left=2, right=2)
    pd_levels   = get_previous_day_levels(candles_5m)
    session_nm  = "London" if get_session_name(candles_5m[-1].get("dt")) == "London" else "New York"
    session_box = get_recent_session_box(candles_5m, session_nm)
    candidates: List[float] = []
    if direction == "LONG":
        eqh = equal_highs_exists(candles_15m)
        candidates.extend([x["price"] for x in swings["highs"] if x["price"] > entry])
        if eqh and eqh > entry:                               candidates.append(eqh)
        if pd_levels["pdh"] and pd_levels["pdh"] > entry:    candidates.append(pd_levels["pdh"])
        if session_box and session_box["high"] > entry:       candidates.append(session_box["high"])
        return min(candidates) if candidates else None
    if direction == "SHORT":
        eql = equal_lows_exists(candles_15m)
        candidates.extend([x["price"] for x in swings["lows"] if x["price"] < entry])
        if eql and eql < entry:                               candidates.append(eql)
        if pd_levels["pdl"] and pd_levels["pdl"] < entry:    candidates.append(pd_levels["pdl"])
        if session_box and session_box["low"] < entry:        candidates.append(session_box["low"])
        return max(candidates) if candidates else None
    return None

def build_trade_levels(candles_15m: List[Dict[str, Any]], candles_5m: List[Dict[str, Any]],
                        direction: str, ob: Dict[str, Any], symbol: str = "") -> Dict[str, Optional[float]]:
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
        "entry": round_price(entry, symbol),
        "sl":    round_price(sl,    symbol),
        "tp":    round_price(tp,    symbol),
        "rr":    round(rr, 2) if rr is not None else None,
    }


# =========================================================
# READY CHECK
# =========================================================
def is_ready_trade_signal(result: Dict[str, Any], pending_info: Dict[str, Any]) -> Dict[str, Any]:
    if int(pending_info.get("seen_count", 0)) < MIN_SETUP_CONFIRM_CYCLES:
        return {"valid": False, "reason": "Setup olgunlaşmadı"}
    setup_model = result.get("setup_model", "YOK")
    killzone    = result.get("killzone", "Killzone Dışı")
    mss         = result.get("mss", "Yok")
    choch       = result.get("choch", "Yok")
    displacement= result.get("displacement", "Yok")
    if setup_model == "OTE_RETRACEMENT":
        if REQUIRE_KILLZONE_FOR_OTE and killzone == "Killzone Dışı":
            return {"valid": False, "reason": "OTE killzone dışı"}
        if REQUIRE_STRUCTURE_FOR_OTE:
            if not (mss != "Yok" or choch != "Yok" or displacement == "Güçlü"):
                return {"valid": False, "reason": "OTE yapı teyidi zayıf"}
    return {"valid": True, "reason": "Ready signal"}


# =========================================================
# SCORING — Güncellenmiş (sadece A+ gönder)
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
    ny_open_extra:    Optional[Dict[str, Any]] = None,
    smt_detail:       Optional[Dict[str, Any]] = None,
) -> Tuple[int, str, List[str]]:
    score = 40
    notes: List[str] = []

    # Bias
    if direction == "LONG":
        if bias    == "Yükseliş": score += 14; notes.append("Ana bias uyumlu")
        if h4_bias == "Yükseliş": score += 8;  notes.append("4H bias destekliyor")
        if w1_bias == "Yükseliş": score += 5;  notes.append("1W bias destekliyor")
        if fvg     == "Bullish FVG": score += 8; notes.append("Bullish FVG")
        if pd      == "Discount":    score += 8; notes.append("Discount bölgesi")
        if smt == "Bullish SMT":
            if smt_detail:
                raw = smt_detail.get("bullish_score", 1.0)
                sw  = smt_detail.get("session_weight", 1.0)
                smt_pts = 3 + min(int(raw * sw * 2), 6)
            else:
                smt_pts = 6
            score += smt_pts; notes.append(f"Bullish SMT ({smt_pts}pt)")
        if mss     == "Bullish MSS": score += 8; notes.append("Bullish MSS")
        elif choch == "Bullish CHoCH": score += 5; notes.append("Bullish CHoCH")
    if direction == "SHORT":
        if bias    == "Düşüş":    score += 14; notes.append("Ana bias uyumlu")
        if h4_bias == "Düşüş":    score += 8;  notes.append("4H bias destekliyor")
        if w1_bias == "Düşüş":    score += 5;  notes.append("1W bias destekliyor")
        if fvg     == "Bearish FVG": score += 8; notes.append("Bearish FVG")
        if pd      == "Premium":     score += 8; notes.append("Premium bölge")
        if smt == "Bearish SMT":
            if smt_detail:
                raw = smt_detail.get("bearish_score", 1.0)
                sw  = smt_detail.get("session_weight", 1.0)
                smt_pts = 3 + min(int(raw * sw * 2), 6)
            else:
                smt_pts = 6
            score += smt_pts; notes.append(f"Bearish SMT ({smt_pts}pt)")
        if mss     == "Bearish MSS": score += 8; notes.append("Bearish MSS")
        elif choch == "Bearish CHoCH": score += 5; notes.append("Bearish CHoCH")

    # Bias conflict
    if bias == "Yükseliş" and h4_bias == "Düşüş": score -= 8; notes.append("1H/4H conflict")
    if bias == "Düşüş"    and h4_bias == "Yükseliş": score -= 8; notes.append("1H/4H conflict")

    # Session
    if killzone_active: score += 8; notes.append("Killzone aktif")

    # Displacement
    if displacement == "Güçlü": score += 8; notes.append("Güçlü displacement")
    elif displacement == "Orta": score += 4; notes.append("Orta displacement")
    else:                        score -= 8; notes.append("Displacement zayıf")

    # Mitigation
    if mitigation_quality == "Güçlü": score += 8; notes.append("Taze mitigation")
    elif mitigation_quality == "Orta": score += 4; notes.append("Mitigation orta")
    else:                              score -= 8; notes.append("Mitigation zayıf")

    # RR
    if rr is not None:
        if rr >= 3.0:   score += 10; notes.append("RR mükemmel")
        elif rr >= 2.5: score += 8;  notes.append("RR güçlü")
        elif rr >= 2.0: score += 5;  notes.append("RR yeterli")
        elif rr < MIN_RR_REQUIRED: score -= 12; notes.append("RR zayıf")

    # Model bonus
    if setup_model == "NY_OPEN_DISP":
        score += 8; notes.append("NY Open Displacement")
        if ny_open_extra and ny_open_extra.get("displacement") == "Güçlü":
            score += 4; notes.append("NY disp güçlü")
    elif setup_model == "IFVG_RECLAIM":
        score += 2; notes.append("IFVG reclaim")
    elif setup_model == "BREAKER_BLOCK":
        score += 5; notes.append("Breaker Block")
    elif setup_model == "INVERSION_FVG":
        score += 4; notes.append("Inversion FVG")
    elif setup_model == "OTE_RETRACEMENT":
        score += 2; notes.append("OTE Retracement")
    elif setup_model == "INVERSION_FVG":
        score += 3; notes.append("Inversion FVG")
    elif setup_model == "SNIPER_OB":
        score += 3; notes.append("Sniper OB")
    elif setup_model == "OTE_RETRACEMENT":
        score += 2

    # Performance
    score += perf_adjustment
    notes.extend(perf_notes)

    # Backtest bulgusu: Sadece A+ gönder
    quality = "Yok"
    if score >= 88: quality = "A+"
    elif score >= 68: quality = "A"

    return score, quality, notes


# =========================================================
# SIGNAL KEY / STATE
# =========================================================
def build_signal_key(result: Dict[str, Any]) -> str:
    symbol    = result["symbol"]
    direction = result["direction"]
    dec       = price_decimals(symbol)
    entry = f"{result['entry']:.{dec}f}" if isinstance(result.get("entry"), (int, float)) else "na"
    sl    = f"{result['sl']:.{dec}f}"    if isinstance(result.get("sl"),    (int, float)) else "na"
    tp    = f"{result['tp']:.{dec}f}"    if isinstance(result.get("tp"),    (int, float)) else "na"
    return f"{symbol}|{direction}|{entry}|{sl}|{tp}"

def should_skip_repeated_signal(result: Dict[str, Any], state: Dict[str, Any]) -> bool:
    key    = build_signal_key(result)
    record = state["signals"].get(key)
    if not record: return False
    last_sent = record.get("last_sent_ts")
    if not isinstance(last_sent, int): return False
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
    symbol    = result["symbol"]
    direction = result["direction"]
    model     = result.get("setup_model", "YOK")
    dec       = price_decimals(symbol)
    ob_low    = f"{result['ob_low']:.{dec}f}"  if isinstance(result.get("ob_low"),  (int, float)) else "na"
    ob_high   = f"{result['ob_high']:.{dec}f}" if isinstance(result.get("ob_high"), (int, float)) else "na"
    return f"{symbol}|{direction}|{model}|{ob_low}|{ob_high}"

def update_pending_setup(state: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
    key    = build_pending_setup_key(result)
    bucket = state["pending_setups"].get(key)
    if not bucket:
        bucket = {"first_seen_ts": now_ts(), "last_seen_ts": now_ts(), "seen_count": 1,
                  "symbol": result["symbol"], "direction": result["direction"],
                  "setup_model": result.get("setup_model", "YOK")}
        state["pending_setups"][key] = bucket
        return bucket
    bucket["last_seen_ts"] = now_ts()
    bucket["seen_count"]   = int(bucket.get("seen_count", 0)) + 1
    return bucket

def cleanup_pending_setups(state: Dict[str, Any]) -> None:
    pending = state.get("pending_setups", {})
    if not isinstance(pending, dict): return
    keys_to_delete = [k for k, v in pending.items()
                      if not isinstance(v.get("last_seen_ts"), int)
                      or (now_ts() - v["last_seen_ts"]) > PENDING_SETUP_MAX_AGE_SECONDS]
    for k in keys_to_delete:
        pending.pop(k, None)

def update_active_signals_outcomes(state: Dict[str, Any],
                                    mtf_map: Dict[str, Dict[str, List[Dict[str, Any]]]]) -> None:
    active = state.get("active_signals", {})
    if not isinstance(active, dict) or not active: return
    keys_to_delete: List[str] = []
    for key, record in active.items():
        if record.get("status") != "OPEN": keys_to_delete.append(key); continue
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
        if not candles_5m: continue
        relevant = [c for c in candles_5m if c.get("dt") and int(c["dt"].timestamp()) >= created_ts]
        if not relevant: continue
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
    """
    FOREX_PAIRS = SIGNAL_PAIRS + SMT_ONLY_PAIRS
    SIGNAL_PAIRS → sinyal üretir (9 parite)
    SMT_ONLY_PAIRS → sadece korelasyon verisi (EUR/USD)
    Tüm pariteler yüklenir çünkü SMT motoru EUR/USD verisine ihtiyaç duyar.
    """
    mtf_map: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
    for market in FOREX_PAIRS:  # FOREX_PAIRS = SIGNAL_PAIRS + SMT_ONLY_PAIRS
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
    # SMT_ONLY pariteler sinyal üretmez (sadece korelasyon verisi)
    smt_only_names = {m["name"] for m in SMT_ONLY_PAIRS}
    if market_name in smt_only_names:
        return None

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

    # Primary Sniper OB
    primary_direction = determine_direction(sweep, mss, choch, displacement)
    setup_model       = "SNIPER_OB"
    direction         = primary_direction
    alt_model_info    = None
    ny_open_extra: Optional[Dict[str, Any]] = None

    if direction != "YOK":
        sniper_check = validate_sniper_model(direction, sweep, mss, choch, displacement)
        if not sniper_check["valid"]:
            print(f"{market_name} -> sniper filtresi: {sniper_check['reason']}")
            direction = "YOK"

    # Alternative Models
    if direction == "YOK":
        alt_model_info = detect_alternative_model(
            candles_15m=candles_15m, candles_5m=candles_5m,
            bias=bias, displacement=displacement,
            mss=mss, choch=choch, market_name=market_name,
        )
        if alt_model_info["valid"]:
            direction   = alt_model_info["direction"]
            setup_model = alt_model_info["model"]
            if setup_model == "NY_OPEN_DISP":
                ny_open_extra = alt_model_info.get("extra")
            elif setup_model in ("BREAKER_BLOCK", "INVERSION_FVG", "IFVG_RECLAIM", "OTE_RETRACEMENT"):
                # Zone bilgisi alt_model_info içinde, result'e eklenecek
                pass  # zone_low/zone_high aşağıda result dict'ine ekleniyor

    if direction == "YOK":
        return None

    # Order Block
    ob = detect_order_block(candles_15m, direction)
    if not ob:
        print(f"{market_name} -> OB bulunamadı")
        return None

    # Levels
    levels = build_trade_levels(candles_15m, candles_5m, direction, ob, symbol=market_name)
    if levels["entry"] is None or levels["sl"] is None or levels["tp"] is None:
        return None
    if levels["rr"] is None or levels["rr"] < MIN_RR_REQUIRED:
        print(f"{market_name} -> RR düşük ({levels['rr']})")
        return None

    # Mitigation
    mitigation = assess_mitigation_quality(candles_5m, ob, direction)
    if not mitigation["valid"]:
        print(f"{market_name} -> mitigation: {mitigation['reason']}")
        return None

    # Entry Timing
    timing = validate_entry_timing(
        current_price=current_price, entry=levels["entry"], tp=levels["tp"],
        sl=levels["sl"], direction=direction, candles_5m=candles_5m,
        touch_index=mitigation["touch_index"],
    )
    if not timing["valid"]:
        print(f"{market_name} -> timing: {timing['reason']}")
        return None

    # Distance
    distance_check = validate_absolute_trade_distances(
        candles_5m=candles_5m, entry=levels["entry"], sl=levels["sl"],
        tp=levels["tp"], current_price=current_price,
    )
    if not distance_check["valid"]:
        print(f"{market_name} -> mesafe: {distance_check['reason']}")
        return None

    # SMT + Performance
    smt_result = detect_forex_smt(market_name, mtf_map)
    smt_label  = smt_result["label"]
    perf_adjustment, perf_notes = get_performance_adjustment(state, market_name)

    # Score
    score, quality, score_notes = score_signal(
        direction=direction, bias=bias, h4_bias=h4_bias, w1_bias=w1_bias,
        fvg=fvg, pd=pd, smt=smt_label, killzone_active=is_killzone_active(),
        displacement=displacement, mitigation_quality=mitigation["quality"],
        rr=levels["rr"], mss=mss, choch=choch,
        perf_adjustment=perf_adjustment, perf_notes=perf_notes,
        setup_model=setup_model, ny_open_extra=ny_open_extra,
        smt_detail=smt_result,
    )

    # Backtest bulgusu: Sadece A+ gönder
    if quality != "A+":
        print(f"{market_name} -> kalite filtresi: {quality} (skor:{score}) — sadece A+ gönderilir")
        return None

    # Killzone dışı ek filtre
    killzone_now    = get_killzone_label()
    killzone_active = killzone_now != "Killzone Dışı"
    min_score_req   = MIN_SIGNAL_SCORE if killzone_active else MIN_SIGNAL_SCORE_OUTSIDE_KILLZONE
    if score < min_score_req:
        print(f"{market_name} -> skor filtresi: {score} < {min_score_req}")
        return None

    # Trade levels
    risk       = abs(levels["entry"] - levels["sl"])
    partial_tp = round_price(levels["entry"] + risk,       market_name) if direction == "LONG" else round_price(levels["entry"] - risk,       market_name)
    tp2        = round_price(levels["entry"] + risk * 3.0, market_name) if direction == "LONG" else round_price(levels["entry"] - risk * 3.0, market_name)

    reason_map = {
        "SNIPER_OB":       "Sniper OB entry aktif",
        "NY_OPEN_DISP":    "NY Open Displacement + MSS",
        "IFVG_RECLAIM":    "IFVG reclaim onaylandı",
        "BREAKER_BLOCK":   "Breaker Block mitigation",
        "INVERSION_FVG":   "Inversion FVG reclaim",
        "OTE_RETRACEMENT": "OTE retracement hazır",
    }

    return {
        "symbol":           market_name,
        "price":            round_price(current_price, market_name),
        "direction":        direction,
        "entry":            levels["entry"],
        "sl":               levels["sl"],
        "tp":               levels["tp"],
        "tp2":              tp2,
        "rr":               levels["rr"],
        "score":            score,
        "quality":          quality,
        "bias":             bias,
        "h1_bias":          h1_bias,
        "h4_bias":          h4_bias,
        "w1_bias":          w1_bias,
        "sweep":            sweep,
        "mss":              mss,
        "choch":            choch,
        "displacement":     displacement,
        "fvg":              fvg,
        "pd":               pd,
        "smt":              smt_label,
        "smt_detail":       smt_result,
        "killzone":         killzone_now,
        "ob_low":           round_price(ob["low"],  market_name),
        "ob_high":          round_price(ob["high"], market_name),
        "ob_time":          ob["datetime"],
        "mitigation_quality":   mitigation["quality"],
        "mitigation_reason":    mitigation["reason"],
        "bars_after_touch":     mitigation["bars_after_touch"],
        "entry_distance":   round_price(timing.get("entry_distance"), market_name),
        "tp_distance":      round_price(distance_check.get("tp_distance"),  market_name),
        "sl_distance":      round_price(distance_check.get("sl_distance"),  market_name),
        "remaining_tp":     round_price(distance_check.get("remaining_tp_distance"), market_name),
        "partial_tp":       partial_tp,
        "be_trigger":       partial_tp,
        "invalidation":     levels["sl"],
        "score_notes":      score_notes,
        "setup_model":      setup_model,
        "model_reason":     alt_model_info["reason"] if alt_model_info else "Primary Sniper OB",
        "ny_open_extra":    ny_open_extra,
        "zone_low":         alt_model_info.get("zone_low")  if alt_model_info else None,
        "zone_high":        alt_model_info.get("zone_high") if alt_model_info else None,
        "reason":           reason_map.get(setup_model, "Setup aktif"),
    }


# =========================================================
# MESSAGE FORMAT
# =========================================================
def fmt_signal(r: Dict[str, Any], key: str) -> str:
    return fmt_val(r.get(key), r.get("symbol", ""))

def format_signal_message(r: Dict[str, Any]) -> str:
    # NY Open özel satır
    ny_lines = ""
    ne = r.get("ny_open_extra")
    if ne and r.get("setup_model") == "NY_OPEN_DISP":
        ny_lines = f"\nNY Displacement: {ne.get('displacement','Yok')}"

    score_notes = ", ".join(r.get("score_notes", [])[:6]) if isinstance(r.get("score_notes"), list) else "Yok"

    model_labels = {
        "SNIPER_OB":       "Sniper OB",
        "NY_OPEN_DISP":    "NY Open Displacement",
        "IFVG_RECLAIM":    "IFVG Reclaim",
        "BREAKER_BLOCK":   "Breaker Block",
        "INVERSION_FVG":   "Inversion FVG",
        "OTE_RETRACEMENT": "OTE Retracement",
    }
    model_label = model_labels.get(r.get("setup_model", ""), r.get("setup_model", "YOK"))

    parts = [
        "🔥 ELITE SNIPER ICT SIGNAL",
        "",
        f"📌 {r['symbol']} | {r['direction']}",
        "",
        f"💰 Price:  {fmt_signal(r, 'price')}",
        f"🎯 Entry:  {fmt_signal(r, 'entry')}",
        f"🛑 SL:     {fmt_signal(r, 'sl')}",
        f"✅ TP1:    {fmt_signal(r, 'tp')}",
        f"🚀 TP2:    {fmt_signal(r, 'tp2')}",
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
        f"SMT Score:   B:{r.get('smt_detail', {}).get('bullish_score', 0):.2f} / S:{r.get('smt_detail', {}).get('bearish_score', 0):.2f}",
        f"SMT Pairs:   {', '.join(r.get('smt_detail', {}).get('pairs_used', [])) or 'Yok'}",
        f"Session:     {r.get('killzone', 'Yok')}",
        "",
        "─── MODEL ───",
        f"Setup Model: {model_label}",
        f"Reason:      {r.get('model_reason', 'Yok')}",
        ny_lines,
        "",
        "─── ENTRY ───",
        f"OB Zone:          {fmt_signal(r, 'ob_low')} - {fmt_signal(r, 'ob_high')}",
        f"Mitigation:       {r.get('mitigation_quality', 'Yok')}",
        f"Bars After Touch: {r.get('bars_after_touch', 'Yok')}",
        f"Distance to Entry:{fmt_signal(r, 'entry_distance')} ({to_pips(r.get('entry_distance'), r.get('symbol',''))})",
        f"TP Distance:      {fmt_signal(r, 'tp_distance')} ({to_pips(r.get('tp_distance'), r.get('symbol',''))})",
        f"SL Distance:      {fmt_signal(r, 'sl_distance')} ({to_pips(r.get('sl_distance'), r.get('symbol',''))})",
        f"Remaining TP:     {fmt_signal(r, 'remaining_tp')} ({to_pips(r.get('remaining_tp'), r.get('symbol',''))})",
        "",
        "─── MANAGEMENT ───",
        f"Partial TP (1R):  {fmt_signal(r, 'partial_tp')}",
        f"Move to BE after: {fmt_signal(r, 'be_trigger')}",
        f"Full TP (2R+):    {fmt_signal(r, 'tp')}",
        f"Invalidation:     {fmt_signal(r, 'invalidation')}",
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
    print(f"Sinyal pariteleri: {[p['name'] for p in SIGNAL_PAIRS]}")
    print(f"London Reversal: KAPALI | NY Open Disp: {'✓' if ENABLE_NY_OPEN_DISPLACEMENT else '✗'}")
    print(f"Breaker Block: {'✓' if ENABLE_BREAKER_BLOCK else '✗'} | Inv FVG: {'✓' if ENABLE_INVERSION_FVG else '✗'}")

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

    # Sadece SIGNAL_PAIRS analiz edilir
    for market in SIGNAL_PAIRS:
        name   = market["name"]
        result = analyze_forex_symbol(name, mtf_map, state)

        if not result:
            print(f"{name} -> setup yok")
            continue

        print(
            f"{name} -> yön:{result['direction']} skor:{result['score']} "
            f"kalite:{result['quality']} rr:{result.get('rr')} model:{result.get('setup_model')}"
        )

        # Sadece A+ — zaten analyze_forex_symbol'de filtrelendi ama çift kontrol
        if result["quality"] != "A+":
            print(f"{name} -> A+ değil, atlandı")
            continue

        pending_info = update_pending_setup(state, result)
        ready_check  = is_ready_trade_signal(result, pending_info)
        if not ready_check["valid"]:
            print(f"{name} -> beklemede: {ready_check['reason']}")
            continue

        if should_skip_repeated_signal(result, state):
            print(f"{name} -> cooldown içinde")
            continue

        msg  = format_signal_message(result)
        sent = send_telegram_message(msg)

        if sent:
            total_signals += 1
            mark_signal_sent(result, state)
            print(f"{name} -> ✅ SINYAL GÖNDERİLDİ")
        else:
            print(f"{name} -> ❌ gönderilemedi")

    save_bot_state(state)
    print(f"ELITE SNIPER SCAN END -> {now_str()} | toplam sinyal: {total_signals}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(run_scan())
