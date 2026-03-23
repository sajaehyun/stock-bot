import os, json
import requests, yfinance as yf, pandas as pd, numpy as np
from datetime import datetime
from dotenv import load_dotenv
import concurrent.futures

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

def get_sp500_tickers():
    try:
        url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
        tickers = pd.read_html(url, storage_options={'User-Agent': 'Mozilla/5.0'})[0]['Symbol'].tolist()
        tickers = [t.replace('.', '-') for t in tickers]
        return tickers
    except Exception as e:
        print(f"Error fetching S&P 500 tickers: {e}")
        return ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN"]

def get_technical_indicators(ticker):
    try:
        hist = yf.Ticker(ticker).history(period="6mo")
        if len(hist) < 30:
            return None
        close = hist['Close'].astype(float)
        volume = hist['Volume'].astype(float)
        high = hist['High'].astype(float)
        low = hist['Low'].astype(float)

        # RSI
        delta = close.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        rsi_val = float(rsi.iloc[-1]) if not pd.isna(rsi.iloc[-1]) else 50.0

        # MACD
        ema12 = close.ewm(span=12).mean()
        ema26 = close.ewm(span=26).mean()
        macd = ema12 - ema26
        signal = macd.ewm(span=9).mean()
        macd_histogram = macd - signal
        macd_val = float(macd_histogram.iloc[-1]) if not pd.isna(macd_histogram.iloc[-1]) else 0.0
        macd_hist_prev = float(macd_histogram.iloc[-2]) if len(macd_histogram) > 1 and not pd.isna(macd_histogram.iloc[-2]) else 0.0

        # Stochastic
        low14 = close.rolling(window=14).min()
        high14 = close.rolling(window=14).max()
        stoch_k = 100 * (close - low14) / (high14 - low14)
        stoch_d = stoch_k.rolling(window=3).mean()
        stoch_k_val = float(stoch_k.iloc[-1]) if not pd.isna(stoch_k.iloc[-1]) else 50.0
        stoch_d_val = float(stoch_d.iloc[-1]) if not pd.isna(stoch_d.iloc[-1]) else 50.0
        stoch_k_prev = float(stoch_k.iloc[-2]) if len(stoch_k) > 1 and not pd.isna(stoch_k.iloc[-2]) else 50.0

        # Bollinger Bands
        bb_mid = close.rolling(window=20).mean()
        bb_std = close.rolling(window=20).std()
        bb_upper = bb_mid + (bb_std * 2)
        bb_lower = bb_mid - (bb_std * 2)
        bb_width = ((bb_upper - bb_lower) / bb_mid * 100)
        bb_upper_val = float(bb_upper.iloc[-1]) if not pd.isna(bb_upper.iloc[-1]) else float(close.iloc[-1])
        bb_lower_val = float(bb_lower.iloc[-1]) if not pd.isna(bb_lower.iloc[-1]) else float(close.iloc[-1])
        bb_width_val = float(bb_width.iloc[-1]) if not pd.isna(bb_width.iloc[-1]) else 15.0

        # 거래량
        vol_avg = volume.rolling(window=20).mean()
        vol_ratio = (volume.iloc[-1] / vol_avg.iloc[-1]) * 100 if vol_avg.iloc[-1] > 0 else 100.0
        vol_trend = "증가" if volume.iloc[-1] > vol_avg.iloc[-1] else "감소"

        # 52주
        high52 = close.rolling(window=252).max()
        low52 = close.rolling(window=252).min()
        current_price = float(close.iloc[-1])
        high52_val = float(high52.iloc[-1]) if not pd.isna(high52.iloc[-1]) else current_price
        low52_val = float(low52.iloc[-1]) if not pd.isna(low52.iloc[-1]) else current_price
        price_to_52high = (current_price / high52_val) * 100 if high52_val > 0 else 100.0
        price_to_52low_pct = ((current_price - low52_val) / (high52_val - low52_val)) * 100 if (high52_val - low52_val) > 0 else 50.0
        price_to_52low_pct = max(0, min(100, price_to_52low_pct))

        # Fibonacci
        fib_range = high52_val - low52_val
        fib_382 = high52_val - (fib_range * 0.382)
        fib_500 = high52_val - (fib_range * 0.500)
        fib_618 = high52_val - (fib_range * 0.618)

        # Elliott Wave
        recent_3d = close.iloc[-3:].values
        is_wave_bottom = (recent_3d[0] > recent_3d[1]) and (recent_3d[1] < recent_3d[2])

        # 이동평균
        ma5 = float(close.rolling(window=5).mean().iloc[-1])
        ma20 = float(close.rolling(window=20).mean().iloc[-1])
        ma60 = float(close.rolling(window=60).mean().iloc[-1])
        ma200 = float(close.rolling(window=200).mean().iloc[-1]) if len(close) >= 200 else ma60

        if ma5 > ma20 > ma60:
            ma_trend = "🏆 골든크로스"
        elif ma5 < ma20 < ma60:
            ma_trend = "💀 데드크로스"
        else:
            ma_trend = "➡️ 중립"

        # Ichimoku
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

        # 변동률
        change_1d = ((close.iloc[-1] - close.iloc[-2]) / close.iloc[-2]) * 100 if close.iloc[-2] > 0 else 0.0
        change_5d = ((close.iloc[-1] - close.iloc[-5]) / close.iloc[-5]) * 100 if close.iloc[-5] > 0 else 0.0
        change_20d = ((close.iloc[-1] - close.iloc[-20]) / close.iloc[-20]) * 100 if close.iloc[-20] > 0 else 0.0

        return {
            'ticker': ticker, 'price': current_price,
            'rsi': rsi_val, 'macd_histogram': macd_val, 'macd_hist_prev': macd_hist_prev,
            'stoch_k': stoch_k_val, 'stoch_k_prev': stoch_k_prev, 'stoch_d': stoch_d_val,
            'bb_upper': bb_upper_val, 'bb_lower': bb_lower_val, 'bb_width': bb_width_val,
            'vol_ratio': vol_ratio, 'vol_trend': vol_trend,
            'price_to_52high': price_to_52high, 'price_to_52low': price_to_52low_pct,
            'fib_382': fib_382, 'fib_500': fib_500, 'fib_618': fib_618,
            'is_wave_bottom': is_wave_bottom,
            'ma5': ma5, 'ma20': ma20, 'ma60': ma60, 'ma200': ma200, 'ma_trend': ma_trend,
            'change_1d': change_1d, 'change_5d': change_5d, 'change_20d': change_20d,
            'cloud_top': cloud_top, 'cloud_bottom': cloud_bottom,
            'is_above_cloud': is_above_cloud, 'is_below_cloud': is_below_cloud,
        }
    except Exception as e:
        print(f"Error processing {ticker}: {e}")
        return None

def calculate_score(data):
    score = 0
    signals = []
    if data['rsi'] < 20:
        score += 30; signals.append("🔴 극도 과매도 (RSI<20)")
    elif data['rsi'] < 30:
        score += 20; signals.append("🟠 과매도 (RSI<30)")
    elif data['rsi'] > 80:
        score -= 10; signals.append("⚠️ 과매수 (RSI>80)")
    if data['macd_histogram'] > 0:
        score += 15; signals.append("📈 MACD 양수 (상승 추세)")
    elif data['macd_histogram'] < -0.5:
        score -= 10; signals.append("📉 MACD 음수 (하락 추세)")
    if data['stoch_k'] < 20:
        score += 25; signals.append("🔵 Stochastic 극저점 (숏스퀴즈 신호)")
    elif data['stoch_k'] < 50 and data['stoch_d'] < data['stoch_k']:
        score += 15; signals.append("🟢 Stochastic 상승 중")
    if data['price'] < data['bb_lower']:
        score += 20; signals.append("🎯 BB 하단 돌파 (반등 신호)")
    elif data['bb_width'] < 10:
        score += 15; signals.append("⚡ BB 폭 축소 (변동성 확대 예상)")
    if data['vol_ratio'] > 200:
        score += 20; signals.append("💥 거래량 폭증 (200% 이상)")
    elif data['vol_ratio'] > 150:
        score += 15; signals.append("📊 거래량 증가 (150% 이상)")
    if data['is_wave_bottom']:
        score += 25; signals.append("🌊 Elliott Wave 바닥 형성")
    if data['price'] < data['fib_382']:
        score += 15; signals.append("📍 Fib 38.2% 지지선 근처")
    if "골든크로스" in data['ma_trend']:
        score += 20; signals.append("🏆 골든크로스 (강한 상승신호)")
    elif "데드크로스" in data['ma_trend']:
        score -= 15; signals.append("💀 데드크로스")
    if data['price_to_52low'] < 30:
        score += 15; signals.append(f"📌 52주 저가 근처 ({data['price_to_52low']:.1f}%)")
    elif data['price_to_52high'] > 90:
        score -= 10; signals.append(f"⚠️ 52주 고가 근처 ({data['price_to_52high']:.1f}%)")
    if data['change_1d'] < -3:
        score += 15; signals.append(f"🔥 당일 급락 ({data['change_1d']:.2f}%)")
    if data['change_5d'] < -10:
        score += 15; signals.append(f"⬇️ 5일 큰 낙폭 ({data['change_5d']:.2f}%)")
    if data['is_above_cloud']:
        score += 15; signals.append("☁️ 구름대 돌파 (상승 추세)")
    if data['is_below_cloud']:
        score -= 50; signals.append("❌ 구름대 아래 (진입 금지)")
    return max(0, score), signals

def detect_short_squeeze_candidates(data):
    squeeze_score = 0
    squeeze_signals = []
    if data['rsi'] < 20:
        squeeze_score += 40; squeeze_signals.append("🚀 RSI 극저 (숏 비중 높음)")
    if data['vol_ratio'] > 200 and data['change_1d'] < -5:
        squeeze_score += 40; squeeze_signals.append("💥 거래량 폭증 + 급락 (숏 청산 신호)")
    if data['price_to_52low'] < 20:
        squeeze_score += 30; squeeze_signals.append("⚠️ 52주 극저가 (매수 기회)")
    if data['price'] < data['bb_lower']:
        squeeze_score += 25; squeeze_signals.append("🎯 BB 하단 이탈 (반등 신호)")
    if data['macd_histogram'] < 0 and data['stoch_k'] < 30:
        squeeze_score += 30; squeeze_signals.append("📊 MACD 음수 + Stochastic 극저 (바닥 신호)")
    return squeeze_score, squeeze_signals

def evaluate_entry_status(data):
    cond_cloud = data['is_above_cloud']
    cond_rsi = 30 <= data['rsi'] <= 70
    cond_macd = data['macd_histogram'] > data['macd_hist_prev']
    cond_stoch = (30 <= data['stoch_k'] <= 70) or (data['stoch_k'] > data['stoch_k_prev'])
    cond_ma20 = data['price'] > data['ma20']
    cond_vol = data['vol_ratio'] >= 100
    if data['is_below_cloud']:
        return "❌ 회피"
    conds = [cond_cloud, cond_rsi, cond_macd, cond_stoch, cond_ma20, cond_vol]
    passed = sum(conds)
    if passed >= 5:
        return "🟢 진입 가능"
    if cond_cloud and passed >= 3:
        return "🟡 선택"
    return "⏳ 대기"

class NpEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, np.integer): return int(obj)
        if isinstance(obj, np.floating): return float(obj)
        if isinstance(obj, np.ndarray): return obj.tolist()
        if isinstance(obj, (bool, np.bool_)): return bool(obj)
        return super(NpEncoder, self).default(obj)

def get_market_summary():
    summary = {}
    indices = {
        'S&P500': '^GSPC', 'Nasdaq100': '^NDX',
        'DowJones': '^DJI', 'Russell2000': '^RUT', 'VIX': '^VIX'
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

    vix_level = idx_data.get('VIX', {}).get('price', 0)
    if vix_level < 15: summary['vix_status'] = "🟢 안정적 (변동성 낮음)"
    elif vix_level < 20: summary['vix_status'] = "🟡 보통 (정상적인 시장)"
    elif vix_level < 30: summary['vix_status'] = "🟠 경계 (변동성 확대)"
    else: summary['vix_status'] = "🔴 공포 (극심한 변동성)"

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
            for item in data['feed'][:2]:
                events.append(item.get('title', 'Unknown News'))
        summary['news_sentiment'] = sentiment_score
        summary['today_events'] = events if events else ["(AlphaVantage) 주요 뉴스/일정 없음"]
    except:
        summary['news_sentiment'] = 0.15
        summary['today_events'] = ["데이터를 불러올 수 없습니다."]
    return summary

def get_tomorrow_prediction(market_summary):
    pred = {}
    qqq_data = get_technical_indicators('QQQ')
    tqqq_data = get_technical_indicators('TQQQ')
    sqqq_data = get_technical_indicators('SQQQ')

    up_prob = 40
    down_prob = 40

    if qqq_data:
        rsi = qqq_data['rsi']
        macd_hist = qqq_data['macd_histogram']
        macd_prev = qqq_data['macd_hist_prev']
        stoch = qqq_data['stoch_k']
        is_above = qqq_data['is_above_cloud']
        ma_trend = qqq_data['ma_trend']

        pred['qqq_rsi'] = rsi
        pred['qqq_price'] = qqq_data['price']
        pred['macd_dir'] = "상승 📈" if macd_hist > macd_prev else "하락 📉"

        if rsi < 35: up_prob += 20; down_prob -= 20
        elif rsi < 45: up_prob += 10; down_prob -= 10
        elif rsi > 65: down_prob += 20; up_prob -= 20
        elif rsi > 55: down_prob += 10; up_prob -= 10

        if macd_hist > 0 and macd_hist > macd_prev:
            up_prob += 15; down_prob -= 15
        elif macd_hist < 0 and macd_hist < macd_prev:
            down_prob += 15; up_prob -= 15

        if is_above: up_prob += 10; down_prob -= 10
        else: down_prob += 15; up_prob -= 15

        if "골든크로스" in ma_trend: up_prob += 10; down_prob -= 10
        elif "데드크로스" in ma_trend: down_prob += 10; up_prob -= 10

        if stoch < 30: up_prob += 10; down_prob -= 10
        elif stoch > 70: down_prob += 10; up_prob -= 10
    else:
        pred['qqq_rsi'] = 50.0
        pred['qqq_price'] = 0
        pred['macd_dir'] = "데이터 없음"

    sentiment = market_summary.get('news_sentiment', 0)
    if sentiment > 0.2: up_prob += 10; down_prob -= 10
    elif sentiment < -0.2: down_prob += 10; up_prob -= 10

    vix = market_summary.get('indices', {}).get('VIX', {}).get('price', 0)
    if vix > 30: down_prob += 10; up_prob -= 10
    elif vix < 15: up_prob += 10; down_prob -= 10

    up_prob = min(max(int(up_prob), 5), 90)
    down_prob = min(max(int(down_prob), 5), 90)
    flat_prob = max(0, 100 - up_prob - down_prob)
    pred['probs'] = {'up': up_prob, 'down': down_prob, 'flat': flat_prob}

    # TQQQ/SQQQ 추천
    if up_prob >= 60:
        etf_rec = "TQQQ"
        etf_icon = "💚"
        etf_reason = f"상승 확률 {up_prob}% — 나스닥 3배 상승 ETF 진입 추천"
        etf_data = tqqq_data
    elif down_prob >= 60:
        etf_rec = "SQQQ"
        etf_icon = "🔴"
        etf_reason = f"하락 확률 {down_prob}% — 나스닥 3배 하락 ETF 진입 추천"
        etf_data = sqqq_data
    else:
        etf_rec = "관망"
        etf_icon = "⏳"
        etf_reason = f"상승 {up_prob}% / 하락 {down_prob}% — 방향성 불명확, 관망 추천"
        etf_data = None

    pred['etf_rec'] = etf_rec
    pred['etf_icon'] = etf_icon
    pred['etf_reason'] = etf_reason

    if etf_data:
        pred['etf_price'] = etf_data['price']
        pred['etf_target1'] = etf_data['price'] * 1.15
        pred['etf_target2'] = etf_data['price'] * 1.25
        pred['etf_stoploss'] = etf_data['price'] * 0.93
    else:
        pred['etf_price'] = 0
        pred['etf_target1'] = 0
        pred['etf_target2'] = 0
        pred['etf_stoploss'] = 0

    risks = []
    if vix >= 25: risks.append(f"VIX {vix:.1f} — 극심한 변동성 구간 (포지션 축소 고려)")
    if sentiment <= -0.1: risks.append("뉴스 감성 부정적 — 갑작스러운 하락 가능성")
    if pred['qqq_rsi'] >= 70: risks.append(f"QQQ RSI {pred['qqq_rsi']:.1f} — 단기 과열")
    if pred['qqq_rsi'] <= 30: risks.append(f"QQQ RSI {pred['qqq_rsi']:.1f} — 극도 과매도 (반등 가능)")
    if not risks:
        risks.append("특별한 리스크 없음 — 정상적인 시장 상황")
        risks.append("지지/저항선 부근 움직임 주시 필요")
    pred['risks'] = risks[:3]
    return pred

def analyze_single_ticker(ticker):
    data = get_technical_indicators(ticker)
    if data is None:
        return None
    score, signals = calculate_score(data)
    squeeze_score, squeeze_signals = detect_short_squeeze_candidates(data)
    entry_status = evaluate_entry_status(data)
    total_score = score + (squeeze_score * 0.5)
    if "🟢 진입 가능" in entry_status:
        return {
            'ticker': ticker, 'data': data,
            'score': score, 'squeeze_score': squeeze_score, 'total_score': total_score,
            'signals': signals, 'squeeze_signals': squeeze_signals, 'entry_status': entry_status,
            'buy_price': data['price'],
            'target_price_1': data['price'] * 1.10,
            'target_price_2': data['price'] * 1.20,
            'stop_loss': data['price'] * 0.95,
            'risk_reward': 2.0
        }
    return None

def analyze(send_telegram=True):
    market_summary = get_market_summary()
    tomorrow_pred = get_tomorrow_prediction(market_summary)

    tickers = get_sp500_tickers()
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        futures = {executor.submit(analyze_single_ticker, ticker): ticker for ticker in tickers}
        for future in concurrent.futures.as_completed(futures):
            try:
                res = future.result()
                if res is not None:
                    results.append(res)
            except Exception as e:
                print(f"Error: {e}")

    results.sort(key=lambda x: x['total_score'], reverse=True)
    top10 = results[:10]

    messages = []

    # ── 증시 요약 메시지 ──
    idx = market_summary.get('indices', {})
    msg_summary = f"📊 일일 증시 요약 & 예측\n"
    msg_summary += f"🕙 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} KST\n"
    msg_summary += f"{'='*50}\n\n"
    msg_summary += "🌐 전일 증시 동향\n"
    msg_summary += f"S&P500: {idx.get('S&P500',{}).get('change',0):+.2f}% | Nasdaq100: {idx.get('Nasdaq100',{}).get('change',0):+.2f}%\n"
    msg_summary += f"DowJones: {idx.get('DowJones',{}).get('change',0):+.2f}% | Russell2000: {idx.get('Russell2000',{}).get('change',0):+.2f}%\n"
    msg_summary += f"😨 VIX: {idx.get('VIX',{}).get('price',0):.2f} ({market_summary.get('vix_status','')})\n\n"

    top_sec = ", ".join([f"{s['name']}({s['change']:+.1f}%)" for s in market_summary.get('top_sectors', [])])
    bot_sec = ", ".join([f"{s['name']}({s['change']:+.1f}%)" for s in market_summary.get('bottom_sectors', [])])
    msg_summary += f"🟢 상위섹터: {top_sec or '없음'}\n"
    msg_summary += f"🔴 하위섹터: {bot_sec or '없음'}\n\n"

    msg_summary += "📅 주요 뉴스\n"
    for ev in market_summary.get('today_events', []):
        msg_summary += f"- {ev}\n"

    msg_summary += f"\n🤖 AI 내일 증시 예상\n"
    msg_summary += f"상승 {tomorrow_pred['probs']['up']}% / 하락 {tomorrow_pred['probs']['down']}% / 횡보 {tomorrow_pred['probs']['flat']}%\n"
    msg_summary += f"QQQ RSI: {tomorrow_pred.get('qqq_rsi',0):.1f} | MACD: {tomorrow_pred.get('macd_dir','')}\n"
    for r in tomorrow_pred.get('risks', []):
        msg_summary += f"⚠️ {r}\n"

    # ── TQQQ/SQQQ 추천 ──
    etf_rec = tomorrow_pred.get('etf_rec', '관망')
    etf_icon = tomorrow_pred.get('etf_icon', '⏳')
    etf_price = tomorrow_pred.get('etf_price', 0)
    msg_summary += f"\n{'='*50}\n"
    msg_summary += f"{etf_icon} 나스닥 ETF 추천: {etf_rec}\n"
    msg_summary += f"{tomorrow_pred.get('etf_reason','')}\n"
    if etf_price > 0:
        msg_summary += f"현재가: ${etf_price:.2f}\n"
        msg_summary += f"목표가1 (+15%): ${tomorrow_pred.get('etf_target1',0):.2f}\n"
        msg_summary += f"목표가2 (+25%): ${tomorrow_pred.get('etf_target2',0):.2f}\n"
        msg_summary += f"손절가 (-7%): ${tomorrow_pred.get('etf_stoploss',0):.2f}\n"
    msg_summary += f"{'='*50}\n"
    messages.append(msg_summary)

    # ── 종목 추천 메시지 ──
    current_msg = "📊 S&P500 추천 종목 TOP 10\n\n"
    if not top10:
        current_msg += "📌 오늘 진입 가능한 추천 종목이 없습니다.\n"
    else:
        for i, result in enumerate(top10, 1):
            d = result['data']
            icon = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}위"
            stock_msg = f"{icon} {d['ticker']} | ${d['price']:.2f} ({d['change_1d']:+.2f}%)\n"
            stock_msg += f"추천가: ${result['buy_price']:.2f} | 목표1: ${result['target_price_1']:.2f} | 손절: ${result['stop_loss']:.2f}\n"
            stock_msg += f"점수: {result['total_score']:.1f} | RSI: {d['rsi']:.1f} | Stoch: {d['stoch_k']:.1f}\n"
            stock_msg += f"MA: {d['ma_trend']} | 거래량: {d['vol_ratio']:.0f}%\n"
            for sig in result['signals'][:3]:
                stock_msg += f"{sig}\n"
            stock_msg += f"{'─'*40}\n"
            if len(current_msg) + len(stock_msg) > 3500:
                messages.append(current_msg)
                current_msg = stock_msg
            else:
                current_msg += stock_msg

    current_msg += "\n📌 투자 조언 아님. 본인 판단으로 투자하세요.\n"
    messages.append(current_msg)

    if send_telegram:
        for m in messages:
            try:
                res = requests.post(TELEGRAM_API, json={
                    'chat_id': CHAT_ID, 'text': m, 'parse_mode': 'Markdown'
                })
                if res.status_code != 200:
                    print(f"텔레그램 전송 실패: {res.status_code} - {res.text}")
            except Exception as e:
                print(f"텔레그램 전송 오류: {e}")
        print("분석 완료 및 텔레그램 전송!")

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
        print(f"히스토리 저장 오류: {e}")
    return save_data

if __name__ == "__main__":
    analyze(send_telegram=True)
