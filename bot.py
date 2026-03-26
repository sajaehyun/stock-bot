"""
S&P 500 + Semiconductor Momentum Scanner + Pre-Signal Scanner
──────────────────────────────────────────────────────────────
Mode 1 (analyze):            당일 상승 종목 → 기술적 점수화
Mode 2 (analyze_presignal):  전체 스캔 → "곧 움직일" 선행 신호 탐색
Mode 3 (analyze_conviction): 7개 독립 필터 + 오버랩 보너스
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

# ──────────────────────────── 환경 변수 ────────────────────────────
load_dotenv()
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "")
TELEGRAM_TOKEN  = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID         = os.getenv("CHAT_ID", "")

# ──────────────────────────── 시간대 ────────────────────────────────
KST = timezone(timedelta(hours=9))

# ──────────────────────────── 상수 ─────────────────────────────────
MAX_WORKERS     = 5
MAX_TICKERS     = 30
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

# ──────────────────────────── 종목 유니버스 ────────────────────────
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

CLOUD_STATUS_KO = {"above": "구름 위 ☁️", "below": "구름 아래 ⛅", "inside": "구름 안 🌫️"}
MA_TREND_KO     = {"bullish": "정배열 📈", "bearish": "역배열 📉"}

# ──────────────────────────── 로깅 ─────────────────────────────────
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
    log.warning("yfinance 미설치 → Finnhub 전용 모드")

_FV_AVAILABLE = False
try:
    from finvizfinance.screener.overview import Overview
    _FV_AVAILABLE = True
except ImportError:
    log.warning("finvizfinance 미설치 → Finviz 스크리너 비활성화")

# ── yfinance용 공유 세션 (타임아웃 5초) ───────────────────────────



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
    clamped = max(RAW_SCORE_MIN, min(RAW_SCORE_MAX, raw))
    return int(round((clamped - RAW_SCORE_MIN) / RAW_SCORE_RANGE * 100))


def _escape_html(text: str) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


# ═══════════════════════════════════════════════════════════════════
# Telegram
# ═══════════════════════════════════════════════════════════════════

def send_telegram(message: str) -> None:
    if not TELEGRAM_TOKEN or not CHAT_ID:
        log.info("Telegram 토큰 미설정 → 전송 스킵")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        resp = requests.post(url, json={"chat_id": CHAT_ID, "text": message[:4000], "parse_mode": "HTML"}, timeout=10)
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
            log.warning("Finnhub 요청 오류: %s", e)
            time.sleep(1)
    return None


# ═══════════════════════════════════════════════════════════════════
# OHLCV
# ═══════════════════════════════════════════════════════════════════

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
        log.warning("Finnhub candles 파싱 오류 [%s]: %s", ticker, e)
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
        if "Close" not in df.columns:
            return None
        if df.columns.duplicated().any():
            df = df.loc[:, ~df.columns.duplicated(keep="first")]
        return df if len(df) >= 40 else None
    except Exception as e:
        log.warning("yfinance 다운로드 오류 [%s]: %s", ticker, e)
        return None


def fetch_ohlcv(ticker: str) -> pd.DataFrame | None:
    df = _finnhub_candles(ticker)
    if df is not None:
        return df
    df = _yfinance_candles(ticker)
    if df is not None:
        log.info("[%s] yfinance 폴백 사용", ticker)
    return df


# ═══════════════════════════════════════════════════════════════════
# 종목 수집 (모멘텀용 3중 폴백)
# ═══════════════════════════════════════════════════════════════════

def fetch_finviz_sp500_gainers() -> list:
    if not _FV_AVAILABLE:
        return []
    try:
        foverview = Overview()
        try:
            foverview.set_filter(signal="Top Gainers", filters_dict={"Index": "S&P 500"})
        except (TypeError, AttributeError):
            foverview.set_filter(filters_dict={"Index": "S&P 500"})
        df = foverview.screener_view()
        if df is None or df.empty:
            return []
        results = []
        for _, row in df.head(MAX_TICKERS).iterrows():
            ticker = str(row.get("Ticker", "")).strip().upper()
            if not ticker:
                continue
            results.append({
                "ticker": ticker, "company": str(row.get("Company", "")),
                "finviz_price": safe_float(row.get("Price"), 0),
                "finviz_change": _parse_pct(row.get("Change", 0)),
            })
        log.info("Finviz: %d 종목 수집", len(results))
        return results
    except Exception as e:
        log.warning("Finviz 오류: %s", e)
        return []


def _fetch_yfinance_batch_fallback() -> list:
    if not _YF_AVAILABLE or not SP500_SYMBOLS:
        return []
    try:
        kw = {"period": "5d", "interval": "1d", "auto_adjust": True, "progress": False}
        if _YF_SUPPORTS_MLI:
            kw["multi_level_index"] = False
        raw = yf.download(SP500_SYMBOLS, **kw)
        if raw is None or raw.empty:
            return []
        if isinstance(raw.columns, pd.MultiIndex):
            close = raw["Close"].copy() if "Close" in raw.columns.get_level_values(0) else None
        elif "Close" in raw.columns:
            close = raw[["Close"]].copy()
        else:
            return []
        if close is None or isinstance(close, pd.Series) or len(close) < 2:
            return []
        last = close.iloc[-1]
        prev = close.iloc[-2]
        chg  = ((last - prev) / prev * 100).dropna().sort_values(ascending=False)
        results = []
        for sym in chg.head(MAX_TICKERS).index:
            price_val = float(last[sym]) if sym in last.index else 0
            chg_val   = float(chg[sym])  if sym in chg.index  else 0
            if price_val <= 0 or np.isnan(price_val):
                continue
            results.append({
                "ticker": str(sym).strip().upper(), "company": str(sym),
                "finviz_price": round(price_val, 2), "finviz_change": round(chg_val, 2),
            })
        log.info("yfinance batch 폴백: %d 종목", len(results))
        return results
    except Exception as e:
        log.warning("yfinance batch 폴백 오류: %s", e)
        return []


def _fetch_finnhub_sp500_fallback() -> list:
    if not FINNHUB_API_KEY:
        return []
    def _fetch_quote(sym):
        q = _finnhub_get("quote", {"symbol": sym})
        if q and q.get("c", 0) > 0 and q.get("pc", 0) > 0:
            chg = (q["c"] - q["pc"]) / q["pc"] * 100
            return {"ticker": sym, "company": sym, "finviz_price": round(q["c"], 2), "finviz_change": round(chg, 2)}
        return None
    with ThreadPoolExecutor(max_workers=3) as ex:
        raw = list(ex.map(_fetch_quote, SP500_SYMBOLS))
    quotes = sorted([r for r in raw if r], key=lambda x: x["finviz_change"], reverse=True)
    result = quotes[:MAX_TICKERS]
    log.info("Finnhub 폴백: %d 종목", len(result))
    return result


# ═══════════════════════════════════════════════════════════════════
# 단기용 어닝/옵션 경량 분석
# ═══════════════════════════════════════════════════════════════════

_EMPTY_EARNINGS = {
    "earnings_near": False, "days_to_earnings": None, "last_beat": None,
    "last_surprise_pct": None, "post_reaction_1d": None,
    "earnings_signals": [], "earnings_adj": 0,
}

_EMPTY_OPTIONS = {
    "put_call_ratio": None, "options_signals": [], "options_adj": 0,
}


def _quick_earnings_check(ticker: str) -> dict:
    result = {**_EMPTY_EARNINGS, "earnings_signals": []}
    if not _YF_AVAILABLE:
        return result
    try:
        t = yf.Ticker(ticker)
        ed = t.earnings_dates
        if ed is None or ed.empty:
            return result

        from datetime import date
        today = date.today()

        future = ed[ed["Reported EPS"].isna()]
        if not future.empty:
            next_date = future.index[0].date()
            days = (next_date - today).days
            result["days_to_earnings"] = days
            if 0 <= days <= 7:
                result["earnings_near"] = True
                result["earnings_signals"].append(f"⚡ 어닝 {days}일 후 ({next_date})")
                result["earnings_adj"] += 5

        past = ed.dropna(subset=["Reported EPS"])
        if not past.empty:
            row = past.iloc[0]
            reported = float(row.get("Reported EPS", 0))
            estimate = float(row.get("EPS Estimate", 0)) if row.get("EPS Estimate") is not None else None
            if estimate is not None and estimate != 0:
                result["last_beat"] = reported > estimate
                result["last_surprise_pct"] = round((reported - estimate) / abs(estimate) * 100, 1)
                if result["last_beat"] and result["last_surprise_pct"] > 10:
                    result["earnings_signals"].append(f"🔥 최근 어닝 +{result['last_surprise_pct']}% 서프라이즈")
                    result["earnings_adj"] += 8
                elif result["last_beat"]:
                    result["earnings_signals"].append(f"✅ 최근 어닝 비트 (+{result['last_surprise_pct']}%)")
                    result["earnings_adj"] += 3
                elif result["last_surprise_pct"] < -10:
                    result["earnings_signals"].append(f"📉 최근 어닝 미스 ({result['last_surprise_pct']}%)")
                    result["earnings_adj"] -= 8

            try:
                hist = t.history(period="3mo", timeout=10)
                if hist is not None and not hist.empty:
                    e_date = pd.to_datetime(past.index[0]).normalize()
                    after  = hist.index[hist.index >= e_date]
                    before = hist.index[hist.index < e_date]
                    if len(after) >= 1 and len(before) >= 1:
                        r1d = round((float(hist.loc[after[0], "Close"]) - float(hist.loc[before[-1], "Close"])) / float(hist.loc[before[-1], "Close"]) * 100, 2)
                        result["post_reaction_1d"] = r1d
                        if result["last_beat"] and r1d < -3:
                            result["earnings_signals"].append(f"⚠️ 비트에도 -{abs(r1d)}% 하락 (sell the news)")
                            result["earnings_adj"] -= 5
                        elif not result["last_beat"] and r1d > 3:
                            result["earnings_signals"].append(f"🔥 미스에도 +{r1d}% 상승 (악재 선반영)")
                            result["earnings_adj"] += 5
            except Exception:
                pass
    except Exception as e:
        log.debug("[%s] 어닝 경량 체크 실패: %s", ticker, e)
    return result


def _quick_options_check(ticker: str) -> dict:
    result = {**_EMPTY_OPTIONS, "options_signals": []}
    if not _YF_AVAILABLE:
        return result
    try:
        t = yf.Ticker(ticker)
        exps = t.options
        if not exps:
            return result
        chain = t.option_chain(exps[0])
        total_call = chain.calls["volume"].fillna(0).sum() if chain.calls is not None and not chain.calls.empty else 0
        total_put  = chain.puts["volume"].fillna(0).sum()  if chain.puts is not None and not chain.puts.empty else 0
        if total_call > 0:
            pcr = round(total_put / total_call, 2)
            result["put_call_ratio"] = pcr
            if pcr > 1.5:
                result["options_signals"].append(f"🔥 P/C {pcr} → 극단적 공포 (역발상)")
                result["options_adj"] += 8
            elif pcr > 1.0:
                result["options_signals"].append(f"✅ P/C {pcr} → 약세 심리")
                result["options_adj"] += 3
            elif pcr < 0.4:
                result["options_signals"].append(f"⚠️ P/C {pcr} → 과도한 낙관")
                result["options_adj"] -= 5
    except Exception as e:
        log.debug("[%s] 옵션 경량 체크 실패: %s", ticker, e)
    return result


def _get_earnings_and_options(ticker: str) -> tuple:
    try:
        eq = _quick_earnings_check(ticker)
    except Exception:
        eq = {**_EMPTY_EARNINGS, "earnings_signals": []}
    try:
        oq = _quick_options_check(ticker)
    except Exception:
        oq = {**_EMPTY_OPTIONS, "options_signals": []}
    return eq, oq


# ═══════════════════════════════════════════════════════════════════
# 기술 지표 계산
# ═══════════════════════════════════════════════════════════════════

_REQUIRED_COLS = ["Close", "High", "Low", "Volume"]


def compute_indicators(ticker: str) -> dict | None:
    df = fetch_ohlcv(ticker)
    if df is None:
        return None
    missing = [c for c in _REQUIRED_COLS if c not in df.columns]
    if missing:
        log.warning("[%s] 컬럼 누락: %s – 스킵", ticker, missing)
        return None
    try:
        close  = pd.Series(df["Close"].values.flatten(),  index=df.index, dtype=float)
        high   = pd.Series(df["High"].values.flatten(),   index=df.index, dtype=float)
        low    = pd.Series(df["Low"].values.flatten(),    index=df.index, dtype=float)
        volume = pd.Series(df["Volume"].values.flatten(), index=df.index, dtype=float)

        price     = round(safe_float(close.iloc[-1]), 2)
        change_1d = 0.0
        if len(close) >= 2:
            prev = safe_float(close.iloc[-2])
            if prev > 0:
                change_1d = round((price - prev) / prev * 100, 2)
        result = {"price": price, "change_1d": change_1d}

        # RSI (14)
        if len(close) >= 15:
            delta    = close.diff()
            gain     = delta.clip(lower=0)
            loss     = (-delta).clip(lower=0)
            avg_gain = gain.ewm(alpha=1/14, adjust=False).mean()
            avg_loss = loss.ewm(alpha=1/14, adjust=False).mean()
            lg, ll   = safe_float(avg_gain.iloc[-1]), safe_float(avg_loss.iloc[-1])
            if lg == 0 and ll == 0:   rsi = 50.0
            elif ll == 0:             rsi = 100.0
            elif lg == 0:             rsi = 0.0
            else:                     rsi = round(100 - 100 / (1 + lg / ll), 2)
            result["rsi"] = rsi
        else:
            result["rsi"] = 50.0

        # MACD (12/26/9)
        if len(close) >= 35:
            ema12 = close.ewm(span=12, adjust=False).mean()
            ema26 = close.ewm(span=26, adjust=False).mean()
            macd_l = ema12 - ema26
            sig_l  = macd_l.ewm(span=9, adjust=False).mean()
            result["macd"]           = round(safe_float(macd_l.iloc[-1]), 4)
            result["macd_signal"]    = round(safe_float(sig_l.iloc[-1]),  4)
            result["macd_histogram"] = round(safe_float(macd_l.iloc[-1]) - safe_float(sig_l.iloc[-1]), 4)
        else:
            result["macd"] = result["macd_signal"] = result["macd_histogram"] = 0.0

        # 이동평균 (20/50/200)
        for p in [20, 50, 200]:
            result[f"ma{p}"] = round(safe_float(close.rolling(p).mean().iloc[-1]), 2) if len(close) >= p else price

        if result["ma20"] > result["ma50"] > result["ma200"]:
            result["ma_trend"] = MA_TREND_KO["bullish"]; result["ma_trend_raw"] = "bullish"
        else:
            result["ma_trend"] = MA_TREND_KO["bearish"]; result["ma_trend_raw"] = "bearish"

        result["golden_cross"] = result["dead_cross"] = False
        if len(close) >= 50:
            ma20_s, ma50_s = close.rolling(20).mean(), close.rolling(50).mean()
            if len(ma20_s) >= 2:
                p20, c20 = safe_float(ma20_s.iloc[-2]), safe_float(ma20_s.iloc[-1])
                p50, c50 = safe_float(ma50_s.iloc[-2]), safe_float(ma50_s.iloc[-1])
                if p20 <= p50 and c20 > c50: result["golden_cross"] = True
                if p20 >= p50 and c20 < c50: result["dead_cross"]   = True

        # Stochastic %K/%D
        if len(close) >= 14:
            low14  = low.rolling(14).min(); high14 = high.rolling(14).max()
            denom  = (high14 - low14).replace(0, np.nan)
            raw_k  = (close - low14) / denom * 100
            result["stoch_k"] = round(safe_float(raw_k.iloc[-1], 50.0), 2)
            result["stoch_d"] = round(safe_float(raw_k.rolling(3).mean().iloc[-1], 50.0), 2)
        else:
            result["stoch_k"] = result["stoch_d"] = 50.0

        # Ichimoku Cloud
        if len(close) >= 52:
            tenkan = (high.rolling(9).max() + low.rolling(9).min()) / 2
            kijun  = (high.rolling(26).max() + low.rolling(26).min()) / 2
            span_a = ((tenkan + kijun) / 2).shift(26)
            span_b = ((high.rolling(52).max() + low.rolling(52).min()) / 2).shift(26)
            la, lb = safe_float(span_a.iloc[-1], price), safe_float(span_b.iloc[-1], price)
            ct, cb = max(la, lb), min(la, lb)
            result["cloud_top"] = round(ct, 2); result["cloud_bottom"] = round(cb, 2)
            if price > ct:   cr = "above"
            elif price < cb: cr = "below"
            else:            cr = "inside"
            result["cloud_status"] = CLOUD_STATUS_KO[cr]; result["cloud_status_raw"] = cr
        else:
            result["cloud_top"] = result["cloud_bottom"] = price
            result["cloud_status"] = CLOUD_STATUS_KO["inside"]; result["cloud_status_raw"] = "inside"

        # VWAP (20D)
        if len(close) >= 20:
            tp      = (high + low + close) / 3
            vol_sum = volume.rolling(20).sum().replace(0, np.nan)
            result["vwap"] = round(safe_float((tp * volume).rolling(20).sum().div(vol_sum).iloc[-1], price), 2)
        else:
            result["vwap"] = price

        # ATR (14)
        if len(close) >= 15:
            tr  = pd.concat([high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()], axis=1).max(axis=1)
            atr = max(safe_float(tr.rolling(14).mean().iloc[-1], price * 0.01), price * 0.01)
        else:
            atr = price * 0.02
        result["atr"]       = round(atr, 2)
        result["target_1"]  = round(price + atr * 1.5, 2)
        result["target_2"]  = round(price + atr * 3.0, 2)
        result["stop_loss"] = round(price - atr * 1.5, 2)

        # 거래량 비율
        if len(volume) >= 21:
            avg_vol = safe_float(volume.rolling(20).mean().iloc[-1], 1)
            result["volume_ratio"] = round(safe_float(volume.iloc[-1]) / avg_vol, 2) if avg_vol > 0 else 1.0
        else:
            result["volume_ratio"] = 1.0

        # ── 선행 신호용 추가 지표 ──
        # 볼린저 밴드
        if len(close) >= 20:
            ma20_bb = close.rolling(20).mean(); std20 = close.rolling(20).std()
            result["bb_upper"] = round(safe_float(ma20_bb.iloc[-1] + 2*std20.iloc[-1]), 2)
            result["bb_lower"] = round(safe_float(ma20_bb.iloc[-1] - 2*std20.iloc[-1]), 2)
            bb_w = (4 * std20 / ma20_bb * 100).dropna()
            result["bb_width"] = round(safe_float(bb_w.iloc[-1]), 2) if len(bb_w) > 0 else 0
            if len(bb_w) >= 120:
                result["bb_width_percentile"] = round((bb_w.tail(120) < safe_float(bb_w.iloc[-1])).sum() / 120 * 100, 1)
            else:
                result["bb_width_percentile"] = 50.0
        else:
            result["bb_upper"] = result["bb_lower"] = price
            result["bb_width"] = 0; result["bb_width_percentile"] = 50.0

        # ATR 백분위
        if len(close) >= 15:
            tr_s  = pd.concat([high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()], axis=1).max(axis=1)
            atr_s = tr_s.rolling(14).mean().dropna()
            if len(atr_s) >= 120:
                result["atr_percentile"] = round((atr_s.tail(120) < safe_float(atr_s.iloc[-1])).sum() / 120 * 100, 1)
            else:
                result["atr_percentile"] = 50.0
        else:
            result["atr_percentile"] = 50.0

        # MACD 히스토그램 전환
        result["macd_cross_up"] = result["macd_cross_down"] = result["macd_approaching_zero"] = False
        if len(close) >= 35:
            ema12_s = close.ewm(span=12, adjust=False).mean()
            ema26_s = close.ewm(span=26, adjust=False).mean()
            hist_s  = ema12_s - ema26_s - (ema12_s - ema26_s).ewm(span=9, adjust=False).mean()
            if len(hist_s) >= 3:
                h1, h2, h3 = safe_float(hist_s.iloc[-3]), safe_float(hist_s.iloc[-2]), safe_float(hist_s.iloc[-1])
                result["macd_cross_up"]         = (h2 <= 0 and h3 > 0) or (h1 < h2 < 0 and h3 > h2)
                result["macd_cross_down"]       = (h2 >= 0 and h3 < 0)
                result["macd_approaching_zero"] = (h3 < 0 and h3 > h2 and h2 > h1)

        # 골든크로스 임박
        result["golden_cross_approaching"] = False; result["ma50_ma200_gap"] = 0.0
        if len(close) >= 200:
            mv50  = safe_float(close.rolling(50).mean().iloc[-1])
            mv200 = safe_float(close.rolling(200).mean().iloc[-1])
            if mv200 > 0:
                gap = (mv50 - mv200) / mv200 * 100
                result["ma50_ma200_gap"] = round(gap, 2)
                if -3.0 < gap < 0:
                    result["golden_cross_approaching"] = True

        return result
    except Exception as e:
        log.error("지표 계산 오류 [%s]: %s", ticker, e, exc_info=True)
        return None


# ═══════════════════════════════════════════════════════════════════
# 모멘텀 점수
# ═══════════════════════════════════════════════════════════════════

def compute_score_and_status(ind: dict, fv: dict, ticker: str = "") -> dict:
    raw = 0; signals = []
    rsi = ind.get("rsi", 50)
    if rsi < 30:     raw += 20; signals.append("✅ RSI 과매도")
    elif rsi < 40:   raw += 10; signals.append("✅ RSI 약세 반등 구간")
    elif rsi > 80:   raw -= 20; signals.append("⚠️ RSI 과열")
    elif rsi > 70:   raw -= 10; signals.append("⚠️ RSI 고열 구간")

    atr = ind.get("atr", 1); mn = ind.get("macd", 0) / atr if atr > 0 else 0
    if mn > 0.5:     raw += 20; signals.append("✅ MACD 강한 상승")
    elif mn > 0:     raw += 10; signals.append("✅ MACD 약한 상승")
    elif mn > -0.5:  raw -= 5;  signals.append("⚠️ MACD 약한 하락")
    else:            raw -= 15; signals.append("⚠️ MACD 하락")

    price = ind.get("price", 0); ma20 = ind.get("ma20", price)
    if price > 0 and ma20 > 0:
        if price > ma20: raw += 15; signals.append("✅ 가격 > MA20")
        else:            raw -= 10; signals.append("⚠️ 가격 < MA20")

    if ind.get("ma_trend_raw") == "bullish": raw += 15; signals.append("✅ MA 정배열")
    else:                                    raw -= 10; signals.append("⚠️ MA 역배열")

    if ind.get("golden_cross"): raw += 20; signals.append("✅ 골든크로스")
    if ind.get("dead_cross"):   raw -= 10; signals.append("⚠️ 데드크로스")

    cr = ind.get("cloud_status_raw", "inside")
    if cr == "above":   raw += 15; signals.append("✅ 구름 위")
    elif cr == "below": raw -= 10; signals.append("⚠️ 구름 아래")
    else:               signals.append("⏳ 구름 안")

    sk = ind.get("stoch_k", 50)
    if sk < 20:   raw += 10; signals.append("✅ Stoch 과매도")
    elif sk > 80: raw -= 5;  signals.append("⚠️ Stoch 과열")

    vwap = ind.get("vwap", price)
    if price > 0 and vwap > 0 and price > vwap: raw += 10; signals.append("✅ 가격 > VWAP")

    vr = ind.get("volume_ratio", 1.0)
    if vr >= 2.0:   raw += 10; signals.append(f"✅ 거래량 급증 ({vr}x)")
    elif vr >= 1.5: raw += 5;  signals.append(f"✅ 거래량 증가 ({vr}x)")

    fc = fv.get("finviz_change", 0)
    if fc >= 5: raw += 5; signals.append(f"✅ Finviz +{fc}%")

    # ── 어닝/옵션 단기 반영 ──
    if ticker:
        eq, oq = _get_earnings_and_options(ticker)
        raw += eq["earnings_adj"]
        raw += oq["options_adj"]
        signals.extend(eq["earnings_signals"])
        signals.extend(oq["options_signals"])
        ind["_earnings"] = eq
        ind["_options"] = oq

    score = normalize_score(raw)
    if score >= 65:   entry = "🟢"; ek = "green"
    elif score >= 45: entry = "⏳"; ek = "wait"
    else:             entry = "❌"; ek = "stop"

    return {"score": score, "raw_score": raw, "entry": entry, "entry_key": ek, "signals": signals}


# ═══════════════════════════════════════════════════════════════════
# 선행 신호 점수
# ═══════════════════════════════════════════════════════════════════

def compute_presignal_score(ind: dict, ticker: str = "") -> dict:
    raw = 0; signals = []; price = ind.get("price", 0)

    sq = (ind.get("bb_width_percentile", 50) + ind.get("atr_percentile", 50)) / 2
    if sq <= 10:   raw += 25; signals.append("🔥 극도의 변동성 수축 (폭발 임박)")
    elif sq <= 20: raw += 20; signals.append("🔥 강한 변동성 수축")
    elif sq <= 35: raw += 12; signals.append("✅ 변동성 수축 진행 중")

    rsi = ind.get("rsi", 50)
    if 30 <= rsi <= 40:   raw += 20; signals.append("✅ RSI 과매도 반등 구간 (30-40)")
    elif 25 <= rsi < 30:  raw += 15; signals.append("✅ RSI 깊은 과매도 (반등 대기)")
    elif 40 < rsi <= 45:  raw += 8;  signals.append("✅ RSI 약세 탈출 중 (40-45)")

    if ind.get("macd_cross_up"):           raw += 20; signals.append("🔥 MACD 히스토그램 음→양 전환")
    elif ind.get("macd_approaching_zero"): raw += 12; signals.append("✅ MACD 제로라인 돌파 임박")

    if ind.get("golden_cross"):
        raw += 15; signals.append("🔥 골든크로스 발생!")
    elif ind.get("golden_cross_approaching"):
        raw += 12; signals.append(f"✅ 골든크로스 임박 (갭 {ind.get('ma50_ma200_gap',0)}%)")

    vr = ind.get("volume_ratio", 1.0); chg = abs(ind.get("change_1d", 0))
    if vr >= 2.0 and chg < 2.0:   raw += 15; signals.append(f"🔥 거래량 급증({vr}x) + 가격 소폭 → 에너지 축적")
    elif vr >= 1.5 and chg < 1.5: raw += 8;  signals.append(f"✅ 거래량 증가({vr}x) + 가격 미반응")

    sk, sd = ind.get("stoch_k", 50), ind.get("stoch_d", 50)
    if 20 < sk <= 30 and sk > sd: raw += 10; signals.append("✅ Stoch 과매도 탈출 중")
    elif sk <= 20:                raw += 5;  signals.append("⏳ Stoch 과매도 (반등 미확인)")

    bbl = ind.get("bb_lower", 0)
    if bbl > 0 and price > 0:
        dist = (price - bbl) / price * 100
        if 0 < dist <= 1.5: raw += 10; signals.append("✅ 볼린저 하단 근접 반등")
        elif dist <= 0:     raw += 5;  signals.append("⏳ 볼린저 하단 이탈")

    if ind.get("cloud_status_raw") == "inside":
        raw += 5; signals.append("⏳ 구름 안 진입 (전환 구간)")

    c1d = ind.get("change_1d", 0)
    if c1d > 5:      raw -= 15; signals.append("⚠️ 당일 5%+ 상승 (후행 위험)")
    elif c1d > 3:    raw -= 8;  signals.append("⚠️ 당일 3%+ 상승")
    if rsi > 70:     raw -= 15; signals.append("⚠️ RSI 과열 → 선행 부적합")
    elif rsi > 60:   raw -= 5;  signals.append("⚠️ RSI 중립 상단")

    # ── 어닝/옵션 선행 반영 ──
    if ticker:
        eq, oq = _get_earnings_and_options(ticker)
        raw += eq["earnings_adj"]
        raw += oq["options_adj"]
        signals.extend(eq["earnings_signals"])
        signals.extend(oq["options_signals"])
        ind["_earnings"] = eq
        ind["_options"] = oq

    score = max(0, min(100, raw))
    if score >= 60:   g = "🔥 강력"; gk = "strong"
    elif score >= 40: g = "✅ 관심"; gk = "watch"
    elif score >= 20: g = "⏳ 대기"; gk = "wait"
    else:             g = "⬜ 약함"; gk = "weak"

    return {"presignal_score": score, "presignal_raw": raw,
            "presignal_grade": g, "grade_key": gk, "presignal_signals": signals}


# ═══════════════════════════════════════════════════════════════════
# 확신 점수
# ═══════════════════════════════════════════════════════════════════

def _compute_conviction_score(ind: dict) -> dict:
    filters_hit = 0; raw = 0; signals = []; price = ind.get("price", 0)

    bb_pct = ind.get("bb_width_percentile", 50); atr_pct = ind.get("atr_percentile", 50)
    squeeze_avg = (bb_pct + atr_pct) / 2
    if squeeze_avg <= 15:
        raw += 18; filters_hit += 1; signals.append("🔥 TTM Squeeze: 극도의 변동성 수축 (폭발 임박)")
    elif squeeze_avg <= 25:
        raw += 12; filters_hit += 1; signals.append("✅ TTM Squeeze: 변동성 수축 진행 중")
    elif squeeze_avg <= 35:
        raw += 6; signals.append("⏳ TTM Squeeze: 약한 수축")

    rsi = ind.get("rsi", 50)
    if 30 <= rsi <= 40:
        raw += 16; filters_hit += 1; signals.append("🔥 RSI 바닥 반등 (30-40)")
    elif 25 <= rsi < 30:
        raw += 12; filters_hit += 1; signals.append("✅ RSI 깊은 과매도 (반등 대기)")
    elif 40 < rsi <= 45:
        raw += 6; signals.append("⏳ RSI 약세 탈출 중")

    if ind.get("macd_cross_up"):
        raw += 16; filters_hit += 1; signals.append("🔥 MACD 히스토그램 음→양 전환")
    elif ind.get("macd_approaching_zero"):
        raw += 10; filters_hit += 1; signals.append("✅ MACD 제로라인 돌파 임박")

    if ind.get("golden_cross"):
        raw += 16; filters_hit += 1; signals.append("🔥 골든크로스 발생!")
    elif ind.get("golden_cross_approaching"):
        gap = ind.get("ma50_ma200_gap", 0)
        raw += 12; filters_hit += 1; signals.append(f"✅ 골든크로스 임박 (MA 갭 {gap}%)")

    vr = ind.get("volume_ratio", 1.0); chg = abs(ind.get("change_1d", 0))
    if vr >= 2.0 and chg < 2.0:
        raw += 14; filters_hit += 1; signals.append(f"🔥 스마트머니 축적: 거래량 {vr}x + 가격 미반응")
    elif vr >= 1.5 and chg < 1.5:
        raw += 8; filters_hit += 1; signals.append(f"✅ 거래량 증가({vr}x) + 가격 안정")

    sk = ind.get("stoch_k", 50); sd = ind.get("stoch_d", 50)
    if 20 < sk <= 35 and sk > sd:
        raw += 12; filters_hit += 1; signals.append("✅ Stoch 과매도 탈출 (%K > %D)")
    elif sk <= 20:
        raw += 5; signals.append("⏳ Stoch 과매도 (반등 미확인)")

    bbl = ind.get("bb_lower", 0)
    if bbl > 0 and price > 0:
        dist = (price - bbl) / price * 100
        if 0 < dist <= 1.5:
            raw += 12; filters_hit += 1; signals.append("🔥 볼린저 하단 근접 반등")
        elif dist <= 0:
            raw += 6; signals.append("⏳ 볼린저 하단 이탈 (바닥 탐색)")
        elif dist <= 3.0:
            raw += 4; signals.append("⏳ 볼린저 하단 접근 중")

    if filters_hit >= 4:
        bonus = 1.6; signals.insert(0, f"⭐ {filters_hit}개 필터 동시 충족 → 1.6x 보너스")
    elif filters_hit >= 3:
        bonus = 1.4; signals.insert(0, f"🔥 {filters_hit}개 필터 동시 충족 → 1.4x 보너스")
    elif filters_hit >= 2:
        bonus = 1.2; signals.insert(0, f"✅ {filters_hit}개 필터 동시 충족 → 1.2x 보너스")
    else:
        bonus = 1.0

    raw = raw * bonus

    c1d = ind.get("change_1d", 0)
    if c1d > 5:   raw -= 20; signals.append("⚠️ 당일 5%+ 상승 (이미 움직임 → 후행 위험)")
    elif c1d > 3: raw -= 10; signals.append("⚠️ 당일 3%+ 상승")
    if rsi > 70:   raw -= 20; signals.append("⚠️ RSI 과열 (>70) → 확신 부적합")
    elif rsi > 60: raw -= 8;  signals.append("⚠️ RSI 중립 상단 (>60)")
    if ind.get("dead_cross"):            raw -= 15; signals.append("⚠️ 데드크로스 발생 → 하방 압력")
    if ind.get("cloud_status_raw") == "below": raw -= 5; signals.append("⚠️ 구름 아래 위치")

    score = max(0, min(100, int(round(raw))))
    if score >= 70:   grade = "⭐ 확신"; grade_key = "conviction"
    elif score >= 50: grade = "🔥 유력"; grade_key = "strong"
    elif score >= 30: grade = "✅ 관심"; grade_key = "watch"
    else:             grade = "⬜ 미달"; grade_key = "weak"

    return {"conviction_score": score, "conviction_raw": round(raw, 1), "conviction_grade": grade,
            "grade_key": grade_key, "filters_hit": filters_hit, "overlap_bonus": bonus,
            "conviction_signals": signals}


# ═══════════════════════════════════════════════════════════════════
# 개별 분석 함수
# ═══════════════════════════════════════════════════════════════════

def analyze_ticker(fv: dict) -> dict | None:
    ticker = fv["ticker"]
    try:
        ind = compute_indicators(ticker)
        if ind is None:
            log.warning("[%s] 지표 계산 실패", ticker); return None
        scoring = compute_score_and_status(ind, fv, ticker)
        return {
            "ticker": ticker, "company": fv.get("company", ticker),
            "price": ind["price"], "change_1d": ind["change_1d"],
            "finviz_change": fv.get("finviz_change", 0),
            **{k: ind.get(k, d) for k, d in [
                ("rsi",50.0),("macd",0.0),("macd_signal",0.0),("macd_histogram",0.0),
                ("ma20",0),("ma50",0),("ma200",0),("ma_trend",""),
                ("golden_cross",False),("dead_cross",False),
                ("stoch_k",50.0),("stoch_d",50.0),
                ("cloud_status",""),("cloud_top",0),("cloud_bottom",0),
                ("vwap",0),("atr",0),("target_1",0),("target_2",0),("stop_loss",0),
                ("volume_ratio",1.0),
            ]},
            "earnings_near": ind.get("_earnings", {}).get("earnings_near", False),
            "days_to_earnings": ind.get("_earnings", {}).get("days_to_earnings"),
            "last_surprise_pct": ind.get("_earnings", {}).get("last_surprise_pct"),
            "put_call_ratio": ind.get("_options", {}).get("put_call_ratio"),
            **scoring,
        }
    except Exception as e:
        log.error("[%s] 분석 오류: %s", ticker, e, exc_info=True); return None


def analyze_ticker_presignal(ticker: str) -> dict | None:
    try:
        ind = compute_indicators(ticker)
        if ind is None:
            return None
        scoring = compute_presignal_score(ind, ticker)
        return {
            "ticker": ticker, "company": ticker,
            "price": ind["price"], "change_1d": ind["change_1d"],
            **{k: ind.get(k, d) for k, d in [
                ("rsi",50.0),("macd_histogram",0),("stoch_k",50.0),("stoch_d",50.0),
                ("volume_ratio",1.0),("bb_width",0),("bb_width_percentile",50),
                ("atr",0),("atr_percentile",50),("ma_trend",""),("ma_trend_raw","bearish"),
                ("golden_cross",False),("golden_cross_approaching",False),
                ("ma50_ma200_gap",0),("macd_cross_up",False),("macd_approaching_zero",False),
                ("cloud_status",""),("cloud_status_raw","inside"),
                ("bb_lower",0),("target_1",0),("target_2",0),("stop_loss",0),
            ]},
            "earnings_near": ind.get("_earnings", {}).get("earnings_near", False),
            "days_to_earnings": ind.get("_earnings", {}).get("days_to_earnings"),
            "last_surprise_pct": ind.get("_earnings", {}).get("last_surprise_pct"),
            "put_call_ratio": ind.get("_options", {}).get("put_call_ratio"),
            **scoring,
        }
    except Exception as e:
        log.error("[%s] 선행 분석 오류: %s", ticker, e, exc_info=True); return None


def analyze_ticker_conviction(ticker: str) -> dict | None:
    try:
        ind = compute_indicators(ticker)
        if ind is None:
            return None
        scoring = _compute_conviction_score(ind)
        return {
            "ticker": ticker, "company": ticker,
            "price": ind["price"], "change_1d": ind["change_1d"],
            **{k: ind.get(k, d) for k, d in [
                ("rsi",50.0),("macd_histogram",0),("stoch_k",50.0),("stoch_d",50.0),
                ("volume_ratio",1.0),("bb_width",0),("bb_width_percentile",50),
                ("atr",0),("atr_percentile",50),("ma_trend",""),("ma_trend_raw","bearish"),
                ("golden_cross",False),("golden_cross_approaching",False),
                ("dead_cross",False),("ma50_ma200_gap",0),
                ("macd_cross_up",False),("macd_approaching_zero",False),
                ("cloud_status",""),("cloud_status_raw","inside"),
                ("bb_lower",0),("target_1",0),("target_2",0),("stop_loss",0),
            ]},
            **scoring,
        }
    except Exception as e:
        log.error("[%s] 확신 분석 오류: %s", ticker, e, exc_info=True); return None


# ═══════════════════════════════════════════════════════════════════
# 선행 신호 — batch 1차 필터링
# ═══════════════════════════════════════════════════════════════════

def _get_presignal_candidates(symbols: list) -> list:
    if not _YF_AVAILABLE or not symbols:
        return symbols[:40]
    try:
        kw = {"period": "3mo", "interval": "1d", "auto_adjust": True, "progress": False}
        if _YF_SUPPORTS_MLI:
            kw["multi_level_index"] = False
        raw = yf.download(symbols, **kw)
        if raw is None or raw.empty:
            return symbols[:40]
        if isinstance(raw.columns, pd.MultiIndex):
            close  = raw["Close"].copy()  if "Close"  in raw.columns.get_level_values(0) else None
            volume = raw["Volume"].copy() if "Volume" in raw.columns.get_level_values(0) else None
        else:
            close  = raw[["Close"]]  if "Close"  in raw.columns else None
            volume = raw[["Volume"]] if "Volume" in raw.columns else None
        if close is None or isinstance(close, pd.Series) or len(close) < 14:
            return symbols[:40]
        last = close.iloc[-1]; prev = close.iloc[-2]
        chg = ((last - prev) / prev * 100).dropna()
        if volume is not None and not isinstance(volume, pd.Series) and len(volume) >= 20:
            vol_last  = volume.iloc[-1]
            vol_avg   = volume.tail(20).mean()
            vol_ratio = (vol_last / vol_avg.replace(0, np.nan)).dropna()
        else:
            vol_ratio = pd.Series(dtype=float)
        candidates = []
        for sym in chg.index:
            c = abs(float(chg[sym])) if sym in chg.index else 99
            if c > 5:
                continue
            vr = float(vol_ratio[sym]) if sym in vol_ratio.index else 1.0
            if np.isnan(vr):
                vr = 1.0
            candidates.append((sym, c, vr))
        candidates.sort(key=lambda x: x[2], reverse=True)
        result = [str(c[0]) for c in candidates[:40]]
        log.info("선행 신호 1차 필터: %d → %d 종목", len(symbols), len(result))
        return result if result else symbols[:40]
    except Exception as e:
        log.warning("선행 신호 1차 필터 오류: %s", e)
        return symbols[:40]


# ═══════════════════════════════════════════════════════════════════
# 메인: 모멘텀 분석
# ═══════════════════════════════════════════════════════════════════

def analyze() -> dict:
    analyzed_at = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S (KST)")
    log.info("═══ 모멘텀 분석 시작: %s ═══", analyzed_at)

    candidates = fetch_finviz_sp500_gainers()
    if len(candidates) < MAX_TICKERS:
        log.info("Finviz %d개 → yfinance batch로 보충", len(candidates))
        extra = _fetch_yfinance_batch_fallback()
        existing = {c["ticker"] for c in candidates}
        for e in extra:
            if e["ticker"] not in existing:
                candidates.append(e); existing.add(e["ticker"])
        candidates = candidates[:MAX_TICKERS]
    if not candidates:
        log.info("yfinance 실패 → Finnhub 폴백")
        candidates = _fetch_finnhub_sp500_fallback()
    if not candidates:
        return {"results": [], "analyzed_at": analyzed_at, "green": 0, "wait": 0, "stop": 0, "error": "데이터 소스 없음"}

    log.info("후보 종목: %d개", len(candidates))
    results = []
    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(candidates))) as ex:
        fmap = {ex.submit(analyze_ticker, fv): fv["ticker"] for fv in candidates}
        for f in as_completed(fmap):
            try:
                r = f.result()
                if r: results.append(r)
            except Exception as e:
                log.error("[%s] future 오류: %s", fmap[f], e)

    if not results:
        return {"results": [], "analyzed_at": analyzed_at, "green": 0, "wait": 0, "stop": 0, "error": "전체 분석 실패"}

    results.sort(key=lambda x: x["score"], reverse=True)
    gc = sum(1 for r in results if r["entry_key"] == "green")
    wc = sum(1 for r in results if r["entry_key"] == "wait")
    sc = sum(1 for r in results if r["entry_key"] == "stop")

    save_data = {"analyzed_at": analyzed_at, "total": len(results), "green": gc, "wait": wc, "stop": sc, "results": results}
    ts = datetime.now(KST).strftime(HISTORY_TS_FMT)
    try:
        with open(HISTORY_DIR / f"{ts}.json", "w", encoding="utf-8") as f:
            json.dump(save_data, f, ensure_ascii=False, indent=2, default=str)
        log.info("히스토리 저장: history/%s.json", ts)
    except Exception as e:
        log.error("히스토리 저장 오류: %s", e)

    top10 = results[:10]
    lines = ["<b>📊 S&amp;P 500 모멘텀 분석</b>",
             f"🕐 {_escape_html(analyzed_at)}",
             f"총 {len(results)}종목 | 🟢{gc} ⏳{wc} ❌{sc}", ""]
    for i, r in enumerate(top10, 1):
        lines.append(f"{i}. <b>{_escape_html(r['ticker'])}</b> {_escape_html(r['entry'])} "
                     f"점수:{r['score']} | ${r['price']} ({r['change_1d']:+.1f}%)")
    send_telegram("\n".join(lines))

    log.info("═══ 모멘텀 분석 완료 ═══")
    log.info("총 %d종목 | 🟢 %d | ⏳ %d | ❌ %d", len(results), gc, wc, sc)
    return save_data


# ═══════════════════════════════════════════════════════════════════
# 메인: 선행 신호 분석
# ═══════════════════════════════════════════════════════════════════

def analyze_presignal(universe: str = "sp500") -> dict:
    uni      = UNIVERSE_MAP.get(universe, UNIVERSE_MAP["sp500"])
    symbols  = uni["symbols"]
    uni_name = uni["name"]
    analyzed_at = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S (KST)")
    log.info("═══ 선행 신호 스캔 시작 [%s]: %s ═══", uni_name, analyzed_at)

    scan_targets = _get_presignal_candidates(symbols)
    log.info("선행 신호 정밀 스캔: %d 종목", len(scan_targets))

    results = []
    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(scan_targets))) as ex:
        fmap = {ex.submit(analyze_ticker_presignal, sym): sym for sym in scan_targets}
        done = 0
        for f in as_completed(fmap):
            done += 1
            if done % 10 == 0:
                log.info("선행 스캔 진행: %d/%d", done, len(scan_targets))
            try:
                r = f.result()
                if r: results.append(r)
            except Exception as e:
                log.error("[%s] 선행 future 오류: %s", fmap[f], e)

    if not results:
        return {"results": [], "analyzed_at": analyzed_at, "universe": uni_name,
                "strong": 0, "watch": 0, "wait": 0, "weak": 0,
                "scanned_total": len(symbols), "error": "전체 분석 실패"}

    results.sort(key=lambda x: x["presignal_score"], reverse=True)
    top_results = results[:PRESIGNAL_MAX_RESULTS]

    stc = sum(1 for r in results if r["grade_key"] == "strong")
    wac = sum(1 for r in results if r["grade_key"] == "watch")
    wtc = sum(1 for r in results if r["grade_key"] == "wait")
    wkc = sum(1 for r in results if r["grade_key"] == "weak")

    save_data = {"analyzed_at": analyzed_at, "scan_type": "presignal", "universe": uni_name,
                 "scanned_total": len(results), "strong": stc, "watch": wac, "wait": wtc, "weak": wkc,
                 "results": top_results}
    ts = datetime.now(KST).strftime(HISTORY_TS_FMT)
    try:
        with open(PRESIGNAL_DIR / f"{ts}.json", "w", encoding="utf-8") as f:
            json.dump(save_data, f, ensure_ascii=False, indent=2, default=str)
        log.info("선행 신호 저장: presignal/%s.json", ts)
    except Exception as e:
        log.error("선행 신호 저장 오류: %s", e)

    top5 = top_results[:5]
    lines = [f"<b>🔮 선행 신호 스캔 [{_escape_html(uni_name)}]</b>",
             f"🕐 {_escape_html(analyzed_at)}",
             f"스캔 {len(results)}종목 | 🔥{stc} ✅{wac} ⏳{wtc}", ""]
    for i, r in enumerate(top5, 1):
        sigs = " | ".join(r.get("presignal_signals", [])[:3])
        lines.append(f"{i}. <b>{_escape_html(r['ticker'])}</b> {_escape_html(r['presignal_grade'])} "
                     f"점수:{r['presignal_score']} | ${r['price']} ({r['change_1d']:+.1f}%)\n   → {_escape_html(sigs)}")
    send_telegram("\n".join(lines))

    log.info("═══ 선행 신호 스캔 완료 [%s] ═══", uni_name)
    log.info("스캔 %d종목 | 🔥 %d | ✅ %d | ⏳ %d | ⬜ %d", len(results), stc, wac, wtc, wkc)
    return save_data


# ═══════════════════════════════════════════════════════════════════
# 메인: 확신 종목 분석
# ═══════════════════════════════════════════════════════════════════

def analyze_conviction(universe: str = "sp500+sox") -> dict:
    uni      = UNIVERSE_MAP.get(universe, UNIVERSE_MAP["sp500+sox"])
    symbols  = uni["symbols"]
    uni_name = uni["name"]
    analyzed_at = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S (KST)")
    log.info("═══ 확신 스캐너 시작 [%s]: %s ═══", uni_name, analyzed_at)

    scan_targets = _get_presignal_candidates(symbols)
    log.info("확신 스캐너 정밀 스캔: %d 종목", len(scan_targets))

    results = []
    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(scan_targets))) as ex:
        fmap = {ex.submit(analyze_ticker_conviction, sym): sym for sym in scan_targets}
        done = 0
        for f in as_completed(fmap):
            done += 1
            if done % 10 == 0:
                log.info("확신 스캔 진행: %d/%d", done, len(scan_targets))
            try:
                r = f.result()
                if r: results.append(r)
            except Exception as e:
                log.error("[%s] 확신 future 오류: %s", fmap[f], e)

    if not results:
        return {"results": [], "analyzed_at": analyzed_at, "universe": uni_name,
                "conviction": 0, "strong": 0, "watch": 0, "weak": 0,
                "scanned_total": len(symbols), "scan_type": "conviction", "error": "전체 분석 실패"}

    results.sort(key=lambda x: x["conviction_score"], reverse=True)
    top_results = results[:CONVICTION_MAX_RESULTS]

    cc = sum(1 for r in results if r["grade_key"] == "conviction")
    sc = sum(1 for r in results if r["grade_key"] == "strong")
    wc = sum(1 for r in results if r["grade_key"] == "watch")
    wk = sum(1 for r in results if r["grade_key"] == "weak")

    save_data = {"analyzed_at": analyzed_at, "scan_type": "conviction", "universe": uni_name,
                 "scanned_total": len(results), "conviction": cc, "strong": sc, "watch": wc, "weak": wk,
                 "results": top_results}
    ts = datetime.now(KST).strftime(HISTORY_TS_FMT)
    try:
        with open(CONVICTION_DIR / f"{ts}.json", "w", encoding="utf-8") as f:
            json.dump(save_data, f, ensure_ascii=False, indent=2, default=str)
        log.info("확신 종목 저장: conviction/%s.json", ts)
    except Exception as e:
        log.error("확신 종목 저장 오류: %s", e)

    top5 = top_results[:5]
    lines = [f"<b>🎯 확신 종목 스캔 [{_escape_html(uni_name)}]</b>",
             f"🕐 {_escape_html(analyzed_at)}",
             f"스캔 {len(results)}종목 | ⭐{cc} 🔥{sc} ✅{wc} ⬜{wk}", ""]
    for i, r in enumerate(top5, 1):
        sigs = " | ".join(r.get("conviction_signals", [])[:3])
        lines.append(f"{i}. <b>{_escape_html(r['ticker'])}</b> {_escape_html(r['conviction_grade'])} "
                     f"점수:{r['conviction_score']} (필터 {r['filters_hit']}개, {r['overlap_bonus']}x) "
                     f"| ${r['price']} ({r['change_1d']:+.1f}%)\n"
                     f"   → {_escape_html(sigs)}")
    send_telegram("\n".join(lines))

    log.info("═══ 확신 스캐너 완료 [%s] ═══", uni_name)
    log.info("스캔 %d종목 | ⭐ %d | 🔥 %d | ✅ %d | ⬜ %d", len(results), cc, sc, wc, wk)
    return save_data


# ═══════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    mode = sys.argv[1] if len(sys.argv) > 1 else "momentum"
    uni  = sys.argv[2] if len(sys.argv) > 2 else "sp500+sox"
    if mode == "presignal":
        analyze_presignal(uni)
    elif mode == "conviction":
        analyze_conviction(uni)
    else:
        analyze()
