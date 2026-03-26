"""
longterm.py — 중장기 투자 판단 스캐너
기관 매집, 펀더멘털, 뉴스 감성, 내부자 거래, 기술적 추세 종합 분석
"""

import os, json, logging, pathlib, math
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from dotenv import load_dotenv

load_dotenv()

import yfinance as yf
import requests

LOG = logging.getLogger("longterm")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# ── 상수 ──────────────────────────────────────────────
MAX_WORKERS = 5
LONGTERM_DIR = pathlib.Path("longterm")
LONGTERM_DIR.mkdir(exist_ok=True)
LONGTERM_MAX_RESULTS = 20
HISTORY_TS_FMT = "%Y-%m-%d_%H%M%S"

FINNHUB_KEY = os.getenv("FINNHUB_API_KEY", "")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# 심볼 (bot.py에서 가져오거나 직접 정의)
try:
    from bot import SP500_SYMBOLS, SOX_SYMBOLS, UNIVERSE_MAP
except ImportError:
    SP500_SYMBOLS = ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA"]
    SOX_SYMBOLS = ["AMD", "INTC", "AVGO", "QCOM", "TXN", "ADI", "MRVL"]
    UNIVERSE_MAP = {"sp500": SP500_SYMBOLS, "sox": SOX_SYMBOLS, "sp500+sox": SP500_SYMBOLS + SOX_SYMBOLS}


# ── 1. 기관 매집 분석 ─────────────────────────────────
def analyze_institutional(ticker_obj):
    """기관 보유 비율, 주요 기관, 변화 추이 분석"""
    result = {
        "inst_pct": None,
        "insider_pct": None,
        "top_holders": [],
        "inst_score": 0,
        "signals": [],
    }
    try:
        info = ticker_obj.info or {}
        result["inst_pct"] = info.get("heldPercentInstitutions")
        result["insider_pct"] = info.get("heldPercentInsiders")

        # 기관 보유 비율 점수
        inst_pct = result["inst_pct"]
        if inst_pct is not None:
            pct = inst_pct * 100
            if pct >= 80:
                result["inst_score"] += 15
                result["signals"].append(f"🏦 기관 보유 {pct:.1f}% (매우 높음)")
            elif pct >= 60:
                result["inst_score"] += 10
                result["signals"].append(f"🏦 기관 보유 {pct:.1f}% (높음)")
            elif pct >= 40:
                result["inst_score"] += 5
                result["signals"].append(f"🏦 기관 보유 {pct:.1f}% (보통)")

        # 주요 기관 홀더
        holders = ticker_obj.institutional_holders
        if holders is not None and not holders.empty:
            for _, row in holders.head(5).iterrows():
                name = row.get("Holder", "")
                shares = row.get("Shares", 0)
                pct_out = row.get("pctHeld", row.get("% Out", 0))
                result["top_holders"].append({
                    "name": str(name),
                    "shares": int(shares) if shares else 0,
                    "pct": float(pct_out) if pct_out else 0,
                })
            # 블랙록, 뱅가드 등 대형 기관 보유 시 보너스
            big_names = ["vanguard", "blackrock", "state street", "fidelity", "berkshire"]
            for h in result["top_holders"]:
                if any(b in h["name"].lower() for b in big_names):
                    result["inst_score"] += 3
                    result["signals"].append(f"⭐ {h['name'][:30]} 보유 중")
                    break

    except Exception as e:
        LOG.warning(f"기관 분석 실패: {e}")

    return result


# ── 2. 내부자 거래 분석 ───────────────────────────────
def analyze_insider(ticker_obj):
    """내부자 매수/매도 분석"""
    result = {
        "insider_purchases": 0,
        "insider_sales": 0,
        "net_insider": 0,
        "insider_score": 0,
        "signals": [],
    }
    try:
        purchases = ticker_obj.insider_purchases
        if purchases is not None and not purchases.empty:
            for _, row in purchases.iterrows():
                txt = str(row.get("Text", "")).lower()
                shares = row.get("Shares", 0)
                if shares is None:
                    shares = 0
                try:
                    shares = int(shares)
                except (ValueError, TypeError):
                    shares = 0
                if "purchase" in txt or "buy" in txt:
                    result["insider_purchases"] += shares
                elif "sale" in txt or "sell" in txt:
                    result["insider_sales"] += shares

        result["net_insider"] = result["insider_purchases"] - result["insider_sales"]

        if result["insider_purchases"] > 0 and result["insider_purchases"] > result["insider_sales"]:
            result["insider_score"] += 15
            result["signals"].append(f"🟢 내부자 순매수 {result['net_insider']:,}주")
        elif result["insider_sales"] > result["insider_purchases"] * 2:
            result["insider_score"] -= 10
            result["signals"].append(f"🔴 내부자 대량 매도")
        elif result["insider_purchases"] > 0:
            result["insider_score"] += 5
            result["signals"].append(f"⏳ 내부자 매수 있음 ({result['insider_purchases']:,}주)")

    except Exception as e:
        LOG.warning(f"내부자 분석 실패: {e}")

    return result


# ── 3. 펀더멘털 분석 ──────────────────────────────────
def analyze_fundamentals(ticker_obj):
    """매출 성장, 이익, PER, 부채비율 등"""
    result = {
        "pe_ratio": None,
        "forward_pe": None,
        "peg_ratio": None,
        "revenue_growth": None,
        "earnings_growth": None,
        "debt_to_equity": None,
        "profit_margin": None,
        "roe": None,
        "free_cashflow": None,
        "dividend_yield": None,
        "fundamental_score": 0,
        "signals": [],
    }
    try:
        info = ticker_obj.info or {}

        result["pe_ratio"] = info.get("trailingPE")
        result["forward_pe"] = info.get("forwardPE")
        result["peg_ratio"] = info.get("pegRatio")
        result["revenue_growth"] = info.get("revenueGrowth")
        result["earnings_growth"] = info.get("earningsGrowth")
        result["debt_to_equity"] = info.get("debtToEquity")
        result["profit_margin"] = info.get("profitMargins")
        result["roe"] = info.get("returnOnEquity")
        result["free_cashflow"] = info.get("freeCashflow")
        result["dividend_yield"] = info.get("dividendYield")

        score = 0

        # PER 분석
        pe = result["forward_pe"] or result["pe_ratio"]
        if pe is not None:
            if 0 < pe < 15:
                score += 10
                result["signals"].append(f"💰 저PER ({pe:.1f})")
            elif 15 <= pe < 25:
                score += 5
                result["signals"].append(f"📊 적정 PER ({pe:.1f})")
            elif pe >= 40:
                score -= 5
                result["signals"].append(f"⚠️ 고PER ({pe:.1f})")

        # PEG 분석
        peg = result["peg_ratio"]
        if peg is not None:
            if 0 < peg < 1:
                score += 10
                result["signals"].append(f"🔥 PEG {peg:.2f} (저평가 성장)")
            elif 1 <= peg < 2:
                score += 5
                result["signals"].append(f"✅ PEG {peg:.2f} (적정)")

        # 매출 성장
        rg = result["revenue_growth"]
        if rg is not None:
            pct = rg * 100
            if pct > 20:
                score += 10
                result["signals"].append(f"📈 매출 성장 {pct:.1f}%")
            elif pct > 10:
                score += 5
                result["signals"].append(f"📊 매출 성장 {pct:.1f}%")
            elif pct < 0:
                score -= 5
                result["signals"].append(f"📉 매출 감소 {pct:.1f}%")

        # 이익 성장
        eg = result["earnings_growth"]
        if eg is not None:
            pct = eg * 100
            if pct > 20:
                score += 10
                result["signals"].append(f"💹 이익 성장 {pct:.1f}%")
            elif pct > 10:
                score += 5

        # 부채비율
        de = result["debt_to_equity"]
        if de is not None:
            if de < 50:
                score += 10
                result["signals"].append(f"🛡️ 낮은 부채비율 ({de:.0f}%)")
            elif de < 100:
                score += 5
                result["signals"].append(f"📊 보통 부채비율 ({de:.0f}%)")
            elif de > 200:
                score -= 10
                result["signals"].append(f"⚠️ 높은 부채비율 ({de:.0f}%)")

        # 이익률
        pm = result["profit_margin"]
        if pm is not None:
            pct = pm * 100
            if pct > 20:
                score += 5
                result["signals"].append(f"💎 높은 이익률 ({pct:.1f}%)")
            elif pct < 0:
                score -= 5
                result["signals"].append(f"🔴 적자 (이익률 {pct:.1f}%)")

        # ROE
        roe = result["roe"]
        if roe is not None:
            pct = roe * 100
            if pct > 20:
                score += 5
                result["signals"].append(f"⭐ 높은 ROE ({pct:.1f}%)")

        result["fundamental_score"] = score

    except Exception as e:
        LOG.warning(f"펀더멘털 분석 실패: {e}")

    return result


# ── 4. 뉴스 감성 분석 ─────────────────────────────────
def analyze_news(ticker_str, ticker_obj):
    """뉴스 감성 분석 (Finnhub + yfinance)"""
    result = {
        "news": [],
        "sentiment_score": 0,
        "positive": 0,
        "negative": 0,
        "neutral": 0,
        "news_score": 0,
        "signals": [],
    }
    try:
        # yfinance 뉴스
        news_list = ticker_obj.news or []
        for item in news_list[:10]:
            content = item.get("content", {})
            title = content.get("title", item.get("title", ""))
            pub = content.get("pubDate", "")
            provider = content.get("provider", {})
            provider_name = provider.get("displayName", "") if isinstance(provider, dict) else str(provider)

            # 간단한 키워드 감성 분석
            title_lower = title.lower()
            pos_words = ["upgrade", "beat", "strong", "growth", "surge", "rally", "buy",
                        "outperform", "record", "positive", "bullish", "raise", "exceed",
                        "win", "award", "contract", "expand", "profit", "revenue",
                        "innovation", "breakthrough", "partnership", "deal", "acquire"]
            neg_words = ["downgrade", "miss", "weak", "decline", "drop", "sell", "cut",
                        "underperform", "warning", "negative", "bearish", "loss", "layoff",
                        "lawsuit", "recall", "investigation", "fine", "penalty", "fraud",
                        "bankruptcy", "default", "crash", "plunge", "slash", "delay"]


            sentiment = "neutral"
            if any(w in title_lower for w in pos_words):
                sentiment = "positive"
                result["positive"] += 1
            elif any(w in title_lower for w in neg_words):
                sentiment = "negative"
                result["negative"] += 1
            else:
                result["neutral"] += 1

            result["news"].append({
                "title": title[:100],
                "sentiment": sentiment,
                "date": str(pub)[:10],
                "source": provider_name,
            })

            # 뉴스 점수 계산
        total = result["positive"] + result["negative"] + result["neutral"]
        if total > 0:
            net = result["positive"] - result["negative"]
            if net >= 3:
                result["news_score"] = 15
                result["signals"].append(f"📰 뉴스 매우 긍정적 (+{result['positive']}/-{result['negative']})")
            elif net >= 1:
                result["news_score"] = 10
                result["signals"].append(f"📰 뉴스 긍정적 (+{result['positive']}/-{result['negative']})")
            elif net == 0 and result["positive"] >= 1:
                result["news_score"] = 5
                result["signals"].append(f"📰 뉴스 혼조 (+{result['positive']}/-{result['negative']})")
            elif net == 0:
                result["news_score"] = 2
                result["signals"].append(f"📰 뉴스 중립 (+{result['positive']}/-{result['negative']})")
            elif net >= -2:
                result["news_score"] = -5
                result["signals"].append(f"📰 뉴스 부정적 (+{result['positive']}/-{result['negative']})")
            else:
                result["news_score"] = -15
                result["signals"].append(f"📰 뉴스 매우 부정적 (+{result['positive']}/-{result['negative']})")

    except Exception as e:
        LOG.warning(f"뉴스 분석 실패: {e}")

    # Finnhub 뉴스 보충
    try:
        if FINNHUB_KEY:
            today = datetime.now()
            from_date = (today - timedelta(days=7)).strftime("%Y-%m-%d")
            to_date = today.strftime("%Y-%m-%d")
            url = f"https://finnhub.io/api/v1/company-news?symbol={ticker_str}&from={from_date}&to={to_date}&token={FINNHUB_KEY}"
            resp = requests.get(url, timeout=5)
            if resp.status_code == 200:
                fh_news = resp.json()[:5]
                for item in fh_news:
                    title = item.get("headline", "")
                    sentiment_val = item.get("sentiment", None)
                    result["news"].append({
                        "title": title[:100],
                        "sentiment": "finnhub",
                        "date": datetime.fromtimestamp(item.get("datetime", 0)).strftime("%Y-%m-%d"),
                        "source": item.get("source", ""),
                    })
    except Exception:
        pass

    return result


# ── 5. 애널리스트 의견 ────────────────────────────────
def analyze_analyst(ticker_obj):
    """애널리스트 추천, 목표가"""
    result = {
        "target_mean": None,
        "target_high": None,
        "target_low": None,
        "recommendation": None,
        "num_analysts": None,
        "analyst_score": 0,
        "signals": [],
    }
    try:
        info = ticker_obj.info or {}
        current_price = info.get("currentPrice") or info.get("regularMarketPrice", 0)

        result["target_mean"] = info.get("targetMeanPrice")
        result["target_high"] = info.get("targetHighPrice")
        result["target_low"] = info.get("targetLowPrice")
        result["recommendation"] = info.get("recommendationKey")
        result["num_analysts"] = info.get("numberOfAnalystOpinions")

        rec = result["recommendation"]
        if rec:
            rec_lower = rec.lower()
            if rec_lower in ["strong_buy", "strongbuy"]:
                result["analyst_score"] = 15
                result["signals"].append("🎯 애널리스트: Strong Buy")
            elif rec_lower == "buy":
                result["analyst_score"] = 10
                result["signals"].append("🎯 애널리스트: Buy")
            elif rec_lower == "hold":
                result["analyst_score"] = 0
                result["signals"].append("🎯 애널리스트: Hold")
            elif rec_lower in ["sell", "underperform"]:
                result["analyst_score"] = -10
                result["signals"].append("🎯 애널리스트: Sell")

        # 목표가 대비 상승여력
        if result["target_mean"] and current_price and current_price > 0:
            upside = ((result["target_mean"] - current_price) / current_price) * 100
            if upside > 30:
                result["analyst_score"] += 10
                result["signals"].append(f"🚀 목표가 상승여력 {upside:.1f}%")
            elif upside > 15:
                result["analyst_score"] += 5
                result["signals"].append(f"📈 목표가 상승여력 {upside:.1f}%")
            elif upside < -10:
                result["analyst_score"] -= 5
                result["signals"].append(f"📉 목표가 하락여력 {upside:.1f}%")

    except Exception as e:
        LOG.warning(f"애널리스트 분석 실패: {e}")

    return result


# ── 6. 장기 기술적 추세 ───────────────────────────────
def analyze_long_trend(ticker_obj):
    """200일 이동평균, 주간 추세 분석"""
    result = {
        "above_200ma": None,
        "ma200_gap_pct": None,
        "above_50ma": None,
        "weekly_rsi": None,
        "trend_score": 0,
        "signals": [],
    }
    try:
        hist = ticker_obj.history(period="2y")
        if hist.empty or len(hist) < 200:
            return result

        close = hist["Close"]
        current = close.iloc[-1]

        ma200 = close.rolling(200).mean().iloc[-1]
        ma50 = close.rolling(50).mean().iloc[-1]

        result["above_200ma"] = current > ma200
        result["ma200_gap_pct"] = ((current - ma200) / ma200) * 100
        result["above_50ma"] = current > ma50

        if current > ma200:
            result["trend_score"] += 10
            result["signals"].append(f"✅ 200일선 위 ({result['ma200_gap_pct']:.1f}%)")
        elif result["ma200_gap_pct"] > -5:
            result["trend_score"] += 3
            result["signals"].append(f"⚠️ 200일선 살짝 아래 ({result['ma200_gap_pct']:.1f}%) - 반등 가능")
        else:
            result["signals"].append(f"⚠️ 200일선 아래 ({result['ma200_gap_pct']:.1f}%)")

        if ma50 > ma200:
            result["trend_score"] += 8
            result["signals"].append("✅ 골든크로스 상태 (50일 > 200일)")
        else:
            result["signals"].append("⚠️ 데드크로스 상태 (50일 < 200일)")

        high_52w = close[-252:].max() if len(close) >= 252 else close.max()
        low_52w = close[-252:].min() if len(close) >= 252 else close.min()
        from_high = ((current - high_52w) / high_52w) * 100
        from_low = ((current - low_52w) / low_52w) * 100

        if from_high > -10:
            result["trend_score"] += 7
            result["signals"].append(f"🔝 52주 고점 근접 ({from_high:.1f}%)")
        elif from_high > -20:
            result["trend_score"] += 3
            result["signals"].append(f"📊 52주 고점 대비 {from_high:.1f}%")
        elif from_high < -30:
            result["signals"].append(f"📉 52주 고점 대비 {from_high:.1f}%")

        if from_low < 20:
            result["trend_score"] += 3
            result["signals"].append(f"📍 52주 저점 근접 (저점 대비 +{from_low:.1f}%)")

        weekly = close.resample("W").last().dropna()
        if len(weekly) >= 15:
            delta = weekly.diff().dropna()
            gain = delta.where(delta > 0, 0).rolling(14).mean().iloc[-1]
            loss = (-delta.where(delta < 0, 0)).rolling(14).mean().iloc[-1]
            if loss != 0:
                rs = gain / loss
                rsi = 100 - (100 / (1 + rs))
                result["weekly_rsi"] = rsi
                if rsi < 30:
                    result["trend_score"] += 5
                    result["signals"].append(f"📉 주간 RSI 과매도 ({rsi:.1f})")
                elif rsi < 45:
                    result["trend_score"] += 2
                    result["signals"].append(f"📊 주간 RSI 낮음 ({rsi:.1f})")

    except Exception as e:
        LOG.warning(f"장기 추세 분석 실패: {e}")

    return result
# ── 7. 어닝 & 가이던스 분석 ───────────────────────────
def analyze_earnings(ticker_str, ticker_obj):
    """최근 어닝 서프라이즈, 가이던스 방향, 연속 비트 횟수 분석"""
    result = {
        "earnings_history": [],
        "consecutive_beats": 0,
        "last_surprise_pct": None,
        "guidance_direction": None,  # "raised", "maintained", "lowered", "none"
        "next_earnings_date": None,
        "earnings_score": 0,
        "signals": [],
    }
    try:
        # ── 1) 어닝 서프라이즈 (yfinance) ──
        earnings_hist = ticker_obj.earnings_dates
        if earnings_hist is not None and not earnings_hist.empty:
            # 과거 실적만 (Reported EPS가 있는 것)
            past = earnings_hist.dropna(subset=["Reported EPS"])
            if not past.empty:
                consecutive = 0
                for idx, row in past.head(8).iterrows():
                    reported = row.get("Reported EPS")
                    estimate = row.get("EPS Estimate")
                    if reported is not None and estimate is not None:
                        try:
                            rep = float(reported)
                            est = float(estimate)
                            if est != 0:
                                surprise_pct = round((rep - est) / abs(est) * 100, 1)
                            else:
                                surprise_pct = 0.0

                            result["earnings_history"].append({
                                "date": str(idx)[:10],
                                "reported": rep,
                                "estimate": est,
                                "surprise_pct": surprise_pct,
                                "beat": rep > est,
                            })

                            if rep > est:
                                consecutive += 1
                            else:
                                break  # 연속 비트 끊김
                        except (ValueError, TypeError):
                            break

                result["consecutive_beats"] = consecutive

                if result["earnings_history"]:
                    result["last_surprise_pct"] = result["earnings_history"][0]["surprise_pct"]

            # 다음 어닝 날짜 (미래)
            future = earnings_hist[earnings_hist["Reported EPS"].isna()]
            if not future.empty:
                result["next_earnings_date"] = str(future.index[0])[:10]

        # ── 2) 어닝 서프라이즈 점수 ──
        beats = result["consecutive_beats"]
        if beats >= 4:
            result["earnings_score"] += 20
            result["signals"].append(f"🔥 {beats}분기 연속 어닝 비트!")
        elif beats >= 2:
            result["earnings_score"] += 10
            result["signals"].append(f"✅ {beats}분기 연속 어닝 비트")
        elif beats == 1:
            result["earnings_score"] += 5
            result["signals"].append("✅ 최근 어닝 비트")

        # 서프라이즈 크기
        last_sp = result["last_surprise_pct"]
        if last_sp is not None:
            if last_sp > 20:
                result["earnings_score"] += 10
                result["signals"].append(f"💥 어닝 서프라이즈 +{last_sp}% (대폭 상회)")
            elif last_sp > 5:
                result["earnings_score"] += 5
                result["signals"].append(f"📈 어닝 서프라이즈 +{last_sp}%")
            elif last_sp < -10:
                result["earnings_score"] -= 10
                result["signals"].append(f"📉 어닝 미스 {last_sp}%")
            elif last_sp < 0:
                result["earnings_score"] -= 5
                result["signals"].append(f"⚠️ 어닝 소폭 미스 {last_sp}%")

        # ── 3) 가이던스 분석 (Finnhub 또는 yfinance info) ──
        info = ticker_obj.info or {}

        # 방법 A: Forward EPS vs Trailing EPS로 가이던스 방향 추정
        forward_eps = info.get("forwardEps")
        trailing_eps = info.get("trailingEps")

        if forward_eps is not None and trailing_eps is not None and trailing_eps != 0:
            growth = (forward_eps - trailing_eps) / abs(trailing_eps) * 100
            if growth > 15:
                result["guidance_direction"] = "raised"
                result["earnings_score"] += 10
                result["signals"].append(f"🚀 가이던스 상향 추정 (Forward EPS +{growth:.1f}%)")
            elif growth > 0:
                result["guidance_direction"] = "maintained"
                result["earnings_score"] += 5
                result["signals"].append(f"✅ 가이던스 유지/소폭 상향 (+{growth:.1f}%)")
            elif growth > -10:
                result["guidance_direction"] = "maintained"
                result["signals"].append(f"⏳ 가이던스 보합 ({growth:.1f}%)")
            else:
                result["guidance_direction"] = "lowered"
                result["earnings_score"] -= 10
                result["signals"].append(f"⚠️ 가이던스 하향 추정 ({growth:.1f}%)")

        # 방법 B: Finnhub EPS surprise API 보충
        if FINNHUB_KEY and not result["earnings_history"]:
            try:
                url = f"https://finnhub.io/api/v1/stock/earnings?symbol={ticker_str}&limit=4&token={FINNHUB_KEY}"
                resp = requests.get(url, timeout=5)
                if resp.status_code == 200:
                    fh_earnings = resp.json()
                    consecutive_fh = 0
                    for e in fh_earnings:
                        actual = e.get("actual")
                        estimate = e.get("estimate")
                        if actual is not None and estimate is not None:
                            beat = actual > estimate
                            sp = round((actual - estimate) / abs(estimate) * 100, 1) if estimate != 0 else 0
                            result["earnings_history"].append({
                                "date": e.get("period", ""),
                                "reported": actual,
                                "estimate": estimate,
                                "surprise_pct": sp,
                                "beat": beat,
                            })
                            if beat:
                                consecutive_fh += 1
                            else:
                                break
                    if consecutive_fh > result["consecutive_beats"]:
                        result["consecutive_beats"] = consecutive_fh
            except Exception:
                pass


        # 방법 C: Finnhub Revenue Estimate (가이던스 구체화)
        if FINNHUB_KEY:
            try:
                url = f"https://finnhub.io/api/v1/stock/revenue-estimate?symbol={ticker_str}&freq=quarterly&token={FINNHUB_KEY}"
                resp = requests.get(url, timeout=5)
                if resp.status_code == 200:
                    rev_data = resp.json().get("data", [])
                    if len(rev_data) >= 2:
                        current_est = rev_data[0].get("revenueAvg", 0)
                        prev_est = rev_data[1].get("revenueAvg", 0)
                        if prev_est and current_est:
                            rev_growth = (current_est - prev_est) / abs(prev_est) * 100
                            if rev_growth > 10:
                                result["earnings_score"] += 5
                                result["signals"].append(f"📊 다음 분기 매출 추정 +{rev_growth:.1f}% (성장)")
            except Exception:
                pass

        # ── 4) 어닝콜 이후 시장 반응 분석 ──
        try:
            hist = ticker_obj.history(period="2y")
            if hist is not None and not hist.empty and result["earnings_history"]:
                import pandas as pd
                reactions = []
                for eh in result["earnings_history"][:4]:
                    try:
                        e_date = pd.to_datetime(eh["date"]).normalize()
                        available = hist.index[hist.index >= e_date]
                        if len(available) < 3:
                            continue

                        day0_idx = available[0]
                        day0_close = float(hist.loc[day0_idx, "Close"])

                        before = hist.index[hist.index < e_date]
                        if len(before) == 0:
                            continue
                        prev_close = float(hist.loc[before[-1], "Close"])

                        reaction_1d = round((day0_close - prev_close) / prev_close * 100, 2)

                        reaction_3d = None
                        if len(available) >= 3:
                            day3_close = float(hist.loc[available[2], "Close"])
                            reaction_3d = round((day3_close - prev_close) / prev_close * 100, 2)

                        reaction_5d = None
                        if len(available) >= 5:
                            day5_close = float(hist.loc[available[4], "Close"])
                            reaction_5d = round((day5_close - prev_close) / prev_close * 100, 2)

                        reactions.append({
                            "date": eh["date"],
                            "beat": eh["beat"],
                            "surprise_pct": eh["surprise_pct"],
                            "reaction_1d": reaction_1d,
                            "reaction_3d": reaction_3d,
                            "reaction_5d": reaction_5d,
                        })
                    except Exception:
                        continue

                result["post_earnings_reactions"] = reactions

                if reactions:
                    latest = reactions[0]
                    r1d = latest["reaction_1d"]

                    if latest["beat"] and r1d > 3:
                        result["earnings_score"] += 10
                        result["signals"].append(f"🚀 어닝 비트 후 시장 긍정 반응 (+{r1d}%)")
                    elif latest["beat"] and r1d > 0:
                        result["earnings_score"] += 5
                        result["signals"].append(f"✅ 어닝 비트 후 소폭 상승 (+{r1d}%)")
                    elif latest["beat"] and r1d < -3:
                        result["earnings_score"] -= 5
                        result["signals"].append(f"⚠️ 어닝 비트에도 하락 ({r1d}%) → sell the news")
                    elif not latest["beat"] and r1d > 3:
                        result["earnings_score"] += 5
                        result["signals"].append(f"🔥 어닝 미스에도 상승 ({r1d}%) → 악재 선반영")
                    elif not latest["beat"] and r1d < -5:
                        result["earnings_score"] -= 10
                        result["signals"].append(f"📉 어닝 미스 + 급락 ({r1d}%)")

                    avg_1d = sum(r["reaction_1d"] for r in reactions) / len(reactions)
                    if avg_1d > 3:
                        result["earnings_score"] += 5
                        result["signals"].append(f"📈 최근 {len(reactions)}분기 어닝 후 평균 +{avg_1d:.1f}% 상승")
                    elif avg_1d < -3:
                        result["earnings_score"] -= 5
                        result["signals"].append(f"📉 최근 {len(reactions)}분기 어닝 후 평균 {avg_1d:.1f}% 하락")

        except Exception as e:
            LOG.warning(f"어닝 후 시장 반응 분석 실패: {e}")

    except Exception as e:
        LOG.warning(f"어닝/가이던스 분석 실패: {e}")

    return result
# ── 8. 섹터/동종업계 비교 분석 ────────────────────────
def analyze_sector_comparison(ticker_obj, all_sector_data=None):
    """같은 섹터 내 PER, 성장률 상대 비교"""
    result = {
        "sector": None,
        "industry": None,
        "sector_rank": None,
        "sector_total": None,
        "pe_vs_sector": None,
        "growth_vs_sector": None,
        "sector_score": 0,
        "signals": [],
    }
    try:
        info = ticker_obj.info or {}
        result["sector"] = info.get("sector", "")
        result["industry"] = info.get("industry", "")

        pe = info.get("forwardPE") or info.get("trailingPE")
        growth = info.get("revenueGrowth")
        margin = info.get("profitMargins")
        sector_pe = info.get("sectorPe") or info.get("industryPe")

        # yfinance에서 sectorPe가 없으면 sector 평균 추정
        if pe is not None:
            # 일반적인 섹터 평균 PER 기준
            sector_avg_pe = {
                "Technology": 30, "Communication Services": 25,
                "Consumer Cyclical": 22, "Healthcare": 28,
                "Financial Services": 15, "Industrials": 20,
                "Consumer Defensive": 22, "Energy": 12,
                "Utilities": 18, "Real Estate": 35,
                "Basic Materials": 16,
            }
            avg_pe = sector_avg_pe.get(result["sector"], 22)

            if pe > 0:
                ratio = pe / avg_pe
                result["pe_vs_sector"] = round(ratio, 2)

                if ratio < 0.7:
                    result["sector_score"] += 10
                    result["signals"].append(f"💰 섹터 대비 PER 저평가 ({pe:.1f} vs 평균 {avg_pe})")
                elif ratio < 0.9:
                    result["sector_score"] += 5
                    result["signals"].append(f"✅ 섹터 대비 PER 소폭 저평가 ({pe:.1f} vs {avg_pe})")
                elif ratio > 1.5:
                    result["sector_score"] -= 5
                    result["signals"].append(f"⚠️ 섹터 대비 PER 고평가 ({pe:.1f} vs {avg_pe})")

        if growth is not None:
            sector_avg_growth = {
                "Technology": 0.15, "Communication Services": 0.10,
                "Consumer Cyclical": 0.08, "Healthcare": 0.12,
                "Financial Services": 0.07, "Industrials": 0.06,
                "Consumer Defensive": 0.04, "Energy": 0.05,
                "Utilities": 0.05, "Real Estate": 0.04,
                "Basic Materials": 0.05,
            }
            avg_g = sector_avg_growth.get(result["sector"], 0.08)

            result["growth_vs_sector"] = round(growth / avg_g, 2) if avg_g > 0 else 1.0

            if growth > avg_g * 2:
                result["sector_score"] += 10
                result["signals"].append(f"🔥 섹터 대비 성장률 2배+ ({growth*100:.1f}% vs {avg_g*100:.1f}%)")
            elif growth > avg_g * 1.3:
                result["sector_score"] += 5
                result["signals"].append(f"📈 섹터 평균 이상 성장 ({growth*100:.1f}% vs {avg_g*100:.1f}%)")
            elif growth < avg_g * 0.5 and growth >= 0:
                result["sector_score"] -= 3
                result["signals"].append(f"📉 섹터 평균 이하 성장 ({growth*100:.1f}% vs {avg_g*100:.1f}%)")

        if margin is not None:
            sector_avg_margin = {
                "Technology": 0.20, "Communication Services": 0.15,
                "Consumer Cyclical": 0.08, "Healthcare": 0.15,
                "Financial Services": 0.25, "Industrials": 0.10,
                "Consumer Defensive": 0.08, "Energy": 0.10,
                "Utilities": 0.12, "Real Estate": 0.20,
                "Basic Materials": 0.08,
            }
            avg_m = sector_avg_margin.get(result["sector"], 0.12)
            if margin > avg_m * 1.5:
                result["sector_score"] += 5
                result["signals"].append(f"💎 섹터 대비 높은 마진 ({margin*100:.1f}% vs {avg_m*100:.1f}%)")

    except Exception as e:
        LOG.warning(f"섹터 비교 분석 실패: {e}")

    return result


# ── 9. 자사주 매입 분석 ───────────────────────────────
def analyze_buyback(ticker_obj):
    """발행주식수 변화로 자사주 매입 추정"""
    result = {
        "shares_current": None,
        "shares_year_ago": None,
        "buyback_pct": None,
        "buyback_score": 0,
        "signals": [],
    }
    try:
        # get_shares_full()로 발행주식수 히스토리
        shares = ticker_obj.get_shares_full(start="2024-01-01")
        if shares is not None and len(shares) >= 2:
            current_shares = float(shares.iloc[-1])
            oldest_shares = float(shares.iloc[0])

            result["shares_current"] = int(current_shares)
            result["shares_year_ago"] = int(oldest_shares)

            if oldest_shares > 0:
                change_pct = ((current_shares - oldest_shares) / oldest_shares) * 100
                result["buyback_pct"] = round(change_pct, 2)

                if change_pct < -3:
                    result["buyback_score"] += 15
                    result["signals"].append(f"🔥 대규모 자사주 매입 (주식수 {change_pct:.1f}% 감소)")
                elif change_pct < -1:
                    result["buyback_score"] += 10
                    result["signals"].append(f"✅ 자사주 매입 진행 (주식수 {change_pct:.1f}% 감소)")
                elif change_pct < 0:
                    result["buyback_score"] += 5
                    result["signals"].append(f"✅ 소규모 자사주 매입 ({change_pct:.1f}%)")
                elif change_pct > 5:
                    result["buyback_score"] -= 10
                    result["signals"].append(f"⚠️ 대규모 유상증자 (주식수 +{change_pct:.1f}% 증가)")
                elif change_pct > 2:
                    result["buyback_score"] -= 5
                    result["signals"].append(f"⚠️ 주식수 증가 (+{change_pct:.1f}%) → 희석 우려")
        else:
            # get_shares_full 실패 시 info에서 추정
            info = ticker_obj.info or {}
            shares_out = info.get("sharesOutstanding")
            float_shares = info.get("floatShares")
            if shares_out and float_shares and shares_out > 0:
                buyback_ratio = float_shares / shares_out
                if buyback_ratio < 0.85:
                    result["buyback_score"] += 5
                    result["signals"].append("✅ 유통주식 비율 낮음 (자사주 보유 추정)")

    except Exception as e:
        LOG.warning(f"자사주 매입 분석 실패: {e}")

    return result


# ── 10. 옵션 시장 분석 (풋/콜 비율) ──────────────────
def analyze_options(ticker_obj):
    """옵션 풋/콜 비율로 시장 심리 분석"""
    result = {
        "put_call_ratio": None,
        "put_volume": 0,
        "call_volume": 0,
        "put_oi": 0,
        "call_oi": 0,
        "options_score": 0,
        "signals": [],
    }
    try:
        expirations = ticker_obj.options
        if not expirations:
            return result

        # 가장 가까운 만기 2개만 분석 (속도)
        total_put_vol = 0
        total_call_vol = 0
        total_put_oi = 0
        total_call_oi = 0

        for exp in expirations[:2]:
            try:
                chain = ticker_obj.option_chain(exp)

                if chain.calls is not None and not chain.calls.empty:
                    total_call_vol += chain.calls["volume"].fillna(0).sum()
                    total_call_oi += chain.calls["openInterest"].fillna(0).sum()

                if chain.puts is not None and not chain.puts.empty:
                    total_put_vol += chain.puts["volume"].fillna(0).sum()
                    total_put_oi += chain.puts["openInterest"].fillna(0).sum()
            except Exception:
                continue

        result["put_volume"] = int(total_put_vol)
        result["call_volume"] = int(total_call_vol)
        result["put_oi"] = int(total_put_oi)
        result["call_oi"] = int(total_call_oi)

        # 풋/콜 비율 (거래량 기준)
        if total_call_vol > 0:
            pcr_vol = total_put_vol / total_call_vol
            result["put_call_ratio"] = round(pcr_vol, 2)

            if pcr_vol > 1.5:
                result["options_score"] += 10
                result["signals"].append(f"🔥 극단적 풋/콜 비율 {pcr_vol:.2f} → 공포 극대 (역발상 매수)")
            elif pcr_vol > 1.0:
                result["options_score"] += 5
                result["signals"].append(f"✅ 풋/콜 비율 {pcr_vol:.2f} → 약세 심리 (반등 가능)")
            elif pcr_vol < 0.5:
                result["options_score"] -= 5
                result["signals"].append(f"⚠️ 풋/콜 비율 {pcr_vol:.2f} → 과도한 낙관")
            elif pcr_vol < 0.7:
                result["signals"].append(f"📊 풋/콜 비율 {pcr_vol:.2f} → 낙관 심리")
            else:
                result["signals"].append(f"📊 풋/콜 비율 {pcr_vol:.2f} → 중립")

        # 미결제약정 기준 보충
        if total_call_oi > 0:
            pcr_oi = total_put_oi / total_call_oi
            if pcr_oi > 1.5 and result["options_score"] >= 0:
                result["options_score"] += 5
                result["signals"].append(f"📊 미결제 풋/콜 {pcr_oi:.2f} → 헤지 수요 높음")

    except Exception as e:
        LOG.warning(f"옵션 분석 실패: {e}")

    return result

# ── 종합 분석 ─────────────────────────────────────────
def analyze_ticker_longterm(ticker_str):
    """개별 티커 중장기 종합 분석"""
    try:
        t = yf.Ticker(ticker_str)
        info = t.info or {}

        inst = analyze_institutional(t)
        insider = analyze_insider(t)
        fundamental = analyze_fundamentals(t)
        news = analyze_news(ticker_str, t)
        analyst = analyze_analyst(t)
        trend = analyze_long_trend(t)
        earnings = analyze_earnings(ticker_str, t)  # ★ 추가
        sector_comp = analyze_sector_comparison(t)       # ★ 추가
        buyback = analyze_buyback(t)                 # ★ 추가
        options = analyze_options(t)                  # ★ 추가
        total_score = (
            inst["inst_score"] +
            insider["insider_score"] +
            fundamental["fundamental_score"] +
            news["news_score"] +
            analyst["analyst_score"] +
            trend["trend_score"] +
            earnings["earnings_score"] + # ★ 추가
            sector_comp["sector_score"] +                 # ★ 추가
            buyback["buyback_score"] +                # ★ 추가
            options["options_score"]  
        )

        if total_score >= 70:
            grade = "🔥 강력 매수"
        elif total_score >= 50:
            grade = "✅ 매수"
        elif total_score >= 25:
            grade = "📊 관심"
        elif total_score >= 0:
            grade = "⏳ 관망"
        else:
            grade = "❌ 주의"

        all_signals = (
            inst["signals"] +
            insider["signals"] +
            fundamental["signals"] +
            news["signals"] +
            analyst["signals"] +
            trend["signals"] +
            earnings["signals"] + # ★ 추가
            sector_comp["signals"] +     # ★ 추가
            buyback["signals"] +                      # ★ 추가
            options["signals"]  
        )

        current_price = info.get("currentPrice") or info.get("regularMarketPrice", 0)
        company_name = info.get("shortName", info.get("longName", ticker_str))
        sector = info.get("sector", "")
        industry = info.get("industry", "")

        return {
            "ticker": ticker_str,
            "company": company_name,
            "sector": sector,
            "industry": industry,
            "price": current_price,
            "total_score": total_score,
            "grade": grade,
            "scores": {
                "institutional": inst["inst_score"],
                "insider": insider["insider_score"],
                "fundamental": fundamental["fundamental_score"],
                "news": news["news_score"],
                "analyst": analyst["analyst_score"],
                "trend": trend["trend_score"],
                "earnings": earnings["earnings_score"],  # ★ 추가
                "sector": sector_comp["sector_score"],         # ★ 추가
                "buyback": buyback["buyback_score"],      # ★ 추가
                "options": options["options_score"],  
            },
            "signals": all_signals,
            "institutional": {
                "pct": inst["inst_pct"],
                "top_holders": inst["top_holders"][:3],
            },
            "fundamentals": {
                "pe": fundamental["pe_ratio"],
                "forward_pe": fundamental["forward_pe"],
                "peg": fundamental["peg_ratio"],
                "revenue_growth": fundamental["revenue_growth"],
                "earnings_growth": fundamental["earnings_growth"],
                "debt_to_equity": fundamental["debt_to_equity"],
                "profit_margin": fundamental["profit_margin"],
                "roe": fundamental["roe"],
                "dividend_yield": fundamental["dividend_yield"],
            },
            "analyst": {
                "recommendation": analyst["recommendation"],
                "target_mean": analyst["target_mean"],
                "target_high": analyst["target_high"],
                "num_analysts": analyst["num_analysts"],
            },
            "news_summary": {
                "positive": news["positive"],
                "negative": news["negative"],
                "neutral": news["neutral"],
                "recent": news["news"][:5],
            },
            "trend": {
                "above_200ma": trend["above_200ma"],
                "ma200_gap": trend["ma200_gap_pct"],
                "above_50ma": trend["above_50ma"],
                "weekly_rsi": trend["weekly_rsi"],
            },
            # ★ 어닝 섹션 추가
            "earnings": {
                "consecutive_beats": earnings["consecutive_beats"],
                "last_surprise_pct": earnings["last_surprise_pct"],
                "guidance_direction": earnings["guidance_direction"],
                "next_earnings_date": earnings["next_earnings_date"],
                "history": earnings["earnings_history"][:4],
                "post_reactions": earnings.get("post_earnings_reactions", []),
            },

        }

    except Exception as e:
        LOG.error(f"[{ticker_str}] 중장기 분석 실패: {e}")
        return None


# ── 메인 스캔 ─────────────────────────────────────────
def analyze_longterm(universe="sp500+sox"):
    """전체 유니버스 중장기 스캔"""
    umap = UNIVERSE_MAP.get(universe, UNIVERSE_MAP["sp500+sox"])
    symbols = umap["symbols"] if isinstance(umap, dict) else umap

    LOG.info(f"중장기 분석 시작: {universe} ({len(symbols)}종목)")

    results = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(analyze_ticker_longterm, s): s for s in symbols}
        done = 0
        for future in as_completed(futures):
            done += 1
            ticker = futures[future]
            if done % 10 == 0:
                LOG.info(f"중장기 분석 진행: {done}/{len(symbols)}")
            try:
                r = future.result()
                if r and r["total_score"] is not None:
                    results.append(r)
            except Exception as e:
                LOG.warning(f"[{ticker}] 실패: {e}")

    # 점수순 정렬
    results.sort(key=lambda x: x["total_score"], reverse=True)
    top = results[:LONGTERM_MAX_RESULTS]

    # 저장
    ts = datetime.now().strftime(HISTORY_TS_FMT)
    save_data = {
        "timestamp": ts,
        "universe": universe,
        "total_scanned": len(symbols),
        "results_count": len(top),
        "results": top,
    }

    fpath = LONGTERM_DIR / f"{ts}.json"
    with open(fpath, "w", encoding="utf-8") as f:
        json.dump(save_data, f, ensure_ascii=False, indent=2, default=str)
    LOG.info(f"중장기 분석 저장: {fpath}")

    # 텔레그램 전송
    _send_longterm_telegram(top, universe, len(symbols))

    return save_data


def _send_longterm_telegram(results, universe, total):
    """텔레그램 중장기 분석 요약"""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return

    lines = [f"📋 중장기 투자 분석 ({universe.upper()}, {total}종목 스캔)\n"]

    for i, r in enumerate(results[:10], 1):
        score = r["total_score"]
        grade = r["grade"]
        ticker = r["ticker"]
        company = r["company"][:15]
        price = r.get("price", 0)

        # ★ 이 줄이 수정된 부분 (어닝 추가)
        sc = r["scores"]
        detail = (f"기관{sc['institutional']}|펀더{sc['fundamental']}|뉴스{sc['news']}"
                  f"|애널{sc['analyst']}|추세{sc['trend']}|어닝{sc.get('earnings', 0)}"
                  f"|섹터{sc.get('sector', 0)}|바이백{sc.get('buyback', 0)}|옵션{sc.get('options', 0)}")


        target = r["analyst"].get("target_mean")
        target_str = f" → 목표${target:.0f}" if target else ""

        lines.append(f"{i}. {grade} {ticker} ({company}) ${price:.2f}{target_str}")
        lines.append(f"   총점 {score}점 [{detail}]")

        # 주요 시그널 2개만
        for sig in r["signals"][:2]:
            lines.append(f"   {sig}")
        lines.append("")

    text = "\n".join(lines)

    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text[:4000],
            "parse_mode": "HTML",
        }, timeout=10)
    except Exception as e:
        LOG.warning(f"텔레그램 전송 실패: {e}")


# ── CLI ───────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    universe = sys.argv[1] if len(sys.argv) > 1 else "sp500+sox"
    analyze_longterm(universe)
