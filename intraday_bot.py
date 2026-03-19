import requests, yfinance as yf, pandas as pd, numpy as np
from datetime import datetime, timedelta

TELEGRAM_TOKEN = "8475611635:AAFYDJ48HdVJyBctnsr9Sl3CLW-4JWk_jmE"
CHAT_ID = "8630004087"
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

SOXL_STOCKS = ["MU","NVDA","AMAT","AMD","AVGO","QCOM","INTC","ON","MCHP","NXPI","MRVL","SNDK","LRCX","KLAC","ASML","TXN","ADI","SLAB","SWKS","MPWR","ONTO","RCLK","PLOW","ICHR","MANH","FORM","COHR","MATH","CAVM","RMBS"]

def get_intraday_indicators(ticker):
    """30분봉 기반 일중 지표 계산"""
    try:
        hist = yf.Ticker(ticker).history(period="2wk", interval="30m")
        
        if len(hist) < 10:
            return None
        
        close = hist['Close'].astype(float)
        volume = hist['Volume'].astype(float)
        high = hist['High'].astype(float)
        low = hist['Low'].astype(float)
        
        current_price = float(close.iloc[-1])
        prev_price = float(close.iloc[-2])
        
        # === 30분봉 RSI (9) ===
        delta = close.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=9).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=9).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        rsi_val = float(rsi.iloc[-1]) if not pd.isna(rsi.iloc[-1]) else 50.0
        
        # === 30분봉 MACD ===
        ema5 = close.ewm(span=5).mean()
        ema10 = close.ewm(span=10).mean()
        macd = ema5 - ema10
        signal = macd.ewm(span=5).mean()
        macd_histogram = macd - signal
        macd_val = float(macd_histogram.iloc[-1]) if not pd.isna(macd_histogram.iloc[-1]) else 0.0
        
        # === Stochastic (5, 3) ===
        low5 = low.rolling(window=5).min()
        high5 = high.rolling(window=5).max()
        stoch_k = 100 * (close - low5) / (high5 - low5)
        stoch_d = stoch_k.rolling(window=3).mean()
        stoch_k_val = float(stoch_k.iloc[-1]) if not pd.isna(stoch_k.iloc[-1]) else 50.0
        stoch_d_val = float(stoch_d.iloc[-1]) if not pd.isna(stoch_d.iloc[-1]) else 50.0
        
        # === Bollinger Bands (10, 2) ===
        bb_mid = close.rolling(window=10).mean()
        bb_std = close.rolling(window=10).std()
        bb_upper = bb_mid + (bb_std * 2)
        bb_lower = bb_mid - (bb_std * 2)
        bb_width = ((bb_upper - bb_lower) / bb_mid * 100) if bb_mid.iloc[-1] > 0 else 15.0
        bb_upper_val = float(bb_upper.iloc[-1]) if not pd.isna(bb_upper.iloc[-1]) else current_price
        bb_lower_val = float(bb_lower.iloc[-1]) if not pd.isna(bb_lower.iloc[-1]) else current_price
        bb_width_val = float(bb_width.iloc[-1]) if not pd.isna(bb_width.iloc[-1]) else 15.0
        
        # === 거래량 분석 ===
        vol_avg = volume.rolling(window=10).mean()
        vol_ratio = (volume.iloc[-1] / vol_avg.iloc[-1]) * 100 if vol_avg.iloc[-1] > 0 else 100.0
        vol_trend = "폭증" if vol_ratio > 150 else ("증가" if volume.iloc[-1] > vol_avg.iloc[-1] else "감소")
        
        # === 당일 고가/저가 ===
        today_high = high.iloc[-10:].max()
        today_low = low.iloc[-10:].min()
        price_range = today_high - today_low
        distance_to_high = ((today_high - current_price) / price_range * 100) if price_range > 0 else 50.0
        distance_to_low = ((current_price - today_low) / price_range * 100) if price_range > 0 else 50.0
        
        # === 변동률 ===
        change_30m = ((current_price - prev_price) / prev_price) * 100 if prev_price > 0 else 0.0
        change_2h = ((current_price - close.iloc[-4]) / close.iloc[-4]) * 100 if len(close) > 4 and close.iloc[-4] > 0 else 0.0
        change_4h = ((current_price - close.iloc[-8]) / close.iloc[-8]) * 100 if len(close) > 8 and close.iloc[-8] > 0 else 0.0
        
        # === EMA ===
        ema3 = float(close.ewm(span=3).mean().iloc[-1])
        ema5_val = float(ema5.iloc[-1])
        ema10_val = float(ema10.iloc[-1])
        
        # === 추세 판정 ===
        if ema3 > ema5_val > ema10_val:
            trend = "🔺 강한 상승"
        elif ema3 > ema5_val:
            trend = "📈 상승"
        elif ema3 < ema5_val < ema10_val:
            trend = "🔻 강한 하강"
        elif ema3 < ema5_val:
            trend = "📉 하강"
        else:
            trend = "➡️ 중립"
        
        # === 모멘텀 ===
        recent_closes = close.iloc[-3:].values
        momentum = sum([1 if recent_closes[i] > recent_closes[i-1] else -1 for i in range(1, len(recent_closes))])
        momentum_signal = "🚀 상승 모멘텀" if momentum > 0 else "⬇️ 하강 모멘텀" if momentum < 0 else "➡️ 모멘텀 없음"
        
        return {
            'ticker': ticker,
            'price': current_price,
            'prev_price': prev_price,
            'rsi': rsi_val,
            'macd_histogram': macd_val,
            'stoch_k': stoch_k_val,
            'stoch_d': stoch_d_val,
            'bb_upper': bb_upper_val,
            'bb_lower': bb_lower_val,
            'bb_width': bb_width_val,
            'vol_ratio': vol_ratio,
            'vol_trend': vol_trend,
            'today_high': today_high,
            'today_low': today_low,
            'distance_to_high': distance_to_high,
            'distance_to_low': distance_to_low,
            'change_30m': change_30m,
            'change_2h': change_2h,
            'change_4h': change_4h,
            'ema3': ema3,
            'ema5': ema5_val,
            'ema10': ema10_val,
            'trend': trend,
            'momentum': momentum,
            'momentum_signal': momentum_signal,
        }
    except Exception as e:
        print(f"Error processing {ticker}: {e}")
        return None

def calculate_intraday_score(data):
    """30분 데이트레이딩 최적화 점수"""
    score = 0
    signals = []
    buy_signals = 0
    
    # === 추세 확인 ===
    if "상승" in data['trend']:
        score += 25
        buy_signals += 1
        signals.append(f"📈 {data['trend']}")
    elif "하강" in data['trend']:
        score -= 20
        signals.append(f"📉 {data['trend']}")
    
    # === RSI (9) ===
    if data['rsi'] < 25:
        score += 30
        buy_signals += 1
        signals.append(f"🔴 극도 과매도 (RSI {data['rsi']:.1f})")
    elif data['rsi'] < 40:
        score += 15
        buy_signals += 1
        signals.append(f"🟠 과매도 (RSI {data['rsi']:.1f})")
    elif data['rsi'] > 75:
        score -= 20
        signals.append(f"⚠️ 과매수 (RSI {data['rsi']:.1f})")
    
    # === MACD ===
    if data['macd_histogram'] > 0 and "상승" in data['trend']:
        score += 20
        buy_signals += 1
        signals.append(f"📊 MACD 양수 (상승 확인)")
    elif data['macd_histogram'] < -0.3:
        score -= 15
        signals.append(f"📉 MACD 음수")
    
    # === Stochastic ===
    if data['stoch_k'] < 30 and data['stoch_d'] < data['stoch_k']:
        score += 35
        buy_signals += 2
        signals.append(f"🔵 Stochastic 극저 + 상향 ({data['stoch_k']:.1f})")
    elif data['stoch_k'] > 70:
        score -= 15
        signals.append(f"⚠️ Stochastic 과매수")
    
    # === BB ===
    if data['price'] < data['bb_lower']:
        score += 20
        buy_signals += 1
        signals.append(f"🎯 BB 하단 터치 (${data['bb_lower']:.2f})")
    elif data['price'] > data['bb_upper']:
        score -= 15
        signals.append(f"⚠️ BB 상단 터치")
    
    # === 거래량 ===
    if data['vol_ratio'] > 200:
        score += 25
        buy_signals += 1
        signals.append(f"💥 거래량 폭증 ({data['vol_ratio']:.0f}%)")
    elif data['vol_ratio'] > 150:
        score += 15
        signals.append(f"📊 거래량 증가 ({data['vol_ratio']:.0f}%)")
    
    # === 모멘텀 ===
    if "상승" in data['momentum_signal']:
        score += 15
        buy_signals += 1
        signals.append("🚀 상승 모멘텀")
    elif "하강" in data['momentum_signal']:
        score -= 15
        signals.append("⬇️ 하강 모멘텀")
    
    # === 당일 저가 근처 ===
    if data['distance_to_low'] < 30:
        score += 20
        buy_signals += 1
        signals.append(f"📌 당일 저가 근처 ({data['distance_to_low']:.1f}%)")
    elif data['distance_to_high'] < 20:
        score -= 15
        signals.append(f"⚠️ 당일 고가 근처")
    
    # === 급락 신호 ===
    if data['change_30m'] < -2:
        score += 15
        buy_signals += 1
        signals.append(f"🔥 급락 신호 ({data['change_30m']:.2f}%)")
    
    return max(0, score), buy_signals, signals

def analyze():
    """30분 데이트레이딩 분석"""
    results = []
    
    for ticker in SOXL_STOCKS:
        data = get_intraday_indicators(ticker)
        if data is None:
            continue
        
        score, buy_signals, signals = calculate_intraday_score(data)
        
        if score > 0 or buy_signals >= 2:
            results.append({
                'ticker': ticker,
                'data': data,
                'score': score,
                'buy_signals': buy_signals,
                'signals': signals,
            })
    
    # === 상위 10개 ===
    results.sort(key=lambda x: (x['buy_signals'], x['score']), reverse=True)
    top10 = results[:10]
    
    if not top10:
        msg = "⚠️ 현재 거래 신호 없음 (조건 미달)\n대기 중..."
        requests.post(TELEGRAM_API, json={'chat_id': CHAT_ID, 'text': msg})
        return
    
    # === 메시지 ===
    msg = f"🚀 30분봉 데이트레이딩 신호 (TOP 10)\n"
    msg += f"⏰ {datetime.now().strftime('%H:%M:%S')} KST\n"
    msg += f"{'='*60}\n\n"
    
    for i, result in enumerate(top10, 1):
        d = result['data']
        icon = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}️⃣"
        
        msg += f"{icon} **{d['ticker']}** | ${d['price']:.2f}\n"
        msg += f"변동: {d['change_30m']:+.2f}% (30m) | {d['change_2h']:+.2f}% (2h) | {d['change_4h']:+.2f}% (4h)\n"
        msg += f"신호: {result['buy_signals']}/7 | 점수: {result['score']:.0f}\n"
        msg += f"\n📊 지표 (30분봉):\n"
        msg += f"RSI: {d['rsi']:.1f} | MACD: {d['macd_histogram']:+.3f} | Stoch: {d['stoch_k']:.1f}\n"
        msg += f"EMA: 3={d['ema3']:.2f} > 5={d['ema5']:.2f} > 10={d['ema10']:.2f}\n"
        msg += f"BB: {d['bb_lower']:.2f}~{d['bb_upper']:.2f} | 거래량: {d['vol_ratio']:.0f}%\n"
        msg += f"추세: {d['trend']}\n"
        msg += f"\n🎯 신호:\n"
        for signal in result['signals'][:5]:
            msg += f"{signal}\n"
        msg += f"\n{'─'*60}\n"
    
    msg += f"\n📌 30분마다 업데이트 | 손절/익절은 자신의 판단으로!\n"
    
    requests.post(TELEGRAM_API, json={'chat_id': CHAT_ID, 'text': msg, 'parse_mode': 'Markdown'})
    print("✅ 30분봉 분석 완료!")

if __name__ == "__main__":
    analyze()
