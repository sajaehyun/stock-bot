"""
S&P 500 + Semiconductor Momentum Scanner + Pre-Signal Scanner
────────────────────────────────────────────────────────────────
Mode 1 (analyze):           현재 기술적 신호 → 모멘텀 추천
Mode 2 (analyze_presignal): 선행 지표 → "곧 터질 종목" 탐색
Mode 3 (analyze_conviction): 7가지 요건 충족 + 확신신호 종목
Universes: S&P 500 / SOX (반도체 30)
"""

import os
import json
import time
import random
import logging
import inspect
import pathlib
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import numpy as np
import pandas as pd
from dotenv import load_dotenv

# ────────────────────────────────── 환경변수 ──────────────────────────────────
load_dotenv()
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "")
TELEGRAM_TOKEN  = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID         = os.getenv("CHAT_ID", "")

# ────────────────────────────────── 시간 ──────────────────────────────────────
KST = timezone(timedelta(hours=9))

# ────────────────────────────────── 상수 ──────────────────────────────────────
MAX_WORKERS     = 5
MAX_TICKERS     = 15
RAW_SCORE_MAX   = 140
RAW_SCORE_MIN   = -80
RAW_SCORE_RANGE = RAW_SCORE_MAX - RAW_SCORE_MIN
FINNHUB_BASE    = "https://finnhub.io/api/v1"
FINNHUB_DELAY   = 1.1
HISTORY_DIR     = pathlib.Path("history")
HISTORY_DIR.mkdir(exist_ok=True)
HISTORY_TS_FMT  = "%Y-%m-%d_%H%M%S"

PRESIGNAL_DIR = pathlib.Path("presignal")
PRESIGNAL_DIR.mkdir(exist_ok=True)
PRESIGNAL_MAX_RESULTS = 20

CONVICTION_DIR = pathlib.Path("conviction")
CONVICTION_DIR.mkdir(exist_ok=True)
CONVICTION_MAX_RESULTS = 20

# ────────────────────────────────── 종목 리스트 ───────────────────────────────
SP500_SYMBOLS = [
    "AAPL","MSFT","NVDA","AMZN","META","GOOGL","GOOG","BRK-B","LLY","AVGO",
    "JPM","TSLA","UNH","V","XOM","MA","JNJ","PG","COST","HD",
    "ABBV","MRK","WMT","CVX","BAC","NFLX","CRM","AMD","KO","PEP",
    "TMO","ACN","MCD","ADBE","LIN","DHR","CSCO","ABT","TXN","NEE",
    "WFC","PM","INTU","AMGN","MS","RTX","SPGI","HON","GE","CAT",
    "ISRG","BLK","VRTX","AXP","SYK","BKNG","PLD","TJX","GILD","ADI",
    "MDLZ","MRSH","CB","MO","SO","DUK","CL","BSX","EOG","ITW",
    "REGN","CME","PH","SLB","ZTS","MCO","USB","FISV","HCA","BDX",
    "CI","ICE","NOC","GD","MET","TGT","F","GM","UBER","NOW",
    "PANW","SNOW","COIN","PLTR","ARM","SMCI","DELL","HPQ","MU","QCOM",
    "SPCX",  # ✅ SpaceX (2026.06.12 NASDAQ 상장)
]

SOX_SYMBOLS = [
    "NVDA","AVGO","AMD","INTC","QCOM","TSM","MU","ASML","AMAT","LRCX",
    "KLAC","ADI","TXN","NXPI","MRVL","ON","SWKS","MCHP","ARM","MPWR",
    "COHR","ENTG","TER","GFS","CRDO","ALAB","MTSI","NVMI","QRVO","RMBS",
]

UNIVERSE_MAP = {
    "sp500":      {"name": "S&P 500",       "symbols": SP500_SYMBOLS},
    "sox":        {"name": "반도체 (SOX)",   "symbols": SOX_SYMBOLS},
    "sp500+sox":  {"name": "S&P 500 + SOX", "symbols": list(dict.fromkeys(SP500_SYMBOLS + SOX_SYMBOLS))},
}

CLOUD_STATUS_KO = {"above": "구름대 위 ☁️", "below": "구름대 아래 🌧", "inside": "구름대 안 🌫️"}
MA_TREND_KO     = {"bullish": "상승추세 📈", "bearish": "하락추세 📉"}

# ────────────────────────────────── 로깅 ──────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler("bot.log", encoding="utf-8"), logging.StreamHandler()],
)
log = logging.getLogger(__name__)

_YF_AVAILABLE    = False
_YF_SUPPORTS_MLI = False
try:
    import yfinance as yf
    _YF_AVAILABLE    = True
    _YF_SUPPORTS_MLI = "multi_level_index" in inspect.signature(yf.download).parameters
except ImportError:
    log.warning("yfinance 미설치 → Finnhub 단독 사용")

_FV_AVAILABLE = False
try:
    from finvizfinance.screener.overview import Overview
    _FV_AVAILABLE = True
except ImportError:
    log.warning("finvizfinance 미설치 → Finviz 스크리닝 기능 비활성화")


# ══════════════════════════════════════════════════════════════════════════════
# 유틸 함수
# ══════════════════════════════════════════════════════════════════════════════


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
    clamped = max(RAW_SCORE_MIN, min(RAW_SCORE_MAX, raw))
    return int(round((clamped - RAW_SCORE_MIN) / RAW_SCORE_RANGE * 100))


def _escape_html(text: str) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


# ══════════════════════════════════════════════════════════════════════════════
# Telegram
# ══════════════════════════════════════════════════════════════════════════════

def send_telegram(message: str) -> None:
    if not TELEGRAM_TOKEN or not CHAT_ID:
        log.info("Telegram 토큰 미설정 → 콘솔 출력")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        resp = requests.post(url, json={"chat_id": CHAT_ID, "text": message[:4000], "parse_mode": "HTML"}, timeout=10)
        if resp.status_code != 200:
            log.warning("Telegram 전송 실패: %s", resp.text[:200])
    except Exception as e:
        log.warning("Telegram 오류: %s", e)


# ══════════════════════════════════════════════════════════════════════════════
# Finnhub API 호출
# ══════════════════════════════════════════════════════════════════════════════

def _finnhub_get(endpoint: str, params: dict, retries: int = 3):
    if not FINNHUB_API_KEY:
        return None
    params = {**params, "token": FINNHUB_API_KEY}
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
            log.warning("Finnhub 네트워크 오류: %s", e)
            time.sleep(1)
    return None


# ══════════════════════════════════════════════════════════════════════════════
# OHLCV
# ══════════════════════════════════════════════════════════════════════════════

def _finnhub_candles(ticker: str, days: int = 730) -> pd.DataFrame | None:
    now   = int(datetime.now().timestamp())
    start = int((datetime.now() - timedelta(days=days)).timestamp())
    data  = _finnhub_get("stock/candle", {"symbol": ticker, "resolution": "D", "from": start, "to": now})
    if not data or data.get("s") != "ok":
        return None
    try:
        df = pd.DataFrame(
            {"Open": data["o"], "High": data["h"], "Low": data["l"], "Close": data["c"], "Volume": data["v"]},
            index=pd.to_datetime(data["t"], unit="s"),
        )
        df.index.name = "Date"
        return df if len(df) >= 40 else None
    except Exception as e:
        log.warning("Finnhub candles 실패 [%s]: %s", ticker, e)
        return None


def _yfinance_candles(ticker: str) -> pd.DataFrame | None:
    if not _YF_AVAILABLE:
        return None
    try:
        t = yf.Ticker(ticker)
        raw = t.history(period="2y", interval="1d", auto_adjust=True, timeout=10)
        if raw is None or raw.empty:
            return None
        df = raw.copy()
        rename = {}
        for c in df.columns:
            cl = str(c).lower().strip()
            if cl == "close":    rename[c] = "Close"
            elif cl == "open":   rename[c] = "Open"
            elif cl == "high":   rename[c] = "High"
            elif cl == "low":    rename[c] = "Low"
            elif cl == "volume": rename[c] = "Volume"
        df = df.rename(columns=rename)
        needed = {"Open", "High", "Low", "Close", "Volume"}
        if not needed.issubset(df.columns):
            return None
        df.index.name = "Date"
        return df[list(needed)] if len(df) >= 40 else None
    except Exception as e:
        log.warning("yfinance candles 실패 [%s]: %s", ticker, e)
        return None


def get_candles(ticker: str) -> pd.DataFrame | None:
    df = _finnhub_candles(ticker)
    if df is not None:
        return df
    log.info("[%s] Finnhub 실패 → yfinance 폴백", ticker)
    return _yfinance_candles(ticker)


def prefetch_ohlcv_batch(tickers: list[str]) -> dict[str, pd.DataFrame]:
    result = {}
    if _YF_AVAILABLE and len(tickers) > 1:
        try:
            kwargs = {"tickers": tickers, "period": "2y", "interval": "1d", "auto_adjust": True, "timeout": 20}
            if _YF_SUPPORTS_MLI:
                kwargs["multi_level_index"] = True
            raw = yf.download(**kwargs)
            if raw is not None and not raw.empty:
                cols = raw.columns
                if isinstance(cols, pd.MultiIndex):
                    for tkr in tickers:
                        try:
                            sub = raw.xs(tkr, axis=1, level=1).copy() if tkr in cols.get_level_values(1) else None
                            if sub is None or sub.empty:
                                continue
                            rename = {}
                            for c in sub.columns:
                                cl = str(c).lower().strip()
                                if cl == "close":    rename[c] = "Close"
                                elif cl == "open":   rename[c] = "Open"
                                elif cl == "high":   rename[c] = "High"
                                elif cl == "low":    rename[c] = "Low"
                                elif cl == "volume": rename[c] = "Volume"
                            sub = sub.rename(columns=rename)
                            needed = {"Open", "High", "Low", "Close", "Volume"}
                            if needed.issubset(sub.columns) and len(sub) >= 40:
                                result[tkr] = sub[list(needed)]
                        except Exception:
                            pass
                else:
                    rename = {}
                    for c in cols:
                        cl = str(c).lower().strip()
                        if cl == "close":    rename[c] = "Close"
                        elif cl == "open":   rename[c] = "Open"
                        elif cl == "high":   rename[c] = "High"
                        elif cl == "low":    rename[c] = "Low"
                        elif cl == "volume": rename[c] = "Volume"
                    raw = raw.rename(columns=rename)
                    needed = {"Open", "High", "Low", "Close", "Volume"}
                    if needed.issubset(raw.columns) and len(raw) >= 40:
                        if len(tickers) == 1:
                            result[tickers[0]] = raw[list(needed)]
        except Exception as e:
            log.warning("yfinance batch 실패: %s", e)
    return result


# ══════════════════════════════════════════════════════════════════════════════
# 기술적 지표
# ══════════════════════════════════════════════════════════════════════════════

def calc_rsi(series: pd.Series, period: int = 14) -> float:
    delta = series.diff().dropna()
    gain  = delta.clip(lower=0)
    loss  = (-delta).clip(lower=0)
    avg_g = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_l = loss.ewm(com=period - 1, min_periods=period).mean()
    rs    = avg_g / avg_l.replace(0, np.nan)
    rsi   = 100 - (100 / (1 + rs))
    return safe_float(rsi.iloc[-1])


def calc_macd(series: pd.Series):
    ema12  = series.ewm(span=12, adjust=False).mean()
    ema26  = series.ewm(span=26, adjust=False).mean()
    macd   = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    hist   = macd - signal
    return safe_float(macd.iloc[-1]), safe_float(signal.iloc[-1]), safe_float(hist.iloc[-1])


def calc_bollinger(series: pd.Series, window: int = 20):
    ma  = series.rolling(window).mean()
    std = series.rolling(window).std()
    upper = ma + 2 * std
    lower = ma - 2 * std
    return safe_float(upper.iloc[-1]), safe_float(ma.iloc[-1]), safe_float(lower.iloc[-1])


def calc_ichimoku(df: pd.DataFrame):
    high = df["High"]
    low  = df["Low"]
    tenkan  = (high.rolling(9).max()  + low.rolling(9).min())  / 2
    kijun   = (high.rolling(26).max() + low.rolling(26).min()) / 2
    span_a  = ((tenkan + kijun) / 2).shift(26)
    span_b  = ((high.rolling(52).max() + low.rolling(52).min()) / 2).shift(26)
    close   = df["Close"].iloc[-1]
    sa_last = safe_float(span_a.iloc[-1])
    sb_last = safe_float(span_b.iloc[-1])
    cloud_top    = max(sa_last, sb_last)
    cloud_bottom = min(sa_last, sb_last)
    if close > cloud_top:
        cloud_pos = "above"
    elif close < cloud_bottom:
        cloud_pos = "below"
    else:
        cloud_pos = "inside"
    return {
        "tenkan":      safe_float(tenkan.iloc[-1]),
        "kijun":       safe_float(kijun.iloc[-1]),
        "span_a":      sa_last,
        "span_b":      sb_last,
        "cloud_pos":   cloud_pos,
        "cloud_top":   cloud_top,
        "cloud_bottom":cloud_bottom,
    }


def calc_ma_trend(df: pd.DataFrame):
    c   = df["Close"]
    ma5  = safe_float(c.rolling(5).mean().iloc[-1])
    ma20 = safe_float(c.rolling(20).mean().iloc[-1])
    ma50 = safe_float(c.rolling(50).mean().iloc[-1])
    ma200= safe_float(c.rolling(200).mean().iloc[-1]) if len(c) >= 200 else 0.0
    trend = "bullish" if ma5 > ma20 > ma50 else "bearish"
    return {"ma5": ma5, "ma20": ma20, "ma50": ma50, "ma200": ma200, "trend": trend}


def calc_volume_surge(df: pd.DataFrame) -> float:
    vol = df["Volume"]
    avg20 = safe_float(vol.rolling(20).mean().iloc[-2])
    last  = safe_float(vol.iloc[-1])
    return round(last / avg20, 2) if avg20 > 0 else 0.0


def calc_atr(df: pd.DataFrame, period: int = 14) -> float:
    high  = df["High"]
    low   = df["Low"]
    close = df["Close"]
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low  - close.shift()).abs(),
    ], axis=1).max(axis=1)
    return safe_float(tr.rolling(period).mean().iloc[-1])


# ══════════════════════════════════════════════════════════════════════════════
# Finnhub 펀더멘털 & 뉴스
# ══════════════════════════════════════════════════════════════════════════════

def get_finnhub_quote(ticker: str) -> dict:
    data = _finnhub_get("quote", {"symbol": ticker})
    if not data:
        return {}
    return {
        "price":   safe_float(data.get("c")),
        "change":  safe_float(data.get("d")),
        "pct":     safe_float(data.get("dp")),
        "high":    safe_float(data.get("h")),
        "low":     safe_float(data.get("l")),
        "open":    safe_float(data.get("o")),
        "prev":    safe_float(data.get("pc")),
    }


def get_finnhub_sentiment(ticker: str) -> dict:
    end   = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    data  = _finnhub_get("company-news", {"symbol": ticker, "from": start, "to": end})
    if not data or not isinstance(data, list):
        return {"count": 0, "score": 0.0}
    count = len(data)
    score = 0.0
    pos_kw = ["surge", "beat", "record", "upgrade", "buy", "strong", "growth", "rally", "bullish", "breakout"]
    neg_kw = ["miss", "cut", "downgrade", "sell", "weak", "decline", "bearish", "crash", "loss", "risk"]
    for item in data[:10]:
        headline = (item.get("headline") or "").lower()
        score += sum(1  for kw in pos_kw if kw in headline)
        score -= sum(0.7 for kw in neg_kw if kw in headline)
    return {"count": count, "score": round(score, 2)}


def get_finnhub_recommendation(ticker: str) -> dict:
    data = _finnhub_get("stock/recommendation", {"symbol": ticker})
    if not data or not isinstance(data, list) or not data:
        return {}
    latest = data[0]
    total  = sum([latest.get("strongBuy", 0), latest.get("buy", 0),
                  latest.get("hold", 0), latest.get("sell", 0), latest.get("strongSell", 0)])
    buy_cnt = latest.get("strongBuy", 0) + latest.get("buy", 0)
    ratio   = round(buy_cnt / total * 100, 1) if total > 0 else 0.0
    return {"strongBuy": latest.get("strongBuy", 0), "buy": latest.get("buy", 0),
            "hold": latest.get("hold", 0), "sell": latest.get("sell", 0),
            "strongSell": latest.get("strongSell", 0), "buy_ratio": ratio}


def get_finnhub_price_target(ticker: str) -> dict:
    data = _finnhub_get("stock/price-target", {"symbol": ticker})
    if not data:
        return {}
    return {
        "mean":   safe_float(data.get("targetMean")),
        "high":   safe_float(data.get("targetHigh")),
        "low":    safe_float(data.get("targetLow")),
        "median": safe_float(data.get("targetMedian")),
    }


def get_finnhub_earnings(ticker: str) -> dict:
    data = _finnhub_get("stock/earnings", {"symbol": ticker, "limit": 4})
    if not data or not isinstance(data, list):
        return {}
    beats = sum(1 for e in data if safe_float(e.get("actual")) > safe_float(e.get("estimate")))
    return {"beats": beats, "total": len(data), "beat_rate": round(beats / len(data) * 100, 1) if data else 0.0}


def get_finnhub_insider(ticker: str) -> dict:
    end   = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
    data  = _finnhub_get("stock/insider-transactions", {"symbol": ticker, "from": start, "to": end})
    if not data or "data" not in data:
        return {"buy": 0, "sell": 0}
    txns = data["data"]
    buy  = sum(1 for t in txns if (t.get("transactionType") or "").upper() in ("P", "BUY", "GRANT"))
    sell = sum(1 for t in txns if (t.get("transactionType") or "").upper() in ("S", "SELL"))
    return {"buy": buy, "sell": sell}


# ══════════════════════════════════════════════════════════════════════════════
# 분석 엔진 — Mode 1
# ══════════════════════════════════════════════════════════════════════════════

def analyze(ticker: str, prefetched: dict | None = None) -> dict | None:
    df = prefetched.get(ticker) if prefetched else None
    if df is None:
        df = get_candles(ticker)
    if df is None or df.empty:
        return None

    close  = df["Close"]
    price  = safe_float(close.iloc[-1])
    prev   = safe_float(close.iloc[-2])
    chg_pct = round((price - prev) / prev * 100, 2) if prev else 0.0

    rsi                    = calc_rsi(close)
    macd_v, macd_sig, macd_hist = calc_macd(close)
    bb_up, bb_mid, bb_low  = calc_bollinger(close)
    ichimoku               = calc_ichimoku(df)
    ma                     = calc_ma_trend(df)
    vol_surge              = calc_volume_surge(df)
    atr                    = calc_atr(df)

    quote      = get_finnhub_quote(ticker)
    sentiment  = get_finnhub_sentiment(ticker)
    rec        = get_finnhub_recommendation(ticker)
    pt         = get_finnhub_price_target(ticker)
    earnings   = get_finnhub_earnings(ticker)
    insider    = get_finnhub_insider(ticker)

    # 점수 계산
    raw = 0.0

    # RSI
    if rsi < 30:   raw += 20
    elif rsi < 40: raw += 10
    elif rsi < 50: raw += 5
    elif rsi > 70: raw -= 15
    elif rsi > 60: raw -= 5

    # MACD
    if macd_v > macd_sig: raw += 15
    else:                  raw -= 10
    if macd_hist > 0:      raw += 5
    else:                  raw -= 3

    # 볼린저밴드
    if price < bb_low:     raw += 15
    elif price > bb_up:    raw -= 10
    elif price > bb_mid:   raw += 5

    # 이치모쿠
    cp = ichimoku["cloud_pos"]
    if cp == "above":  raw += 20
    elif cp == "below": raw -= 15

    t_vs_k = ichimoku["tenkan"] - ichimoku["kijun"]
    if t_vs_k > 0:  raw += 10
    else:            raw -= 5

    # MA 추세
    if ma["trend"] == "bullish":  raw += 15
    else:                          raw -= 10

    # 거래량 급증
    if vol_surge >= 3.0:    raw += 20
    elif vol_surge >= 2.0:  raw += 12
    elif vol_surge >= 1.5:  raw += 6

    # 전일 대비 등락
    if chg_pct >= 3:    raw += 10
    elif chg_pct >= 1:  raw += 5
    elif chg_pct <= -3: raw -= 10
    elif chg_pct <= -1: raw -= 3

    # 뉴스 감성
    raw += min(10, max(-10, sentiment["score"] * 2))

    # 애널리스트 추천
    if rec.get("buy_ratio", 0) >= 70:  raw += 10
    elif rec.get("buy_ratio", 0) >= 50: raw += 5

    # 목표가 대비 업사이드
    upside = 0.0
    if pt.get("mean") and price > 0:
        upside = round((pt["mean"] - price) / price * 100, 1)
        if upside >= 20:   raw += 15
        elif upside >= 10: raw += 8
        elif upside < 0:   raw -= 8

    # 실적 비트율
    if earnings.get("beat_rate", 0) >= 75: raw += 8
    elif earnings.get("beat_rate", 0) >= 50: raw += 3

    # 내부자 거래
    if insider["buy"] > insider["sell"]:   raw += 5
    elif insider["sell"] > insider["buy"]: raw -= 5

    score = normalize_score(raw)

    if score >= 65:   signal = "BUY"
    elif score <= 35: signal = "SELL"
    else:              signal = "WATCH"

    return {
        "ticker": ticker, "price": price, "chg_pct": chg_pct,
        "score": score, "signal": signal, "raw": raw,
        "rsi": rsi, "macd_hist": macd_hist,
        "ichimoku": ichimoku, "ma": ma,
        "vol_surge": vol_surge, "atr": atr,
        "bb": {"upper": bb_up, "mid": bb_mid, "lower": bb_low},
        "sentiment": sentiment, "rec": rec, "pt": pt,
        "earnings": earnings, "insider": insider,
        "upside": upside,
    }


# ══════════════════════════════════════════════════════════════════════════════
# 분석 엔진 — Mode 2 (Pre-Signal)
# ══════════════════════════════════════════════════════════════════════════════

def analyze_presignal(ticker: str, prefetched: dict | None = None) -> dict | None:
    df = prefetched.get(ticker) if prefetched else None
    if df is None:
        df = get_candles(ticker)
    if df is None or df.empty:
        return None

    close = df["Close"]
    price = safe_float(close.iloc[-1])
    prev  = safe_float(close.iloc[-2])
    chg_pct = round((price - prev) / prev * 100, 2) if prev else 0.0

    rsi                         = calc_rsi(close)
    macd_v, macd_sig, macd_hist = calc_macd(close)
    bb_up, bb_mid, bb_low       = calc_bollinger(close)
    ichimoku                    = calc_ichimoku(df)
    ma                          = calc_ma_trend(df)
    vol_surge                   = calc_volume_surge(df)

    signals = []
    score   = 0

    # RSI 과매도 반등
    if 25 <= rsi <= 42:
        signals.append(f"RSI 과매도 반등 구간 ({rsi:.1f})")
        score += 30

    # MACD 데드크로스 직전 반전
    gap = macd_v - macd_sig
    if -0.5 < gap < 0.1 and macd_hist > -0.05:
        signals.append(f"MACD 골든크로스 임박 (gap={gap:.3f})")
        score += 25

    # 볼린저 밴드 하단 접근
    if price <= bb_low * 1.02:
        signals.append(f"볼린저 하단 접근 (BB하단={bb_low:.2f})")
        score += 20

    # 이치모쿠 구름대 진입 직전
    cloud_dist = (price - ichimoku["cloud_bottom"]) / price * 100
    if 0 < cloud_dist < 3:
        signals.append(f"구름대 하단 돌파 임박 ({cloud_dist:.1f}%)")
        score += 20

    # MA 정배열 직전
    if ma["ma5"] > ma["ma20"] and ma["ma20"] < ma["ma50"] * 1.01:
        signals.append("MA 정배열 전환 임박")
        score += 15

    # 거래량 선행 급증
    if 1.5 <= vol_surge < 2.5:
        signals.append(f"거래량 선행 급증 ({vol_surge:.1f}x)")
        score += 15

    # 전일 대비 소폭 반등
    if 0.5 <= chg_pct <= 2.5:
        signals.append(f"소폭 반등 시작 (+{chg_pct:.1f}%)")
        score += 10

    if not signals or score < 40:
        return None

    return {
        "ticker": ticker, "price": price, "chg_pct": chg_pct,
        "score": score, "signals": signals,
        "rsi": rsi, "macd_hist": macd_hist,
        "vol_surge": vol_surge,
        "ichimoku": ichimoku, "ma": ma,
    }


# ══════════════════════════════════════════════════════════════════════════════
# 분석 엔진 — Mode 3 (Conviction)
# ══════════════════════════════════════════════════════════════════════════════

def analyze_conviction(ticker: str, prefetched: dict | None = None) -> dict | None:
    base = analyze(ticker, prefetched)
    if base is None or base["score"] < 60:
        return None

    df = prefetched.get(ticker) if prefetched else None
    if df is None:
        df = get_candles(ticker)
    if df is None:
        return None

    checks = {}

    # 1. 이치모쿠 구름 위
    checks["ichimoku_above"] = base["ichimoku"]["cloud_pos"] == "above"

    # 2. MA 정배열
    checks["ma_bullish"] = base["ma"]["trend"] == "bullish"

    # 3. RSI 모멘텀 구간
    checks["rsi_momentum"] = 45 <= base["rsi"] <= 68

    # 4. MACD 양전환
    checks["macd_positive"] = base["macd_hist"] > 0

    # 5. 거래량 급증
    checks["volume_surge"] = base["vol_surge"] >= 1.5

    # 6. 애널리스트 매수 우세
    checks["analyst_buy"] = base["rec"].get("buy_ratio", 0) >= 55

    # 7. 목표가 업사이드
    checks["pt_upside"] = base["upside"] >= 10

    passed = sum(checks.values())
    if passed < 5:
        return None

    return {**base, "conviction_checks": checks, "conviction_passed": passed}


# ══════════════════════════════════════════════════════════════════════════════
# 결과 저장
# ══════════════════════════════════════════════════════════════════════════════

def _save_history(results: list[dict], universe: str, mode: str) -> None:
    ts   = datetime.now(KST).strftime(HISTORY_TS_FMT)
    path = HISTORY_DIR / f"{universe}_{mode}_{ts}.json"
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2, default=str)
        log.info("히스토리 저장: %s", path)
    except Exception as e:
        log.warning("히스토리 저장 실패: %s", e)


def _save_presignal(results: list[dict], universe: str) -> None:
    ts   = datetime.now(KST).strftime(HISTORY_TS_FMT)
    path = PRESIGNAL_DIR / f"{universe}_presignal_{ts}.json"
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2, default=str)
        log.info("프리시그널 저장: %s", path)
    except Exception as e:
        log.warning("프리시그널 저장 실패: %s", e)


def _save_conviction(results: list[dict], universe: str) -> None:
    ts   = datetime.now(KST).strftime(HISTORY_TS_FMT)
    path = CONVICTION_DIR / f"{universe}_conviction_{ts}.json"
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2, default=str)
        log.info("확신신호 저장: %s", path)
    except Exception as e:
        log.warning("확신신호 저장 실패: %s", e)


# ══════════════════════════════════════════════════════════════════════════════
# 메시지 포매터
# ══════════════════════════════════════════════════════════════════════════════

def _signal_emoji(signal: str) -> str:
    return {"BUY": "🟢", "SELL": "🔴", "WATCH": "🟡"}.get(signal, "⚪")


def _score_bar(score: int) -> str:
    filled = round(score / 10)
    return "█" * filled + "░" * (10 - filled)


def format_mode1(results: list[dict], universe_name: str) -> str:
    now = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
    lines = [f"<b>📊 모멘텀 스캐너 — {_escape_html(universe_name)}</b>",
             f"<i>{now}</i>", ""]

    buy_list   = [r for r in results if r["signal"] == "BUY"]
    watch_list = [r for r in results if r["signal"] == "WATCH"]
    sell_list  = [r for r in results if r["signal"] == "SELL"]

    def _fmt(r):
        chg = r["chg_pct"]
        chg_str = f"+{chg:.1f}%" if chg >= 0 else f"{chg:.1f}%"
        upside_str = f" | 목표가↑{r['upside']:.0f}%" if r.get("upside", 0) > 5 else ""
        rec_str = f" | 매수비율 {r['rec'].get('buy_ratio',0):.0f}%" if r.get("rec") else ""
        return (
            f"{_signal_emoji(r['signal'])} <b>{r['ticker']}</b> ${r['price']:.2f} ({chg_str})\n"
            f"   점수: {r['score']}/100 [{_score_bar(r['score'])}]\n"
            f"   RSI {r['rsi']:.1f} | MACD히스트 {r['macd_hist']:.3f} | 거래량 {r['vol_surge']:.1f}x\n"
            f"   {CLOUD_STATUS_KO.get(r['ichimoku']['cloud_pos'],'')} | {MA_TREND_KO.get(r['ma']['trend'],'')}"
            f"{upside_str}{rec_str}"
        )

    if buy_list:
        lines.append("🟢 <b>매수 추천</b>")
        for r in buy_list[:5]:
            lines.append(_fmt(r))
        lines.append("")

    if watch_list:
        lines.append("🟡 <b>관망</b>")
        for r in watch_list[:5]:
            lines.append(_fmt(r))
        lines.append("")

    if sell_list:
        lines.append("🔴 <b>매도/회피</b>")
        for r in sell_list[:3]:
            lines.append(_fmt(r))
        lines.append("")

    lines.append(f"<i>총 {len(results)}종목 분석 완료</i>")
    return "\n".join(lines)


def format_mode2(results: list[dict], universe_name: str) -> str:
    now = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
    lines = [f"<b>🔍 선행신호 스캐너 — {_escape_html(universe_name)}</b>",
             f"<i>{now}</i>", ""]
    for r in results[:PRESIGNAL_MAX_RESULTS]:
        chg = r["chg_pct"]
        chg_str = f"+{chg:.1f}%" if chg >= 0 else f"{chg:.1f}%"
        lines.append(
            f"⚡ <b>{r['ticker']}</b> ${r['price']:.2f} ({chg_str}) | 선행점수: {r['score']}\n"
            f"   RSI {r['rsi']:.1f} | 거래량 {r['vol_surge']:.1f}x\n"
            f"   신호: {' / '.join(r['signals'])}"
        )
    lines.append(f"\n<i>총 {len(results)}종목 선행신호 감지</i>")
    return "\n".join(lines)


def format_mode3(results: list[dict], universe_name: str) -> str:
    now = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
    lines = [f"<b>💎 확신신호 스캐너 — {_escape_html(universe_name)}</b>",
             f"<i>{now}</i>", ""]
    check_labels = {
        "ichimoku_above": "이치모쿠 구름 위",
        "ma_bullish":     "MA 정배열",
        "rsi_momentum":   "RSI 모멘텀",
        "macd_positive":  "MACD 양전환",
        "volume_surge":   "거래량 급증",
        "analyst_buy":    "애널리스트 매수우세",
        "pt_upside":      "목표가 업사이드",
    }
    for r in results[:CONVICTION_MAX_RESULTS]:
        chg = r["chg_pct"]
        chg_str = f"+{chg:.1f}%" if chg >= 0 else f"{chg:.1f}%"
        passed = r["conviction_passed"]
        checks = r["conviction_checks"]
        check_str = " | ".join(
            f"{'✅' if v else '❌'} {check_labels[k]}"
            for k, v in checks.items()
        )
        lines.append(
            f"💎 <b>{r['ticker']}</b> ${r['price']:.2f} ({chg_str})\n"
            f"   점수: {r['score']}/100 | 조건 충족: {passed}/7\n"
            f"   {check_str}\n"
            f"   업사이드: {r.get('upside',0):.1f}% | RSI {r['rsi']:.1f}"
        )
    lines.append(f"\n<i>총 {len(results)}종목 확신신호 감지</i>")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# 스캔 실행
# ══════════════════════════════════════════════════════════════════════════════

def run_scan(universe: str = "sp500", mode: str = "analyze") -> None:
    uni  = UNIVERSE_MAP.get(universe, UNIVERSE_MAP["sp500"])
    syms = uni["symbols"][:MAX_TICKERS]
    name = uni["name"]
    log.info("스캔 시작: %s / mode=%s / %d종목", name, mode, len(syms))

    # yfinance 배치 프리페치
    prefetched = prefetch_ohlcv_batch(syms)
    log.info("배치 프리페치 완료: %d/%d", len(prefetched), len(syms))

    results = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        if mode == "analyze":
            futs = {ex.submit(analyze, s, prefetched): s for s in syms}
        elif mode == "analyze_presignal":
            futs = {ex.submit(analyze_presignal, s, prefetched): s for s in syms}
        elif mode == "analyze_conviction":
            futs = {ex.submit(analyze_conviction, s, prefetched): s for s in syms}
        else:
            futs = {ex.submit(analyze, s, prefetched): s for s in syms}

        for fut in as_completed(futs):
            tkr = futs[fut]
            try:
                res = fut.result()
                if res:
                    results.append(res)
                    log.info("[%s] 완료: signal=%s score=%s",
                             tkr, res.get("signal", res.get("score")), res.get("score"))
            except Exception as e:
                log.warning("[%s] 오류: %s", tkr, e)

    if not results:
        send_telegram(f"⚠️ {name} 스캔 결과 없음 (mode={mode})")
        return

    if mode == "analyze":
        results.sort(key=lambda x: x["score"], reverse=True)
        _save_history(results, universe, mode)
        msg = format_mode1(results, name)
    elif mode == "analyze_presignal":
        results.sort(key=lambda x: x["score"], reverse=True)
        _save_presignal(results, universe)
        msg = format_mode2(results, name)
    elif mode == "analyze_conviction":
        results.sort(key=lambda x: x["conviction_passed"], reverse=True)
        _save_conviction(results, universe)
        msg = format_mode3(results, name)
    else:
        results.sort(key=lambda x: x["score"], reverse=True)
        msg = format_mode1(results, name)

    send_telegram(msg)
    log.info("스캔 완료: %d종목 결과 전송", len(results))


# ══════════════════════════════════════════════════════════════════════════════
# Flask 웹 UI
# ══════════════════════════════════════════════════════════════════════════════

from flask import Flask, request, jsonify, render_template

app = Flask(__name__)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/scan", methods=["POST"])
def scan():
    data     = request.get_json(silent=True) or {}
    universe = data.get("universe", "sp500")
    mode     = data.get("mode", "analyze")
    if universe not in UNIVERSE_MAP:
        return jsonify({"error": f"Unknown universe: {universe}"}), 400
    valid_modes = {"analyze", "analyze_presignal", "analyze_conviction"}
    if mode not in valid_modes:
        return jsonify({"error": f"Unknown mode: {mode}"}), 400
    try:
        run_scan(universe=universe, mode=mode)
        return jsonify({"status": "ok", "universe": universe, "mode": mode})
    except Exception as e:
        log.exception("스캔 오류")
        return jsonify({"error": str(e)}), 500


@app.route("/history")
def history():
    files = sorted(HISTORY_DIR.glob("*.json"), reverse=True)[:20]
    result = []
    for f in files:
        try:
            with open(f, encoding="utf-8") as fp:
                result.append({"file": f.name, "data": json.load(fp)})
        except Exception:
            pass
    return jsonify(result)


@app.route("/presignal")
def presignal_history():
    files = sorted(PRESIGNAL_DIR.glob("*.json"), reverse=True)[:20]
    result = []
    for f in files:
        try:
            with open(f, encoding="utf-8") as fp:
                result.append({"file": f.name, "data": json.load(fp)})
        except Exception:
            pass
    return jsonify(result)


@app.route("/conviction")
def conviction_history():
    files = sorted(CONVICTION_DIR.glob("*.json"), reverse=True)[:20]
    result = []
    for f in files:
        try:
            with open(f, encoding="utf-8") as fp:
                result.append({"file": f.name, "data": json.load(fp)})
        except Exception:
            pass
    return jsonify(result)


@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "yfinance": _YF_AVAILABLE,
        "finnhub":  bool(FINNHUB_API_KEY),
        "telegram": bool(TELEGRAM_TOKEN and CHAT_ID),
        "time_kst": datetime.now(KST).isoformat(),
    })


# ══════════════════════════════════════════════════════════════════════════════
# 진입점
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
