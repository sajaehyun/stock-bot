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
MAX_WORKERS = 5
MAX_FINVIZ_TICKERS = 30

# ─────────────────────────────────────────────
# 유틸리티 함수
# ─────────────────────────────────────────────

def safe_float(val, default=0.0):
    try:
        # 시리즈나 배열인 경우 첫 번째 요소 추출
        if hasattr(val, 'iloc'):
            val = val.iloc[0]
        elif hasattr(val, '__iter__') and not isinstance(val, (str, dict)):
            val = val[0]
            
        v = float(val)
        if np.isnan(v) or np.isinf(v):
            return default
        return v
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
        # 버전 호환성을 위한 하이브리드 필터 설정
        filter_cfg = {'Index': 'S&P 500', 'Order': 'Change Desc'}
        try:
            foverview.set_filter(filters_dict=filter_cfg)
        except (TypeError, Exception):
            try:
                foverview.set_filter(filter_dict=filter_cfg)
            except:
                print("Finviz 필터 적용 실패. 전체 데이터를 사용합니다.")
                
        df = foverview.screener_view()
        
        if df is None or df.empty:
            print("Finviz 데이터를 가져오지 못했습니다.")
            return []
            
        tickers_data = []
        for _, row in df.head(MAX_FINVIZ_TICKERS).iterrows():
            try:
                ticker = row.get('Ticker', '')
                company = row.get('Company', '')
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
    """yfinance 데이터를 사용해 기술 지표 계산 (Retry와 컬럼 대응 포함)"""
    df = None
    for attempt in range(3):
        try:
            # MA200을 위해 최소 250일 이상의 데이터 권장
            raw = yf.download(ticker, period="2y", interval="1d", progress=False, auto_adjust=True)
            if raw is not None and len(raw) >= 200:
                df = raw
                break
        except Exception as e:
            print(f"[{ticker}] 다운로드 실패 ({attempt+1}/3): {e}")
        time.sleep(2 * (attempt + 1))
            
    if df is None or len(df) < 52: 
        return None

    try:
        # MultiIndex 컬럼 제거
        if hasattr(df.columns, 'get_level_values') and isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
            
        close = df["Close"].squeeze()
        if isinstance(close, pd.DataFrame): close = close.iloc[:, 0]
        
        high = df["High"].squeeze()
        if isinstance(high, pd.DataFrame): high = high.iloc[:, 0]
        
        low = df["Low"].squeeze()
        if isinstance(low, pd.DataFrame): low = low.iloc[:, 0]
        
        volume = df["Volume"].squeeze()
        if isinstance(volume, pd.DataFrame): volume = volume.iloc[:, 0]

        curr_price = safe_float(close.iloc[-1])
        prev_price = safe_float(close.iloc[-2]) if len(close) > 1 else curr_price
        change_1d = (curr_price - prev_price) / prev_price * 100 if prev_price != 0 else 0

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

        # Moving Averages (Trend)
        ma20 = close.rolling(20).mean()
        ma50 = close.rolling(50).mean()
        ma200 = close.rolling(200).mean()
        
        m20_val = safe_float(ma20.iloc[-1])
        m50_val = safe_float(ma50.iloc[-1])
        m200_val = safe_float(ma200.iloc[-1])

        if m50_val > m200_val and m200_val > 0:
            ma_trend = "골든크로스"
        elif m50_val < m200_val and m200_val > 0:
            ma_trend = "데드크로스"
        else:
            ma_trend = "중립"
        
        # Stochastic (14, 3)
        low14 = low.rolling(14).min()
        high14 = high.rolling(14).max()
        stoch_k = 100 * (close - low14) / (high14 - low14)
        stoch_d = stoch_k.rolling(3).mean()
        curr_stoch_d = safe_float(stoch_d.iloc[-1])

        # Ichimoku Cloud (Current values)
        tenkan = (high.rolling(9).max() + low.rolling(9).min()) / 2
        kijun = (high.rolling(26).max() + low.rolling(26).min()) / 2
        span_a = (tenkan + kijun) / 2
        span_b = (high.rolling(52).max() + low.rolling(52).min()) / 2
        
        sa_val = safe_float(span_a.iloc[-1])
        sb_val = safe_float(span_b.iloc[-1])
        is_above_cloud = curr_price > max(sa_val, sb_val)
        is_below_cloud = curr_price < min(sa_val, sb_val)

        # ATR (14) - Clamp to min 1% for sanity
        tr1 = high - low
        tr2 = abs(high - close.shift())
        tr3 = abs(low - close.shift())
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()
        curr_atr = max(safe_float(atr.iloc[-1]), curr_price * 0.01)

        # Targets
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
            "ma20": m20_val,
            "ma50": m50_val,
            "ma200": m200_val,
            "stochastic_d": curr_stoch_d,
            "is_above_cloud": is_above_cloud,
            "is_below_cloud": is_below_cloud,
            "vol_ratio": vol_ratio,
            "target1": target1,
            "target2": target2,
            "stop_loss": stop_loss,
            "ma_trend": ma_trend
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
    
    # 1. RSI
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
    
    # 3. MA Trend & Cross
    if ind["ma_trend"] == "골든크로스":
        score += 20
        signals.append("MA 골든크로스")
    
    if ind["price"] > ind["ma20"]:
        score += 15
        signals.append("MA20 위")

    # 4. Stochastic
    if ind["stochastic_d"] < 20:
        score += 10
        signals.append("스토캐스틱 과매도")
    elif ind["stochastic_d"] > 80:
        score -= 10
        signals.append("스토캐스틱 과매수")

    # 5. Ichimoku Cloud
    if ind["is_above_cloud"]:
        score += 20
        signals.append("구름대 위")
    elif ind["is_below_cloud"]:
        score -= 20
        signals.append("구름대 아래 (저항)")

    # 6. Volume
    if ind["vol_ratio"] > 150:
        score += 15
        signals.append("거래량 급증")

    # 7. Momentum
    if fv["change"] > 5:
        score += 10
        signals.append("강한 상승 모멘텀")

    # 진입 상태 결정
    if score >= 65:
        entry = "🟢 진입 가능"
    elif score >= 40:
        entry = "⏳ 대기 (관망)"
    else:
        entry = "❌ 회피 (리스크)"

    return score, signals, entry

# ─────────────────────────────────────────────
# 종목 분석 프로세스 (단일)
# ─────────────────────────────────────────────

def analyze_ticker(fv):
    ticker = fv["ticker"]
    ind = compute_indicators(ticker)
    if ind is None: return None
    
    score, signals, entry = compute_score_and_status(ind, fv)
    
    return {
        "ticker": ticker,
        "company": fv["company"],
        "price": ind["price"],
        "change": ind["change_1d"],
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
# 메인 분석 실행
# ─────────────────────────────────────────────

def analyze():
    print(f"[{datetime.now()}] S&P 500 정밀 분석 시작...")
    
    candidates = fetch_finviz_sp500_gainers()
    if not candidates:
        return {"results": [], "error": "후보군 수집 실패"}

    results = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(analyze_ticker, fv): fv for fv in candidates}
        for future in as_completed(futures):
            res = future.result()
            if res:
                results.append(res)

    results.sort(key=lambda x: x['score'], reverse=True)
    
    today = datetime.now().strftime('%Y-%m-%d')
    save_data = {
        "analyzed_at": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "results": results
    }

    try:
        if not os.path.exists("history"):
            os.makedirs("history")
        with open(f"history/{today}.json", "w", encoding="utf-8") as f:
            json.dump(save_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[History] 파일 저장 오류: {e}")

    if results:
        top_10 = results[:10]
        report = f"📊 *{today} S&P 500 모멘텀 Top 10*\n\n"
        for i, r in enumerate(top_10, 1):
            report += f"{i}. *{r['ticker']}* ({r['company']})\n"
            report += f"   점수: {r['score']} | 상태: {r['entry']}\n"
            report += f"   추세: {r['ma_trend']} | RSI: {r['rsi']:.1f}\n"
            report += f"   가격: ${r['price']:.2f} ({r['change']:+.2f}%)\n\n"
        send_telegram(report)

    print(f"분석 완료: {len(results)} 종목 처리됨")
    return save_data

if __name__ == "__main__":
    analyze()
