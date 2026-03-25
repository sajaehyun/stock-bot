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

        # 종합 점수 (100점 만점 스케일)
        total_score = (
            inst["inst_score"] +
            insider["insider_score"] +
            fundamental["fundamental_score"] +
            news["news_score"] +
            analyst["analyst_score"] +
            trend["trend_score"]
        )

        # 등급
        if total_score >= 60:
            grade = "🔥 강력 매수"
        elif total_score >= 40:
            grade = "✅ 매수"
        elif total_score >= 20:
            grade = "📊 관심"
        elif total_score >= 0:
            grade = "⏳ 관망"
        else:
            grade = "❌ 주의"

        # 모든 시그널 합치기
        all_signals = (
            inst["signals"] +
            insider["signals"] +
            fundamental["signals"] +
            news["signals"] +
            analyst["signals"] +
            trend["signals"]
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

        # 세부 점수
        sc = r["scores"]
        detail = f"기관{sc['institutional']}|펀더{sc['fundamental']}|뉴스{sc['news']}|애널{sc['analyst']}|추세{sc['trend']}"

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
