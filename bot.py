import os
import json
import time
import requests
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor, as_completed
from finvizfinance.screener.overview import Overview

load_dotenv()

# ─────────────────────────────────────────────
# 환경변수 및 상수
# ─────────────────────────────────────────────
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID        = os.getenv("CHAT_ID")

# 분석 설정
MAX_WORKERS = 5  # yfinance Rate Limit 방지를 위해 하향 조정
MAX_FINVIZ_TICKERS = 30

# ─────────────────────────────────────────────
# 유틸리티 함수
# ─────────────────────────────────────────────

def safe_float(val, default=0.0):
    try:
        if pd.isna(val): return default
        return float(val)
    except:
        return default

def send_telegram(message):
    if not TELEGRAM_TOKEN or not CHAT_ID: return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        res = requests.post(url, json={"chat_id": CHAT_ID, "text": message, "parse_mode": "Markdown"})
        res.raise_for_status()
    except Exception as e:
        print(f"[Telegram] 전송 실패: {e}")

# ─────────────────────────────────────────────
# Finviz에서 S&P 500 상승 종목 수집
# ─────────────────────────────────────────────

def fetch_finviz_sp500_gainers():
    """finvizfinance 라이브러리를 사용해 S&P 500 상승 상위 종목 수집"""
    print("Finviz에서 S&P 500 상승 종목 수집 중...")
    try:
        foverview = Overview()
        # S&P 500 필터 및 당일 상승률 내림차순 정렬
        foverview.set_filter(filters_dict={'Index': 'S&P 500', 'Order': 'Change Desc'})
        df = foverview.screener_view()
        
        if df is None or df.empty:
            print("Finviz 데이터를 가져오지 못했습니다.")
            return []
            
        tickers_data = []
        # 상위 30개 종목 추출
        for _, row in df.head(MAX_FINVIZ_TICKERS).iterrows():
            try:
                # finvizfinance 결과 컬럼명 대응
                ticker = row.get('Ticker', '')
                company = row.get('Company', '')
                # 'Price'와 'Change' 백분율 추출
                price = safe_float(row.get('Price'))
                change_str = str(row.get('Change', '0%')).replace('%', '')
                change = safe_float(change_str)
                
                if ticker:
                    tickers_data.append({
                        "ticker": ticker,
                        "company": company,
                        "price": price,
                        "change": change
                    })
            except Exception:
                continue
        
        print(f"Finviz 수집 완료: {len(tickers_data)} 종목")
        return tickers_data
    except Exception as e:
        print(f"Finviz 수집 중 오류: {e}")
        return []

# ─────────────────────────────────────────────
# 기술적 지표 계산
# ─────────────────────────────────────────────

def compute_indicators(ticker):
    """yfinance 데이터를 사용해 기술 지표 계산 (Retry 로직 포함)"""
    df = None
    for attempt in range(3):
        try:
            # yfinance 0.2.x+ 대응을 위해 데이터 다운로드
            df = yf.download(ticker, period="60d", interval="1d", progress=False, auto_adjust=True)
            if df is not None and len(df) >= 40: # 이치모쿠 kijun(26), rolling(52) 대응 가능하도록 충분히 확보
                break
            time.sleep(1)
        except Exception as e:
            time.sleep(2 * (attempt + 1))
            
    if df is None or len(df) < 20: 
        return None

    try:
        # MultiIndex 컬럼 제거 (yfinance 0.2.x+ 대응)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
            
        close = df["Close"].squeeze()
        if isinstance(close, pd.DataFrame):
            close = close.iloc[:, 0]
            
        high = df["High"].squeeze()
        if isinstance(high, pd.DataFrame): high = high.iloc[:, 0]
        
        low = df["Low"].squeeze()
        if isinstance(low, pd.DataFrame): low = low.iloc[:, 0]
        
        volume = df["Volume"].squeeze()
        if isinstance(volume, pd.DataFrame): volume = volume.iloc[:, 0]

        curr_price = safe_float(close.iloc[-1])
        prev_price = safe_float(close.iloc[-2]) if len(close) > 1 else curr_price
        change_1d = ((curr_price - prev_price) / prev_price * 100) if prev_price != 0 else 0

        # RSI (14)
        delta = close.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        curr_rsi = safe_float(rsi.iloc[-1])

        # MACD (12, 26, 9)
        ma12 = close.ewm(span=12, adjust=False).mean()
        ma26 = close.ewm(span=26, adjust=False).mean()
        macd_line = ma12 - ma26
        signal_line = macd_line.ewm(span=9, adjust=False).mean()
        macd_hist = macd_line - signal_line
        curr_macd_hist = safe_float(macd_hist.iloc[-1])

        # Moving Averages
        ma20 = close.rolling(20).mean()
        curr_ma20 = safe_float(ma20.iloc[-1])
        
        # Stochastic (14, 3)
        low14 = low.rolling(14).min()
        high14 = high.rolling(14).max()
        stoch_k = 100 * (close - low14) / (high14 - low14)
        stoch_d = stoch_k.rolling(3).mean()
        curr_stoch_d = safe_float(stoch_d.iloc[-1])

        # Ichimoku Cloud (Unshifted for current comparison)
        tenkan = (high.rolling(9).max() + low.rolling(9).min()) / 2
        kijun = (high.rolling(26).max() + low.rolling(26).min()) / 2
        # Span A/B 계산 (shift 없이 현재 기준)
        span_a = (tenkan + kijun) / 2
        span_b = (high.rolling(52).max() + low.rolling(52).min()) / 2
        
        sa_val = safe_float(span_a.iloc[-1])
        sb_val = safe_float(span_b.iloc[-1])
        is_above_cloud = curr_price > max(sa_val, sb_val)
        is_below_cloud = curr_price < min(sa_val, sb_val)

        # ATR (14) for Dynamic Targets/Stop Loss
        tr1 = high - low
        tr2 = abs(high - close.shift())
        tr3 = abs(low - close.shift())
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()
        curr_atr = safe_float(atr.iloc[-1])

        # Targets (ATR based)
        target1 = curr_price + (1.5 * curr_atr)
        target2 = curr_price + (3.0 * curr_atr)
        stop_loss = curr_price - (1.5 * curr_atr)

        # Volume Ratio
        vol_avg = volume.rolling(20).mean().iloc[-1]
        vol_ratio = (volume.iloc[-1] / vol_avg * 100) if vol_avg > 0 else 100

        return {
            "price": curr_price,
            "change_1d": change_1d,
            "rsi": curr_rsi,
            "macd_histogram": curr_macd_hist,
            "ma20": curr_ma20,
            "stochastic_d": curr_stoch_d,
            "is_above_cloud": is_above_cloud,
            "is_below_cloud": is_below_cloud,
            "vol_ratio": vol_ratio,
            "target1": target1,
            "target2": target2,
            "stop_loss": stop_loss,
            "ma_trend": "정배열" if curr_price > curr_ma20 else "역배열"
        }
    except Exception as e:
        print(f"지표 계산 오류 ({ticker}): {e}")
        return None

# ─────────────────────────────────────────────
# 점수 및 진입 상태 계산
# ─────────────────────────────────────────────

def compute_score_and_status(ind, fv):
    """지표 기반 점수 산출 및 진입 상태 결정"""
    score = 0
    signals = []
    
    # 1. RSI (과매수/과매도)
    if ind["rsi"] < 35:
        score += 20
        signals.append("RSI 과매도")
    elif ind["rsi"] < 50:
        score += 10
    elif ind["rsi"] > 70:
        score -= 10
        signals.append("RSI 과매수 경고")

    # 2. MACD Histogram
    if ind["macd_histogram"] > 0:
        score += 15
        signals.append("MACD 상방")
    
    # 3. MA 20
    if ind["price"] > ind["ma20"]:
            score += 15
            signals.append("MA20 돌파/상회")

    # 4. Ichimoku Cloud
    if ind["is_above_cloud"]:
        score += 20
        signals.append("구름대 상단 돌파")
    elif ind["is_below_cloud"]:
        score -= 20
        signals.append("구름대 하단 저항")

    # 5. Volume
    if ind["vol_ratio"] > 150:
        score += 15
        signals.append("거래량 급증")

    # 6. Finviz Momentum (상승률 가점)
    if fv["change"] > 5:
        score += 10
        signals.append("강한 모멘텀")

    # 진입 상태 결정
    if score >= 60:
        entry = "🟢 진입 가능"
    elif score >= 40:
        entry = "⏳ 대기 (관망)"
    else:
        entry = "❌ 회피 (리스크)"

    return score, signals, entry

# ─────────────────────────────────────────────
# 단일 종목 분석 프로세스
# ─────────────────────────────────────────────

def analyze_ticker(fv):
    ticker = fv["ticker"]
    ind = compute_indicators(ticker)
    if ind is None: return None
    
    score, signals, entry = compute_score_and_status(ind, fv)
    
    # yfinance 데이터를 우선하여 병합
    return {
        "ticker": ticker,
        "company": fv["company"],
        "price": ind["price"],
        "change": ind["change_1d"], # yfinance 기준 일일 변동률
        "rsi": ind["rsi"],
        "macd_histogram": ind["macd_histogram"],
        "ma20": ind["ma20"],
        "stochastic_d": ind["stochastic_d"],
        "is_above_cloud": ind["is_above_cloud"],
        "is_below_cloud": ind["is_below_cloud"],
        "vol_ratio": ind["vol_ratio"],
        "target1": ind["target1"],
        "target2": ind["target2"],
        "stop_loss": ind["stop_loss"],
        "ma_trend": ind["ma_trend"],
        "score": score,
        "signals": signals,
        "entry": entry,
        "analyzed_at": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    }

# ─────────────────────────────────────────────
# 메인 분석 루프
# ─────────────────────────────────────────────

def analyze():
    print(f"[{datetime.now()}] S&P 500 모멘텀 분석 시작...")
    
    # 1. 대상 종목 선정
    candidates = fetch_finviz_sp500_gainers()
    if not candidates:
        return {"results": [], "error": "Finviz 수집 실패"}

    # 2. 병렬 분석 실행
    results = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(analyze_ticker, fv): fv for fv in candidates}
        for future in as_completed(futures):
            res = future.result()
            if res:
                results.append(res)

    # 점수순 정렬
    results.sort(key=lambda x: x['score'], reverse=True)
    
    today = datetime.now().strftime('%Y-%m-%d')
    save_data = {
        "analyzed_at": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "results": results
    }

    # 3. 로컬 히스토리 저장
    try:
        if not os.path.exists("history"):
            os.makedirs("history")
        with open(f"history/{today}.json", "w", encoding="utf-8") as f:
            json.dump(save_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[History] 저장 오류: {e}")

    # 4. 텔레그램 리포트 (상위 10개)
    if results:
        top_10 = results[:10]
        report = f" {today} S&P 500 모멘텀 Top 10 \n\n"
        for i, r in enumerate(top_10, 1):
            report += f"{i}. *{r['ticker']}* ({r['company']})\n"
            report += f"   점수: {r['score']} | 상태: {r['entry']}\n"
            report += f"   가격: ${r['price']:.2f} ({r['change']:+.2f}%)\n"
            report += f"   RSI: {r['rsi']:.1f} | MACD: {r['macd_histogram']:.2f}\n\n"
        send_telegram(report)

    print(f"분석 완료: {len(results)} 종목 처리됨")
    return save_data

if __name__ == "__main__":
    analyze()
