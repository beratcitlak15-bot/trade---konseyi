import os
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
TIMEFRAME = "15min"
OUTPUTSIZE = 180

# ELITE mod:
# A ve A+ sinyal gönderir ama sniper kadar aşırı katı değildir
MIN_SIGNAL_SCORE = 80

MARKETS = [
    "EUR/USD",
    "GBP/USD",
    "XAU/USD",
    "USD/JPY",
    "AUD/USD",
]

http = requests.Session()

# =========================================================
# UTILS
# =========================================================
def safe_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except Exception:
        return None


def now_str() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


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


def get_killzone_label(now_utc: Optional[datetime] = None) -> str:
    dt = now_utc or datetime.now(UTC)
    hour = dt.hour

    if 7 <= hour < 10:
        return "London Killzone"
    if 12 <= hour < 15:
        return "New York Killzone"
    return "Killzone Dışı"


def is_killzone_active(now_utc: Optional[datetime] = None) -> bool:
    return get_killzone_label(now_utc) != "Killzone Dışı"


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


def get_recent_session_box(candles: List[Dict[str, Any]], session_name: str) -> Optional[Dict[str, Any]]:
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


def equal_highs_exists(candles: List[Dict[str, Any]], tolerance_ratio: float = 0.0008) -> Optional[float]:
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


def equal_lows_exists(candles: List[Dict[str, Any]], tolerance_ratio: float = 0.0008) -> Optional[float]:
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
# DISPLACEMENT
# =========================================================
def detect_displacement(candles: List[Dict[str, Any]]) -> str:
    if len(candles) < 12:
        return "Zayıf"

    last = candles[-1]
    avg_b = average_body(candles[-11:-1], 10)
    avg_r = average_range(candles[-11:-1], 10)

    if avg_b == 0 or avg_r == 0:
        return "Zayıf"

    body = candle_body(last)
    rng = candle_range(last)

    if rng == 0:
        return "Zayıf"

    strong = body >= avg_b * 1.8 and rng >= avg_r * 1.4
    medium = body >= avg_b * 1.2 and rng >= avg_r * 1.1

    if strong:
        return "Güçlü"
    if medium:
        return "Orta"
    return "Zayıf"


# =========================================================
# HTF BIAS (PRO)
# =========================================================
def detect_htf_bias_advanced(candles: List[Dict[str, Any]]) -> str:
    if len(candles) < 40:
        return "Nötr"

    swings = collect_swings(candles)
    prev_high, last_high = last_two(swings["highs"])
    prev_low, last_low = last_two(swings["lows"])

    if not prev_high or not last_high or not prev_low or not last_low:
        return "Nötr"

    if last_high["price"] > prev_high["price"] and last_low["price"] > prev_low["price"]:
        return "Yükseliş"

    if last_high["price"] < prev_high["price"] and last_low["price"] < prev_low["price"]:
        return "Düşüş"

    return "Nötr"


# =========================================================
# MSS / CHOCH (GERÇEK STRUCTURE)
# =========================================================
def detect_mss_choch_advanced(
    candles: List[Dict[str, Any]],
    htf_bias: str,
) -> Dict[str, str]:

    if len(candles) < 25:
        return {"mss": "Yok", "choch": "Yok"}

    swings = collect_swings(candles)
    highs = swings["highs"]
    lows = swings["lows"]

    if len(highs) < 2 or len(lows) < 2:
        return {"mss": "Yok", "choch": "Yok"}

    prev_high, last_high = last_two(highs)
    prev_low, last_low = last_two(lows)

    last_close = candles[-1]["close"]

    mss = "Yok"
    choch = "Yok"

    if last_close > last_high["price"]:
        if htf_bias == "Düşüş":
            choch = "Bullish CHoCH"
        else:
            mss = "Bullish MSS"

    elif last_close < last_low["price"]:
        if htf_bias == "Yükseliş":
            choch = "Bearish CHoCH"
        else:
            mss = "Bearish MSS"

    return {"mss": mss, "choch": choch}


# =========================================================
# LIQUIDITY SWEEP (PRO)
# =========================================================
def detect_liquidity_sweep_advanced(candles: List[Dict[str, Any]]) -> Dict[str, Any]:
    if len(candles) < 30:
        return {"label": "Yok", "source": "Yok"}

    last = candles[-1]

    prev_day = get_previous_day_levels(candles)
    asia = get_recent_session_box(candles[-80:], "Asia")

    eqh = equal_highs_exists(candles)
    eql = equal_lows_exists(candles)

    # ÜST sweep
    if prev_day["pdh"] and last["high"] > prev_day["pdh"] and last["close"] < prev_day["pdh"]:
        return {"label": "Üst likidite sweep", "source": "PDH"}

    if asia and last["high"] > asia["high"] and last["close"] < asia["high"]:
        return {"label": "Üst likidite sweep", "source": "Asia High"}

    if eqh and last["high"] > eqh and last["close"] < eqh:
        return {"label": "Üst likidite sweep", "source": "EQH"}

    # ALT sweep
    if prev_day["pdl"] and last["low"] < prev_day["pdl"] and last["close"] > prev_day["pdl"]:
        return {"label": "Alt likidite sweep", "source": "PDL"}

    if asia and last["low"] < asia["low"] and last["close"] > asia["low"]:
        return {"label": "Alt likidite sweep", "source": "Asia Low"}

    if eql and last["low"] < eql and last["close"] > eql:
        return {"label": "Alt likidite sweep", "source": "EQL"}

    return {"label": "Yok", "source": "Yok"}

# =========================================================
# PREMIUM / DISCOUNT (ADVANCED)
# =========================================================
def detect_premium_discount_advanced(candles: List[Dict[str, Any]]) -> str:
    if len(candles) < 30:
        return "Nötr"

    swings = collect_swings(candles)
    highs = swings["highs"]
    lows = swings["lows"]

    if len(highs) < 2 or len(lows) < 2:
        return "Nötr"

    recent_highs = highs[-4:] if len(highs) >= 4 else highs
    recent_lows = lows[-4:] if len(lows) >= 4 else lows

    dealing_high = max(x["price"] for x in recent_highs)
    dealing_low = min(x["price"] for x in recent_lows)

    if dealing_high <= dealing_low:
        return "Nötr"

    equilibrium = (dealing_high + dealing_low) / 2
    price = candles[-1]["close"]

    if price > equilibrium:
        return "Premium"
    if price < equilibrium:
        return "Discount"
    return "Nötr"

# =========================================================
# FVG (SMART)
# =========================================================
def detect_fvg_advanced(candles: List[Dict[str, Any]]) -> Dict[str, Any]:
    if len(candles) < 6:
        return {"label": "Yok", "zone": None}

    for i in range(len(candles) - 3, len(candles) - 8, -1):
        a = candles[i - 1]
        c = candles[i + 1]

        if c["low"] > a["high"]:
            return {
                "label": "Bullish FVG",
                "zone": (a["high"], c["low"]),
            }

        if c["high"] < a["low"]:
            return {
                "label": "Bearish FVG",
                "zone": (c["high"], a["low"]),
            }

    return {"label": "Yok", "zone": None}


# =========================================================
# ORDER BLOCK (ELITE)
# =========================================================
def detect_true_order_block_advanced(
    candles: List[Dict[str, Any]],
    direction: str,
) -> Dict[str, Any]:

    if len(candles) < 12:
        return {"label": "Yok", "zone": None}

    for i in range(len(candles) - 2, len(candles) - 10, -1):
        c = candles[i]

        if direction == "LONG" and c["close"] < c["open"]:
            return {
                "label": "Bullish OB",
                "zone": (c["low"], c["high"]),
            }

        if direction == "SHORT" and c["close"] > c["open"]:
            return {
                "label": "Bearish OB",
                "zone": (c["low"], c["high"]),
            }

    return {"label": "Yok", "zone": None}


# =========================================================
# SMT (ELITE - OPSİYONEL)
# =========================================================
def detect_smt_for_symbol_advanced(
    symbol: str,
    market_candles: Dict[str, List[Dict[str, Any]]],
) -> Dict[str, str]:

    pairs = {
        "EUR/USD": ["GBP/USD", "AUD/USD"],
        "GBP/USD": ["EUR/USD", "AUD/USD"],
        "AUD/USD": ["EUR/USD", "GBP/USD"],
    }

    if symbol not in pairs:
        return {"smt": "Yok", "strength": "Zayıf"}

    base = market_candles.get(symbol)
    if not base or len(base) < 20:
        return {"smt": "Yok", "strength": "Zayıf"}

    for other_symbol in pairs[symbol]:
        other = market_candles.get(other_symbol)
        if not other or len(other) < 20:
            continue

        base_high = base[-1]["high"]
        other_high = other[-1]["high"]

        base_prev = base[-2]["high"]
        other_prev = other[-2]["high"]

        if base_high > base_prev and other_high <= other_prev:
            return {"smt": "Bearish SMT", "strength": "Orta"}

        base_low = base[-1]["low"]
        other_low = other[-1]["low"]

        base_prev_low = base[-2]["low"]
        other_prev_low = other[-2]["low"]

        if base_low < base_prev_low and other_low >= other_prev_low:
            return {"smt": "Bullish SMT", "strength": "Orta"}

    return {"smt": "Yok", "strength": "Zayıf"}


# =========================================================
# ELITE TRADE FILTERS
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


# =========================================================
# NO-CHASE (ELITE VERSION - YUMUŞAK)
# =========================================================
def is_no_chase(
    candles: List[Dict[str, Any]],
    entry: float,
    direction: str,
) -> bool:
    if len(candles) < 5:
        return False

    last = candles[-1]["close"]
    avg = average_range(candles[-10:], 10)

    if avg == 0:
        return False

    # sadece aşırı kaçmışsa iptal
    if direction == "LONG":
        if last > entry + avg * 1.5:
            return True

    if direction == "SHORT":
        if last < entry - avg * 1.5:
            return True

    return False


# =========================================================
# ELITE SCORING
# =========================================================
def score_signal_advanced(
    direction: str,
    htf_bias: str,
    fvg: str,
    premium_discount: str,
    smt: str,
    killzone_active: bool,
    sweep_source: str,
    displacement: str,
) -> Dict[str, Any]:

    score = 40

    # HTF uyum
    if direction == "LONG" and htf_bias == "Yükseliş":
        score += 15
    if direction == "SHORT" and htf_bias == "Düşüş":
        score += 15

    # SMT bonus (zorunlu değil)
    if smt == "Bullish SMT" or smt == "Bearish SMT":
        score += 10

    # FVG
    if "FVG" in fvg:
        score += 10

    # PD
    if direction == "LONG" and premium_discount == "Discount":
        score += 10
    if direction == "SHORT" and premium_discount == "Premium":
        score += 10

    # Sweep kalitesi
    if sweep_source in ("PDH", "PDL", "Asia High", "Asia Low", "EQH", "EQL"):
        score += 5

    # Displacement
    if displacement == "Güçlü":
        score += 10

    # Killzone
    if killzone_active:
        score += 5

    if score >= 90:
        quality = "A+"
    elif score >= 80:
        quality = "A"
    else:
        quality = "B"

    return {"score": score, "quality": quality}


# =========================================================
# TRADE LEVELS
# =========================================================
def build_trade_levels(
    candles: List[Dict[str, Any]],
    direction: str,
    ob_zone,
    fvg_zone,
):
    price = candles[-1]["close"]

    if direction == "LONG":
        entry = ob_zone[0] if ob_zone else price
        sl = min(x["low"] for x in candles[-10:])
        tp = entry + (entry - sl) * 2

    else:
        entry = ob_zone[1] if ob_zone else price
        sl = max(x["high"] for x in candles[-10:])
        tp = entry - (sl - entry) * 2

    return {"entry": entry, "sl": sl, "tp": tp}


# =========================================================
# FINAL ANALYZE ENGINE
# =========================================================
def analyze_symbol(
    symbol: str,
    raw_data_map,
    market_candles,
):

    candles = market_candles.get(symbol)
    if not candles or len(candles) < 40:
        return None

    price = candles[-1]["close"]

    htf = detect_htf_bias_advanced(candles)

    structure = detect_mss_choch_advanced(candles, htf)
    mss = structure["mss"]
    choch = structure["choch"]

    sweep_data = detect_liquidity_sweep_advanced(candles)
    sweep = sweep_data["label"]

    displacement = detect_displacement(candles)

    fvg_data = detect_fvg_advanced(candles)
    fvg = fvg_data["label"]

    pd = detect_premium_discount_advanced(candles)

    smt_data = detect_smt_for_symbol_advanced(symbol, market_candles)
    smt = smt_data["smt"]

    direction = "YOK"

    if has_required_long_conditions(sweep, mss, choch, displacement):
        direction = "LONG"

    elif has_required_short_conditions(sweep, mss, choch, displacement):
        direction = "SHORT"

    if direction == "YOK":
        return None

    ob_data = detect_true_order_block_advanced(candles, direction)

    levels = build_trade_levels(
        candles,
        direction,
        ob_data["zone"],
        fvg_data["zone"],
    )

    # NO CHASE
    if is_no_chase(candles, levels["entry"], direction):
        print(f"{symbol} skip (no-chase)")
        return None

    scored = score_signal_advanced(
        direction,
        htf,
        fvg,
        pd,
        smt,
        is_killzone_active(),
        sweep_data["source"],
        displacement,
    )

    return {
        "symbol": symbol,
        "price": price,
        "direction": direction,
        "entry": levels["entry"],
        "sl": levels["sl"],
        "tp": levels["tp"],
        "score": scored["score"],
        "quality": scored["quality"],
        "htf": htf,
        "mss": mss,
        "choch": choch,
        "smt": smt,
        "killzone": get_killzone_label(),
    }


# =========================================================
# MESSAGE
# =========================================================
def format_signal_message(r):
    return (
        f"🔥 ELITE ICT SIGNAL\n\n"
        f"{r['symbol']} | {r['direction']}\n\n"
        f"Entry: {r['entry']:.5f}\n"
        f"SL: {r['sl']:.5f}\n"
        f"TP: {r['tp']:.5f}\n\n"
        f"Score: {r['score']} ({r['quality']})\n\n"
        f"HTF: {r['htf']}\n"
        f"MSS: {r['mss']}\n"
        f"CHoCH: {r['choch']}\n"
        f"SMT: {r['smt']}\n"
        f"Session: {r['killzone']}"
    )


# =========================================================
# MAIN RUN
# =========================================================
def run_scan():

    print("=" * 50)
    print("ELITE SCAN START")

    raw_data_map = {}
    market_candles = {}

    for symbol in MARKETS:
        raw = fetch_twelvedata_series(symbol)
        if raw:
            raw_data_map[symbol] = raw
            market_candles[symbol] = build_candles(raw)

    for symbol in MARKETS:
        result = analyze_symbol(symbol, raw_data_map, market_candles)

        if not result:
            continue

        if result["score"] >= MIN_SIGNAL_SCORE and result["quality"] in ("A", "A+"):
            msg = format_signal_message(result)
            send_telegram_message(msg)
            print(f"{symbol} SIGNAL")

    print("SCAN END")
    print("=" * 50)


if __name__ == "__main__":
    run_scan()