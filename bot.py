import os, json
import requests, yfinance as yf, pandas as pd, numpy as np
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "8475611635:AAFYDJ48HdVJyBctnsr9Sl3CLW-4JWk_jmE")
CHAT_ID = os.getenv("CHAT_ID", "8630004087")
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

SOXL_STOCKS = ["MU","NVDA","AMAT","AMD","AVGO","QCOM","INTC","ON","MCHP","NXPI","MRVL","SNDK","LRCX","KLAC","ASML","TXN","ADI","SLAB","SWKS","MPWR","ONTO","RCLK","PLOW","ICHR","MANH","FORM","COHR","MATH","CAVM","RMBS"]

def get_technical_indicators(ticker):
    """야후 파이낸스 데이터로 기술 지표 계산"""
    try:
        hist = yf.Ticker(ticker).history(period="6mo")
        if len(hist) < 30:
            return None
        
        close = hist['Close'].astype(float)
        volume = hist['Volume'].astype(float)
        high = hist['High'].astype(float)
        low = hist['Low'].astype(float)
        
        # === RSI (14) ===
        delta = close.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        rsi_val = float(rsi.iloc[-1]) if not pd.isna(rsi.iloc[-1]) else 50.0
        
        # === MACD ===
        ema12 = close.ewm(span=12).mean()
        ema26 = close.ewm(span=26).mean()
        macd = ema12 - ema26
        signal = macd.ewm(span=9).mean()
        macd_histogram = macd - signal
        macd_val = float(macd_histogram.iloc[-1]) if not pd.isna(macd_histogram.iloc[-1]) else 0.0
        macd_hist_prev = float(macd_histogram.iloc[-2]) if len(macd_histogram) > 1 and not pd.isna(macd_histogram.iloc[-2]) else 0.0
        
        # === Stochastic ===
        low14 = close.rolling(window=14).min()
        high14 = close.rolling(window=14).max()
        stoch_k = 100 * (close - low14) / (high14 - low14)
        stoch_d = stoch_k.rolling(window=3).mean()
        stoch_k_val = float(stoch_k.iloc[-1]) if not pd.isna(stoch_k.iloc[-1]) else 50.0
        stoch_d_val = float(stoch_d.iloc[-1]) if not pd.isna(stoch_d.iloc[-1]) else 50.0
        stoch_k_prev = float(stoch_k.iloc[-2]) if len(stoch_k) > 1 and not pd.isna(stoch_k.iloc[-2]) else 50.0
        
        # === Bollinger Bands ===
        bb_mid = close.rolling(window=20).mean()
        bb_std = close.rolling(window=20).std()
        bb_upper = bb_mid + (bb_std * 2)
        bb_lower = bb_mid - (bb_std * 2)
        bb_width = ((bb_upper - bb_lower) / bb_mid * 100)
        bb_upper_val = float(bb_upper.iloc[-1]) if not pd.isna(bb_upper.iloc[-1]) else close.iloc[-1]
        bb_lower_val = float(bb_lower.iloc[-1]) if not pd.isna(bb_lower.iloc[-1]) else close.iloc[-1]
        bb_width_val = float(bb_width.iloc[-1]) if not pd.isna(bb_width.iloc[-1]) else 15.0
        
        # === 거래량 분석 ===
        vol_avg = volume.rolling(window=20).mean()
        vol_ratio = (volume.iloc[-1] / vol_avg.iloc[-1]) * 100 if vol_avg.iloc[-1] > 0 else 100.0
        vol_trend = "증가" if volume.iloc[-1] > vol_avg.iloc[-1] else "감소"
        
        # === 52주 범위 (지지·저항) ===
        high52 = close.rolling(window=252).max()
        low52 = close.rolling(window=252).min()
        current_price = float(close.iloc[-1])
        high52_val = float(high52.iloc[-1]) if not pd.isna(high52.iloc[-1]) else current_price
        low52_val = float(low52.iloc[-1]) if not pd.isna(low52.iloc[-1]) else current_price
        
        price_to_52high = (current_price / high52_val) * 100 if high52_val > 0 else 100.0
        price_to_52low_pct = ((current_price - low52_val) / (high52_val - low52_val)) * 100 if (high52_val - low52_val) > 0 else 50.0
        price_to_52low_pct = max(0, min(100, price_to_52low_pct))  # 0~100 범위
        
        # === Fibonacci (38.2%, 50%, 61.8%) ===
        fib_range = high52_val - low52_val
        fib_382 = high52_val - (fib_range * 0.382)
        fib_500 = high52_val - (fib_range * 0.500)
        fib_618 = high52_val - (fib_range * 0.618)
        
        # === Elliott Wave 판정 ===
        recent_3d = close.iloc[-3:].values
        is_wave_bottom = (recent_3d[0] > recent_3d[1]) and (recent_3d[1] < recent_3d[2])
        
        # === 이동평균선 (5, 20, 60, 200) ===
        ma5 = float(close.rolling(window=5).mean().iloc[-1])
        ma20 = float(close.rolling(window=20).mean().iloc[-1])
        ma60 = float(close.rolling(window=60).mean().iloc[-1])
        ma200 = float(close.rolling(window=200).mean().iloc[-1]) if len(close) >= 200 else ma60
        
        # === 골든크로스/데드크로스 ===
        if ma5 > ma20 > ma60:
            ma_trend = "🏆 골든크로스"
        elif ma5 < ma20 < ma60:
            ma_trend = "💀 데드크로스"
        else:
            ma_trend = "➡️ 중립"
            
        # === Ichimoku Cloud ===
        high9 = high.rolling(window=9).max()
        low9 = low.rolling(window=9).min()
        tenkan_sen = (high9 + low9) / 2
        
        high26 = high.rolling(window=26).max()
        low26 = low.rolling(window=26).min()
        kijun_sen = (high26 + low26) / 2
        
        senkou_span_a = ((tenkan_sen + kijun_sen) / 2).shift(26)
        
        high52_ichi = high.rolling(window=52).max()
        low52_ichi = low.rolling(window=52).min()
        senkou_span_b = ((high52_ichi + low52_ichi) / 2).shift(26)
        
        sa_val = float(senkou_span_a.iloc[-1]) if len(senkou_span_a) > 0 and not pd.isna(senkou_span_a.iloc[-1]) else current_price
        sb_val = float(senkou_span_b.iloc[-1]) if len(senkou_span_b) > 0 and not pd.isna(senkou_span_b.iloc[-1]) else current_price
        
        cloud_top = max(sa_val, sb_val)
        cloud_bottom = min(sa_val, sb_val)
        is_above_cloud = current_price > cloud_top
        is_below_cloud = current_price < cloud_bottom
        
        # === 변동률 ===
        change_1d = ((close.iloc[-1] - close.iloc[-2]) / close.iloc[-2]) * 100 if close.iloc[-2] > 0 else 0.0
        change_5d = ((close.iloc[-1] - close.iloc[-5]) / close.iloc[-5]) * 100 if close.iloc[-5] > 0 else 0.0
        change_20d = ((close.iloc[-1] - close.iloc[-20]) / close.iloc[-20]) * 100 if close.iloc[-20] > 0 else 0.0
        
        return {
            'ticker': ticker,
            'price': current_price,
            'rsi': rsi_val,
            'macd_histogram': macd_val,
            'macd_hist_prev': macd_hist_prev,
            'stoch_k': stoch_k_val,
            'stoch_k_prev': stoch_k_prev,
            'stoch_d': stoch_d_val,
            'bb_upper': bb_upper_val,
            'bb_lower': bb_lower_val,
            'bb_width': bb_width_val,
            'vol_ratio': vol_ratio,
            'vol_trend': vol_trend,
            'price_to_52high': price_to_52high,
            'price_to_52low': price_to_52low_pct,
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
            'cloud_top': cloud_top,
            'cloud_bottom': cloud_bottom,
            'is_above_cloud': is_above_cloud,
            'is_below_cloud': is_below_cloud,
        }
    except Exception as e:
        print(f"Error processing {ticker}: {e}")
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
        signals.append(f"📍 Fib 38.2% 지지선 근처")
    
    # === 이동평균선 신호 ===
    if "골든크로스" in data['ma_trend']:
        score += 20
        signals.append("🏆 골든크로스 (강한 상승신호)")
    elif "데드크로스" in data['ma_trend']:
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
        
    # === Ichimoku 신호 ===
    if data['is_above_cloud']:
        score += 15
        signals.append("☁️ 구름대 돌파 (상승 추세)")
    if data['is_below_cloud']:
        score -= 50
        signals.append("❌ 구름대 아래 (진입 금지)")
    
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

def evaluate_entry_status(data):
    """진입 조건 필터링 검사"""
    cond_cloud = data['is_above_cloud']
    cond_rsi = 30 <= data['rsi'] <= 70
    cond_macd = data['macd_histogram'] > data['macd_hist_prev']
    cond_stoch = (30 <= data['stoch_k'] <= 70) or (data['stoch_k'] > data['stoch_k_prev'])
    cond_ma20 = data['price'] > data['ma20']
    cond_vol = data['vol_ratio'] >= 100
    
    if data['is_below_cloud']:
        return "❌ 회피"
        
    if cond_cloud and cond_rsi and cond_macd and cond_stoch and cond_ma20 and cond_vol:
        return "🟢 진입 가능"
        
    if cond_cloud and (cond_rsi or cond_macd or cond_ma20):
        return "🟡 선택"
        
    return "⏳ 대기"
    
class NpEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, np.integer):
            return int(obj)
        if isinstance(obj, np.floating):
            return float(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        if isinstance(obj, bool) or isinstance(obj, np.bool_):
            return bool(obj)
        return super(NpEncoder, self).default(obj)

def get_market_summary():
    """1. 시장 요약 정보 수집"""
    summary = {}
    
    # 지수 수집
    indices = {
        'S&P500': '^GSPC',
        'Nasdaq100': '^NDX',
        'DowJones': '^DJI',
        'Russell2000': '^RUT',
        'VIX': '^VIX'
    }
    
    idx_data = {}
    for name, ticker in indices.items():
        try:
            hist = yf.Ticker(ticker).history(period="5d")
            if len(hist) >= 2:
                prev_close = hist['Close'].iloc[-2]
                curr_close = hist['Close'].iloc[-1]
                pct_change = ((curr_close - prev_close) / prev_close) * 100
                idx_data[name] = {'price': float(curr_close), 'change': float(pct_change)}
            else:
                idx_data[name] = {'price': 0, 'change': 0}
        except:
            idx_data[name] = {'price': 0, 'change': 0}
            
    summary['indices'] = idx_data
    
    # VIX 해석
    vix_level = idx_data.get('VIX', {}).get('price', 0)
    if vix_level < 15:
        summary['vix_status'] = "🟢 안정적 (변동성 낮음)"
    elif vix_level < 20:
        summary['vix_status'] = "🟡 보통 (정상적인 시장)"
    elif vix_level < 30:
        summary['vix_status'] = "🟠 경계 (변동성 확대)"
    else:
        summary['vix_status'] = "🔴 공포 (극심한 변동성)"
        
    # 섹터 수익률
    sectors = {
        'XLK': '기술', 'XLV': '헬스케어', 'XLF': '금융', 
        'XLY': '임의소비재', 'XLC': '통신', 'XLI': '산업',
        'XLP': '필수소비재', 'XLE': '에너지', 'XLU': '유틸리티',
        'XLRE': '부동산', 'XLB': '소재'
    }
    
    sector_changes = []
    for ticker, name in sectors.items():
        try:
            hist = yf.Ticker(ticker).history(period="5d")
            if len(hist) >= 2:
                prev = hist['Close'].iloc[-2]
                curr = hist['Close'].iloc[-1]
                pct = ((curr - prev) / prev) * 100
                sector_changes.append({'name': name, 'change': float(pct)})
        except:
            pass
            
    if sector_changes:
        sector_changes.sort(key=lambda x: x['change'], reverse=True)
        summary['top_sectors'] = sector_changes[:3]
        summary['bottom_sectors'] = sector_changes[-3:]
    else:
        summary['top_sectors'] = []
        summary['bottom_sectors'] = []
    
    # Alpha Vantage 주요 경제지표/뉴스 (News Sentiment 활용)
    # 실제 경제지표 캘린더는 프리티어로 제약이 많으므로 News Sentiment 로 대체/병행
    av_api_key = os.getenv("ALPHA_VANTAGE_API_KEY", "demo")
    av_url = f"https://www.alphavantage.co/query?function=NEWS_SENTIMENT&tickers=QQQ&apikey={av_api_key}"
    try:
        req = requests.get(av_url, timeout=5)
        data = req.json()
        sentiment_score = 0
        events = []
        if 'feed' in data and len(data['feed']) > 0:
            scores = [float(item['overall_sentiment_score']) for item in data['feed'][:10] if 'overall_sentiment_score' in item]
            if scores:
                sentiment_score = sum(scores) / len(scores)
            
            # 이벤트 요약 (최신 뉴스 2건)
            for item in data['feed'][:2]:
                events.append(item.get('title', 'Unknown News'))
                
        summary['news_sentiment'] = sentiment_score
        summary['today_events'] = events if events else ["(AlphaVantage) 주요 뉴스/일정 없음"]
    except:
        summary['news_sentiment'] = 0.15 # default neutral
        summary['today_events'] = ["데이터를 불러올 수 없습니다."]
        
    return summary

def get_tomorrow_prediction(market_summary):
    """2. AI 내일 증시 예상"""
    pred = {}
    
    # QQQ(나스닥 100) 기술적 지표로 전체 시장 대변
    qqq_data = get_technical_indicators('QQQ')
    
    up_prob = 40
    down_prob = 40
    
    if qqq_data:
        rsi = qqq_data['rsi']
        macd_hist = qqq_data['macd_histogram']
        pred['qqq_rsi'] = rsi
        pred['macd_dir'] = "상승 📈" if macd_hist > 0 else "하락 📉"
        
        # 확률 계산 로직 (간단한 rule-based)
        if rsi < 40:
            up_prob += 15; down_prob -= 15
        elif rsi > 60:
            down_prob += 15; up_prob -= 15
            
        if macd_hist > 0:
            up_prob += 10; down_prob -= 10
        else:
            down_prob += 10; up_prob -= 10
    else:
        pred['qqq_rsi'] = 50.0
        pred['macd_dir'] = "데이터 없음"
            
    sentiment = market_summary.get('news_sentiment', 0)
    if sentiment > 0.2:
        up_prob += 10; down_prob -= 10
    elif sentiment < -0.2:
        down_prob += 10; up_prob -= 10
        
    up_prob = min(max(int(up_prob), 5), 90)
    down_prob = min(max(int(down_prob), 5), 90)
    flat_prob = 100 - up_prob - down_prob
    
    pred['probs'] = {'up': up_prob, 'down': down_prob, 'flat': flat_prob}
        
    # 리스크 요인
    risks = []
    vix_level = market_summary.get('indices', {}).get('VIX', {}).get('price', 0)
    
    if vix_level >= 20:
        risks.append(f"VIX 지수 상승 ({vix_level:.2f})에 따른 시장 변동성 확대")
    if sentiment <= -0.1:
        risks.append("부정적인 뉴스 센티멘트 확산 현상")
    if pred['qqq_rsi'] >= 70:
        risks.append(f"나스닥100 단기 과열 (RSI: {pred['qqq_rsi']:.1f}) 차익실현 매물 경계")
    if not risks:
        risks.append("특별한 거시적 리스크 없음")
        risks.append("기술적 주요 지지/저항선 부근 움직임 주시")
        
    pred['risks'] = risks[:3]
    return pred

def analyze(send_telegram=True):
    """메인 분석 함수"""
    market_summary = get_market_summary()
    tomorrow_pred = get_tomorrow_prediction(market_summary)
    results = []
    
    for ticker in SOXL_STOCKS:
        data = get_technical_indicators(ticker)
        if data is None:
            continue
        
        score, signals = calculate_score(data)
        squeeze_score, squeeze_signals = detect_short_squeeze_candidates(data)
        entry_status = evaluate_entry_status(data)
        
        # 총점 (일반 신호 + 숏스퀴즈)
        total_score = score + (squeeze_score * 0.5)  # 숏스퀴즈 가중치 50%
        
        if total_score > 0 or score > 0:
            results.append({
                'ticker': ticker,
                'data': data,
                'score': score,
                'squeeze_score': squeeze_score,
                'total_score': total_score,
                'signals': signals,
                'squeeze_signals': squeeze_signals,
                'entry_status': entry_status,
                'buy_price': data['price'],
                'target_price_1': data['price'] * 1.10,
                'target_price_2': data['price'] * 1.20,
                'stop_loss': data['price'] * 0.95,
                'risk_reward': 2.0
            })
    
    # === 상위 10개 선별 ===
    results.sort(key=lambda x: x['total_score'], reverse=True)
    top10 = results[:10]
    
    
    # === 메시지 생성 및 분할 ===
    messages = []
    
    msg_summary = f"📊 일일 증시 요약 & 예측\n"
    msg_summary += f"🕙 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} KST\n"
    msg_summary += f"{'='*60}\n\n"
    
    # 1. 증시 요약
    idx = market_summary.get('indices', {})
    msg_summary += "🌐 **전일 증시 동향**\n"
    if 'S&P500' in idx: msg_summary += f"S&P500: {idx['S&P500']['change']:+.2f}% | "
    if 'Nasdaq100' in idx: msg_summary += f"Nasdaq100: {idx['Nasdaq100']['change']:+.2f}%\n"
    if 'DowJones' in idx: msg_summary += f"DowJones: {idx['DowJones']['change']:+.2f}% | "
    if 'Russell2000' in idx: msg_summary += f"Russell2000: {idx['Russell2000']['change']:+.2f}%\n"
    
    msg_summary += f"\n😨 **VIX 공포지수**: {idx.get('VIX',{}).get('price',0):.2f} ({market_summary.get('vix_status', '데이터 없음')})\n\n"
    
    msg_summary += "📊 **섹터별 등락 현황**\n"
    top_sec = ", ".join([f"{s['name']}({s['change']:+.1f}%)" for s in market_summary.get('top_sectors', [])])
    bot_sec = ", ".join([f"{s['name']}({s['change']:+.1f}%)" for s in market_summary.get('bottom_sectors', [])])
    msg_summary += f"🟢 TOP 3: {top_sec if top_sec else '없음'}\n"
    msg_summary += f"🔴 BOTTOM 3: {bot_sec if bot_sec else '없음'}\n\n"
    
    msg_summary += "📅 **오늘 주요 일정/뉴스 (Alpha Vantage)**\n"
    for ev in market_summary.get('today_events', []):
        msg_summary += f"- {ev}\n"
    msg_summary += "\n"
    
    # 2. 내일 증시 예상
    msg_summary += "🤖 **AI 내일 증시 예상**\n"
    msg_summary += f"기대 방향성: 상승 {tomorrow_pred.get('probs',{}).get('up','-')}% / 하락 {tomorrow_pred.get('probs',{}).get('down','-')}% / 횡보 {tomorrow_pred.get('probs',{}).get('flat','-')}%\n"
    msg_summary += f"나스닥100 RSI: {tomorrow_pred.get('qqq_rsi',0):.1f} | MACD: {tomorrow_pred.get('macd_dir','-')} | 뉴스 감성: {market_summary.get('news_sentiment',0):.2f}\n"
    msg_summary += "⚠️ **주요 리스크 요인**\n"
    for r in tomorrow_pred.get('risks', []):
        msg_summary += f"- {r}\n"
    
    msg_summary += f"\n{'='*60}\n"
    messages.append(msg_summary)
    
    # 리포트 메시지 분할 작성
    current_msg = f"📊 SOXL 고급 분석 리포트 (TOP 10)\n\n"
    
    for i, result in enumerate(top10, 1):
        d = result['data']
        icon = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}️⃣"
        
        stock_msg = f"{icon} **{d['ticker']}** | ${d['price']:.2f}\n"
        stock_msg += f"상태: {result['entry_status']} | 변동: {d['change_1d']:+.2f}% (1d)\n"
        stock_msg += f"추천가: ${result['buy_price']:.2f} | 1차 목표가: ${result['target_price_1']:.2f} | 손절가: ${result['stop_loss']:.2f}\n"
        stock_msg += f"총점: {result['total_score']:.1f} | 위험수익비: {result['risk_reward']}배\n"
        stock_msg += f"\n📈 기술지표:\n"
        stock_msg += f"RSI: {d['rsi']:.1f} | MACD: {d['macd_histogram']:+.3f} | Stoch: {d['stoch_k']:.1f}\n"
        stock_msg += f"BB: {d['bb_lower']:.2f}~{d['bb_upper']:.2f} (폭: {d['bb_width']:.1f}%)\n"
        stock_msg += f"MA추세: {d['ma_trend']} | 거래량: {d['vol_ratio']:.0f}% | 52주: {d['price_to_52low']:.1f}%\n"
        stock_msg += f"\n🎯 신호:\n"
        for signal in result['signals'][:3]:  # 상위 3개만
            stock_msg += f"{signal}\n"
        if result['squeeze_signals']:
            stock_msg += f"\n🚀 숏스퀴즈:\n"
            for squeeze in result['squeeze_signals'][:2]:  # 상위 2개만
                stock_msg += f"{squeeze}\n"
        stock_msg += f"\n{'─'*60}\n"
        
        if len(current_msg) + len(stock_msg) > 3500:
            messages.append(current_msg)
            current_msg = stock_msg
        else:
            current_msg += stock_msg
    
    current_msg += f"\n📌 면책: 기계 학습 기반 분석이며, 투자 조언이 아닙니다.\n"
    messages.append(current_msg)
    
    # === 텔레그램 전송 ===
    if send_telegram:
        for m in messages:
            try:
                res = requests.post(TELEGRAM_API, json={'chat_id': CHAT_ID, 'text': m, 'parse_mode': 'Markdown'})
                if res.status_code != 200:
                    print(f"텔레그램 분할 전송 실패: {res.status_code} - {res.text}")
            except Exception as e:
                print(f"텔레그램 전송 예외 발생: {e}")
        print("분석 완료 및 텔레그램 분할 전송 수행!")
            
    # === 기록 저장 ===
    history_dir = "history"
    if not os.path.exists(history_dir):
        os.makedirs(history_dir)
        
    today_str = datetime.now().strftime('%Y-%m-%d')
    history_file = os.path.join(history_dir, f"{today_str}.json")
    
    try:
        save_data = {
            "market_summary": market_summary,
            "tomorrow_pred": tomorrow_pred,
            "top10": top10
        }
        with open(history_file, "w", encoding='utf-8') as f:
            json.dump(save_data, f, ensure_ascii=False, indent=4, cls=NpEncoder)
    except Exception as e:
        print(f"Error saving history: {e}")
        
    return save_data

if __name__ == "__main__":
    analyze(send_telegram=True)
