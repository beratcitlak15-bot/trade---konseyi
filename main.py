from flask import Flask, jsonify
import os
import time
import json
import gc
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple, List

import pandas as pd
import requests
from tvDatafeed import TvDatafeed, Interval

app = Flask(__name__)

# =========================================================
# ENV
# =========================================================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
TV_USERNAME = os.getenv("TV_USERNAME", "").strip()
TV_PASSWORD = os.getenv("TV_PASSWORD", "").strip()

# =========================================================
# SETTINGS
# =========================================================
SCAN_INTERVAL_SECONDS = 30
MIN_SIGNAL_CONFIDENCE = 74
STRONG_SIGNAL_CONFIDENCE = 82
SIGNAL_COOLDOWN_MINUTES = 45

DEFAULT_SL_ATR_MULT = 1.20
DEFAULT_TP_ATR_MULT = 2.20

STATE_FILE = "bot_state.json"

# =========================================================
# MARKETS
# =========================================================
MARKETS: Dict[str, Dict[str, str]] = {
    "EURUSD": {"symbol": "EURUSD", "exchange": "OANDA"},
    "GBPUSD": {"symbol": "GBPUSD", "exchange": "OANDA"},
    "USDJPY": {"symbol": "USDJPY", "exchange": "OANDA"},
    "XAUUSD": {"symbol": "XAUUSD", "exchange": "OANDA"},
    "XAGUSD": {"symbol": "XAGUSD", "exchange": "OANDA"},
    "NASDAQ": {"symbol": "US100", "exchange": "CAPITALCOM"},
    "US30": {"symbol": "US30", "exchange": "CAPITALCOM"},
    "SPX500": {"symbol": "US500", "exchange": "CAPITALCOM"},
    "DXY": {"symbol": "DXY", "exchange": "TVC"},
}

SMT_PAIRS = {
    "EURUSD": "GBPUSD",
    "GBPUSD": "EURUSD",
    "NASDAQ": "SPX500",
    "SPX500": "NASDAQ",
    "XAUUSD": "DXY",
    "DXY": "XAUUSD",
}

# =========================================================
# GLOBALS
# =========================================================
tv: Optional[TvDatafeed] = None

STATE: Dict[str, Any] = {
    "active_signals": {},
    "recent_signal_keys": {},
    "last_scan_at": None,
}

_last_scan_data_cache: Dict[str, Dict[str, pd.DataFrame]] = {}
_last_scan_data_ts: Optional[datetime] = None
CACHE_TTL_SECONDS = 20


# =========================================================
# GENERAL
# =========================================================
def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def round_price(market: str, value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    if market in ["EURUSD", "GBPUSD", "USDJPY"]:
        return round(float(value), 5)
    return round(float(value), 2)


def df_last_close(df: Optional[pd.DataFrame]) -> Optional[float]:
    if df is None or df.empty:
        return None
    return float(df.iloc[-1]["close"])


def avg_range(df: pd.DataFrame, lookback: int = 14) -> Optional[float]:
    if df is None or len(df) < lookback:
        return None
    x = df.tail(lookback)
    return float((x["high"] - x["low"]).mean())


def candle_body(row: pd.Series) -> float:
    return abs(float(row["close"] - row["open"]))


def candle_range(row: pd.Series) -> float:
    return float(row["high"] - row["low"])


def local_high(df: pd.DataFrame, idx: int, left: int = 2, right: int = 2) -> bool:
    if idx - left < 0 or idx + right >= len(df):
        return False
    level = float(df.iloc[idx]["high"])
    for i in range(idx - left, idx + right + 1):
        if i == idx:
            continue
        if float(df.iloc[i]["high"]) >= level:
            return False
    return True


def local_low(df: pd.DataFrame, idx: int, left: int = 2, right: int = 2) -> bool:
    if idx - left < 0 or idx + right >= len(df):
        return False
    level = float(df.iloc[idx]["low"])
    for i in range(idx - left, idx + right + 1):
        if i == idx:
            continue
        if float(df.iloc[i]["low"]) <= level:
            return False
    return True


def last_swing_high(df: pd.DataFrame, lookback: int = 60) -> Optional[Tuple[int, float]]:
    if df is None or len(df) < 10:
        return None
    start = max(2, len(df) - lookback)
    result = None
    for i in range(start, len(df) - 2):
        if local_high(df, i):
            result = (i, float(df.iloc[i]["high"]))
    return result


def last_swing_low(df: pd.DataFrame, lookback: int = 60) -> Optional[Tuple[int, float]]:
    if df is None or len(df) < 10:
        return None
    start = max(2, len(df) - lookback)
    result = None
    for i in range(start, len(df) - 2):
        if local_low(df, i):
            result = (i, float(df.iloc[i]["low"]))
    return result


def session_info() -> Dict[str, Any]:
    h = utc_now().hour

    if 0 <= h < 6:
        return {"session": "Asya", "killzone": False}
    if 6 <= h < 10:
        return {"session": "Londra Killzone", "killzone": True}
    if 10 <= h < 13:
        return {"session": "Londra", "killzone": False}
    if 13 <= h < 16:
        return {"session": "London-NY Overlap", "killzone": True}
    if 16 <= h < 18:
        return {"session": "New York Killzone", "killzone": True}
    if 18 <= h < 21:
        return {"session": "New York", "killzone": False}
    return {"session": "Geçiş", "killzone": False}


def near_zone(price: Optional[float], zone: Optional[Tuple[float, float]], tolerance_ratio: float = 0.15) -> bool:
    if price is None or zone is None:
        return False
    lo, hi = zone
    width = hi - lo
    if width <= 0:
        return False
    tol = width * tolerance_ratio
    return (lo - tol) <= price <= (hi + tol)


# =========================================================
# TELEGRAM
# =========================================================
def telegram_api(method: str, payload: dict) -> dict:
    if not TELEGRAM_BOT_TOKEN:
        return {"ok": False, "error": "TELEGRAM_BOT_TOKEN eksik"}

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}"
    try:
        r = requests.post(url, json=payload, timeout=20)
        return r.json()
    except Exception as e:
        return {"ok": False, "error": str(e)}


def send_telegram_message(text: str, reply_to_message_id: Optional[int] = None) -> dict:
    if not TELEGRAM_CHAT_ID:
        return {"ok": False, "error": "TELEGRAM_CHAT_ID eksik"}

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
    }
    if reply_to_message_id is not None:
        payload["reply_to_message_id"] = reply_to_message_id

    return telegram_api("sendMessage", payload)


# =========================================================
# STATE
# =========================================================
def load_state() -> None:
    global STATE
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                STATE["active_signals"] = raw.get("active_signals", {})
                STATE["recent_signal_keys"] = raw.get("recent_signal_keys", {})
                STATE["last_scan_at"] = raw.get("last_scan_at")
    except Exception:
        pass


def save_state() -> None:
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(STATE, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# =========================================================
# TV INIT
# =========================================================
def init_tv() -> bool:
    global tv
    try:
        if TV_USERNAME and TV_PASSWORD:
            tv = TvDatafeed(username=TV_USERNAME, password=TV_PASSWORD)
        else:
            tv = TvDatafeed()
        return True
    except Exception:
        tv = None
        return False


# =========================================================
# DATA FETCH
# =========================================================
def get_hist_raw(symbol: str, exchange: str, interval: Interval, n_bars: int) -> Optional[pd.DataFrame]:
    if tv is None:
        return None

    try:
        df = tv.get_hist(symbol=symbol, exchange=exchange, interval=interval, n_bars=n_bars)
        if df is None or df.empty:
            return None

        df = df.reset_index()

        if "datetime" in df.columns:
            df["datetime"] = pd.to_datetime(df["datetime"], utc=True, errors="coerce")

        # sadece gerekli kolonları tut
        keep_cols = [c for c in ["datetime", "open", "high", "low", "close", "volume"] if c in df.columns]
        df = df[keep_cols]

        # tip küçültme
        for c in ["open", "high", "low", "close", "volume"]:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce", downcast="float")

        return df
    except Exception:
        return None


def get_market_pack(market: str) -> Optional[Dict[str, pd.DataFrame]]:
    cfg = MARKETS[market]
    symbol = cfg["symbol"]
    exchange = cfg["exchange"]

    df_5m = get_hist_raw(symbol, exchange, Interval.in_5_minute, 180)
    df_15m = get_hist_raw(symbol, exchange, Interval.in_15_minute, 120)
    df_1h = get_hist_raw(symbol, exchange, Interval.in_1_hour, 100)

    if df_5m is None or df_15m is None or df_1h is None:
        return None

    return {"5m": df_5m, "15m": df_15m, "1h": df_1h}


def build_scan_cache(force_refresh: bool = False) -> Dict[str, Dict[str, pd.DataFrame]]:
    global _last_scan_data_cache, _last_scan_data_ts

    now = utc_now()

    if (
        not force_refresh
        and _last_scan_data_ts is not None
        and (now - _last_scan_data_ts).total_seconds() < CACHE_TTL_SECONDS
        and _last_scan_data_cache
    ):
        return _last_scan_data_cache

    cache: Dict[str, Dict[str, pd.DataFrame]] = {}

    for market in MARKETS.keys():
        pack = get_market_pack(market)
        if pack is not None:
            cache[market] = pack
        time.sleep(0.15)

    _last_scan_data_cache = cache
    _last_scan_data_ts = now
    gc.collect()
    return cache


# =========================================================
# ANALYSIS PIECES
# =========================================================
def detect_htf_bias(df_1h: pd.DataFrame, df_15m: pd.DataFrame) -> str:
    if df_1h is None or df_15m is None or len(df_1h) < 40 or len(df_15m) < 40:
        return "Nötr"

    c1 = df_1h["close"]
    c15 = df_15m["close"]

    ema20_1h = c1.ewm(span=20).mean().iloc[-1]
    ema50_1h = c1.ewm(span=50).mean().iloc[-1]
    ema20_15m = c15.ewm(span=20).mean().iloc[-1]

    last1 = float(c1.iloc[-1])
    last15 = float(c15.iloc[-1])

    if last1 > ema20_1h > ema50_1h and last15 > ema20_15m:
        return "Yükseliş"

    if last1 < ema20_1h < ema50_1h and last15 < ema20_15m:
        return "Düşüş"

    return "Nötr"


def detect_liquidity_sweep(df_5m: pd.DataFrame) -> Dict[str, Any]:
    if df_5m is None or len(df_5m) < 50:
        return {"label": "Yok", "type": None, "level": None}

    prev = df_5m.iloc[:-1]
    last = df_5m.iloc[-1]

    h = last_swing_high(prev, 55)
    l = last_swing_low(prev, 55)

    if h:
        _, level = h
        if float(last["high"]) > level and float(last["close"]) < level:
            return {"label": "Üst likidite sweep", "type": "bearish", "level": level}

    if l:
        _, level = l
        if float(last["low"]) < level and float(last["close"]) > level:
            return {"label": "Alt likidite sweep", "type": "bullish", "level": level}

    return {"label": "Yok", "type": None, "level": None}


def detect_mss(df_5m: pd.DataFrame) -> Dict[str, Any]:
    if df_5m is None or len(df_5m) < 60:
        return {"label": "Yok", "type": None, "level": None}

    prev = df_5m.iloc[:-1]
    last = df_5m.iloc[-1]

    h = last_swing_high(prev, 50)
    l = last_swing_low(prev, 50)

    if h and float(last["close"]) > h[1]:
        return {"label": "Bullish MSS", "type": "bullish", "level": h[1]}

    if l and float(last["close"]) < l[1]:
        return {"label": "Bearish MSS", "type": "bearish", "level": l[1]}

    return {"label": "Yok", "type": None, "level": None}


def detect_choch(df_15m: pd.DataFrame) -> Dict[str, Any]:
    if df_15m is None or len(df_15m) < 60:
        return {"label": "Yok", "type": None}

    prev = df_15m.iloc[:-1]
    last = df_15m.iloc[-1]

    h = last_swing_high(prev, 45)
    l = last_swing_low(prev, 45)

    if h and float(last["close"]) > h[1]:
        return {"label": "Bullish CHoCH", "type": "bullish"}
    if l and float(last["close"]) < l[1]:
        return {"label": "Bearish CHoCH", "type": "bearish"}

    return {"label": "Yok", "type": None}


def detect_displacement(df_5m: pd.DataFrame) -> Dict[str, Any]:
    if df_5m is None or len(df_5m) < 18:
        return {"label": "Zayıf", "strong": False}

    ar = avg_range(df_5m.iloc[:-1], 14)
    if ar is None:
        return {"label": "Zayıf", "strong": False}

    last = df_5m.iloc[-1]
    rng = candle_range(last)
    body = candle_body(last)

    strong = rng >= ar * 1.45 and body >= ar * 0.70
    return {"label": "Güçlü" if strong else "Zayıf", "strong": strong}


def detect_fvg(df_5m: pd.DataFrame) -> Dict[str, Any]:
    if df_5m is None or len(df_5m) < 3:
        return {"label": "Yok", "type": None, "zone": None}

    a = df_5m.iloc[-3]
    b = df_5m.iloc[-2]
    c = df_5m.iloc[-1]

    if float(a["high"]) < float(c["low"]) and float(b["close"]) > float(b["open"]):
        return {
            "label": "Bullish FVG",
            "type": "bullish",
            "zone": (float(a["high"]), float(c["low"])),
        }

    if float(a["low"]) > float(c["high"]) and float(b["close"]) < float(b["open"]):
        return {
            "label": "Bearish FVG",
            "type": "bearish",
            "zone": (float(c["high"]), float(a["low"])),
        }

    return {"label": "Yok", "type": None, "zone": None}


def detect_ifvg(df_5m: pd.DataFrame) -> Dict[str, Any]:
    if df_5m is None or len(df_5m) < 8:
        return {"label": "Yok", "type": None}

    recent = df_5m.tail(8).reset_index(drop=True)
    left = recent.iloc[:5]
    right = recent.iloc[5:]

    base = detect_fvg(left)
    if base["zone"] is None:
        return {"label": "Yok", "type": None}

    z1, z2 = base["zone"]
    last_close_ = float(right.iloc[-1]["close"])

    if base["type"] == "bullish" and last_close_ < z1:
        return {"label": "Bearish iFVG", "type": "bearish"}

    if base["type"] == "bearish" and last_close_ > z2:
        return {"label": "Bullish iFVG", "type": "bullish"}

    return {"label": "Yok", "type": None}


def detect_cisd(df_5m: pd.DataFrame) -> Dict[str, Any]:
    if df_5m is None or len(df_5m) < 16:
        return {"label": "Yok", "type": None}

    x = df_5m.tail(10).reset_index(drop=True)
    first = x.iloc[:5]
    second = x.iloc[5:]

    first_move = float(first.iloc[-1]["close"] - first.iloc[0]["open"])
    second_move = float(second.iloc[-1]["close"] - second.iloc[0]["open"])

    if first_move < 0 and second_move > abs(first_move) * 0.55:
        return {"label": "Bullish CISD", "type": "bullish"}

    if first_move > 0 and second_move < -abs(first_move) * 0.55:
        return {"label": "Bearish CISD", "type": "bearish"}

    return {"label": "Yok", "type": None}


def detect_pd_zone(df_5m: pd.DataFrame) -> str:
    if df_5m is None or len(df_5m) < 40:
        return "Belirsiz"

    hi = float(df_5m.tail(35)["high"].max())
    lo = float(df_5m.tail(35)["low"].min())
    eq = (hi + lo) / 2
    px = float(df_5m.iloc[-1]["close"])
    return "Discount" if px < eq else "Premium"


def detect_true_order_block(df_5m: pd.DataFrame) -> Dict[str, Any]:
    if df_5m is None or len(df_5m) < 20:
        return {"label": "Yok", "type": None, "zone": None}

    recent = df_5m.tail(12).reset_index(drop=True)

    for i in range(len(recent) - 3, 1, -1):
        cur = recent.iloc[i]
        nxt = recent.iloc[i + 1]

        if float(cur["close"]) < float(cur["open"]):
            if candle_range(nxt) > candle_range(cur) * 1.2 and float(nxt["close"]) > float(cur["high"]):
                return {
                    "label": "Bullish OB",
                    "type": "bullish",
                    "zone": (float(cur["low"]), float(cur["high"])),
                }

    for i in range(len(recent) - 3, 1, -1):
        cur = recent.iloc[i]
        nxt = recent.iloc[i + 1]

        if float(cur["close"]) > float(cur["open"]):
            if candle_range(nxt) > candle_range(cur) * 1.2 and float(nxt["close"]) < float(cur["low"]):
                return {
                    "label": "Bearish OB",
                    "type": "bearish",
                    "zone": (float(cur["low"]), float(cur["high"])),
                }

    return {"label": "Yok", "type": None, "zone": None}


def detect_asia_range_context(df_5m: pd.DataFrame) -> Dict[str, Any]:
    if df_5m is None or "datetime" not in df_5m.columns or len(df_5m) < 60:
        return {"label": "Belirsiz", "high": None, "low": None, "sweep": None}

    df = df_5m.copy()
    df["date"] = df["datetime"].dt.date
    today = utc_now().date()
    rows = df[df["date"] == today]

    if rows.empty:
        return {"label": "Belirsiz", "high": None, "low": None, "sweep": None}

    asia = rows[(rows["datetime"].dt.hour >= 0) & (rows["datetime"].dt.hour < 6)]
    if asia.empty:
        return {"label": "Belirsiz", "high": None, "low": None, "sweep": None}

    asia_high = float(asia["high"].max())
    asia_low = float(asia["low"].min())
    last = df.iloc[-1]

    sweep = None
    if float(last["high"]) > asia_high and float(last["close"]) < asia_high:
        sweep = "asia_high_swept"
    elif float(last["low"]) < asia_low and float(last["close"]) > asia_low:
        sweep = "asia_low_swept"

    label = "Asia range tanımlı"
    if sweep == "asia_high_swept":
        label = "Asia high sweep"
    elif sweep == "asia_low_swept":
        label = "Asia low sweep"

    return {"label": label, "high": asia_high, "low": asia_low, "sweep": sweep}


def detect_smt(primary_df: pd.DataFrame, comparison_df: pd.DataFrame) -> Dict[str, Any]:
    if primary_df is None or comparison_df is None or len(primary_df) < 35 or len(comparison_df) < 35:
        return {"label": "Yok", "type": None}

    p_prev = primary_df.iloc[:-8].tail(20)
    c_prev = comparison_df.iloc[:-8].tail(20)
    p_now = primary_df.tail(8)
    c_now = comparison_df.tail(8)

    p_hi_prev = float(p_prev["high"].max())
    c_hi_prev = float(c_prev["high"].max())
    p_lo_prev = float(p_prev["low"].min())
    c_lo_prev = float(c_prev["low"].min())

    p_hi_now = float(p_now["high"].max())
    c_hi_now = float(c_now["high"].max())
    p_lo_now = float(p_now["low"].min())
    c_lo_now = float(c_now["low"].min())

    if p_lo_now < p_lo_prev and c_lo_now >= c_lo_prev:
        return {"label": "Bullish SMT", "type": "bullish"}

    if p_hi_now > p_hi_prev and c_hi_now <= c_hi_prev:
        return {"label": "Bearish SMT", "type": "bearish"}

    return {"label": "Yok", "type": None}


# =========================================================
# ANALYSIS ENGINE
# =========================================================
def analyze_market(market: str, cache: Optional[Dict[str, Dict[str, pd.DataFrame]]] = None) -> Optional[Dict[str, Any]]:
    data = cache if cache is not None else build_scan_cache(force_refresh=False)

    if market not in data:
        return None

    pack = data[market]
    df_5m = pack["5m"]
    df_15m = pack["15m"]
    df_1h = pack["1h"]

    price = round_price(market, df_last_close(df_5m))
    if price is None:
        return None

    sess = session_info()
    htf_bias = detect_htf_bias(df_1h, df_15m)
    sweep = detect_liquidity_sweep(df_5m)
    mss = detect_mss(df_5m)
    choch = detect_choch(df_15m)
    displacement = detect_displacement(df_5m)
    fvg = detect_fvg(df_5m)
    ifvg = detect_ifvg(df_5m)
    cisd = detect_cisd(df_5m)
    pd_zone = detect_pd_zone(df_5m)
    ob = detect_true_order_block(df_5m)
    asia = detect_asia_range_context(df_5m)

    smt = {"label": "Yok", "type": None}
    pair = SMT_PAIRS.get(market)
    if pair and pair in data:
        smt = detect_smt(df_5m, data[pair]["5m"])

    # scores
    long_score = 0
    short_score = 0

    if htf_bias == "Yükseliş":
        long_score += 20
    elif htf_bias == "Düşüş":
        short_score += 20

    if sweep["type"] == "bullish":
        long_score += 18
    elif sweep["type"] == "bearish":
        short_score += 18

    if asia["sweep"] == "asia_low_swept":
        long_score += 10
    elif asia["sweep"] == "asia_high_swept":
        short_score += 10

    if mss["type"] == "bullish":
        long_score += 18
    elif mss["type"] == "bearish":
        short_score += 18

    if choch["type"] == "bullish":
        long_score += 10
    elif choch["type"] == "bearish":
        short_score += 10

    if displacement["strong"]:
        long_score += 8
        short_score += 8

    if fvg["type"] == "bullish":
        long_score += 8
    elif fvg["type"] == "bearish":
        short_score += 8

    if ifvg["type"] == "bullish":
        long_score += 8
    elif ifvg["type"] == "bearish":
        short_score += 8

    if cisd["type"] == "bullish":
        long_score += 10
    elif cisd["type"] == "bearish":
        short_score += 10

    if pd_zone == "Discount":
        long_score += 8
    elif pd_zone == "Premium":
        short_score += 8

    if ob["type"] == "bullish" and near_zone(price, ob["zone"]):
        long_score += 10
    elif ob["type"] == "bearish" and near_zone(price, ob["zone"]):
        short_score += 10

    if sess["killzone"]:
        long_score += 5
        short_score += 5

    if smt["type"] == "bullish":
        long_score += 7
    elif smt["type"] == "bearish":
        short_score += 7

    candidate_direction = "Bekle"
    confidence = max(long_score, short_score)

    if long_score >= MIN_SIGNAL_CONFIDENCE and long_score > short_score:
        candidate_direction = "LONG"
        confidence = long_score
    elif short_score >= MIN_SIGNAL_CONFIDENCE and short_score > long_score:
        candidate_direction = "SHORT"
        confidence = short_score

    # hard filters
    killzone_pass = sess["killzone"]
    sweep_pass_long = (sweep["type"] == "bullish") or (asia["sweep"] == "asia_low_swept")
    sweep_pass_short = (sweep["type"] == "bearish") or (asia["sweep"] == "asia_high_swept")
    structure_pass_long = (mss["type"] == "bullish") or (choch["type"] == "bullish")
    structure_pass_short = (mss["type"] == "bearish") or (choch["type"] == "bearish")

    retest_pass_long = near_zone(price, ob["zone"]) or near_zone(price, fvg["zone"])
    retest_pass_short = near_zone(price, ob["zone"]) or near_zone(price, fvg["zone"])

    direction = "Bekle"

    if candidate_direction == "LONG":
        if htf_bias == "Yükseliş" and killzone_pass and sweep_pass_long and structure_pass_long:
            direction = "LONG"

    elif candidate_direction == "SHORT":
        if htf_bias == "Düşüş" and killzone_pass and sweep_pass_short and structure_pass_short:
            direction = "SHORT"

    # entry / sl / tp
    ar = avg_range(df_5m, 14)
    if ar is None:
        return None

    if direction == "LONG":
        entry = price
        sl = round_price(market, price - ar * DEFAULT_SL_ATR_MULT)
        tp = round_price(market, price + ar * DEFAULT_TP_ATR_MULT)
    elif direction == "SHORT":
        entry = price
        sl = round_price(market, price + ar * DEFAULT_SL_ATR_MULT)
        tp = round_price(market, price - ar * DEFAULT_TP_ATR_MULT)
    else:
        entry = price
        sl = "-"
        tp = "-"

    ob_text = ob["label"]
    if ob["zone"] is not None:
        ob_text = f"{ob['label']} ({round_price(market, ob['zone'][0])} - {round_price(market, ob['zone'][1])})"

    signal_tier = "Yok"
    if direction != "Bekle":
        extra_strength = 0
        if displacement["strong"]:
            extra_strength += 1
        if (direction == "LONG" and retest_pass_long) or (direction == "SHORT" and retest_pass_short):
            extra_strength += 1
        if confidence >= STRONG_SIGNAL_CONFIDENCE and extra_strength >= 1:
            signal_tier = "A+"
        else:
            signal_tier = "Standart"

    return {
        "market": market,
        "session": sess["session"],
        "killzone": sess["killzone"],
        "price": price,
        "htf_bias": htf_bias,
        "sweep": sweep["label"],
        "mss": mss["label"],
        "choch": choch["label"],
        "fvg": fvg["label"],
        "ifvg": ifvg["label"],
        "cisd": cisd["label"],
        "pd_zone": pd_zone,
        "displacement": displacement["label"],
        "asia_range": asia["label"],
        "smt": smt["label"],
        "true_ob": ob_text,
        "direction": direction,
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "confidence": min(confidence, 100),
        "signal_tier": signal_tier,
        "scores": {"long": long_score, "short": short_score},
        "filters": {
            "killzone": killzone_pass,
            "sweep": sweep_pass_long if direction == "LONG" else sweep_pass_short if direction == "SHORT" else False,
            "structure": structure_pass_long if direction == "LONG" else structure_pass_short if direction == "SHORT" else False,
            "displacement": displacement["strong"],
            "retest": retest_pass_long if direction == "LONG" else retest_pass_short if direction == "SHORT" else False,
        },
    }


# =========================================================
# TEXT BUILDERS
# =========================================================
def build_manual_text(a: Dict[str, Any]) -> str:
    return (
        f"📋 Manuel analiz sonucu\n\n"
        f"Varlık: {a['market']}\n"
        f"Seans: {a['session']}\n"
        f"Killzone: {'Evet' if a['killzone'] else 'Hayır'}\n"
        f"Anlık Fiyat: {a['price']}\n"
        f"HTF Bias: {a['htf_bias']}\n"
        f"Likidite Sweep: {a['sweep']}\n"
        f"MSS: {a['mss']}\n"
        f"CHoCH: {a['choch']}\n"
        f"FVG: {a['fvg']}\n"
        f"iFVG: {a['ifvg']}\n"
        f"CISD: {a['cisd']}\n"
        f"Premium/Discount: {a['pd_zone']}\n"
        f"Displacement: {a['displacement']}\n"
        f"Asia Range: {a['asia_range']}\n"
        f"SMT: {a['smt']}\n"
        f"True Order Block: {a['true_ob']}\n"
        f"Skor: {a['confidence']}/100\n"
        f"Sinyal Kalitesi: {a['signal_tier']}\n"
        f"Killzone filtresi: {'Geçti' if a['filters']['killzone'] else 'Kaldı'}\n"
        f"Sweep filtresi: {'Geçti' if a['filters']['sweep'] else 'Kaldı'}\n"
        f"Yapı filtresi: {'Geçti' if a['filters']['structure'] else 'Kaldı'}\n"
        f"Displacement filtresi: {'Geçti' if a['filters']['displacement'] else 'Kaldı'}\n"
        f"Retest filtresi: {'Geçti' if a['filters']['retest'] else 'Kaldı'}\n"
        f"Durum: {'Sinyal var' if a['direction'] != 'Bekle' else 'Şu an net setup yok'}\n"
        f"Not: Bot şartlar tamam değilse sessiz kalır."
    )


def build_signal_text(a: Dict[str, Any]) -> str:
    return (
        f"🚨 ANALİZ + SİNYAL RAPORU\n\n"
        f"Varlık: {a['market']}\n"
        f"Seans: {a['session']}\n"
        f"Killzone: {'Evet' if a['killzone'] else 'Hayır'}\n"
        f"Anlık Fiyat: {a['price']}\n\n"
        f"HTF Bias: {a['htf_bias']}\n"
        f"Likidite Sweep: {a['sweep']}\n"
        f"MSS: {a['mss']}\n"
        f"CHoCH: {a['choch']}\n"
        f"FVG: {a['fvg']}\n"
        f"iFVG: {a['ifvg']}\n"
        f"CISD: {a['cisd']}\n"
        f"Premium/Discount: {a['pd_zone']}\n"
        f"Displacement: {a['displacement']}\n"
        f"Asia Range: {a['asia_range']}\n"
        f"SMT: {a['smt']}\n"
        f"True Order Block: {a['true_ob']}\n\n"
        f"İşlem Yönü: {a['direction']}\n"
        f"Giriş: {a['entry']}\n"
        f"Zarar Durdur: {a['sl']}\n"
        f"Kar Al: {a['tp']}\n"
        f"Güven Skoru: {a['confidence']}/100\n"
        f"Sinyal Kalitesi: {a['signal_tier']}\n"
        f"Long/Short Skor: {a['scores']['long']} / {a['scores']['short']}\n"
        f"Killzone filtresi: {'Geçti' if a['filters']['killzone'] else 'Kaldı'}\n"
        f"Sweep filtresi: {'Geçti' if a['filters']['sweep'] else 'Kaldı'}\n"
        f"Yapı filtresi: {'Geçti' if a['filters']['structure'] else 'Kaldı'}\n"
        f"Displacement filtresi: {'Geçti' if a['filters']['displacement'] else 'Kaldı'}\n"
        f"Retest filtresi: {'Geçti' if a['filters']['retest'] else 'Kaldı'}\n\n"
        f"⚠️ Not: Bu sinyal otomatik tarama sonucudur. Son kararı yine sen ver."
    )


def build_tp_text(sig: Dict[str, Any], current_price: float) -> str:
    return (
        f"✅ TP oldu\n\n"
        f"Varlık: {sig['market']}\n"
        f"Yön: {sig['direction']}\n"
        f"Giriş: {sig['entry']}\n"
        f"TP: {sig['tp']}\n"
        f"Anlık Fiyat: {current_price}"
    )


def build_sl_text(sig: Dict[str, Any], current_price: float) -> str:
    return (
        f"❌ SL oldu\n\n"
        f"Varlık: {sig['market']}\n"
        f"Yön: {sig['direction']}\n"
        f"Giriş: {sig['entry']}\n"
        f"SL: {sig['sl']}\n"
        f"Anlık Fiyat: {current_price}"
    )


# =========================================================
# SIGNAL MEMORY
# =========================================================
def signal_key(a: Dict[str, Any]) -> str:
    return f"{a['market']}|{a['direction']}|{a['entry']}|{a['sl']}|{a['tp']}"


def signal_recently_sent(market: str, key: str) -> bool:
    recent = STATE["recent_signal_keys"].get(market)
    if not recent:
        return False

    saved_key = recent.get("key")
    ts = recent.get("ts")

    if saved_key != key or not ts:
        return False

    try:
        dt = datetime.fromisoformat(ts)
        if utc_now().replace(tzinfo=None) - dt < timedelta(minutes=SIGNAL_COOLDOWN_MINUTES):
            return True
    except Exception:
        return False

    return False


def remember_signal(market: str, key: str) -> None:
    STATE["recent_signal_keys"][market] = {
        "key": key,
        "ts": utc_now().replace(tzinfo=None).isoformat()
    }
    save_state()


# =========================================================
# ACTIVE SIGNAL TRACKER
# =========================================================
def check_active_signals(cache: Optional[Dict[str, Dict[str, pd.DataFrame]]] = None) -> None:
    active = STATE["active_signals"]
    if not isinstance(active, dict):
        STATE["active_signals"] = {}
        active = STATE["active_signals"]

    if cache is None:
        cache = build_scan_cache(force_refresh=False)

    remove_list: List[str] = []

    for market, sig in active.items():
        if market not in cache:
            continue

        current_price = round_price(market, df_last_close(cache[market]["5m"]))
        if current_price is None:
            continue

        reply_id = sig.get("message_id")

        if sig["direction"] == "LONG":
            if current_price >= sig["tp"]:
                send_telegram_message(build_tp_text(sig, current_price), reply_id)
                remove_list.append(market)
            elif current_price <= sig["sl"]:
                send_telegram_message(build_sl_text(sig, current_price), reply_id)
                remove_list.append(market)

        elif sig["direction"] == "SHORT":
            if current_price <= sig["tp"]:
                send_telegram_message(build_tp_text(sig, current_price), reply_id)
                remove_list.append(market)
            elif current_price >= sig["sl"]:
                send_telegram_message(build_sl_text(sig, current_price), reply_id)
                remove_list.append(market)

    for market in remove_list:
        STATE["active_signals"].pop(market, None)

    if remove_list:
        save_state()


# =========================================================
# SCANNER LOOP
# =========================================================
def scanner_loop() -> None:
    while True:
        try:
            cache = build_scan_cache(force_refresh=True)
            check_active_signals(cache=cache)

            for market in MARKETS.keys():
                if market in STATE["active_signals"]:
                    continue

                a = analyze_market(market, cache=cache)
                if a is None:
                    continue

                if a["direction"] == "Bekle":
                    continue

                key = signal_key(a)
                if signal_recently_sent(market, key):
                    continue

                result = send_telegram_message(build_signal_text(a))
                message_id = None
                if isinstance(result, dict):
                    message_id = result.get("result", {}).get("message_id")

                STATE["active_signals"][market] = {
                    "market": a["market"],
                    "direction": a["direction"],
                    "entry": a["entry"],
                    "sl": a["sl"],
                    "tp": a["tp"],
                    "message_id": message_id,
                    "created_at": utc_now().replace(tzinfo=None).isoformat(),
                }

                remember_signal(market, key)
                time.sleep(0.25)

            STATE["last_scan_at"] = utc_now().replace(tzinfo=None).isoformat()
            save_state()

            # RAM temizliği
            cache.clear()
            gc.collect()
            time.sleep(SCAN_INTERVAL_SECONDS)

        except Exception:
            gc.collect()
            time.sleep(SCAN_INTERVAL_SECONDS)


# =========================================================
# ROUTES
# =========================================================
@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "ok": True,
        "status": "Trade Konseyi optimize sürüm aktif",
        "markets": list(MARKETS.keys()),
        "scan_interval_seconds": SCAN_INTERVAL_SECONDS,
        "active_signals_count": len(STATE["active_signals"]),
    })


@app.route("/status", methods=["GET"])
def status():
    text = (
        f"✅ Sistem durumu\n\n"
        f"Seans: {session_info()['session']}\n"
        f"Aktif sinyal sayısı: {len(STATE['active_signals'])}\n"
        f"İzlenen market sayısı: {len(MARKETS)}\n"
        f"Son tarama: {STATE.get('last_scan_at')}"
    )
    tg = send_telegram_message(text)
    return jsonify({"ok": True, "telegram_result": tg})


@app.route("/manual/<market>", methods=["GET"])
def manual_market(market: str):
    market = market.upper()
    if market not in MARKETS:
        return jsonify({"ok": False, "error": "Geçersiz market"}), 400

    cache = build_scan_cache(force_refresh=True)
    a = analyze_market(market, cache=cache)
    if a is None:
        return jsonify({"ok": False, "error": "Veri alınamadı"}), 500

    if a["direction"] == "Bekle":
        tg = send_telegram_message(build_manual_text(a))
    else:
        tg = send_telegram_message(build_signal_text(a))

    cache.clear()
    gc.collect()
    return jsonify({"ok": True, "analysis": a, "telegram_result": tg})


@app.route("/manual-all", methods=["GET"])
def manual_all():
    cache = build_scan_cache(force_refresh=True)
    results = []
    sent_count = 0

    for market in MARKETS.keys():
        try:
            a = analyze_market(market, cache=cache)
            if a is None:
                results.append({"market": market, "status": "error", "detail": "Veri alınamadı"})
                continue

            if a["direction"] == "Bekle":
                tg = send_telegram_message(build_manual_text(a))
            else:
                tg = send_telegram_message(build_signal_text(a))

            sent_count += 1
            results.append({
                "market": market,
                "status": "sent",
                "direction": a["direction"],
                "confidence": a["confidence"],
                "telegram_ok": tg.get("ok", False) if isinstance(tg, dict) else False,
            })
            time.sleep(0.25)

        except Exception as e:
            results.append({"market": market, "status": "error", "detail": str(e)})

    cache.clear()
    gc.collect()

    return jsonify({
        "ok": True,
        "message": f"{sent_count} market Telegram'a gönderildi.",
        "results": results,
    })


@app.route("/active-signals", methods=["GET"])
def active_signals():
    return jsonify({"ok": True, "active_signals": STATE["active_signals"]})


# =========================================================
# STARTUP
# =========================================================
def start_background() -> None:
    load_state()
    init_tv()
    t = threading.Thread(target=scanner_loop, daemon=True)
    t.start()


start_background()

if __name__ == "__main__":
    load_state()
    init_tv()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)