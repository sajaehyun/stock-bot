import requests, yfinance as yf, pandas as pd, numpy as np
from datetime import datetime, timedelta

TELEGRAM_TOKEN = "8475611635:AAFYDJ48HdVJyBctnsr9Sl3CLW-4JWk_jmE"
CHAT_ID = "8630004087"
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

SOXL_STOCKS = ["MU","NVDA","AMAT","AMD","AVGO","QCOM","INTC","ON","MCHP","NXPI","MRVL","SNDK","LRCX","KLAC","ASML","TXN","ADI","SLAB","SWKS","MPWR","ONTO","RCLK","PLOW","ICHR","MANH","FORM","COHR","MATH","CAVM","RMBS"]

def get_technical_indicators(ticker):
    """야후 파이낸스 데이터로 기술 지표 계산"""
    try:
        hist = yf.Ticker(ticker).history(period="6mo")
        if len(hist) < 30:
            return None
        
        close = hist['Close']
        volume = hist['Volume']
        
        # === RSI (14) ===
        delta = close.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        
        # === MACD ===
        ema12 = close.ewm(span=12).mean()
        ema26 = close.ewm(span=26).mean()
        macd = ema12 - ema26
        signal = macd.ewm(span=9).mean()
        macd_histogram = macd - signal
        
        # === Stochastic ===
        low14 = close.rolling(window=14).min()
        high14 = close.rolling(window=14).max()
        stoch_k = 100 * (close - low14) / (high14 - low14)
        stoch_d = stoch_k.rolling(window=3).mean()
        
        # === Bollinger Bands ===
        bb_mid = close.rolling(window=20).mean()
        bb_std = close.rolling(window=20).std()
        bb_upper = bb_mid + (bb_std * 2)
        bb_lower = bb_mid - (bb_std * 2)
        bb_width = (bb_upper - bb_lower) / bb_mid * 100
        
        # === 거래량 분석 ===
        vol_avg = volume.rolling(window=20).mean()
        vol_ratio = (volume.iloc[-1] / vol_avg.iloc[-1]) * 100
        vol_trend = "증가" if volume.iloc[-1] > vol_avg.iloc[-1] else "감소"
        
        # === 52주 범위 (지지·저항) ===
        high52 = close.rolling(window=252).max()
        low52 = close.rolling(window=252).min()
        price_to_52high = (close.iloc[-1] / high52.iloc[-1]) * 100
        price_to_52low = ((close.iloc[-1] - low52.iloc[-1]) / (high52.iloc[-1] - low52.iloc[-1])) * 100
        
        # === Fibonacci (38.2%, 50%, 61.8%) ===
        fib_high = high52.iloc[-1]
        fib_low = low52.iloc[-1]
        fib_range = fib_high - fib_low
        fib_382 = fib_high - (fib_range * 0.382)
        fib_500 = fib_high - (fib_range * 0.500)
        fib_618 = fib_high - (fib_range * 0.618)
        
        # === Elliott Wave 판정 ===
        recent_3d = close.iloc[-3:].values
        is_wave_bottom = (recent_3d[0] > recent_3d[1]) and (recent_3d[1] < recent_3d[2])
        
        # === 이동평균선 (5, 20, 60, 200) ===
        ma5 = close.rolling(window=5).mean().iloc[-1]
        ma20 = close.rolling(window=20).mean().iloc[-1]
        ma60 = close.rolling(window=60).mean().iloc[-1]
        ma200 = close.rolling(window=200).mean().iloc[-1]
        
        # === 골든크로스/데드크로스 ===
        ma_trend = "골든크로스" if (ma5 > ma20 > ma60) else ("데드크로스" if (ma5 < ma20 < ma60) else "중립")
        
        # === 변동률 ===
        change_1d = ((close.iloc[-1] - close.iloc[-2]) / close.iloc[-2]) * 100
        change_5d = ((close.iloc[-1] - close.iloc[-5]) / close.iloc[-5]) * 100
        change_20d = ((close.iloc[-1] - close.iloc[-20]) / close.iloc[-20]) * 100
        
        return {
            'ticker': ticker,
            'price': close.iloc[-1],
            'rsi': rsi.iloc[-1],
            'macd_histogram': macd_histogram.iloc[-1],
            'stoch_k': stoch_k.iloc[-1],
            'stoch_d': stoch_d.iloc[-1],
            'bb_upper': bb_upper.iloc[-1],
            'bb_lower': bb_lower.iloc[-1],
            'bb_width': bb_width.iloc[-1],
            'vol_ratio': vol_ratio,
            'vol_trend': vol_trend,
            'price_to_52high': price_to_52high,
            'price_to_52low': price_to_52low,
            'fib_382': fib_382,
            'fib_500': fib_500,
            'fib_618': fib_618,
            'is_wave_bottom': is_wave_bottom,
            'ma5': ma5,
            'ma20': ma20,
            'ma60': ma60,
            'ma200': ma200,
            'ma_trend': ma_trend,
            'change_1d': change_1d,
            'change_5d': change_5d,
            'change_20d': change_20d,
        }
    except:
        return None

def calculate_score(data):
    """전문가 수준의 점수 계산"""
    score = 0
    signals = []
    
    # === RSI 신호 (극도의 과매도) ===
    if data['rsi'] < 20:
        score += 30
        signals.append("🔴 극도 과매도 (RSI<20)")
    elif data['rsi'] < 30:
        score += 20
        signals.append("🟠 과매도 (RSI<30)")
    elif data['rsi'] > 80:
        score -= 10
        signals.append("⚠️ 과매수 (RSI>80)")
    
    # === MACD 신호 ===
    if data['macd_histogram'] > 0:
        score += 15
        signals.append("📈 MACD 양수 (상승 추세)")
    elif data['macd_histogram'] < -0.5:
        score -= 10
        signals.append("📉 MACD 음수 (하락 추세)")
    
    # === Stochastic 신호 (더블 바닥) ===
    if data['stoch_k'] < 20:
        score += 25
        signals.append("🔵 Stochastic 극저점 (숏스퀴즈 신호)")
    elif data['stoch_k'] < 50 and data['stoch_d'] < data['stoch_k']:
        score += 15
        signals.append("🟢 Stochastic 상승 중")
    
    # === Bollinger Bands 신호 ===
    if data['price'] < data['bb_lower']:
        score += 20
        signals.append("🎯 BB 하단 돌파 (반등 신호)")
    elif data['bb_width'] < 10:
        score += 15
        signals.append("⚡ BB 폭 축소 (변동성 확대 예상)")
    
    # === 거래량 신호 ===
    if data['vol_ratio'] > 200:
        score += 20
        signals.append("💥 거래량 폭증 (200% 이상)")
    elif data['vol_ratio'] > 150:
        score += 15
        signals.append("📊 거래량 증가 (150% 이상)")
    
    # === Elliott Wave 신호 ===
    if data['is_wave_bottom']:
        score += 25
        signals.append("🌊 Elliott Wave 바닥 형성 (Wave 2 저점)")
    
    # === Fibonacci 신호 ===
    if data['price'] < data['fib_382']:
        score += 15
        signals.append(f"📍 Fib 38.2% 지지선 근처 (${data['fib_382']:.2f})")
    
    # === 이동평균선 신호 ===
    if data['ma_trend'] == "골든크로스":
        score += 20
        signals.append("🏆 골든크로스 (강한 상승신호)")
    elif data['ma_trend'] == "데드크로스":
        score -= 15
        signals.append("💀 데드크로스")
    
    # === 52주 범위 신호 ===
    if data['price_to_52low'] < 30:
        score += 15
        signals.append(f"📌 52주 저가 근처 ({data['price_to_52low']:.1f}%)")
    elif data['price_to_52high'] > 90:
        score -= 10
        signals.append(f"⚠️ 52주 고가 근처 ({data['price_to_52high']:.1f}%)")
    
    # === 단기 변동률 신호 ===
    if data['change_1d'] < -3:
        score += 15
        signals.append(f"🔥 당일 급락 ({data['change_1d']:.2f}%)")
    if data['change_5d'] < -10:
        score += 15
        signals.append(f"⬇️ 5일 큰 낙폭 ({data['change_5d']:.2f}%)")
    
    return max(0, score), signals

def detect_short_squeeze_candidates(data):
    """숏스퀴즈 가능성 높은 종목 판정"""
    squeeze_score = 0
    squeeze_signals = []
    
    # 1. 극도의 과매도 (RSI < 20)
    if data['rsi'] < 20:
        squeeze_score += 40
        squeeze_signals.append("🚀 RSI 극저 (숏 비중 높음)")
    
    # 2. 거래량 폭증 + 가격 급락
    if data['vol_ratio'] > 200 and data['change_1d'] < -5:
        squeeze_score += 40
        squeeze_signals.append("💥 거래량 폭증 + 급락 (숏 청산 신호)")
    
    # 3. 52주 저가 근처 (심각한 약세 → 반등 가능)
    if data['price_to_52low'] < 20:
        squeeze_score += 30
        squeeze_signals.append("⚠️ 52주 극저가 (매수 기회)")
    
    # 4. Bollinger Bands 하단 이탈
    if data['price'] < data['bb_lower']:
        squeeze_score += 25
        squeeze_signals.append("🎯 BB 하단 이탈 (반등 신호)")
    
    # 5. 음수 MACD + Stochastic 극저
    if data['macd_histogram'] < 0 and data['stoch_k'] < 30:
        squeeze_score += 30
        squeeze_signals.append("📊 MACD 음수 + Stochastic 극저 (바닥 신호)")
    
    return squeeze_score, squeeze_signals

def analyze():
    """메인 분석 함수"""
    results = []
    
    for ticker in SOXL_STOCKS:
        data = get_technical_indicators(ticker)
        if data is None:
            continue
        
        score, signals = calculate_score(data)
        squeeze_score, squeeze_signals = detect_short_squeeze_candidates(data)
        
        # 총점 (일반 신호 + 숏스퀴즈)
        total_score = score + (squeeze_score * 0.5)  # 숏스퀴즈 가중치 50%
        
        if total_score > 0:
            results.append({
                'ticker': ticker,
                'data': data,
                'score': score,
                'squeeze_score': squeeze_score,
                'total_score': total_score,
                'signals': signals,
                'squeeze_signals': squeeze_signals,
            })
    
    # === 상위 5개 선별 ===
    results.sort(key=lambda x: x['total_score'], reverse=True)
    top5 = results[:5]
    
    # === 메시지 생성 ===
    msg = f"📊 SOXL 고급 분석 리포트\n"
    msg += f"🕙 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} KST\n"
    msg += f"{'='*50}\n\n"
    
    for i, result in enumerate(top5, 1):
        d = result['data']
        icon = "🔴" if i == 1 else "🟡" if i == 2 else "🟢" if i == 3 else "🔵"
        
        msg += f"{icon} **{i}순위: {d['ticker']}**\n"
        msg += f"현재가: ${d['price']:.2f} | 변동: {d['change_1d']:+.2f}% (1일), {d['change_5d']:+.2f}% (5일)\n"
        msg += f"점수: {result['score']:.0f} | 숏스퀴즈 점수: {result['squeeze_score']:.0f}\n"
        msg += f"\n**기술지표:**\n"
        msg += f"- RSI: {d['rsi']:.1f} | MACD: {d['macd_histogram']:+.3f}\n"
        msg += f"- Stochastic K: {d['stoch_k']:.1f} | D: {d['stoch_d']:.1f}\n"
        msg += f"- BB: {d['bb_lower']:.2f} ~ {d['bb_upper']:.2f} (폭: {d['bb_width']:.1f}%)\n"
        msg += f"- MA 트렌드: {d['ma_trend']} (MA5/20/60: {d['ma5']:.1f}/{d['ma20']:.1f}/{d['ma60']:.1f})\n"
        msg += f"- 거래량: {d['vol_ratio']:.0f}% ({d['vol_trend']})\n"
        msg += f"- 52주: {d['price_to_52low']:.1f}% (저가 대비)\n"
        msg += f"\n**매매신호:**\n"
        for signal in result['signals']:
            msg += f"{signal}\n"
        if result['squeeze_signals']:
            msg += f"\n**🚀 숏스퀴즈 신호:**\n"
            for squeeze in result['squeeze_signals']:
                msg += f"{squeeze}\n"
        msg += f"\n{'─'*50}\n\n"
    
    msg += f"📌 **면책조항:** 이 분석은 기계 학습 기반이며, 투자 조언이 아닙니다.\n"
    msg += f"실제 거래 전 전문가 상담을 받으세요.\n"
    
    # === 텔레그램 전송 ===
    requests.post(TELEGRAM_API, json={'chat_id': CHAT_ID, 'text': msg, 'parse_mode': 'Markdown'})
    print("✅ 분석 완료 및 텔레그램 전송!")

if __name__ == "__main__":
    analyze()
