"""
S&P 500 Momentum Scanner
─────────────────────────
Data: Finnhub (primary) → yfinance (fallback)
Candidates: Finviz → yfinance batch → Finnhub quote
Secrets: .env only (no hard-coded keys)
"""

import os
import json
import time
import random
import logging
import inspect
import pathlib
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import numpy as np
import pandas as pd
from dotenv import load_dotenv

# ──────────────────────────── 환경 변수 ────────────────────────────
load_dotenv()
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "")
TELEGRAM_TOKEN  = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID         = os.getenv("CHAT_ID", "")

# ──────────────────────────── 상수 ─────────────────────────────────
MAX_WORKERS     = 5
MAX_TICKERS     = 30
RAW_SCORE_MAX   = 140   # +20+20+15+15+20+15+10+10+10+5 = +140
RAW_SCORE_MIN   = -80   # -20-15-10-10-10-10-5          = -80
RAW_SCORE_RANGE = RAW_SCORE_MAX - RAW_SCORE_MIN          # 220
FINNHUB_BASE    = "https://finnhub.io/api/v1"
FINNHUB_DELAY   = 1.1
HISTORY_DIR     = pathlib.Path("history")
HISTORY_DIR.mkdir(exist_ok=True)
HISTORY_TS_FMT  = "%Y-%m-%d_%H%M%S"   # app.py 와 공유 → bot.HISTORY_TS_FMT 로 참조

# 한글 매핑
CLOUD_STATUS_KO = {
    "above":  "구름 위 ☁️",
    "below":  "구름 아래 ⛅",
    "inside": "구름 안 🌫️",
}
MA_TREND_KO = {
    "bullish": "정배열 📈",
    "bearish": "역배열 📉",
}

# ──────────────────────────── 로깅 ─────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("bot.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)

# yfinance 호환성 체크
_YF_AVAILABLE    = False
_YF_SUPPORTS_MLI = False
try:
    import yfinance as yf
    _YF_AVAILABLE    = True
    _YF_SUPPORTS_MLI = "multi_level_index" in inspect.signature(yf.download).parameters
except ImportError:
    log.warning("yfinance 미설치 → Finnhub 전용 모드")

_FV_AVAILABLE = False
try:
    from finvizfinance.screener.overview import Overview
    _FV_AVAILABLE = True
except ImportError:
    log.warning("finvizfinance 미설치 → Finviz 스크리너 비활성화")


# ═══════════════════════════════════════════════════════════════════
# 유틸리티
# ═══════════════════════════════════════════════════════════════════

def safe_float(val, default: float = 0.0) -> float:
    try:
        if val is None:
            return default
        if isinstance(val, pd.DataFrame):
            val = val.squeeze()
        if isinstance(val, pd.Series):
            val = val.iloc[-1] if len(val) > 0 else default
        v = float(val)
        return default if (np.isnan(v) or np.isinf(v)) else v
    except (TypeError, ValueError, IndexError):
        return default


def _parse_pct(s) -> float:
    if isinstance(s, (int, float)):
        return float(s)
    try:
        return float(str(s).replace("%", "").replace(",", "").strip())
    except (ValueError, TypeError):
        return 0.0


def normalize_score(raw: float) -> int:
    """raw score → 0‑100 정수 (클램핑 포함)."""
    clamped = max(RAW_SCORE_MIN, min(RAW_SCORE_MAX, raw))
    return int(round((clamped - RAW_SCORE_MIN) / RAW_SCORE_RANGE * 100))


def _escape_html(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


# ═══════════════════════════════════════════════════════════════════
# Telegram
# ═══════════════════════════════════════════════════════════════════

def send_telegram(message: str) -> None:
    if not TELEGRAM_TOKEN or not CHAT_ID:
        log.info("Telegram 토큰 미설정 → 전송 스킵")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = message[:4000]   # ✅ Telegram 4096자 제한 대응
    try:
        resp = requests.post(
            url,
            json={"chat_id": CHAT_ID, "text": payload, "parse_mode": "HTML"},
            timeout=10,
        )
        if resp.status_code != 200:
            log.warning("Telegram 전송 실패: %s", resp.text[:200])
    except Exception as e:
        log.warning("Telegram 오류: %s", e)


# ═══════════════════════════════════════════════════════════════════
# Finnhub API 헬퍼
# ═══════════════════════════════════════════════════════════════════

def _finnhub_get(endpoint: str, params: dict, retries: int = 3):
    if not FINNHUB_API_KEY:
        return None
    params = {**params, "token": FINNHUB_API_KEY}   # 원본 dict 변조 방지
    url = f"{FINNHUB_BASE}/{endpoint}"
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, timeout=15)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 429:
                wait = float(r.headers.get("Retry-After", 2)) + random.uniform(0, 1)
                log.warning("Finnhub 429 → %.1fs 대기 (attempt %d)", wait, attempt + 1)
                time.sleep(wait)
                continue
            log.warning("Finnhub %d: %s", r.status_code, r.text[:120])
            return None
        except requests.RequestException as e:
            log.warning("Finnhub 요청 오류: %s", e)
            time.sleep(1)
    return None


# ═══════════════════════════════════════════════════════════════════
# OHLCV (Finnhub → yfinance fallback)
# ═══════════════════════════════════════════════════════════════════

def _finnhub_candles(ticker: str, days: int = 730) -> pd.DataFrame | None:
    now   = int(datetime.now().timestamp())
    start = int((datetime.now() - timedelta(days=days)).timestamp())
    data  = _finnhub_get(
        "stock/candle",
        {"symbol": ticker, "resolution": "D", "from": start, "to": now},
    )
    if not data or data.get("s") != "ok":
        return None
    try:
        df = pd.DataFrame(
            {
                "Open":   data["o"],
                "High":   data["h"],
                "Low":    data["l"],
                "Close":  data["c"],
                "Volume": data["v"],
            },
            index=pd.to_datetime(data["t"], unit="s"),
        )
        df.index.name = "Date"
        return df if len(df) >= 40 else None
    except Exception as e:
        log.warning("Finnhub candles 파싱 오류 [%s]: %s", ticker, e)
        return None


def _yfinance_candles(ticker: str) -> pd.DataFrame | None:
    if not _YF_AVAILABLE:
        return None
    try:
        kw = {"period": "2y", "interval": "1d", "auto_adjust": True, "progress": False}
        if _YF_SUPPORTS_MLI:
            kw["multi_level_index"] = False
        df = yf.download(ticker, **kw)
        if df is None or df.empty:
            return None

        # 컬럼명 정규화
        col_map = {}
        for c in df.columns:
            cl = str(c).lower().strip()
            if "close" in cl and "adj" not in cl:
                col_map[c] = "Close"
            elif "adj" in cl and "close" in cl:
                col_map[c] = "Adj Close"
            elif "open" in cl:
                col_map[c] = "Open"
            elif "high" in cl:
                col_map[c] = "High"
            elif "low" in cl:
                col_map[c] = "Low"
            elif "vol" in cl:
                col_map[c] = "Volume"
        df = df.rename(columns=col_map)

        if "Close" not in df.columns and "Adj Close" in df.columns:
            df["Close"] = df["Adj Close"]
        if "Close" not in df.columns:
            return None
        return df if len(df) >= 40 else None
    except Exception as e:
        log.warning("yfinance 다운로드 오류 [%s]: %s", ticker, e)
        return None


def fetch_ohlcv(ticker: str) -> pd.DataFrame | None:
    """Finnhub 우선, 실패 시 yfinance fallback."""
    df = _finnhub_candles(ticker)
    if df is not None:
        return df
    time.sleep(FINNHUB_DELAY)
    df = _yfinance_candles(ticker)
    if df is not None:
        log.info("[%s] yfinance 폴백 사용", ticker)
    return df


# ═══════════════════════════════════════════════════════════════════
# 종목 수집 (3중 폴백)
# ═══════════════════════════════════════════════════════════════════

def fetch_finviz_sp500_gainers() -> list:
    if not _FV_AVAILABLE:
        return []
    try:
        foverview = Overview()
        # finvizfinance 버전별 signal 파라미터 호환
        try:
            foverview.set_filter(
                signal="top_gainers",
                filters_dict={"idx_sp500": "S&P 500"},
            )
        except TypeError:
            foverview.set_filter(filters_dict={"idx_sp500": "S&P 500"})

        df = foverview.screener_view()
        if df is None or df.empty:
            return []

        results = []
        for _, row in df.head(MAX_TICKERS).iterrows():
            ticker = str(row.get("Ticker", "")).strip().upper()
            if not ticker:
                continue
            results.append({
                "ticker":        ticker,
                "company":       str(row.get("Company", "")),
                "finviz_price":  safe_float(row.get("Price"), 0),
                "finviz_change": _parse_pct(row.get("Change", 0)),
            })
        log.info("Finviz: %d 종목 수집", len(results))
        return results
    except Exception as e:
        log.warning("Finviz 오류: %s", e)
        return []


def _fetch_yfinance_batch_fallback() -> list:
    if not _YF_AVAILABLE:
        return []
    try:
        tables  = pd.read_html("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies")
        symbols = [s.replace(".", "-") for s in tables[0]["Symbol"].tolist()]

        kw = {"period": "5d", "interval": "1d", "auto_adjust": True, "progress": False}
        if _YF_SUPPORTS_MLI:
            kw["multi_level_index"] = False
        df = yf.download(symbols, **kw)
        if df is None or df.empty:
            return []

        # MultiIndex 처리
        if isinstance(df.columns, pd.MultiIndex):
            close = df["Close"]
        elif "Close" in df.columns:
            close = df[["Close"]]
        else:
            return []

        if len(close) < 2:
            return []

        last = close.iloc[-1]
        prev = close.iloc[-2]
        chg  = ((last - prev) / prev * 100).dropna().sort_values(ascending=False)

        results = []
        for sym in chg.head(MAX_TICKERS).index:
            # ✅ MultiIndex 잔존 방어: sym in index 체크 후 접근
            price_val  = safe_float(last[sym] if sym in last.index else 0)
            change_val = round(safe_float(chg[sym] if sym in chg.index else 0), 2)
            results.append({
                "ticker":        str(sym).strip().upper(),
                "company":       str(sym),
                "finviz_price":  price_val,
                "finviz_change": change_val,
            })
        log.info("yfinance batch 폴백: %d 종목", len(results))
        return results
    except Exception as e:
        log.warning("yfinance batch 폴백 오류: %s", e)
        return []


def _fetch_finnhub_sp500_fallback() -> list:
    """Finnhub S&P 500 구성 종목 quote — 병렬 처리 (max_workers=3으로 rate-limit 제어)."""
    data = _finnhub_get("index/constituents", {"symbol": "^GSPC"})
    if not data or "constituents" not in data:
        log.warning("Finnhub S&P 500 구성 종목 조회 실패")
        return []
    symbols = data["constituents"][:80]

    def _fetch_quote(sym: str):
        # ✅ sleep 제거: max_workers=3 이 rate-limit 역할 담당
        q = _finnhub_get("quote", {"symbol": sym})
        if q and q.get("c", 0) > 0 and q.get("pc", 0) > 0:
            chg = (q["c"] - q["pc"]) / q["pc"] * 100
            return {
                "ticker":        sym,
                "company":       sym,
                "finviz_price":  round(q["c"], 2),
                "finviz_change": round(chg, 2),
            }
        return None

    with ThreadPoolExecutor(max_workers=3) as ex:
        raw = list(ex.map(_fetch_quote, symbols))

    quotes = sorted([r for r in raw if r], key=lambda x: x["finviz_change"], reverse=True)
    result = quotes[:MAX_TICKERS]
    log.info("Finnhub 폴백: %d 종목", len(result))
    return result


# ═══════════════════════════════════════════════════════════════════
# 기술 지표 계산
# ═══════════════════════════════════════════════════════════════════

_REQUIRED_COLS = ["Close", "High", "Low", "Volume"]


def compute_indicators(ticker: str) -> dict | None:
    df = fetch_ohlcv(ticker)
    if df is None:
        return None

    # ✅ 필수 컬럼 존재 확인 (KeyError 방지)
    missing = [c for c in _REQUIRED_COLS if c not in df.columns]
    if missing:
        log.warning("[%s] 컬럼 누락: %s – 스킵", ticker, missing)
        return None

    try:
        close  = df["Close"]
        high   = df["High"]
        low    = df["Low"]
        volume = df["Volume"]

        # DataFrame → Series 보정
        if isinstance(close,  pd.DataFrame): close  = close.squeeze()
        if isinstance(high,   pd.DataFrame): high   = high.squeeze()
        if isinstance(low,    pd.DataFrame): low    = low.squeeze()
        if isinstance(volume, pd.DataFrame): volume = volume.squeeze()

        # ── 현재가 / 1일 변화율 ──────────────────────────────────
        price     = safe_float(close.iloc[-1])
        change_1d = 0.0
        if len(close) >= 2:
            prev = safe_float(close.iloc[-2])
            if prev > 0:
                change_1d = round((price - prev) / prev * 100, 2)

        result = {"price": price, "change_1d": change_1d}

        # ── RSI (14) — Wilder's EMA ──────────────────────────────
        if len(close) >= 15:
            delta    = close.diff()
            gain     = delta.clip(lower=0)
            loss     = (-delta).clip(upper=0)
            avg_gain = gain.ewm(alpha=1 / 14, adjust=False).mean()
            avg_loss = loss.ewm(alpha=1 / 14, adjust=False).mean()
            last_gain = safe_float(avg_gain.iloc[-1])
            last_loss = safe_float(avg_loss.iloc[-1])
            if last_gain == 0 and last_loss == 0:
                rsi = 50.0          # 완전 보합 → 중립
            elif last_loss == 0:
                rsi = 100.0         # 무손실 상승 → 과매수
            elif last_gain == 0:
                rsi = 0.0           # 무이익 하락 → 과매도
            else:
                rs  = last_gain / last_loss
                rsi = round(100 - 100 / (1 + rs), 2)
            result["rsi"] = rsi
        else:
            result["rsi"] = 50.0

        # ── MACD (12/26/9) ───────────────────────────────────────
        if len(close) >= 35:
            ema12       = close.ewm(span=12, adjust=False).mean()
            ema26       = close.ewm(span=26, adjust=False).mean()
            macd_line   = ema12 - ema26
            signal_line = macd_line.ewm(span=9, adjust=False).mean()
            result["macd"]           = round(safe_float(macd_line.iloc[-1]),   4)
            result["macd_signal"]    = round(safe_float(signal_line.iloc[-1]), 4)
            result["macd_histogram"] = round(
                safe_float(macd_line.iloc[-1]) - safe_float(signal_line.iloc[-1]), 4
            )
        else:
            result["macd"] = result["macd_signal"] = result["macd_histogram"] = 0.0

        # ── 이동평균 (20 / 50 / 200) ─────────────────────────────
        for period in [20, 50, 200]:
            if len(close) >= period:
                result[f"ma{period}"] = round(
                    safe_float(close.rolling(period).mean().iloc[-1]), 2
                )
            else:
                result[f"ma{period}"] = price

        # MA 트렌드 (한글 + raw 키 분리)
        if result["ma20"] > result["ma50"] > result["ma200"]:
            result["ma_trend"]     = MA_TREND_KO["bullish"]
            result["ma_trend_raw"] = "bullish"
        else:
            result["ma_trend"]     = MA_TREND_KO["bearish"]
            result["ma_trend_raw"] = "bearish"

        # 골든 / 데드 크로스
        result["golden_cross"] = False
        result["dead_cross"]   = False
        if len(close) >= 50:
            ma20_s = close.rolling(20).mean()
            ma50_s = close.rolling(50).mean()
            if len(ma20_s) >= 2:
                p20, c20 = safe_float(ma20_s.iloc[-2]), safe_float(ma20_s.iloc[-1])
                p50, c50 = safe_float(ma50_s.iloc[-2]), safe_float(ma50_s.iloc[-1])
                if p20 <= p50 and c20 > c50:
                    result["golden_cross"] = True
                if p20 >= p50 and c20 < c50:
                    result["dead_cross"] = True

        # ── Stochastic %K / %D (표준) ────────────────────────────
        if len(close) >= 14:
            low14  = low.rolling(14).min()
            high14 = high.rolling(14).max()
            denom  = (high14 - low14).replace(0, np.nan)
            raw_k  = (close - low14) / denom * 100   # raw %K
            stoch_d = raw_k.rolling(3).mean()          # %D = %K의 3일 SMA
            result["stoch_k"] = round(safe_float(raw_k.iloc[-1],  50.0), 2)
            result["stoch_d"] = round(safe_float(stoch_d.iloc[-1], 50.0), 2)
        else:
            result["stoch_k"] = result["stoch_d"] = 50.0

        # ── Ichimoku Cloud (shift 26 적용) ───────────────────────
        if len(close) >= 52:
            tenkan = (high.rolling(9).max()  + low.rolling(9).min())  / 2
            kijun  = (high.rolling(26).max() + low.rolling(26).min()) / 2
            span_a = ((tenkan + kijun) / 2).shift(26)
            span_b = ((high.rolling(52).max() + low.rolling(52).min()) / 2).shift(26)
            last_a = safe_float(span_a.iloc[-1], price)
            last_b = safe_float(span_b.iloc[-1], price)
            cloud_top    = max(last_a, last_b)
            cloud_bottom = min(last_a, last_b)
            result["cloud_top"]    = round(cloud_top,    2)
            result["cloud_bottom"] = round(cloud_bottom, 2)
            if price > cloud_top:
                cloud_raw = "above"
            elif price < cloud_bottom:
                cloud_raw = "below"
            else:
                cloud_raw = "inside"
            result["cloud_status"]     = CLOUD_STATUS_KO[cloud_raw]
            result["cloud_status_raw"] = cloud_raw
        else:
            result["cloud_top"]        = price
            result["cloud_bottom"]     = price
            result["cloud_status"]     = CLOUD_STATUS_KO["inside"]
            result["cloud_status_raw"] = "inside"

        # ── 20D VWAP (rolling sum) ───────────────────────────────
        if len(close) >= 20:
            tp      = (high + low + close) / 3
            tp_vol  = tp * volume
            vol_sum = volume.rolling(20).sum().replace(0, np.nan)
            vwap_s  = tp_vol.rolling(20).sum() / vol_sum
            result["vwap"] = round(safe_float(vwap_s.iloc[-1], price), 2)
        else:
            result["vwap"] = price

        # ── ATR (14) ─────────────────────────────────────────────
        if len(close) >= 15:
            tr = pd.concat([
                high - low,
                (high - close.shift(1)).abs(),
                (low  - close.shift(1)).abs(),
            ], axis=1).max(axis=1)
            atr = safe_float(tr.rolling(14).mean().iloc[-1], price * 0.01)
            atr = max(atr, price * 0.01)
        else:
            atr = price * 0.02
        result["atr"]       = round(atr, 2)
        result["target_1"]  = round(price + atr * 1.5, 2)
        result["target_2"]  = round(price + atr * 3.0, 2)
        result["stop_loss"] = round(price - atr * 1.5, 2)

        # ── 거래량 비율 (x 단위, rolling 방식으로 통일) ──────────
        if len(volume) >= 21:
            # ✅ rolling().mean().iloc[-1] 로 다른 지표와 패턴 통일
            avg_vol = safe_float(volume.rolling(20).mean().iloc[-1], 1)
            last_vol = safe_float(volume.iloc[-1])
            result["volume_ratio"] = round(last_vol / avg_vol, 2) if avg_vol > 0 else 1.0
        else:
            result["volume_ratio"] = 1.0

        return result

    except Exception as e:
        log.error("지표 계산 오류 [%s]: %s", ticker, e, exc_info=True)
        return None


# ═══════════════════════════════════════════════════════════════════
# 점수 · 진입 상태
# ═══════════════════════════════════════════════════════════════════
# 최대: +20+20+15+15+20+15+10+10+10+5 = +140
# 최소: -20-15-10-10-10-10-5          = -80

def compute_score_and_status(ind: dict, fv: dict) -> dict:
    raw     = 0
    signals = []

    # RSI (+20 / -20)
    rsi = ind.get("rsi", 50)
    if rsi < 30:
        raw += 20; signals.append("✅ RSI 과매도")
    elif rsi < 40:
        raw += 10; signals.append("✅ RSI 약세 반등 구간")
    elif rsi > 80:
        raw -= 20; signals.append("⚠️ RSI 과열")
    elif rsi > 70:
        raw -= 10; signals.append("⚠️ RSI 고열 구간")

    # MACD ATR 비율 (+20 / -15)
    macd      = ind.get("macd", 0)
    atr       = ind.get("atr", 1)
    macd_norm = macd / atr if atr > 0 else 0
    if macd_norm > 0.5:
        raw += 20; signals.append("✅ MACD 강한 상승")
    elif macd_norm > 0:
        raw += 10; signals.append("✅ MACD 약한 상승")
    elif macd_norm > -0.5:
        raw -= 5;  signals.append("⚠️ MACD 약한 하락")
    else:
        raw -= 15; signals.append("⚠️ MACD 하락")

    # 가격 vs MA20 (+15 / -10)
    price = ind.get("price", 0)
    ma20  = ind.get("ma20", price)
    if price > 0 and ma20 > 0:
        if price > ma20:
            raw += 15; signals.append("✅ 가격 > MA20")
        else:
            raw -= 10; signals.append("⚠️ 가격 < MA20")

    # MA 트렌드 (+15 / -10) — raw 키로 판단
    if ind.get("ma_trend_raw") == "bullish":
        raw += 15; signals.append("✅ MA 정배열")
    else:
        raw -= 10; signals.append("⚠️ MA 역배열")

    # 골든 / 데드 크로스 (+20 / -10)
    if ind.get("golden_cross"):
        raw += 20; signals.append("✅ 골든크로스")
    if ind.get("dead_cross"):
        raw -= 10; signals.append("⚠️ 데드크로스")

    # Ichimoku (+15 / -10)
    cloud_raw = ind.get("cloud_status_raw", "inside")
    if cloud_raw == "above":
        raw += 15; signals.append("✅ 구름 위")
    elif cloud_raw == "below":
        raw -= 10; signals.append("⚠️ 구름 아래")
    else:
        signals.append("⏳ 구름 안")

    # Stochastic (+10 / -5)
    stoch_k = ind.get("stoch_k", 50)
    if stoch_k < 20:
        raw += 10; signals.append("✅ Stoch 과매도")
    elif stoch_k > 80:
        raw -= 5;  signals.append("⚠️ Stoch 과열")

    # VWAP (+10)
    vwap = ind.get("vwap", price)
    if price > 0 and vwap > 0 and price > vwap:
        raw += 10; signals.append("✅ 가격 > VWAP")

    # 거래량 (+10 / +5)
    vol_ratio = ind.get("volume_ratio", 1.0)
    if vol_ratio >= 2.0:
        raw += 10; signals.append(f"✅ 거래량 급증 ({vol_ratio}x)")
    elif vol_ratio >= 1.5:
        raw += 5;  signals.append(f"✅ 거래량 증가 ({vol_ratio}x)")

    # Finviz 모멘텀 보너스 (+5)
    fv_chg = fv.get("finviz_change", 0)
    if fv_chg >= 5:
        raw += 5; signals.append(f"✅ Finviz +{fv_chg}%")

    score = normalize_score(raw)

    if score >= 70:
        entry = "🟢"; entry_key = "green"
    elif score >= 50:
        entry = "⏳"; entry_key = "wait"
    else:
        entry = "❌"; entry_key = "stop"

    return {
        "score":     score,
        "raw_score": raw,
        "entry":     entry,
        "entry_key": entry_key,
        "signals":   signals,
    }


# ═══════════════════════════════════════════════════════════════════
# 개별 종목 분석
# ═══════════════════════════════════════════════════════════════════

def analyze_ticker(fv: dict) -> dict | None:
    """
    반환 키 (dashboard.html 1:1 대응):
      ticker, company, price, change_1d, finviz_change,
      score, raw_score, entry, entry_key, signals,
      rsi, macd, macd_signal, macd_histogram,
      ma20, ma50, ma200, ma_trend, golden_cross, dead_cross,
      stoch_k, stoch_d, cloud_status, cloud_top, cloud_bottom,
      vwap, atr, target_1, target_2, stop_loss, volume_ratio
    """
    ticker = fv["ticker"]
    try:
        # ✅ sleep 제거: fetch_ohlcv 내부 FINNHUB_DELAY 와 ThreadPoolExecutor 가 충분히 제어
        ind = compute_indicators(ticker)
        if ind is None:
            log.warning("[%s] 지표 계산 실패", ticker)
            return None

        scoring = compute_score_and_status(ind, fv)

        return {
            "ticker":         ticker,
            "company":        fv.get("company", ticker),
            "price":          ind["price"],
            "change_1d":      ind["change_1d"],
            "finviz_change":  fv.get("finviz_change", 0),
            "score":          scoring["score"],
            "raw_score":      scoring["raw_score"],
            "entry":          scoring["entry"],
            "entry_key":      scoring["entry_key"],
            "signals":        scoring["signals"],
            "rsi":            ind.get("rsi",            50.0),
            "macd":           ind.get("macd",           0.0),
            "macd_signal":    ind.get("macd_signal",    0.0),
            "macd_histogram": ind.get("macd_histogram", 0.0),
            "ma20":           ind.get("ma20",           0),
            "ma50":           ind.get("ma50",           0),
            "ma200":          ind.get("ma200",          0),
            "ma_trend":       ind.get("ma_trend",       ""),
            "golden_cross":   ind.get("golden_cross",   False),
            "dead_cross":     ind.get("dead_cross",     False),
            "stoch_k":        ind.get("stoch_k",        50.0),
            "stoch_d":        ind.get("stoch_d",        50.0),
            "cloud_status":   ind.get("cloud_status",   ""),
            "cloud_top":      ind.get("cloud_top",      0),
            "cloud_bottom":   ind.get("cloud_bottom",   0),
            "vwap":           ind.get("vwap",           0),
            "atr":            ind.get("atr",            0),
            "target_1":       ind.get("target_1",       0),
            "target_2":       ind.get("target_2",       0),
            "stop_loss":      ind.get("stop_loss",      0),
            "volume_ratio":   ind.get("volume_ratio",   1.0),
        }
    except Exception as e:
        log.error("[%s] 분석 오류: %s", ticker, e, exc_info=True)
        return None


# ═══════════════════════════════════════════════════════════════════
# 메인 분석 오케스트레이터
# ═══════════════════════════════════════════════════════════════════

def analyze() -> dict:
    analyzed_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log.info("═══ 분석 시작: %s ═══", analyzed_at)

    # 3중 폴백 수집
    candidates = fetch_finviz_sp500_gainers()
    if not candidates:
        log.info("Finviz 실패 → yfinance batch 폴백")
        candidates = _fetch_yfinance_batch_fallback()
    if not candidates:
        log.info("yfinance batch 실패 → Finnhub 폴백")
        candidates = _fetch_finnhub_sp500_fallback()
    if not candidates:
        log.error("모든 데이터 소스 실패")
        return {
            "results":     [],
            "analyzed_at": analyzed_at,
            "green": 0, "wait": 0, "stop": 0,
            "error": "데이터 소스 없음 — 네트워크 또는 API 키를 확인하세요.",
        }

    log.info("후보 종목: %d개", len(candidates))

    # 병렬 분석
    results = []
    actual_workers = min(MAX_WORKERS, len(candidates))
    with ThreadPoolExecutor(max_workers=actual_workers) as executor:
        future_map = {
            executor.submit(analyze_ticker, fv): fv["ticker"]
            for fv in candidates
        }
        for future in as_completed(future_map):
            ticker = future_map[future]
            try:
                result = future.result()
                if result:
                    results.append(result)
            except Exception as e:
                log.error("[%s] future 오류: %s", ticker, e)

    if not results:
        log.error("분석 결과 없음")
        return {
            "results":     [],
            "analyzed_at": analyzed_at,
            "green": 0, "wait": 0, "stop": 0,
            "error": "전체 종목 분석 실패 — bot.log 를 확인하세요.",
        }

    results.sort(key=lambda x: x["score"], reverse=True)

    green_count = sum(1 for r in results if r["entry_key"] == "green")
    wait_count  = sum(1 for r in results if r["entry_key"] == "wait")
    stop_count  = sum(1 for r in results if r["entry_key"] == "stop")

    # 히스토리 저장
    ts        = datetime.now().strftime(HISTORY_TS_FMT)
    save_data = {
        "analyzed_at": analyzed_at,
        "total":       len(results),
        "green":       green_count,
        "wait":        wait_count,
        "stop":        stop_count,
        "results":     results,
    }
    save_path = HISTORY_DIR / f"{ts}.json"
    try:
        with open(save_path, "w", encoding="utf-8") as f:
            json.dump(save_data, f, ensure_ascii=False, indent=2, default=str)
        log.info("히스토리 저장: %s", save_path)
    except Exception as e:
        log.error("히스토리 저장 오류: %s", e)

    # Telegram 보고서 (4000자 제한)
    top10 = results[:10]
    lines = [
        "<b>📊 S&amp;P 500 모멘텀 분석</b>",
        f"🕐 {_escape_html(analyzed_at)}",
        f"총 {len(results)}종목 | 🟢{green_count} ⏳{wait_count} ❌{stop_count}",
        "",
    ]
    for i, r in enumerate(top10, 1):
        lines.append(
            f"{i}. <b>{_escape_html(r['ticker'])}</b> "
            f"{_escape_html(r['entry'])} "
            f"점수:{r['score']} | "
            f"${r['price']} ({r['change_1d']:+.1f}%)"
        )
    send_telegram("\n".join(lines))   # ✅ send_telegram 내부에서 [:4000] 처리

    # 콘솔 요약
    log.info("═══ 분석 완료 ═══")
    log.info(
        "총 %d종목 | 🟢 %d | ⏳ %d | ❌ %d",
        len(results), green_count, wait_count, stop_count,
    )
    for r in top10:
        log.info(
            "  %s %s  score=%d  $%.2f (%+.1f%%)",
            r["entry"], r["ticker"], r["score"], r["price"], r["change_1d"],
        )

    return save_data


if __name__ == "__main__":
    analyze()
