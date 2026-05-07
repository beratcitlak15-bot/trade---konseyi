"""
ELITE SNIPER BACKTEST MODÜLÜ
=============================
TwelveData'dan mevcut veriyi çeker ve botun tüm mantığını
geçmiş veriye uygulayarak performans raporu üretir.

Kullanım:
    python backtest.py

Çıktı:
    backtest_report.json → Ham sonuçlar
    backtest_results.txt → Okunabilir rapor
    bot_state.json → Canlı bota beslenen win rate verileri
"""

import csv
import glob
import json
import os
import re
import time
from datetime import datetime, UTC
from typing import Any, Dict, List, Optional, Tuple

import requests

# =========================================================
# BACKTEST AYARLARI
# =========================================================
TWELVEDATA_API_KEY = os.getenv("TWELVEDATA_API_KEY", "").strip()
BACKTEST_OUTPUTSIZE = 5000 # TwelveData'dan çekilecek maksimum bar
BACKTEST_REPORT = "backtest_report.json"
BACKTEST_TXT = "backtest_results.txt"
BOT_STATE_FILE = "bot_state.json"

# Backtest edilecek pariteler
BACKTEST_PAIRS = [
    "EUR/USD", "GBP/USD", "USD/JPY", "AUD/USD",
    "USD/CHF", "USD/CAD", "EUR/AUD", "GBP/JPY", "EUR/JPY",
]

# Zaman dilimleri
BACKTEST_TIMEFRAMES = {
    "5min": {"outputsize": 5000, "ttl": 0},
    "15min": {"outputsize": 5000, "ttl": 0},
    "1h": {"outputsize": 5000, "ttl": 0},
    "4h": {"outputsize": 5000, "ttl": 0},
    "1week": {"outputsize": 500, "ttl": 0},
}

# Dukascopy dosya adı eşleştirmesi
# Dukascopy format: EURUSD_5 Mins_Bid_2023.01.01_2024.12.31.xls
DATA_FOLDER = "backtest_data" # XLS dosyalarının bulunduğu klasör

DUKASCOPY_TF_MAP = {
    "5min": "5 Mins",
    "15min": "15 Mins",
    "1h": "Hourly",
    "4h": "4 Hours",
    "1week": "Weekly",
}

DUKASCOPY_SYMBOL_MAP = {
    "EUR/USD": "EURUSD",
    "GBP/USD": "GBPUSD",
    "USD/JPY": "USDJPY",
    "AUD/USD": "AUDUSD",
    "USD/CHF": "USDCHF",
    "USD/CAD": "USDCAD",
    "EUR/AUD": "EURAUD",
    "GBP/JPY": "GBPJPY",
    "EUR/JPY": "EURJPY",
}

# =========================================================
# ANA BOT KODUNDAN IMPORT
# =========================================================
# main.py'deki tüm fonksiyonları kullan
try:
    from main import (
        build_candles, detect_htf_bias, detect_mss_choch,
        detect_liquidity_sweep, detect_displacement, detect_fvg,
        detect_pd, determine_direction, validate_sniper_model,
        detect_alternative_model, detect_order_block,
        build_trade_levels, assess_mitigation_quality,
        validate_entry_timing, validate_absolute_trade_distances,
        detect_forex_smt, score_signal, is_killzone_active,
        get_killzone_label, get_performance_adjustment,
        is_weekend_market_closed, average_range, safe_float,
        is_jpy_pair, round_price,
        # Parametreler
        MIN_SIGNAL_SCORE, MIN_RR_REQUIRED,
        MIN_SIGNAL_SCORE_OUTSIDE_KILLZONE,
        SMT_RELATIONS,
    )
    print("✅ main.py import başarılı")
except ImportError as e:
    print(f"❌ main.py import hatası: {e}")
    raise

http = requests.Session()


# =========================================================
# DUKASCOPY XLS OKUYUCU
# =========================================================
def parse_dukascopy_datetime(dt_str: str) -> Optional[datetime]:
    """
    Dukascopy tarih formatı: 2023.01.02 00:00:00
    EET (UTC+2 kış, UTC+3 yaz) — yaklaşık UTC+2 kabul ediyoruz
    """
    dt_str = dt_str.strip()
    for fmt in ("%Y.%m.%d %H:%M:%S", "%Y.%m.%d %H:%M"):
        try:
            dt = datetime.strptime(dt_str, fmt)
            # EET ~ UTC+2, basit dönüşüm
            from datetime import timezone, timedelta
            eet = timezone(timedelta(hours=2))
            return dt.replace(tzinfo=eet).astimezone(UTC).replace(tzinfo=UTC)
        except Exception:
            pass
    return None


def find_dukascopy_file(symbol: str, timeframe: str) -> Optional[str]:
    """
    Dukascopy dosyasını bul.
    Format: EURUSD_5 Mins_Bid_2023.01.01_2024.12.31.xls
    """
    sym = DUKASCOPY_SYMBOL_MAP.get(symbol, "")
    tf = DUKASCOPY_TF_MAP.get(timeframe, "")

    if not sym or not tf:
        return None

    # Klasörde ara
    folder = DATA_FOLDER
    if not os.path.exists(folder):
        # Ana dizinde ara
        folder = "."

    pattern = os.path.join(folder, f"{sym}_{tf}_Bid*.xls")
    files = glob.glob(pattern)

    if not files:
        # Büyük/küçük harf farkı için tekrar ara
        pattern2 = os.path.join(folder, f"{sym}*{tf}*Bid*.xls")
        files = glob.glob(pattern2)

    if files:
        # En büyük dosyayı seç (en fazla veri)
        return max(files, key=os.path.getsize)

    return None


def read_dukascopy_xls(filepath: str) -> List[Dict[str, Any]]:
    """
    Dukascopy XLS dosyasını oku ve mum listesi döndür.
    Format: Time (EET),Open,High,Low,Close,Volume
    """
    candles: List[Dict[str, Any]] = []

    try:
        # XLS aslında CSV formatında olabilir, önce dene
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            content_raw = f.read()

        # Header satırını bul
        lines = content_raw.strip().splitlines()
        start_line = 0
        for i, line in enumerate(lines):
            if "Time" in line and "Open" in line:
                start_line = i + 1
                break

        for line in lines[start_line:]:
            line = line.strip()
            if not line:
                continue

            # Virgül veya noktalı virgül ayırıcı
            if ";" in line:
                parts = line.split(";")
            else:
                parts = line.split(",")

            if len(parts) < 5:
                continue

            try:
                dt_str = parts[0].strip().strip('"')
                o = float(parts[1].strip())
                h = float(parts[2].strip())
                l = float(parts[3].strip())
                c = float(parts[4].strip())

                dt = parse_dukascopy_datetime(dt_str)
                if dt is None:
                    continue

                candles.append({
                    "datetime": dt.strftime("%Y-%m-%d %H:%M:%S"),
                    "dt": dt,
                    "open": o,
                    "high": h,
                    "low": l,
                    "close": c,
                })
            except Exception:
                continue

    except Exception as e:
        print(f" ❌ XLS okuma hatası ({filepath}): {e}")

    # Kronolojik sırala (eski → yeni)
    candles.sort(key=lambda x: x["dt"])
    return candles


def load_dukascopy_data() -> Dict[str, Dict[str, List[Dict[str, Any]]]]:
    """
    Tüm Dukascopy XLS dosyalarını yükle.
    Önce DATA_FOLDER'a bak, sonra mevcut dizine.
    """
    print("\n📂 Dukascopy XLS dosyaları yükleniyor...")
    print("=" * 50)

    mtf_map: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}

    for symbol in BACKTEST_PAIRS:
        print(f"\n🔍 {symbol}")
        mtf_map[symbol] = {}

        for tf in BACKTEST_TIMEFRAMES:
            filepath = find_dukascopy_file(symbol, tf)

            if filepath:
                candles = read_dukascopy_xls(filepath)
                mtf_map[symbol][tf] = candles
                print(f" ✅ {tf}: {len(candles)} bar — {os.path.basename(filepath)}")
            else:
                mtf_map[symbol][tf] = []
                print(f" ⚠️ {tf}: dosya bulunamadı")

    return mtf_map


# =========================================================
# VERİ ÇEKME (TwelveData — fallback)
# =========================================================
def fetch_series(symbol: str, interval: str, outputsize: int) -> Optional[Dict[str, Any]]:
    if not TWELVEDATA_API_KEY:
        print("❌ TWELVEDATA_API_KEY yok")
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
        response = http.get(url, params=params, timeout=30)
        data = response.json()

        if response.status_code != 200:
            print(f" ❌ {symbol} {interval} HTTP: {response.status_code}")
            return None

        if data.get("status") == "error":
            print(f" ❌ {symbol} {interval} API: {data.get('message', '')}")
            return None

        if "values" not in data or not data["values"]:
            print(f" ⚠️ {symbol} {interval} veri boş")
            return None

        print(f" ✅ {symbol} {interval} → {len(data['values'])} bar")
        return data

    except Exception as e:
        print(f" ❌ {symbol} {interval} hata: {e}")
        return None


def fetch_all_data() -> Dict[str, Dict[str, List[Dict[str, Any]]]]:
    """Tüm parite ve zaman dilimleri için veri çek."""
    print("\n📡 TwelveData'dan veri çekiliyor...")
    print("=" * 50)

    mtf_map: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}

    for symbol in BACKTEST_PAIRS:
        print(f"\n🔍 {symbol}")
        mtf_map[symbol] = {}

        for interval, cfg in BACKTEST_TIMEFRAMES.items():
            raw = fetch_series(symbol, interval, cfg["outputsize"])
            if raw:
                mtf_map[symbol][interval] = build_candles(raw)
            else:
                mtf_map[symbol][interval] = []

            # Rate limit koruması
            time.sleep(0.5)

    return mtf_map


# =========================================================
# TEK NOKTA ANALİZİ
# =========================================================
def analyze_single_point(
    market_name: str,
    mtf_snapshot: Dict[str, List[Dict[str, Any]]],
    state: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """
    Belirli bir anda botun ne yapacağını simüle et.
    mtf_snapshot: Her TF için o ana kadarki mumlar.
    """
    candles_5m = mtf_snapshot.get("5min", [])
    candles_15m = mtf_snapshot.get("15min", [])
    candles_1h = mtf_snapshot.get("1h", [])
    candles_4h = mtf_snapshot.get("4h", [])
    candles_1w = mtf_snapshot.get("1week", [])

    if (len(candles_5m) < 20 or len(candles_15m) < 30
            or len(candles_1h) < 20 or len(candles_4h) < 10
            or len(candles_1w) < 5):
        return None

    current_price = candles_5m[-1]["close"]

    h1_bias = detect_htf_bias(candles_1h)
    h4_bias = detect_htf_bias(candles_4h)
    w1_bias = detect_htf_bias(candles_1w)
    bias = h1_bias if h1_bias != "Nötr" else (h4_bias if h4_bias != "Nötr" else w1_bias)

    mss, choch = detect_mss_choch(candles_15m)
    sweep = detect_liquidity_sweep(candles_15m)
    displacement = detect_displacement(candles_15m)
    fvg = detect_fvg(candles_15m)
    pd = detect_pd(candles_15m)

    primary_direction = determine_direction(sweep, mss, choch, displacement)
    setup_model = "SNIPER_OB"
    direction = primary_direction
    alt_model_info = None
    london_extra = None

    if direction != "YOK":
        sniper_check = validate_sniper_model(direction, sweep, mss, choch, displacement)
        if not sniper_check["valid"]:
            direction = "YOK"

    if direction == "YOK":
        alt_model_info = detect_alternative_model(
            candles_15m=candles_15m, candles_5m=candles_5m,
            bias=bias, displacement=displacement,
            mss=mss, choch=choch, market_name=market_name,
        )
        if alt_model_info["valid"]:
            direction = alt_model_info["direction"]
            setup_model = alt_model_info["model"]
            if setup_model == "LONDON_REVERSAL":
                london_extra = alt_model_info.get("extra")

    if direction == "YOK":
        return None

    ob = detect_order_block(candles_15m, direction)
    if not ob:
        return None

    levels = build_trade_levels(candles_15m, candles_5m, direction, ob, symbol=market_name)
    if levels["entry"] is None or levels["sl"] is None or levels["tp"] is None:
        return None
    if levels["rr"] is None or levels["rr"] < MIN_RR_REQUIRED:
        return None

    mitigation = assess_mitigation_quality(candles_5m, ob, direction)
    if not mitigation["valid"]:
        return None

    timing = validate_entry_timing(
        current_price=current_price, entry=levels["entry"],
        tp=levels["tp"], sl=levels["sl"], direction=direction,
        candles_5m=candles_5m, touch_index=mitigation["touch_index"],
    )
    if not timing["valid"]:
        return None

    distance_check = validate_absolute_trade_distances(
        candles_5m=candles_5m, entry=levels["entry"],
        sl=levels["sl"], tp=levels["tp"], current_price=current_price,
    )
    if not distance_check["valid"]:
        return None

    smt_result = detect_forex_smt(market_name, {market_name: mtf_snapshot})
    smt_label = smt_result["label"]
    perf_adjustment, perf_notes = get_performance_adjustment(state, market_name)

    score, quality, score_notes = score_signal(
        direction=direction, bias=bias, h4_bias=h4_bias, w1_bias=w1_bias,
        fvg=fvg, pd=pd, smt=smt_label, killzone_active=is_killzone_active(),
        displacement=displacement, mitigation_quality=mitigation["quality"],
        rr=levels["rr"], mss=mss, choch=choch,
        perf_adjustment=perf_adjustment, perf_notes=perf_notes,
        setup_model=setup_model, london_extra=london_extra,
        smt_detail=smt_result,
    )

    # Killzone kontrolü
    killzone_now = get_killzone_label()
    killzone_active_now = killzone_now != "Killzone Dışı"
    min_score = MIN_SIGNAL_SCORE if killzone_active_now else MIN_SIGNAL_SCORE_OUTSIDE_KILLZONE

    if score < min_score or quality not in ("A", "A+"):
        return None

    return {
        "symbol": market_name,
        "datetime": candles_5m[-1]["datetime"],
        "direction": direction,
        "entry": levels["entry"],
        "sl": levels["sl"],
        "tp": levels["tp"],
        "rr": levels["rr"],
        "score": score,
        "quality": quality,
        "setup_model": setup_model,
        "bias": bias,
        "h1_bias": h1_bias,
        "h4_bias": h4_bias,
        "killzone": killzone_now,
        "displacement":displacement,
        "mss": mss,
        "choch": choch,
        "sweep": sweep,
        "smt": smt_label,
    }


# =========================================================
# SONUÇ HESAPLAMA
# =========================================================
def evaluate_signal_outcome(
    signal: Dict[str, Any],
    future_5m: List[Dict[str, Any]],
    max_bars: int = 200,
) -> str:
    """
    Sinyal verildikten sonra fiyat TP'ye mi SL'ye mi gitti?
    future_5m: Sinyalden sonraki mumlar.
    """
    direction = signal["direction"]
    entry = signal["entry"]
    sl = signal["sl"]
    tp = signal["tp"]

    if not future_5m:
        return "TIMEOUT"

    bars = future_5m[:max_bars]

    for candle in bars:
        if direction == "LONG":
            if candle["low"] <= sl:
                return "SL"
            if candle["high"] >= tp:
                return "TP"
        elif direction == "SHORT":
            if candle["high"] >= sl:
                return "SL"
            if candle["low"] <= tp:
                return "TP"

    return "TIMEOUT"


# =========================================================
# BACKTEST ÇALIŞTIR
# =========================================================
def run_backtest(mtf_map: Dict[str, Dict[str, List[Dict[str, Any]]]]) -> Dict[str, Any]:
    """Ana backtest döngüsü."""
    print("\n🔄 Backtest çalıştırılıyor...")
    print("=" * 50)

    all_signals: List[Dict[str, Any]] = []
    state: Dict[str, Any] = {
        "signals": {}, "active_signals": {},
        "performance": {}, "pending_setups": {},
    }

    for symbol in BACKTEST_PAIRS:
        tfs = mtf_map.get(symbol, {})
        candles_5m = tfs.get("5min", [])
        candles_15m = tfs.get("15min", [])
        candles_1h = tfs.get("1h", [])
        candles_4h = tfs.get("4h", [])
        candles_1w = tfs.get("1week", [])

        if len(candles_5m) < 50:
            print(f" ⚠️ {symbol} yeterli 5M bar yok, atlandı")
            continue

        print(f"\n📊 {symbol} analiz ediliyor ({len(candles_5m)} bar)...")

        signals_found = 0
        last_signal_i = -50 # Cooldown: aynı yerden tekrar sinyal verme

        # 5M barları üzerinde kayan pencere
        # Minimum lookback: 50 bar
        for i in range(50, len(candles_5m) - 10):

            # Cooldown: son sinyalden 12 bar geçmeden tekrar bakma
            if i - last_signal_i < 12:
                continue

            # Snapshot oluştur — o ana kadarki veriler
            snap_5m = candles_5m[:i+1]
            snap_15m = candles_15m[:min(i//3+1, len(candles_15m))]
            snap_1h = candles_1h[:min(i//12+1, len(candles_1h))]
            snap_4h = candles_4h[:min(i//48+1, len(candles_4h))]
            snap_1w = candles_1w[:min(i//336+1, len(candles_1w))]

            if len(snap_15m) < 30 or len(snap_1h) < 20:
                continue

            snapshot = {
                "5min": snap_5m, "15min": snap_15m,
                "1h": snap_1h, "4h": snap_4h, "1week": snap_1w,
            }

            signal = analyze_single_point(symbol, snapshot, state)
            if not signal:
                continue

            # Sinyalden sonraki mumlar
            future_5m = candles_5m[i+1:]
            outcome = evaluate_signal_outcome(signal, future_5m)

            signal["outcome"] = outcome
            signal["bar_index"] = i
            all_signals.append(signal)
            last_signal_i = i
            signals_found += 1

            # Performance state güncelle
            if outcome in ("TP", "SL"):
                perf = state["performance"].setdefault(symbol, {
                    "wins": 0, "losses": 0, "total": 0,
                    "last_outcome": None, "win_rate": None,
                })
                if outcome == "TP":
                    perf["wins"] += 1
                else:
                    perf["losses"] += 1
                perf["total"] = perf["wins"] + perf["losses"]
                perf["last_outcome"] = outcome
                if perf["total"] > 0:
                    perf["win_rate"] = round(perf["wins"] / perf["total"] * 100, 2)

        print(f" → {signals_found} sinyal bulundu")

    return {
        "signals": all_signals,
        "performance": state["performance"],
    }


# =========================================================
# RAPOR ÜRET
# =========================================================
def generate_report(results: Dict[str, Any]) -> str:
    """Backtest sonuçlarından okunabilir rapor üret."""
    signals = results["signals"]
    perf = results["performance"]

    if not signals:
        return "Hiç sinyal bulunamadı."

    total = len(signals)
    tp_list = [s for s in signals if s["outcome"] == "TP"]
    sl_list = [s for s in signals if s["outcome"] == "SL"]
    to_list = [s for s in signals if s["outcome"] == "TIMEOUT"]

    tp_count = len(tp_list)
    sl_count = len(sl_list)
    to_count = len(to_list)
    decided = tp_count + sl_count

    win_rate = round(tp_count / decided * 100, 2) if decided > 0 else 0
    avg_rr = round(sum(s.get("rr", 0) for s in tp_list) / tp_count, 2) if tp_count > 0 else 0

    # Model bazlı analiz
    model_stats: Dict[str, Dict] = {}
    for s in signals:
        m = s.get("setup_model", "YOK")
        if m not in model_stats:
            model_stats[m] = {"tp": 0, "sl": 0, "timeout": 0}
        if s["outcome"] == "TP":
            model_stats[m]["tp"] += 1
        elif s["outcome"] == "SL":
            model_stats[m]["sl"] += 1
        else:
            model_stats[m]["timeout"] += 1

    # Session bazlı analiz
    session_stats: Dict[str, Dict] = {}
    for s in signals:
        kz = s.get("killzone", "Killzone Dışı")
        if kz not in session_stats:
            session_stats[kz] = {"tp": 0, "sl": 0}
        if s["outcome"] == "TP":
            session_stats[kz]["tp"] += 1
        elif s["outcome"] == "SL":
            session_stats[kz]["sl"] += 1

    # Parite bazlı analiz
    symbol_stats: Dict[str, Dict] = {}
    for s in signals:
        sym = s["symbol"]
        if sym not in symbol_stats:
            symbol_stats[sym] = {"tp": 0, "sl": 0, "timeout": 0}
        if s["outcome"] == "TP":
            symbol_stats[sym]["tp"] += 1
        elif s["outcome"] == "SL":
            symbol_stats[sym]["sl"] += 1
        else:
            symbol_stats[sym]["timeout"] += 1

    lines = [
        "=" * 60,
        " ELITE SNIPER BACKTEST RAPORU",
        f" Tarih: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}",
        "=" * 60,
        "",
        "── GENEL SONUÇLAR ──────────────────────────────────────",
        f"Toplam Sinyal : {total}",
        f"TP : {tp_count}",
        f"SL : {sl_count}",
        f"TIMEOUT : {to_count}",
        f"Karar Verilen : {decided}",
        f"Win Rate : %{win_rate}",
        f"Ortalama RR (TP) : {avg_rr}",
        "",
        "── PArite BAZLI ────────────────────────────────────────",
    ]

    for sym, st in sorted(symbol_stats.items(), key=lambda x: -(x[1]["tp"])):
        dec = st["tp"] + st["sl"]
        wr = round(st["tp"] / dec * 100, 1) if dec > 0 else 0
        lines.append(f"{sym:<12} TP:{st['tp']:>3} SL:{st['sl']:>3} TO:{st['timeout']:>3} WR:%{wr}")

    lines += ["", "── MODEL BAZLI ─────────────────────────────────────────"]
    for model, st in sorted(model_stats.items(), key=lambda x: -(x[1]["tp"])):
        dec = st["tp"] + st["sl"]
        wr = round(st["tp"] / dec * 100, 1) if dec > 0 else 0
        lines.append(f"{model:<20} TP:{st['tp']:>3} SL:{st['sl']:>3} WR:%{wr}")

    lines += ["", "── SESSION BAZLI ───────────────────────────────────────"]
    for kz, st in sorted(session_stats.items(), key=lambda x: -(x[1]["tp"])):
        dec = st["tp"] + st["sl"]
        wr = round(st["tp"] / dec * 100, 1) if dec > 0 else 0
        lines.append(f"{kz:<25} TP:{st['tp']:>3} SL:{st['sl']:>3} WR:%{wr}")

    lines += ["", "── PArite PERFORMANS (bot_state'e aktarıldı) ───────────"]
    for sym, p in sorted(perf.items(), key=lambda x: -(x[1].get("win_rate") or 0)):
        wr = p.get("win_rate", 0) or 0
        lines.append(f"{sym:<12} WR:%{wr:<6} TP:{p['wins']:>3} SL:{p['losses']:>3} Toplam:{p['total']:>3}")

    lines += ["", "=" * 60]
    return "\n".join(lines)


# =========================================================
# BOT STATE GÜNCELLE
# =========================================================
def update_bot_state_with_backtest(results: Dict[str, Any]) -> None:
    """
    Backtest sonuçlarını bot_state.json'a kaydet.
    Canlı bot bu verileri performance_adjustment'da kullanır.
    """
    perf = results["performance"]

    # Mevcut state'i yükle
    existing: Dict[str, Any] = {"signals": {}, "active_signals": {}, "performance": {}, "pending_setups": {}}
    if os.path.exists(BOT_STATE_FILE):
        try:
            with open(BOT_STATE_FILE, "r", encoding="utf-8") as f:
                existing = json.load(f)
        except Exception:
            pass

    # Backtest sonuçlarını performance'a aktar
    for symbol, p in perf.items():
        existing_perf = existing["performance"].get(symbol, {
            "wins": 0, "losses": 0, "total": 0,
            "last_outcome": None, "win_rate": None,
        })
        # Backtest verisi canlı veriye eklenir (birleştirme)
        existing_perf["wins"] = existing_perf.get("wins", 0) + p["wins"]
        existing_perf["losses"] = existing_perf.get("losses", 0) + p["losses"]
        existing_perf["total"] = existing_perf["wins"] + existing_perf["losses"]
        if existing_perf["total"] > 0:
            existing_perf["win_rate"] = round(
                existing_perf["wins"] / existing_perf["total"] * 100, 2
            )
        existing["performance"][symbol] = existing_perf

    with open(BOT_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(existing, f, indent=2)

    print(f"\n✅ bot_state.json güncellendi — {len(perf)} parite kaydedildi")


# =========================================================
# ANA ÇALIŞTIRICI
# =========================================================
def main() -> None:
    print("🚀 ELITE SNIPER BACKTEST BAŞLIYOR")
    print(f"📅 {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"🔑 TwelveData key: {'✓' if TWELVEDATA_API_KEY else '✗'}")

    # TwelveData key opsiyonel — Dukascopy verisi varsa kullanılmaz
    if not TWELVEDATA_API_KEY:
        print("\n⚠️ TwelveData key yok — Dukascopy XLS verisi kullanılacak")

    # 1. Veri yükle — önce Dukascopy XLS, yoksa TwelveData
    dukascopy_map = load_dukascopy_data()

    # Dukascopy'den yeterli veri geldiyse onu kullan
    has_dukascopy = any(
        len(dukascopy_map.get(sym, {}).get("5min", [])) > 100
        for sym in BACKTEST_PAIRS
    )

    if has_dukascopy:
        print("\n✅ Dukascopy verisi kullanılıyor")
        mtf_map = dukascopy_map
    else:
        print("\n⚠️ Dukascopy verisi yetersiz, TwelveData'dan çekiliyor...")
        td_map = fetch_all_data()
        mtf_map = {}
        for sym in BACKTEST_PAIRS:
            mtf_map[sym] = {}
            for tf in BACKTEST_TIMEFRAMES:
                # Dukascopy varsa onu kullan, yoksa TwelveData
                dk = dukascopy_map.get(sym, {}).get(tf, [])
                td = td_map.get(sym, {}).get(tf, [])
                mtf_map[sym][tf] = dk if len(dk) > len(td) else td

    # 2. Backtest çalıştır
    results = run_backtest(mtf_map)

    # 3. Rapor üret
    print("\n📝 Rapor hazırlanıyor...")
    report_txt = generate_report(results)
    print("\n" + report_txt)

    # 4. Dosyalara kaydet
    with open(BACKTEST_REPORT, "w", encoding="utf-8") as f:
        json.dump(results["signals"], f, indent=2, default=str)

    with open(BACKTEST_TXT, "w", encoding="utf-8") as f:
        f.write(report_txt)

    print(f"\n💾 {BACKTEST_REPORT} kaydedildi ({len(results['signals'])} sinyal)")
    print(f"💾 {BACKTEST_TXT} kaydedildi")

    # 5. bot_state.json güncelle
    update_bot_state_with_backtest(results)

    print("\n✅ BACKTEST TAMAMLANDI!")


if __name__ == "__main__":
    main()
