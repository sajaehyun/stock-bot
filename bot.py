import os
import json
import time
import requests
import pandas as pd
import numpy as np
import yfinance as yf
from finvizfinance.screener.overview import Overview
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

# ─────────────────────────────────────────────
# 환경변수 및 상수
# ─────────────────────────────────────────────
TELEGRAM_TOKEN  = os.getenv("TELEGRAM_TOKEN")
CHAT_ID         = os.getenv("CHAT_ID")
AV_API_KEY      = os.getenv("AV_API_KEY", "TV93LGAM5I8HYMLX")
AV_BASE_URL     = "https://www.alphavantage.co/query"

# 무료 플랜: 분당 5회, 일 25회
# TOP_GAINERS 1회 + 종목당 1회 → 최대 15종목 (총 16회/일)
AV_DELAY_SEC    = 13      # 종목 간 딜레이 (분당 5회 안전하게 유지)
MAX_TICKERS     = 15      # 일 25회 제한 내에서 여유롭게

RAW_SCORE_MAX   =  120
RAW_SCORE_MIN   =  -50
RAW_SCORE_RANGE = RAW_SCORE_MAX - RAW_SCORE_MIN


# ─────────────────────────────────────────────
# 유틸리티
# ─────────────────────────────────────────────
def safe_float(val, default=0.0):
    """스칼라/Series/NaN/Inf/문자열 모두 안전하게 float 변환"""
    try:
        if hasattr(val, 'iloc'):
            val = val.iloc[-1]
        if isinstance(val, str):
            val = val.replace('%', '').replace(',', '').replace('$', '').strip()
        v = float(val)
        return default if (np.isnan(v) or np.isinf(v)) else v
    except Exception:
        return default


def normalize_score(raw: int) -> int:
    normalized = (raw - RAW_SCORE_MIN) / RAW_SCORE_RANGE * 100
    return round(max(0.0, min(100.0, normalized)))


def send_telegram(message: str):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        res = requests.post(
            url,
            json={"chat_id": CHAT_ID, "text": message, "parse_mode": "Markdown"},
            timeout=10
        )
        res.raise_for_status()
    except Exception as e:
        print(f"[Telegram] 전송 실패: {e}")


def av_request(params: dict, retry: int = 3) -> dict | None:
    """Alpha Vantage API 공통 요청 (rate limit 자동 재시도)"""
    params["apikey"] = AV_API_KEY
    for attempt in range(retry):
        try:
            res = requests.get(AV_BASE_URL, params=params, timeout=30)
            data = res.json()

            # API 제한 감지
            if "Note" in data or "Information" in data:
                msg = data.get("Note") or data.get("Information", "")
                print(f"[Alpha Vantage 제한] {msg[:100]}")
                print(f"[대기] 60초 후 재시도...")
                time.sleep(60)
                continue

            return data

        except Exception as e:
            print(f"[Alpha Vantage 요청 오류] {e} ({attempt+1}/{retry})")
            time.sleep(5 * (attempt + 1))

    return None


# ─────────────────────────────────────────────
# Fallback 데이터 수집 (Finviz / yfinance)
# ─────────────────────────────────────────────
def fetch_top_gainers_finviz() -> list[dict]:
    """Finviz 스크리너를 이용한 당일 상승 종목 수집 (Alpha Vantage 백업)"""
    print("[Finviz] 당일 상승 종목 수집 중 (Fallback)...")
    try:
        f_overview = Overview()
        f_overview.set_filter(filters_dict={'Signal': 'Top Gainers'})
        df = f_overview.screener_view()
        
        if df.empty:
            return []
            
        results = []
        for _, row in df.head(MAX_TICKERS).iterrows():
            ticker = str(row['Ticker']).strip()
            price  = safe_float(row['Price'])
            change = safe_float(row['Change'])
            volume = safe_float(row['Volume'])
            
            # 워런트/ETF 등 제외
            if not ticker or len(ticker) > 5 or any(c in ticker for c in ['.', '-', '+']):
                continue
            if price < 5:
                continue
                
            results.append({
                "ticker":  ticker,
                "company": ticker,
                "price":   price,
                "change":  change,
                "volume":  volume,
            })
        return results
    except Exception as e:
        print(f"[Finviz] 수집 실패: {e}")
        return []


def fetch_daily_data_yf(ticker: str) -> pd.DataFrame | None:
    """yfinance를 이용한 일별 데이터 수집 (Alpha Vantage 백업)"""
    print(f"[{ticker}] yfinance 데이터 수집 중 (Fallback)...")
    try:
        # 최근 6개월 데이터
        df = yf.download(ticker, period="6mo", interval="1d", progress=False)
        if df.empty:
            return None
            
        # MultiIndex 컬럼 처리 (yfinance 최신버전 호환)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
            
        df = df.rename(columns={
            "Open":   "Open",
            "High":   "High",
            "Low":    "Low",
            "Close":  "Close",
            "Volume": "Volume",
        })
        
        for col in ["Open", "High", "Low", "Close", "Volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna(subset=["Close"])
        
        if len(df) < 50:
            return None
            
        return df
    except Exception as e:
        print(f"[{ticker}] yfinance 수집 실패: {e}")
        return None


# ─────────────────────────────────────────────
# 1. 당일 상승 종목 수집
# ─────────────────────────────────────────────
def fetch_top_gainers() -> list[dict]:
    """Alpha Vantage 또는 Finviz에서 상승 종목 수집"""
    print("[API] 당일 상승 종목 수집 시작...")
    
    # 1. Alpha Vantage 시도
    results = []
    data = av_request({"function": "TOP_GAINERS_LOSERS"})
    if data:
        gainers = data.get("top_gainers", [])
        for item in gainers[:MAX_TICKERS]:
            ticker = str(item.get("ticker", "")).strip()
            price  = safe_float(item.get("price", 0))
            change = safe_float(item.get("change_percentage", "0"))
            volume = safe_float(item.get("volume", 0))
            
            if not ticker or len(ticker) > 5 or any(c in ticker for c in ['.', '-', '+']):
                continue
            if price < 5:
                continue
                
            results.append({
                "ticker":  ticker,
                "company": ticker,
                "price":   price,
                "change":  change,
                "volume":  volume,
            })
            
    # 2. 실패 시 Finviz 시도
    if not results:
        results = fetch_top_gainers_finviz()
        
    print(f"[API] 최종 {len(results)}개 종목 수집 완료")
    return results


# ─────────────────────────────────────────────
# 2. 일별 OHLCV 데이터 수집
# ─────────────────────────────────────────────
def fetch_daily_data(ticker: str) -> pd.DataFrame | None:
    """Alpha Vantage 또는 yfinance에서 일별 데이터 수집"""
    # 1. Alpha Vantage 시도
    data = av_request({
        "function":   "TIME_SERIES_DAILY",
        "symbol":     ticker,
        "outputsize": "full",
    })

    if data and "Time Series (Daily)" in data:
        ts = data.get("Time Series (Daily)")
        df = pd.DataFrame.from_dict(ts, orient="index")
        df.index = pd.to_datetime(df.index)
        df = df.sort_index()
        df = df.rename(columns={
            "1. open":   "Open",
            "2. high":   "High",
            "3. low":    "Low",
            "4. close":  "Close",
            "5. volume": "Volume",
        })
        for col in ["Open", "High", "Low", "Close", "Volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna(subset=["Close"])
        if len(df) >= 50:
            print(f"[{ticker}] Alpha Vantage 수신 완료 ({len(df)}행)")
            return df

    # 2. 실패 시 yfinance 시도
    return fetch_daily_data_yf(ticker)


# ─────────────────────────────────────────────
# 3. 기술적 지표 계산
# ─────────────────────────────────────────────
def compute_indicators(ticker: str) -> dict | None:
    df = fetch_daily_data(ticker)
    if df is None:
        return None

    try:
        close  = df["Close"]
        high   = df["High"]
        low    = df["Low"]
        volume = df["Volume"]

        n = len(df)
        curr_price = safe_float(close.iloc[-1])
        prev_price = safe_float(close.iloc[-2]) if n > 1 else curr_price
        change_1d  = ((curr_price - prev_price) / prev_price * 100) if prev_price != 0 else 0.0

        # RSI (14)
        delta = close.diff()
        gain  = delta.where(delta > 0, 0).ewm(alpha=1/14, adjust=False).mean()
        loss  = (-delta.where(delta < 0, 0)).ewm(alpha=1/14, adjust=False).mean()
        rs    = gain / loss.replace(0, np.nan)
        rsi   = safe_float((100 - (100 / (1 + rs))).iloc[-1])

        # MACD
        ema12     = close.ewm(span=12, adjust=False).mean()
        ema26     = close.ewm(span=26, adjust=False).mean()
        macd_line = ema12 - ema26
        signal    = macd_line.ewm(span=9, adjust=False).mean()
        curr_macd = safe_float((macd_line - signal).iloc[-1])

        # MA 20 / 50 / 200
        m20_val  = safe_float(close.rolling(min(20,  n)).mean().iloc[-1])
        m50_val  = safe_float(close.rolling(min(50,  n)).mean().iloc[-1])
        m200_val = safe_float(close.rolling(min(200, n)).mean().iloc[-1])

        if n >= 50 and m50_val > m200_val and m200_val > 0:
            ma_trend = "골튼크로스"
        elif n >= 50 and m50_val < m200_val and m200_val > 0:
            ma_trend = "데드크로스"
        else:
            ma_trend = "중립"

        # Stochastic (14, 3)
        low14   = low.rolling(14).min()
        high14  = high.rolling(14).max()
        stoch_k = 100 * (close - low14) / (high14 - low14).replace(0, np.nan)
        curr_stoch_d = safe_float(stoch_k.rolling(3).mean().iloc[-1])

        # Ichimoku
        tenkan = (high.rolling(9).max()  + low.rolling(9).min())  / 2
        kijun  = (high.rolling(26).max() + low.rolling(26).min()) / 2
        span_a = (tenkan + kijun) / 2
        span_b = (high.rolling(52).max() + low.rolling(52).min()) / 2
        sa = safe_float(span_a.iloc[-1])
        sb = safe_float(span_b.iloc[-1])
        is_above_cloud = curr_price > max(sa, sb) if (sa > 0 and sb > 0) else False
        is_below_cloud = curr_price < min(sa, sb) if (sa > 0 and sb > 0) else False

        # VWAP (20일 Rolling)
        typical = (high + low + close) / 3
        vwap_20 = (
            (typical * volume).rolling(20).sum()
            / volume.rolling(20).sum()
        )
        curr_vwap     = safe_float(vwap_20.iloc[-1])
        is_above_vwap = curr_price > curr_vwap
        vwap_gap_pct  = ((curr_price - curr_vwap) / curr_vwap * 100) if curr_vwap > 0 else 0.0

        # ATR (14)
        tr = pd.concat([
            high - low,
            (high - close.shift()).abs(),
            (low  - close.shift()).abs()
        ], axis=1).max(axis=1)
        curr_atr = max(safe_float(tr.rolling(14).mean().iloc[-1]), curr_price * 0.01)

        # 거래량 비율
        vol_avg   = safe_float(volume.rolling(20).mean().iloc[-1])
        vol_ratio = (safe_float(volume.iloc[-1]) / vol_avg * 100) if vol_avg > 0 else 100.0

        return {
            "price":          curr_price,
            "change_1d":      change_1d,
            "rsi":            rsi,
            "macd_histogram": curr_macd,
            "ma20":           m20_val,
            "ma50":           m50_val,
            "ma200":          m200_val,
            "ma_trend":       ma_trend,
            "stochastic_d":   curr_stoch_d,
            "is_above_cloud": is_above_cloud,
            "is_below_cloud": is_below_cloud,
            "vwap":           curr_vwap,
            "is_above_vwap":  is_above_vwap,
            "vwap_gap_pct":   round(vwap_gap_pct, 2),
            "vol_ratio":      vol_ratio,
            "target1":        round(curr_price + 1.5 * curr_atr, 2),
            "target2":        round(curr_price + 3.0 * curr_atr, 2),
            "stop_loss":      round(curr_price - 1.5 * curr_atr, 2),
        }

    except Exception as e:
        print(f"[{ticker}] 지표 계산 오류: {e}")
        return None


# ─────────────────────────────────────────────
# 4. 점수 및 진입 상태 계산
# ─────────────────────────────────────────────
def compute_score_and_status(ind: dict, fv: dict) -> tuple[int, list[str], str]:
    raw     = 0
    signals = []

    # 지표 기반 점수 합산
    if ind["rsi"] < 35:
        raw += 20; signals.append(f"RSI 과매도 {ind['rsi']:.1f}")
    elif ind["rsi"] <= 65:
        raw += 10; signals.append(f"RSI 적정 {ind['rsi']:.1f}")
    elif ind["rsi"] > 70:
        raw -= 10; signals.append(f"RSI 과매수 {ind['rsi']:.1f} ⚠️")

    if ind["macd_histogram"] > 0:
        raw += 15; signals.append("MACD 상방 ✅")

    if ind["price"] > ind["ma20"]:
        raw += 15; signals.append("MA20 상회 ✅")

    if ind["ma_trend"] == "골든크로스":
        raw += 15; signals.append("골든크로스 ✅")
    elif ind["ma_trend"] == "데드크로스":
        raw -= 10; signals.append("데드크로스 ⚠️")

    if ind["is_above_cloud"]:
        raw += 20; signals.append("구름대 상단 돌파 ✅")
    elif ind["is_below_cloud"]:
        raw -= 20; signals.append("구름대 하단 저항 ⚠️")

    if ind["stochastic_d"] < 20:
        raw += 10; signals.append(f"스토캐스틱 과매도 {ind['stochastic_d']:.1f}")
    elif ind["stochastic_d"] > 80:
        raw -= 10; signals.append(f"스토캐스틱 과매수 {ind['stochastic_d']:.1f} ⚠️")

    if ind["is_above_vwap"]:
        raw += 15; signals.append(f"VWAP 상회 +{ind['vwap_gap_pct']:.1f}% ✅")
    else:
        raw -= 10; signals.append(f"VWAP 하회 {ind['vwap_gap_pct']:.1f}% ⚠️")

    if ind["vol_ratio"] > 150:
        raw += 15; signals.append(f"거래량 급증 {ind['vol_ratio']:.0f}%")

    if fv.get("change", 0) > 3:
        raw += 10; signals.append(f"당일 강한 상승 +{fv['change']:.1f}% ✅")

    score = normalize_score(raw)
    
    if score >= 70:
        entry = "🟢 진입 가능"
    elif score >= 50:
        entry = "⏳ 대기 (관망)"
    else:
        entry = "❌ 회피 (위험)"
        
    return score, signals, entry


def analyze_ticker(fv: dict, delay: int = 0) -> dict | None:
    ticker = fv["ticker"]
    if delay > 0:
        time.sleep(delay)
    
    ind = compute_indicators(ticker)
    if not ind:
        return None
        
    score, signals, entry = compute_score_and_status(ind, fv)
    
    return {**fv, **ind, "score": score, "signals": signals, "entry": entry}


def analyze():
    """메인 분석 실행 함수"""
    print(f"\n{'='*50}")
    print(f"🚀 주식 분석 봇 가동 ({datetime.now().strftime('%H:%M:%S')})")
    print(f"{'='*50}")

    # Step 1: 종목 수집
    candidates = fetch_top_gainers()
    if not candidates:
        print("[경고] 수집된 종목이 없습니다.")
        return {"results": []}

    # Step 2: 각 종목 분석
    results = []
    for i, fv in enumerate(candidates):
        # Alpha Vantage 사용 시 딜레이, yfinance 사용 시 딜레이 불필요하지만 안전을 위해 유지
        res = analyze_ticker(fv, delay=AV_DELAY_SEC if i > 0 else 0)
        if res:
            results.append(res)
            print(f"  [OK] {res['ticker']:6s} | 점수: {res['score']:2d} | 상태: {res['entry']}")
        else:
            print(f"  [SKIP] {fv['ticker']} 데이터 수집 실패")

    if not results:
        print("[오류] 분석된 종목이 없습니다.")
        return {"results": []}

    # 점수 기준 정렬
    results.sort(key=lambda x: x["score"], reverse=True)

    save_data = {
        "analyzed_at": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "results":     results,
    }

    # 데이터 저장
    try:
        os.makedirs("history", exist_ok=True)
        filename = datetime.now().strftime('%Y-%m-%d_%H%M%S')
        filepath = f"history/{filename}.json"
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(save_data, f, ensure_ascii=False, indent=2)
        print(f"[History] {filepath} 저장 완료")
    except Exception as e:
        print(f"[History] 저장 오류: {e}")

    # 텔레그램 전송
    top10 = results[:10]
    today_str = datetime.now().strftime('%Y-%m-%d')
    report = f"📊 *{today_str} 당일 상승 종목 Top 분석*\n\n"
    for i, r in enumerate(top10, 1):
        vwap_status = "상회" if r['is_above_vwap'] else "하회"
        report += (
            f"{i}. *{r['ticker']}*\n"
            f"   상태: {r['entry']} | 점수: {r['score']}\n"
            f"   가격: ${r['price']:.2f} ({r['change']:+.2f}%)\n"
            f"   RSI: {r['rsi']:.1f} | VWAP {vwap_status} ({r['vwap_gap_pct']:+.1f}%)\n\n"
        )
    send_telegram(report)

    print(f"\n✅ 분석 완료: 총 {len(results)}개 종목")
    print(f"   🟢 진입 가능: {sum(1 for r in results if '🟢' in r['entry'])}개")
    print(f"   ⏳ 대기:      {sum(1 for r in results if '⏳' in r['entry'])}개")
    print(f"   ❌ 회피:      {sum(1 for r in results if '❌' in r['entry'])}개")

    return save_data


if __name__ == "__main__":
    analyze()
