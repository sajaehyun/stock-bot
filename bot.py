import requests, yfinance as yf
from datetime import datetime

TELEGRAM_TOKEN = "8475611635:AAFYDJ48HdVJyBctnsr9Sl3CLW-4JWk_jmE"
CHAT_ID = "8630004087"
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

SOXL_STOCKS = ["MU","NVDA","AMAT","AMD","AVGO","QCOM","INTC","ON","MCHP","NXPI","MRVL","SNDK","LRCX","KLAC","ASML","TXN","ADI","SLAB","SWKS","MPWR","ONTO","RCLK","PLOW","ICHR","MANH","FORM","COHR","MATH","CAVM","RMBS"]

def get_rsi(prices):
    delta = prices.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def analyze():
    results = []
    for ticker in SOXL_STOCKS:
        try:
            hist = yf.Ticker(ticker).history(period="3mo")
            if len(hist) < 14:
                continue
            cur = hist['Close'].iloc[-1]
            prev = hist['Close'].iloc[-2]
            rsi = get_rsi(hist['Close']).iloc[-1]
            vol_ratio = (hist['Volume'].iloc[-1] / hist['Volume'].rolling(20).mean().iloc[-1]) * 100
            change = ((cur - prev) / prev) * 100
            score = 0
            if rsi < 30:
                score += 25
            if vol_ratio > 150:
                score += 15
            if score:
                results.append((score, ticker, cur, rsi, change, vol_ratio))
        except:
            pass
    results.sort(reverse=True)
    top5 = results[:5]
    msg = f"📊 {datetime.now().strftime('%Y-%m-%d %H:%M')} SOXL 자동 분석\n\n"
    for i, (s, t, p, r, ch, v) in enumerate(top5, 1):
        icon = "🔴" if i == 1 else "🟡" if i == 2 else "🟢"
        trend = "📈" if ch > 0 else "📉"
        msg += f"{icon} {i}위: {t}\n가격: ${p:.2f} {trend} {ch:+.2f}%\nRSI: {r:.1f} | 거래량: {v:.0f}%\n점수: {s}\n\n"
    requests.post(TELEGRAM_API, json={'chat_id': CHAT_ID, 'text': msg})

analyze()
