import os
import json
import time
import requests
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor, as_completed
from bs4 import BeautifulSoup

load_dotenv()

# ─────────────────────────────────────────────
# 환경변수
# ─────────────────────────────────────────────
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID        = os.getenv("CHAT_ID")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    )
}

# ─────────────────────────────────────────────
# 1. Finviz 스크리너 – S&P 500 상승률 Top 30 스크랩
# ─────────────────────────────────────────────
def fetch_finviz_sp500_gainers(top_n: int = 30) -> list[dict]:
    url = (
        "https://finviz.com/screener.ashx"
        "?v=111&f=idx_sp500&o=-change&r=1"
    )
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        print(f"[Finviz] 요청 실패: {e}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    table = soup.find("table", {"id": lambda x: x and "screener" in x.lower()})
    if table is None:
        rows = soup.select("tr[valign]") or soup.select("tbody tr")
    else:
        rows = table.select("tbody tr")

    results = []
    for row in rows:
        cols = row.find_all("td")
        if len(cols) < 10:
            continue
        try:
            ticker     = cols[1].get_text(strip=True)
            company    = cols[2].get_text(strip=True)
            sector     = cols[3].get_text(strip=True)
            industry   = cols[4].get_text(strip=True)
            market_cap = cols[6].get_text(strip=True)
            pe         = cols[7].get_text(strip=True)
            price_txt  = cols[8].get_text(strip=True)
            change_txt = cols[9].get_text(strip=True)
            volume_txt = cols[10].get_text(strip=True) if len(cols) > 10 else "0"

            price  = float(price_txt.replace(",", "")) if price_txt not in ("-", "") else 0.0
            change = float(change_txt.replace("%", "").replace(",", "")) if change_txt not in ("-", "") else 0.0
            volume = int(volume_txt.replace(",", "")) if volume_txt not in ("-", "") else 0

            if ticker and price > 0:
                results.append({
                    "ticker":     ticker,
                    "company":    company,
                    "sector":     sector,
                    "industry":   industry,
                    "price":      price,
                    "change":     change,
                    "volume":     volume,
                    "pe":         pe,
                    "market_cap": market_cap,
                })
        except (ValueError, IndexError):
            continue

        if len(results) >= top_n:
            break

    print(f"[Finviz] {len(results)}개 종목 수집 완료")
    return results


# ─────────────────────────────────────────────
# 2. 기술 지표 계산 (yfinance)
# ─────────────────────────────────────────────
def safe_float(val, default=0.0):
    try:
        v = float(val)
        return v if not np.isnan(v) else default
    except Exception:
        return default


def compute_indicators(ticker: str) -> dict | None:
    try:
        df = yf.download(ticker, period="60d", interval="1d", progress=False, auto_adjust=True)
        if df is None or len(df) < 20:
            return None

        df = df.copy()
        close = df["Close"].squeeze()
        high  = df["High"].squeeze()
        low   = df["Low"].squeeze()
        vol   = df["Volume"].squeeze()

        # RSI
        delta   = close.diff()
        gain    = delta.clip(lower=0).rolling(14).mean()
        loss    = (-delta.clip(upper=0)).rolling(14).mean()
        rs      = gain / loss.replace(0, np.nan)
        rsi_val = safe_float(100 - (100 / (1 + rs)).iloc[-1])

        # MACD
        ema12     = close.ewm(span=12, adjust=False).mean()
        ema26     = close.ewm(span=26, adjust=False).mean()
        macd_line = ema12 - ema26
        signal    = macd_line.ewm(span=9, adjust=False).mean()
        hist      = macd_line - signal
        macd_hist      = safe_float(hist.iloc[-1])
        macd_hist_prev = safe_float(hist.iloc[-2]) if len(hist) > 1 else macd_hist

        # Stochastic
        low14  = low.rolling(14).min()
        high14 = high.rolling(14).max()
        k_raw  = 100 * (close - low14) / (high14 - low14).replace(0, np.nan)
        stoch_k      = safe_float(k_raw.rolling(3).mean().iloc[-1])
        stoch_k_prev = safe_float(k_raw.rolling(3).mean().iloc[-2]) if len(k_raw) > 1 else stoch_k

        # Bollinger Bands
        ma20    = close.rolling(20).mean()
        std20   = close.rolling(20).std()
        bb_upper_val = safe_float((ma20 + 2 * std20).iloc[-1])
        bb_lower_val = safe_float((ma20 - 2 * std20).iloc[-1])
        ma20_val     = safe_float(ma20.iloc[-1])

        # MA Trends
        ma50  = safe_float(close.rolling(50).mean().iloc[-1]) if len(close) >= 50 else 0.0
        ma200 = safe_float(close.rolling(200).mean().iloc[-1]) if len(close) >= 200 else 0.0
        ma_trend = "골든크로스" if ma50 > ma200 and ma200 > 0 else ("데드크로스" if ma50 < ma200 and ma200 > 0 else "중립")

        # Volume
        avg_vol   = safe_float(vol.rolling(20).mean().iloc[-1])
        vol_ratio = (safe_float(vol.iloc[-1]) / avg_vol * 100) if avg_vol > 0 else 100.0

        # Change
        change_1d = safe_float((close.iloc[-1] - close.iloc[-2]) / close.iloc[-2] * 100) if len(close) > 1 else 0.0

        # Ichimoku
        nine_high  = high.rolling(9).max()
        nine_low   = low.rolling(9).min()
        tenkan     = (nine_high + nine_low) / 2
        kijun      = (high.rolling(26).max() + low.rolling(26).min()) / 2
        span_a     = ((tenkan + kijun) / 2).shift(26)
        span_b     = ((high.rolling(52).max() + low.rolling(52).min()) / 2).shift(26)
        price_now  = safe_float(close.iloc[-1])
        sa_val     = safe_float(span_a.iloc[-1])
        sb_val     = safe_float(span_b.iloc[-1])
        is_above_cloud = price_now > max(sa_val, sb_val)
        is_below_cloud = price_now < min(sa_val, sb_val)

        return {
            "price":          price_now,
            "rsi":            rsi_val,
            "macd_histogram": macd_hist,
            "macd_hist_prev": macd_hist_prev,
            "stoch_k":        stoch_k,
            "stoch_k_prev":   stoch_k_prev,
            "ma20":           ma20_val,
            "bb_upper":       bb_upper_val,
            "bb_lower":       bb_lower_val,
            "ma50":           ma50,
            "ma200":          ma200,
            "ma_trend":       ma_trend,
            "vol_ratio":      vol_ratio,
            "change_1d":      change_1d,
            "is_above_cloud": is_above_cloud,
            "is_below_cloud": is_below_cloud,
        }
    except Exception as e:
        print(f"[{ticker}] 지표 계산 오류: {e}")
        return None


# ─────────────────────────────────────────────
# 3. 진입 조건 평가
# ─────────────────────────────────────────────
def evaluate_entry_status(data: dict) -> str:
    if data["is_below_cloud"] and data["rsi"] < 25:
        return "❌ 회피"
    
    score_entry = sum([
        30 <= data["rsi"] <= 72,
        data["macd_histogram"] > data["macd_hist_prev"],
        data["stoch_k"] > data["stoch_k_prev"],
        data["price"] > data["ma20"],
        data["vol_ratio"] >= 100,
        data["price"] < data["bb_upper"] and data["rsi"] > 30 and data["change_1d"] > 0,
        "골든크로스" in data["ma_trend"],
        data["is_above_cloud"]
    ])

    if score_entry >= 5: return "🟢 진입 가능"
    if score_entry >= 3: return "⏳ 대기"
    return "❌ 회피"


# ─────────────────────────────────────────────
# 4. 복합 점수 계산
# ─────────────────────────────────────────────
def compute_score(ind: dict, fv: dict) -> tuple[int, list[str]]:
    score = 0
    sigs = []
    if 40 <= ind["rsi"] <= 65: score += 15; sigs.append(f"RSI 적정 {ind['rsi']:.1f}")
    elif ind["rsi"] < 35: score += 8; sigs.append(f"RSI 과매도 반등 {ind['rsi']:.1f}")
    
    if ind["macd_histogram"] > 0 and ind["macd_histogram"] > ind["macd_hist_prev"]:
        score += 15; sigs.append("MACD 상승 전환 ✅")
    
    if ind["price"] > ind["ma20"]: score += 10; sigs.append("MA20 위")
    if "골든크로스" in ind["ma_trend"]: score += 15; sigs.append("골든크로스 ✅")
    if ind["vol_ratio"] >= 150: score += 10; sigs.append(f"거래량 급증 {ind['vol_ratio']:.0f}%")
    if ind["is_above_cloud"]: score += 10; sigs.append("구름대 위 ✅")
    if fv["change"] >= 1.5: score += 10; sigs.append(f"당일 강세 +{fv['change']:.1f}%")
    
    return score, sigs


# ─────────────────────────────────────────────
# 5. 분석 실행
# ─────────────────────────────────────────────
def analyze_ticker(fv: dict) -> dict | None:
    ind = compute_indicators(fv["ticker"])
    if not ind: return None
    entry = evaluate_entry_status(ind)
    score, sigs = compute_score(ind, fv)
    
    res = {**fv, **ind, "entry": entry, "score": score, "signals": sigs}
    res["target1"] = round(res["price"] * 1.10, 2)
    res["target2"] = round(res["price"] * 1.20, 2)
    res["stop_loss"] = round(res["price"] * 0.95, 2)
    return res


def send_telegram(text: str):
    if not (TELEGRAM_TOKEN and CHAT_ID): return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try: requests.post(url, data={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}, timeout=10)
    except: pass


def analyze(send_msg: bool = True):
    print(f"🚀 분석 시작: {datetime.now()}")
    fv_list = fetch_finviz_sp500_gainers(30)
    if not fv_list: return {"results": []}

    results = []
    with ThreadPoolExecutor(max_workers=10) as exe:
        futures = [exe.submit(analyze_ticker, fv) for fv in fv_list]
        for f in as_completed(futures):
            r = f.result()
            if r: results.append(r)
    
    results.sort(key=lambda x: x["score"], reverse=True)
    top20 = results[:20]

    # Telegram 요약 전송
    if send_msg and top20:
        msg = f"📊 <b>Stock-Bot Top 20 ({datetime.now().strftime('%Y-%m-%d')})</b>\n"
        for i, r in enumerate(top20[:10], 1):
            msg += f"{i}. <b>{r['ticker']}</b> {r['entry']} (점수:{r['score']})\n"
        send_telegram(msg)

    # 히스토리 저장
    os.makedirs("history", exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    save_data = {
        "analyzed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "results": top20
    }
    with open(f"history/{today}.json", "w", encoding="utf-8") as f:
        json.dump(save_data, f, ensure_ascii=False, indent=2)

    return save_data


if __name__ == "__main__":
    analyze(True)
