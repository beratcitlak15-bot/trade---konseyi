from flask import Flask, jsonify, request
import os
import time
import json
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import requests
from tvDatafeed import TvDatafeed, Interval

app = Flask(__name__)

# =========================================================
# ENV / SETTINGS
# =========================================================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
TV_USERNAME = os.getenv("TV_USERNAME")
TV_PASSWORD = os.getenv("TV_PASSWORD")

DEFAULT_ALLOWED = "8463420441"
ALLOWED_CHAT_IDS = [
    int(x.strip()) for x in os.getenv("ALLOWED_CHAT_IDS", DEFAULT_ALLOWED).split(",") if x.strip()
]

STATE_FILE = "bot_state.json"
SCAN_INTERVAL_SECONDS = 30
MIN_SIGNAL_CONFIDENCE = 82
SIGNAL_COOLDOWN_MINUTES = 45
MAX_ACTIVE_SIGNALS = 20

DEFAULT_SL_ATR_MULT = 1.25
DEFAULT_TP_ATR_MULT = 2.50

# =========================================================
# MARKET CONFIG
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


def is_allowed_chat(chat_id: int) -> bool:
    return chat_id in ALLOWED_CHAT_IDS

# =========================================================
# STATE STORAGE
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
# TV DATA
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


def get_hist(market_name: str, interval: Interval, n_bars: int = 250) -> Optional[pd.DataFrame]:
    if tv is None:
        return None

    cfg = MARKETS[market_name]

    try:
        df = tv.get_hist(
            symbol=cfg["symbol"],
            exchange=cfg["exchange"],
            interval=interval,
            n_bars=n_bars,
        )

        if df is None or df.empty:
            return None

        df = df.reset_index()
        if "datetime" in df.columns:
            df["datetime"] = pd.to_datetime(df["datetime"], utc=True, errors="coerce")

        return df
    except Exception:
        return None

# =========================================================
# GENERAL HELPERS
# =========================================================
def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def round_price(market: str, value: Optional[float]) -> Optional[float]:
    if value is None:
        return None

    if market in ["EURUSD", "GBPUSD", "USDJPY"]:
        return round(float(value), 5)

    return round(float(value), 2)


def last_close(df: pd.DataFrame) -> Optional[float]:
    if df is None or df.empty:
        return None
    return float(df.iloc[-1]["close"])


def avg_range(df: pd.DataFrame, lookback: int = 14) -> Optional[float]:
    if df is None or len(df) < lookback:
        return None
    sample = df.tail(lookback)
    return float((sample["high"] - sample["low"]).mean())


def candle_body(row: pd.Series) -> float:
    return abs(float(row["close"] - row["open"]))


def candle_range(row: pd.Series) -> float:
    return float(row["high"] - row["low"])


def local_high(df: pd.DataFrame, idx: int, left: int = 2, right: int = 2) -> bool:
    if idx - left < 0 or idx + right >= len(df):
        return False

    h = float(df.iloc[idx]["high"])
    for i in range(idx - left, idx + right + 1):
        if i == idx:
            continue
        if float(df.iloc[i]["high"]) >= h:
            return False
    return True


def local_low(df: pd.DataFrame, idx: int, left: int = 2, right: int = 2) -> bool:
    if idx - left < 0 or idx + right >= len(df):
        return False

    low = float(df.iloc[idx]["low"])
    for i in range(idx - left, idx + right + 1):
        if i == idx:
            continue
        if float(df.iloc[i]["low"]) <= low:
            return False
    return True


def last_swing_high(df: pd.DataFrame, lookback: int = 80) -> Optional[Tuple[int, float]]:
    if df is None or len(df) < 10:
        return None

    start = max(2, len(df) - lookback)
    result = None

    for i in range(start, len(df) - 2):
        if local_high(df, i):
            result = (i, float(df.iloc[i]["high"]))

    return result


def last_swing_low(df: pd.DataFrame, lookback: int = 80) -> Optional[Tuple[int, float]]:
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


def price_in_zone(price: Optional[float], zone: Optional[Tuple[float, float]]) -> bool:
    if price is None or zone is None:
        return False

    lo, hi = zone
    return lo <= price <= hi


def near_zone(price: Optional[float], zone: Optional[Tuple[float, float]], tolerance_ratio: float = 0.15) -> bool:
    if price is None or zone is None:
        return False

    lo, hi = zone
    width = hi - lo
    if width <= 0:
        return False

    tolerance = width * tolerance_ratio
    return (lo - tolerance) <= price <= (hi + tolerance)


def valid_entry_retest(
    direction: str,
    price: Optional[float],
    ob_zone: Optional[Tuple[float, float]],
    fvg_zone: Optional[Tuple[float, float]],
) -> bool:
    if price is None:
        return False

    in_ob = near_zone(price, ob_zone)
    in_fvg = near_zone(price, fvg_zone)

    if direction in ["LONG", "SHORT"]:
        return in_ob or in_fvg

    return False

# =========================================================
# ICT ANALYSIS COMPONENTS
# =========================================================
def detect_htf_bias(df_4h: pd.DataFrame, df_1h: pd.DataFrame) -> str:
    if df_4h is None or df_1h is None or len(df_4h) < 60 or len(df_1h) < 60:
        return "Nötr"

    close_4h = df_4h["close"]
    close_1h = df_1h["close"]

    ema20_4h = close_4h.ewm(span=20).mean().iloc[-1]
    ema50_4h = close_4h.ewm(span=50).mean().iloc[-1]
    ema20_1h = close_1h.ewm(span=20).mean().iloc[-1]
    ema50_1h = close_1h.ewm(span=50).mean().iloc[-1]

    last_4h = float(close_4h.iloc[-1])
    last_1h = float(close_1h.iloc[-1])

    if last_4h > ema20_4h > ema50_4h and last_1h > ema20_1h > ema50_1h:
        return "Yükseliş"

    if last_4h < ema20_4h < ema50_4h and last_1h < ema20_1h < ema50_1h:
        return "Düşüş"

    return "Nötr"


def detect_liquidity_sweep(df_5m: pd.DataFrame) -> Dict[str, Any]:
    if df_5m is None or len(df_5m) < 50:
        return {"label": "Yok", "type": None, "level": None}

    prev = df_5m.iloc[:-1]
    last = df_5m.iloc[-1]

    h = last_swing_high(prev, 60)
    l = last_swing_low(prev, 60)

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

    if h:
        _, level = h
        if float(last["close"]) > level:
            return {"label": "Bullish MSS", "type": "bullish", "level": level}

    if l:
        _, level = l
        if float(last["close"]) < level:
            return {"label": "Bearish MSS", "type": "bearish", "level": level}

    return {"label": "Yok", "type": None, "level": None}


def detect_choch(df_15m: pd.DataFrame) -> Dict[str, Any]:
    if df_15m is None or len(df_15m) < 80:
        return {"label": "Yok", "type": None}

    prev = df_15m.iloc[:-1]
    last = df_15m.iloc[-1]

    h = last_swing_high(prev, 70)
    l = last_swing_low(prev, 70)

    if h and float(last["close"]) > h[1]:
        return {"label": "Bullish CHoCH", "type": "bullish"}

    if l and float(last["close"]) < l[1]:
        return {"label": "Bearish CHoCH", "type": "bearish"}

    return {"label": "Yok", "type": None}


def detect_displacement(df_5m: pd.DataFrame) -> Dict[str, Any]:
    if df_5m is None or len(df_5m) < 20:
        return {"label": "Zayıf", "strong": False}

    last = df_5m.iloc[-1]
    ar = avg_range(df_5m.iloc[:-1], 14)
    if ar is None:
        return {"label": "Zayıf", "strong": False}

    rng = candle_range(last)
    body = candle_body(last)

    strong = rng >= ar * 1.5 and body >= ar * 0.8
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
            "zone": (float(a["high"]), float(c["low"]))
        }

    if float(a["low"]) > float(c["high"]) and float(b["close"]) < float(b["open"]):
        return {
            "label": "Bearish FVG",
            "type": "bearish",
            "zone": (float(c["high"]), float(a["low"]))
        }

    return {"label": "Yok", "type": None, "zone": None}


def detect_ifvg(df_5m: pd.DataFrame) -> Dict[str, Any]:
    if df_5m is None or len(df_5m) < 8:
        return {"label": "Yok", "type": None}

    recent = df_5m.tail(8).reset_index(drop=True)

    mid = recent.iloc[:5]
    end = recent.iloc[5:]

    fvg = detect_fvg(mid)

    if fvg["type"] == "bullish" and fvg["zone"] is not None:
        zone_low, zone_high = fvg["zone"]
        last_close_ = float(end.iloc[-1]["close"])
        if last_close_ < zone_low:
            return {"label": "Bearish iFVG", "type": "bearish"}

    if fvg["type"] == "bearish" and fvg["zone"] is not None:
        zone_low, zone_high = fvg["zone"]
        last_close_ = float(end.iloc[-1]["close"])
        if last_close_ > zone_high:
            return {"label": "Bullish iFVG", "type": "bullish"}

    return {"label": "Yok", "type": None}


def detect_cisd(df_5m: pd.DataFrame) -> Dict[str, Any]:
    if df_5m is None or len(df_5m) < 25:
        return {"label": "Yok", "type": None}

    recent = df_5m.tail(10).reset_index(drop=True)
    first_half = recent.iloc[:5]
    second_half = recent.iloc[5:]

    first_move = float(first_half.iloc[-1]["close"] - first_half.iloc[0]["open"])
    second_move = float(second_half.iloc[-1]["close"] - second_half.iloc[0]["open"])

    if first_move < 0 and second_move > abs(first_move) * 0.6:
        return {"label": "Bullish CISD", "type": "bullish"}

    if first_move > 0 and second_move < -abs(first_move) * 0.6:
        return {"label": "Bearish CISD", "type": "bearish"}

    return {"label": "Yok", "type": None}


def detect_pd_zone(df_5m: pd.DataFrame) -> str:
    if df_5m is None or len(df_5m) < 50:
        return "Belirsiz"

    hi = float(df_5m.tail(40)["high"].max())
    lo = float(df_5m.tail(40)["low"].min())
    eq = (hi + lo) / 2
    close_now = float(df_5m.iloc[-1]["close"])

    return "Discount" if close_now < eq else "Premium"


def detect_true_order_block(df_5m: pd.DataFrame) -> Dict[str, Any]:
    if df_5m is None or len(df_5m) < 30:
        return {"label": "Yok", "type": None, "zone": None}

    recent = df_5m.tail(15).reset_index(drop=True)

    for i in range(len(recent) - 3, 1, -1):
        cur = recent.iloc[i]
        nxt = recent.iloc[i + 1]

        if float(cur["close"]) < float(cur["open"]):
            if candle_range(nxt) > candle_range(cur) * 1.2 and float(nxt["close"]) > float(cur["high"]):
                return {
                    "label": "Bullish OB",
                    "type": "bullish",
                    "zone": (float(cur["low"]), float(cur["high"]))
                }

    for i in range(len(recent) - 3, 1, -1):
        cur = recent.iloc[i]
        nxt = recent.iloc[i + 1]

        if float(cur["close"]) > float(cur["open"]):
            if candle_range(nxt) > candle_range(cur) * 1.2 and float(nxt["close"]) < float(cur["low"]):
                return {
                    "label": "Bearish OB",
                    "type": "bearish",
                    "zone": (float(cur["low"]), float(cur["high"]))
                }

    return {"label": "Yok", "type": None, "zone": None}


def detect_asia_range_context(df_5m: pd.DataFrame) -> Dict[str, Any]:
    if df_5m is None or "datetime" not in df_5m.columns or len(df_5m) < 80:
        return {"label": "Belirsiz", "high": None, "low": None, "sweep": None}

    df = df_5m.copy()
    df["date"] = df["datetime"].dt.date
    today = utc_now().date()
    today_rows = df[df["date"] == today]

    if today_rows.empty:
        return {"label": "Belirsiz", "high": None, "low": None, "sweep": None}

    asia = today_rows[(today_rows["datetime"].dt.hour >= 0) & (today_rows["datetime"].dt.hour < 6)]
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


def detect_smt(primary_market: str, primary_df: pd.DataFrame, comparison_df: pd.DataFrame) -> Dict[str, Any]:
    if primary_df is None or comparison_df is None or len(primary_df) < 30 or len(comparison_df) < 30:
        return {"label": "Yok", "type": None}

    p_last = primary_df.tail(10)
    c_last = comparison_df.tail(10)

    p_hi_prev = float(primary_df.iloc[:-10].tail(20)["high"].max())
    c_hi_prev = float(comparison_df.iloc[:-10].tail(20)["high"].max())
    p_lo_prev = float(primary_df.iloc[:-10].tail(20)["low"].min())
    c_lo_prev = float(comparison_df.iloc[:-10].tail(20)["low"].min())

    p_hi_now = float(p_last["high"].max())
    c_hi_now = float(c_last["high"].max())
    p_lo_now = float(p_last["low"].min())
    c_lo_now = float(c_last["low"].min())

    if p_lo_now < p_lo_prev and c_lo_now >= c_lo_prev:
        return {"label": "Bullish SMT", "type": "bullish"}

    if p_hi_now > p_hi_prev and c_hi_now <= c_hi_prev:
        return {"label": "Bearish SMT", "type": "bearish"}

    return {"label": "Yok", "type": None}

# =========================================================
# ANALYSIS ENGINE
# =========================================================
def analyze_market(market_name: str) -> Optional[Dict[str, Any]]:
    df_5m = get_hist(market_name, Interval.in_5_minute, 250)
    df_15m = get_hist(market_name, Interval.in_15_minute, 250)
    df_1h = get_hist(market_name, Interval.in_1_hour, 250)
    df_4h = get_hist(market_name, Interval.in_4_hour, 250)

    if any(x is None for x in [df_5m, df_15m, df_1h, df_4h]):
        return None

    price = round_price(market_name, last_close(df_5m))
    if price is None:
        return None

    htf_bias = detect_htf_bias(df_4h, df_1h)
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
    sess = session_info()

    smt = {"label": "Yok", "type": None}
    pair = SMT_PAIRS.get(market_name)
    if pair and pair in MARKETS:
        pair_df = get_hist(pair, Interval.in_5_minute, 250)
        if pair_df is not None:
            smt = detect_smt(market_name, df_5m, pair_df)

    long_score = 0
    short_score = 0

    if htf_bias == "Yükseliş":
        long_score += 20
    elif htf_bias == "Düşüş":
        short_score += 20

    if sweep["type"] == "bullish":
        long_score += 15
    elif sweep["type"] == "bearish":
        short_score += 15

    if mss["type"] == "bullish":
        long_score += 18
    elif mss["type"] == "bearish":
        short_score += 18

    if choch["type"] == "bullish":
        long_score += 8
    elif choch["type"] == "bearish":
        short_score += 8

    if displacement["strong"]:
        long_score += 10
        short_score += 10

    if fvg["type"] == "bullish":
        long_score += 8
    elif fvg["type"] == "bearish":
        short_score += 8

    if ifvg["type"] == "bullish":
        long_score += 10
    elif ifvg["type"] == "bearish":
        short_score += 10

    if cisd["type"] == "bullish":
        long_score += 10
    elif cisd["type"] == "bearish":
        short_score += 10

    if pd_zone == "Discount":
        long_score += 8
    elif pd_zone == "Premium":
        short_score += 8

    if ob["type"] == "bullish" and price_in_zone(price, ob["zone"]):
        long_score += 12
    elif ob["type"] == "bearish" and price_in_zone(price, ob["zone"]):
        short_score += 12

    if asia["sweep"] == "asia_low_swept":
        long_score += 8
    elif asia["sweep"] == "asia_high_swept":
        short_score += 8

    if sess["killzone"]:
        long_score += 5
        short_score += 5

    if smt["type"] == "bullish":
        long_score += 8
    elif smt["type"] == "bearish":
        short_score += 8

    direction = "Bekle"
    confidence = max(long_score, short_score)

    candidate_direction = "Bekle"
    if long_score >= MIN_SIGNAL_CONFIDENCE and long_score > short_score:
        candidate_direction = "LONG"
        confidence = long_score
    elif short_score >= MIN_SIGNAL_CONFIDENCE and short_score > long_score:
        candidate_direction = "SHORT"
        confidence = short_score

    sweep_required = False
    killzone_required = sess["killzone"]
    displacement_required = displacement["strong"]
    entry_retest_required = False

    fvg_zone = fvg["zone"] if fvg["zone"] is not None else None
    ob_zone = ob["zone"] if ob["zone"] is not None else None

    if candidate_direction == "LONG":
        sweep_required = sweep["type"] == "bullish"
        entry_retest_required = valid_entry_retest("LONG", price, ob_zone, fvg_zone)

        if (
            htf_bias == "Yükseliş"
            and sweep_required
            and killzone_required
            and displacement_required
            and entry_retest_required
            and (mss["type"] == "bullish" or choch["type"] == "bullish")
        ):
            direction = "LONG"

    elif candidate_direction == "SHORT":
        sweep_required = sweep["type"] == "bearish"
        entry_retest_required = valid_entry_retest("SHORT", price, ob_zone, fvg_zone)

        if (
            htf_bias == "Düşüş"
            and sweep_required
            and killzone_required
            and displacement_required
            and entry_retest_required
            and (mss["type"] == "bearish" or choch["type"] == "bearish")
        ):
            direction = "SHORT"

    ar = avg_range(df_5m, 14)
    if ar is None:
        return None

    if direction == "LONG":
        entry = price
        sl = round_price(market_name, price - ar * DEFAULT_SL_ATR_MULT)
        tp = round_price(market_name, price + ar * DEFAULT_TP_ATR_MULT)
    elif direction == "SHORT":
        entry = price
        sl = round_price(market_name, price + ar * DEFAULT_SL_ATR_MULT)
        tp = round_price(market_name, price - ar * DEFAULT_TP_ATR_MULT)
    else:
        entry = price
        sl = "-"
        tp = "-"

    ob_text = ob["label"]
    if ob["zone"] is not None:
        ob_text = f"{ob['label']} ({round_price(market_name, ob['zone'][0])} - {round_price(market_name, ob['zone'][1])})"

    return {
        "market": market_name,
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
        "scores": {"long": long_score, "short": short_score},
        "filters": {
            "killzone_required": killzone_required,
            "displacement_required": displacement_required,
            "sweep_required": sweep_required,
            "entry_retest_required": entry_retest_required,
        },
    }

# =========================================================
# SIGNAL HELPERS
# =========================================================
def signal_key(analysis: Dict[str, Any]) -> str:
    return (
        f"{analysis['market']}|{analysis['direction']}|"
        f"{analysis['entry']}|{analysis['sl']}|{analysis['tp']}"
    )


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
        f"Long/Short Skor: {a['scores']['long']} / {a['scores']['short']}\n"
        f"Killzone filtresi: {'Geçti' if a['filters']['killzone_required'] else 'Kaldı'}\n"
        f"Sweep filtresi: {'Geçti' if a['filters']['sweep_required'] else 'Kaldı'}\n"
        f"Displacement filtresi: {'Geçti' if a['filters']['displacement_required'] else 'Kaldı'}\n"
        f"Retest filtresi: {'Geçti' if a['filters']['entry_retest_required'] else 'Kaldı'}\n\n"
        f"⚠️ Not: Bu sinyal otomatik profesyonel tarama sonucudur. Son kararı yine sen ver."
    )


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
        f"Killzone filtresi: {'Geçti' if a['filters']['killzone_required'] else 'Kaldı'}\n"
        f"Sweep filtresi: {'Geçti' if a['filters']['sweep_required'] else 'Kaldı'}\n"
        f"Displacement filtresi: {'Geçti' if a['filters']['displacement_required'] else 'Kaldı'}\n"
        f"Retest filtresi: {'Geçti' if a['filters']['entry_retest_required'] else 'Kaldı'}\n"
        f"Durum: Şu an net setup yok.\n"
        f"Not: Bot sessiz kalır."
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
# ACTIVE SIGNAL TRACKER
# =========================================================
def check_active_signals() -> None:
    active = STATE["active_signals"]

    if not isinstance(active, dict):
        STATE["active_signals"] = {}
        active = STATE["active_signals"]

    remove_list: List[str] = []

    for market, sig in active.items():
        df_5m = get_hist(market, Interval.in_5_minute, 5)
        if df_5m is None:
            continue

        current_price = round_price(market, last_close(df_5m))
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
# AUTO SCANNER
# =========================================================
def scanner_loop() -> None:
    while True:
        try:
            check_active_signals()

            if len(STATE["active_signals"]) > MAX_ACTIVE_SIGNALS:
                time.sleep(SCAN_INTERVAL_SECONDS)
                continue

            for market in MARKETS.keys():
                if market in STATE["active_signals"]:
                    continue

                a = analyze_market(market)
                if a is None:
                    continue

                if a["direction"] == "Bekle":
                    continue

                if a["confidence"] < MIN_SIGNAL_CONFIDENCE:
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
                save_state()

            STATE["last_scan_at"] = utc_now().replace(tzinfo=None).isoformat()
            save_state()
            time.sleep(SCAN_INTERVAL_SECONDS)

        except Exception:
            time.sleep(SCAN_INTERVAL_SECONDS)

# =========================================================
# ROUTES
# =========================================================
@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "ok": True,
        "status": "Trade Konseyi PRO aktif",
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


@app.route("/test", methods=["GET"])
def test():
    data = {}
    for market in MARKETS.keys():
        a = analyze_market(market)
        data[market] = a if a else {"error": "veri alınamadı"}
    return jsonify({"ok": True, "data": data})


@app.route("/manual/<market>", methods=["GET"])
def manual_market(market: str):
    market = market.upper()

    if market not in MARKETS:
        return jsonify({"ok": False, "error": "Geçersiz market"}), 400

    a = analyze_market(market)
    if a is None:
        return jsonify({"ok": False, "error": "Veri alınamadı"}), 500

    if a["direction"] == "Bekle":
        tg = send_telegram_message(build_manual_text(a))
    else:
        tg = send_telegram_message(build_signal_text(a))

    return jsonify({"ok": True, "analysis": a, "telegram_result": tg})


@app.route("/manual-all", methods=["GET"])
def manual_all():
    results = []
    sent_count = 0

    for market in MARKETS.keys():
        try:
            a = analyze_market(market)

            if a is None:
                results.append({
                    "market": market,
                    "status": "error",
                    "detail": "Veri alınamadı"
                })
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
                "telegram_ok": tg.get("ok", False) if isinstance(tg, dict) else False
            })

        except Exception as e:
            results.append({
                "market": market,
                "status": "error",
                "detail": str(e)
            })

    return jsonify({
        "ok": True,
        "message": f"{sent_count} market Telegram'a gönderildi.",
        "results": results
    })


@app.route("/active-signals", methods=["GET"])
def active_signals():
    return jsonify({"ok": True, "active_signals": STATE["active_signals"]})


@app.route("/telegram-webhook", methods=["POST"])
def telegram_webhook():
    data = request.get_json(silent=True) or {}
    message = data.get("message", {})
    chat = message.get("chat", {})
    chat_id = chat.get("id")

    if not chat_id or not is_allowed_chat(chat_id):
        return jsonify({"ok": True})

    text = message.get("text", "").strip()

    if text == "/start":
        send_telegram_message("✅ Trade Konseyi PRO aktif. Yetkili kullanıcı doğrulandı.")

    elif text == "/markets":
        send_telegram_message("İzlenen marketler:\n" + "\n".join(MARKETS.keys()))

    elif text == "/status":
        send_telegram_message(
            f"✅ Sistem durumu\n\n"
            f"Seans: {session_info()['session']}\n"
            f"Aktif sinyal sayısı: {len(STATE['active_signals'])}\n"
            f"İzlenen market sayısı: {len(MARKETS)}"
        )

    elif text.lower().startswith("/manual "):
        market = text.lower().replace("/manual ", "").upper()
        if market in MARKETS:
            a = analyze_market(market)
            if a:
                if a["direction"] == "Bekle":
                    send_telegram_message(build_manual_text(a))
                else:
                    send_telegram_message(build_signal_text(a))

    return jsonify({"ok": True})

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