import os
import json
import time
import random
import requests
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    from finvizfinance.screener.overview import Overview
    FINVIZ_AVAILABLE = True
except ImportError:
    FINVIZ_AVAILABLE = False
    print("[경고] finvizfinance 미설치 → pip install finvizfinance")

load_dotenv()

# ─────────────────────────────────────────────
# 환경변수 및 상수
# ─────────────────────────────────────────────
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID        = os.getenv("CHAT_ID")
MAX_WORKERS    = 5
MAX_TICKERS    = 30

# 점수 정규화 기준
RAW_SCORE_MAX  =  120  # 모든 가점 합산 시 최대
RAW_SCORE_MIN  =  -50  # 모든 패널티 합산 시 최소
RAW_SCORE_RANGE = RAW_SCORE_MAX - RAW_SCORE_MIN  # 170


# ─────────────────────────────────────────────
# 유틸리티
# ─────────────────────────────────────────────
def safe_float(val, default=0.0):
    """스칼라/Series/NaN/Inf 모두 안전하게 float 변환"""
    try:
        if hasattr(val, 'iloc'):
            val = val.iloc[-1]
        v = float(val)
        return default if (np.isnan(v) or np.isinf(v)) else v
    except Exception:
        return default


def normalize_score(raw: int) -> int:
    """원시 점수(-50 ~ +120)를 0~100 스케일로 정규화"""
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


# ─────────────────────────────────────────────
# 1. Finviz S&P 500 상승 종목 수집
# ─────────────────────────────────────────────
def fetch_finviz_sp500_gainers() -> list[dict]:
    if not FINVIZ_AVAILABLE:
        return []

    print("[Finviz] S&P 500 상승 종목 수집 중...")
    try:
        foverview  = Overview()
        filter_cfg = {'Index': 'S&P 500', 'Order': 'Change Desc'}

        try:
            foverview.set_filter(filters_dict=filter_cfg)
        except TypeError:
            try:
                foverview.set_filter(filter_dict=filter_cfg)
            except Exception:
                print("[Finviz] 필터 적용 실패 → 전체 데이터 사용")

        df = foverview.screener_view()
        if df is None or df.empty:
            print("[Finviz] 데이터 없음")
            return []

        results = []
        for _, row in df.head(MAX_TICKERS).iterrows():
            try:
                ticker  = str(row.get('Ticker',  '')).strip()
                company = str(row.get('Company', '')).strip()
                price   = safe_float(row.get('Price', 0))
                change  = safe_float(
                    str(row.get('Change', '0')).replace('%', '')
                )
                if ticker and price > 0:
                    results.append({
                        "ticker":  ticker,
                        "company": company,
                        "price":   price,
                        "change":  change,
                    })
            except Exception:
                continue

        print(f"[Finviz] {len(results)}개 종목 수집 완료")
        return results

    except Exception as e:
        print(f"[Finviz] 수집 오류: {e}")
        return []


# ─────────────────────────────────────────────
# 2. 기술적 지표 계산
# ─────────────────────────────────────────────
def compute_indicators(ticker: str) -> dict | None:
    df = None

    for attempt in range(3):
        try:
            raw = yf.download(
                ticker, period="2y", interval="1d",
                progress=False, auto_adjust=True
            )
            if raw is not None and len(raw) >= 200:
                df = raw
                break
        except Exception as e:
            print(f"[{ticker}] 다운로드 실패 ({attempt+1}/3): {e}")
        time.sleep(2 * (attempt + 1))

    if df is None or len(df) < 200:
        print(f"[{ticker}] 데이터 부족 스킵")
        return None

    try:
        # MultiIndex 제거
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        def to_series(col):
            s = df[col].squeeze()
            return s.iloc[:, 0] if isinstance(s, pd.DataFrame) else s

        close  = to_series("Close")
        high   = to_series("High")
        low    = to_series("Low")
        volume = to_series("Volume")

        # ── 현재가 / 일일 변동률 ──────────────────────
        curr_price = safe_float(close.iloc[-1])
        prev_price = safe_float(close.iloc[-2]) if len(close) > 1 else curr_price
        change_1d  = (
            (curr_price - prev_price) / prev_price * 100
        ) if prev_price != 0 else 0.0

        # ── RSI (14) - Wilder's EMA 표준 방식 ─────────
        delta = close.diff()
        gain  = delta.where(delta > 0, 0).ewm(alpha=1/14, adjust=False).mean()
        loss  = (-delta.where(delta < 0, 0)).ewm(alpha=1/14, adjust=False).mean()
        rs    = gain / loss.replace(0, np.nan)
        rsi   = safe_float((100 - (100 / (1 + rs))).iloc[-1])

        # ── MACD (12, 26, 9) ──────────────────────────
        ema12     = close.ewm(span=12, adjust=False).mean()
        ema26     = close.ewm(span=26, adjust=False).mean()
        macd_line = ema12 - ema26
        signal    = macd_line.ewm(span=9, adjust=False).mean()
        macd_hist = macd_line - signal
        curr_macd = safe_float(macd_hist.iloc[-1])

        # ── MA 20 / 50 / 200 ──────────────────────────
        m20_val  = safe_float(close.rolling(20).mean().iloc[-1])
        m50_val  = safe_float(close.rolling(50).mean().iloc[-1])
        m200_val = safe_float(close.rolling(200).mean().iloc[-1])

        if m50_val > m200_val and m200_val > 0:
            ma_trend = "골든크로스"
        elif m50_val < m200_val and m200_val > 0:
            ma_trend = "데드크로스"
        else:
            ma_trend = "중립"

        # ── Stochastic (14, 3) ────────────────────────
        low14   = low.rolling(14).min()
        high14  = high.rolling(14).max()
        stoch_k = 100 * (close - low14) / (high14 - low14).replace(0, np.nan)
        stoch_d = stoch_k.rolling(3).mean()
        curr_stoch_d = safe_float(stoch_d.iloc[-1])

        # ── Ichimoku (현재 기준 비교용 - shift 없음) ──
        tenkan = (high.rolling(9).max()  + low.rolling(9).min())  / 2
        kijun  = (high.rolling(26).max() + low.rolling(26).min()) / 2
        span_a = (tenkan + kijun) / 2
        span_b = (high.rolling(52).max() + low.rolling(52).min()) / 2

        sa = safe_float(span_a.iloc[-1])
        sb = safe_float(span_b.iloc[-1])
        is_above_cloud = curr_price > max(sa, sb) if (sa > 0 and sb > 0) else False
        is_below_cloud = curr_price < min(sa, sb) if (sa > 0 and sb > 0) else False

        # ── VWAP (20일 Rolling) ───────────────────────
        typical_price = (high + low + close) / 3
        vwap_20 = (
            (typical_price * volume).rolling(20).sum()
            / volume.rolling(20).sum()
        )
        curr_vwap     = safe_float(vwap_20.iloc[-1])
        is_above_vwap = curr_price > curr_vwap
        vwap_gap_pct  = (
            (curr_price - curr_vwap) / curr_vwap * 100
        ) if curr_vwap > 0 else 0.0

        # ── ATR (14) 기반 동적 목표가 ─────────────────
        tr = pd.concat([
            high - low,
            (high - close.shift()).abs(),
            (low  - close.shift()).abs()
        ], axis=1).max(axis=1)
        atr      = tr.rolling(14).mean()
        curr_atr = max(safe_float(atr.iloc[-1]), curr_price * 0.01)

        target1   = round(curr_price + 1.5 * curr_atr, 2)
        target2   = round(curr_price + 3.0 * curr_atr, 2)
        stop_loss = round(curr_price - 1.5 * curr_atr, 2)

        # ── 거래량 비율 ───────────────────────────────
        vol_avg   = safe_float(volume.rolling(20).mean().iloc[-1])
        vol_ratio = (
            safe_float(volume.iloc[-1]) / vol_avg * 100
        ) if vol_avg > 0 else 100.0

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
            "target1":        target1,
            "target2":        target2,
            "stop_loss":      stop_loss,
        }

    except Exception as e:
        print(f"[{ticker}] 지표 계산 오류: {e}")
        return None


# ─────────────────────────────────────────────
# 3. 점수 및 진입 상태 계산
# ─────────────────────────────────────────────
def compute_score_and_status(ind: dict, fv: dict) -> tuple[int, list[str], str]:
    raw   = 0
    signals = []

    # 1. RSI
    if ind["rsi"] < 35:
        raw += 20
        signals.append(f"RSI 과매도 {ind['rsi']:.1f}")
    elif ind["rsi"] <= 65:
        raw += 10
        signals.append(f"RSI 적정 {ind['rsi']:.1f}")
    elif ind["rsi"] > 70:
        raw -= 10
        signals.append(f"RSI 과매수 {ind['rsi']:.1f} ⚠️")

    # 2. MACD
    if ind["macd_histogram"] > 0:
        raw += 15
        signals.append("MACD 상방 ✅")

    # 3. MA20
    if ind["price"] > ind["ma20"]:
        raw += 15
        signals.append("MA20 상회 ✅")

    # 4. MA 골든/데드크로스
    if ind["ma_trend"] == "골든크로스":
        raw += 15
        signals.append("골든크로스 ✅")
    elif ind["ma_trend"] == "데드크로스":
        raw -= 10
        signals.append("데드크로스 ⚠️")

    # 5. Ichimoku
    if ind["is_above_cloud"]:
        raw += 20
        signals.append("구름대 상단 돌파 ✅")
    elif ind["is_below_cloud"]:
        raw -= 20
        signals.append("구름대 하단 저항 ⚠️")

    # 6. Stochastic
    if ind["stochastic_d"] < 20:
        raw += 10
        signals.append(f"스토캐스틱 과매도 {ind['stochastic_d']:.1f}")
    elif ind["stochastic_d"] > 80:
        raw -= 10
        signals.append(f"스토캐스틱 과매수 {ind['stochastic_d']:.1f} ⚠️")

    # 7. VWAP
    if ind["is_above_vwap"]:
        raw += 15
        signals.append(f"VWAP 상회 +{ind['vwap_gap_pct']:.1f}% ✅")
    else:
        raw -= 10
        signals.append(f"VWAP 하회 {ind['vwap_gap_pct']:.1f}% ⚠️")

    # 8. 거래량
    if ind["vol_ratio"] > 150:
        raw += 15
        signals.append(f"거래량 급증 {ind['vol_ratio']:.0f}%")

    # 9. Finviz 모멘텀
    if fv["change"] > 3:
        raw += 10
        signals.append(f"강한 모멘텀 +{fv['change']:.1f}%")

    # 0~100 정규화
    score = normalize_score(raw)

    if score >= 70:
        entry = "🟢 진입 가능"
    elif score >= 50:
        entry = "⏳ 대기 (관망)"
    else:
        entry = "❌ 회피 (리스크)"

    return score, signals, entry


# ─────────────────────────────────────────────
# 4. 단일 종목 분석 (딜레이 포함)
# ─────────────────────────────────────────────
def analyze_ticker(fv: dict, delay: float = 0.0) -> dict | None:
    """rate limit 방지를 위한 딜레이 후 분석 실행"""
    if delay > 0:
        time.sleep(delay)

    ticker = fv["ticker"]
    ind    = compute_indicators(ticker)
    if ind is None:
        return None

    score, signals, entry = compute_score_and_status(ind, fv)

    return {
        "ticker":         ticker,
        "company":        fv["company"],
        "price":          ind["price"],
        "change":         ind["change_1d"],
        "rsi":            ind["rsi"],
        "macd_histogram": ind["macd_histogram"],
        "ma20":           ind["ma20"],
        "ma_trend":       ind["ma_trend"],
        "is_above_cloud": ind["is_above_cloud"],
        "is_below_cloud": ind["is_below_cloud"],
        "vwap":           ind["vwap"],
        "is_above_vwap":  ind["is_above_vwap"],
        "vwap_gap_pct":   ind["vwap_gap_pct"],
        "vol_ratio":      ind["vol_ratio"],
        "target1":        ind["target1"],
        "target2":        ind["target2"],
        "stop_loss":      ind["stop_loss"],
        "score":          score,
        "signals":        signals,
        "entry":          entry,
        "analyzed_at":    datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    }


# ─────────────────────────────────────────────
# 5. 메인 분석 루프
# ─────────────────────────────────────────────
def analyze() -> dict:
    print(f"\n{'='*50}")
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] S&P 500 모멘텀 분석 시작")
    print(f"{'='*50}")

    candidates = fetch_finviz_sp500_gainers()
    if not candidates:
        print("[오류] Finviz 수집 실패 → 분석 중단")
        return {"results": []}

    results = []

    # 종목마다 0.5초 간격 jitter로 rate limit 방지
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(analyze_ticker, fv, i * 0.5): fv
            for i, fv in enumerate(candidates)
        }
        for future in as_completed(futures):
            try:
                res = future.result()
                if res:
                    results.append(res)
            except Exception as e:
                print(f"[분석 오류] {e}")

    if not results:
        print("[오류] 분석된 종목 없음")
        return {"results": []}

    results.sort(key=lambda x: x["score"], reverse=True)

    save_data = {
        "analyzed_at": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "results":     results,
    }

    # 히스토리 저장 (타임스탬프 포함 파일명으로 덮어쓰기 방지)
    try:
        os.makedirs("history", exist_ok=True)
        filename = datetime.now().strftime('%Y-%m-%d_%H%M%S')
        with open(f"history/{filename}.json", "w", encoding="utf-8") as f:
            json.dump(save_data, f, ensure_ascii=False, indent=2)
        print(f"[History] history/{filename}.json 저장 완료")
    except Exception as e:
        print(f"[History] 저장 오류: {e}")

    # 텔레그램 리포트
    top10     = results[:10]
    today_str = datetime.now().strftime('%Y-%m-%d')
    report    = f"📊 *{today_str} S&P 500 모멘텀 Top 10*\n\n"
    for i, r in enumerate(top10, 1):
        vwap_str = f"VWAP {'상회' if r['is_above_vwap'] else '하회'} {r['vwap_gap_pct']:+.1f}%"
        report += (
            f"{i}. *{r['ticker']}* ({r['company']})\n"
            f"   상태: {r['entry']} | 점수: {r['score']}\n"
            f"   가격: ${r['price']:.2f} ({r['change']:+.2f}%)\n"
            f"   RSI: {r['rsi']:.1f} | {vwap_str}\n\n"
        )
    send_telegram(report)

    print(f"\n✅ 분석 완료: 총 {len(results)}개 종목 처리")
    print(f"   🟢 진입 가능: {sum(1 for r in results if '🟢' in r['entry'])}개")
    print(f"   ⏳ 대기:      {sum(1 for r in results if '⏳' in r['entry'])}개")
    print(f"   ❌ 회피:      {sum(1 for r in results if '❌' in r['entry'])}개")

    return save_data


if __name__ == "__main__":
    analyze()
