"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  장기 투자 종합 분석기 (Long-Term Investment Analyzer)
  
  적용된 투자 이론:
    - Warren Buffett  : 경제적 해자, ROE, 내재가치 (DCF)
    - Benjamin Graham : 안전마진, P/B, P/E, 유동비율
    - Peter Lynch     : PEG 비율, 성장성 vs 가격 비교
    - Philip Fisher   : 이익률 추세, 지속 성장성
    - Joel Greenblatt : Magic Formula (ROC + EY)
    - John Templeton  : 저점 매수, 상대적 저평가
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import os, io, json, warnings, time, requests
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime
from dataclasses import dataclass, field
from typing import Optional
from contextlib import redirect_stdout

from rich.console import Console
from rich.ansi import AnsiDecoder
from rich.theme import Theme

warnings.filterwarnings("ignore")

# ─── S&P 500 전 종목 자동 수집 ────────────────────────────────
def _load_sp500_targets() -> dict:
    try:
        url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
        table = pd.read_html(url, storage_options={'User-Agent': 'Mozilla/5.0'})[0]
        result = {}
        for _, row in table.iterrows():
            ticker = row['Symbol'].replace('.', '-')
            name = row['Security']
            result[ticker] = name
        print(f"✅ S&P 500 총 {len(result)}개 종목 로드 완료")
        return result
    except Exception as e:
        print(f"⚠ S&P 500 로드 실패, 기본 종목 사용: {e}")
        return {
            "AAPL": "Apple Inc.", "MSFT": "Microsoft Corp.", "NVDA": "NVIDIA Corp.",
            "GOOGL": "Alphabet Inc.", "AMZN": "Amazon.com Inc.", "META": "Meta Platforms",
            "TSLA": "Tesla Inc.", "BRK-B": "Berkshire Hathaway", "AVGO": "Broadcom Inc.",
        }

TARGET_STOCKS = _load_sp500_targets()


# ─── 색상 코드 (터미널 출력용) ─────────────────────────────────
class C:
    RESET   = "\033[0m"
    BOLD    = "\033[1m"
    RED     = "\033[91m"
    GREEN   = "\033[92m"
    YELLOW  = "\033[93m"
    BLUE    = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN    = "\033[96m"
    WHITE   = "\033[97m"
    GRAY    = "\033[90m"
    BG_DARK = "\033[40m"

def cprint(text, color=C.WHITE, bold=False):
    prefix = C.BOLD if bold else ""
    print(f"{prefix}{color}{text}{C.RESET}")


# ─── 데이터 클래스 ─────────────────────────────────────────────
@dataclass
class StockMetrics:
    ticker: str
    name: str
    price: float = 0.0
    currency: str = "USD"

    # Valuation
    pe_ratio: Optional[float] = None
    pb_ratio: Optional[float] = None
    ps_ratio: Optional[float] = None
    ev_ebitda: Optional[float] = None
    peg_ratio: Optional[float] = None

    # Profitability
    roe: Optional[float] = None
    roa: Optional[float] = None
    gross_margin: Optional[float] = None
    operating_margin: Optional[float] = None
    net_margin: Optional[float] = None
    return_on_capital: Optional[float] = None

    # Financial Health
    current_ratio: Optional[float] = None
    debt_to_equity: Optional[float] = None
    quick_ratio: Optional[float] = None
    interest_coverage: Optional[float] = None

    # Growth
    revenue_growth_yoy: Optional[float] = None
    earnings_growth_yoy: Optional[float] = None
    revenue_growth_3y: Optional[float] = None

    # Technical / Price
    price_52w_high: Optional[float] = None
    price_52w_low: Optional[float] = None
    ma50: Optional[float] = None
    ma200: Optional[float] = None
    rsi: Optional[float] = None
    beta: Optional[float] = None

    # DCF / Intrinsic Value
    intrinsic_value_dcf: Optional[float] = None
    book_value_per_share: Optional[float] = None
    eps: Optional[float] = None
    eps_growth_estimate: Optional[float] = None

    # Dividend
    dividend_yield: Optional[float] = None
    payout_ratio: Optional[float] = None

    # Scores (0~100)
    score_buffett: float = 0.0
    score_graham: float = 0.0
    score_lynch: float = 0.0
    score_fisher: float = 0.0
    score_greenblatt: float = 0.0
    score_templeton: float = 0.0
    score_technical: float = 0.0
    total_score: float = 0.0

    signal: str = "HOLD"
    signal_reason: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


# ─── 스코어링 엔진 ─────────────────────────────────────────────

def score_buffett(m: StockMetrics) -> tuple:
    score = 0.0
    reasons = []
    MAX = 100.0

    if m.roe is not None:
        if m.roe >= 20:
            score += 30; reasons.append(f"✅ [버핏] ROE {m.roe:.1f}% — 탁월한 경쟁 우위")
        elif m.roe >= 15:
            score += 22; reasons.append(f"✅ [버핏] ROE {m.roe:.1f}% — 양호한 수익성")
        elif m.roe >= 10:
            score += 12; reasons.append(f"⚠ [버핏] ROE {m.roe:.1f}% — 평균 수준")
        else:
            reasons.append(f"❌ [버핏] ROE {m.roe:.1f}% — 경쟁 우위 부족")

    if m.net_margin is not None:
        if m.net_margin >= 20:
            score += 20; reasons.append(f"✅ [버핏] 순이익률 {m.net_margin:.1f}% — 해자(Moat) 확인")
        elif m.net_margin >= 10:
            score += 13; reasons.append(f"✅ [버핏] 순이익률 {m.net_margin:.1f}% — 양호")
        elif m.net_margin >= 5:
            score += 6

    if m.debt_to_equity is not None:
        de = m.debt_to_equity
        if de < 30:
            score += 20; reasons.append(f"✅ [버핏] 부채비율 {de:.0f}% — 재무 건전")
        elif de < 80:
            score += 12
        elif de < 150:
            score += 5
        else:
            reasons.append(f"❌ [버핏] 부채비율 {de:.0f}% — 과도한 부채")

    if m.intrinsic_value_dcf and m.price > 0:
        margin = (m.intrinsic_value_dcf - m.price) / m.intrinsic_value_dcf * 100
        if margin >= 30:
            score += 20; reasons.append(f"✅ [버핏] DCF 안전마진 {margin:.0f}% — 강력 매수")
        elif margin >= 15:
            score += 13; reasons.append(f"✅ [버핏] DCF 안전마진 {margin:.0f}% — 매수 고려")
        elif margin >= 0:
            score += 6
        else:
            reasons.append(f"⚠ [버핏] DCF 대비 {abs(margin):.0f}% 고평가")

    if m.interest_coverage is not None:
        if m.interest_coverage > 10:
            score += 10
        elif m.interest_coverage > 5:
            score += 6

    return round(min(score, MAX), 1), reasons


def score_graham(m: StockMetrics) -> tuple:
    score = 0.0
    reasons = []
    MAX = 100.0

    if m.pe_ratio is not None and m.pe_ratio > 0:
        if m.pe_ratio <= 10:
            score += 25; reasons.append(f"✅ [그레이엄] P/E {m.pe_ratio:.1f} — 심각한 저평가")
        elif m.pe_ratio <= 15:
            score += 18; reasons.append(f"✅ [그레이엄] P/E {m.pe_ratio:.1f} — 저평가 구간")
        elif m.pe_ratio <= 20:
            score += 10
        elif m.pe_ratio <= 25:
            score += 4
        else:
            reasons.append(f"❌ [그레이엄] P/E {m.pe_ratio:.1f} — 고평가 경고")

    if m.pb_ratio is not None and m.pb_ratio > 0:
        if m.pb_ratio <= 1.0:
            score += 25; reasons.append(f"✅ [그레이엄] P/B {m.pb_ratio:.2f} — 청산가치 이하")
        elif m.pb_ratio <= 1.5:
            score += 18; reasons.append(f"✅ [그레이엄] P/B {m.pb_ratio:.2f} — 자산 대비 저평가")
        elif m.pb_ratio <= 3.0:
            score += 8
        else:
            reasons.append(f"⚠ [그레이엄] P/B {m.pb_ratio:.2f} — 자산 대비 고평가")

    if m.eps and m.book_value_per_share and m.eps > 0 and m.book_value_per_share > 0:
        graham_num = (22.5 * m.eps * m.book_value_per_share) ** 0.5
        if m.price > 0:
            discount = (graham_num - m.price) / graham_num * 100
            if discount >= 20:
                score += 25; reasons.append(f"✅ [그레이엄] Graham Number {graham_num:.2f} (현가 {discount:.0f}% 할인)")
            elif discount >= 0:
                score += 15; reasons.append(f"✅ [그레이엄] Graham Number {graham_num:.2f} (현가 할인)")
            else:
                reasons.append(f"⚠ [그레이엄] Graham Number {graham_num:.2f} (현가 {abs(discount):.0f}% 초과)")

    if m.current_ratio is not None:
        if m.current_ratio >= 2.0:
            score += 15; reasons.append(f"✅ [그레이엄] 유동비율 {m.current_ratio:.1f} — 안전")
        elif m.current_ratio >= 1.5:
            score += 10
        elif m.current_ratio >= 1.0:
            score += 4
        else:
            reasons.append(f"❌ [그레이엄] 유동비율 {m.current_ratio:.1f} — 단기 유동성 위험")

    if m.dividend_yield is not None and m.dividend_yield > 0:
        if m.dividend_yield >= 3:
            score += 10
        elif m.dividend_yield >= 1.5:
            score += 6

    return round(min(score, MAX), 1), reasons


def score_lynch(m: StockMetrics) -> tuple:
    score = 0.0
    reasons = []
    MAX = 100.0

    if m.peg_ratio is not None and m.peg_ratio > 0:
        if m.peg_ratio <= 0.5:
            score += 40; reasons.append(f"✅ [린치] PEG {m.peg_ratio:.2f} — 성장 대비 심각 저평가 (10루타 후보)")
        elif m.peg_ratio <= 1.0:
            score += 28; reasons.append(f"✅ [린치] PEG {m.peg_ratio:.2f} — 성장 대비 저평가")
        elif m.peg_ratio <= 1.5:
            score += 14
        elif m.peg_ratio <= 2.0:
            score += 6
        else:
            reasons.append(f"❌ [린치] PEG {m.peg_ratio:.2f} — 성장 대비 고평가")
    else:
        if m.earnings_growth_yoy is not None and m.pe_ratio and m.pe_ratio > 0:
            implied_peg = m.pe_ratio / max(m.earnings_growth_yoy, 0.1)
            reasons.append(f"ℹ [린치] 추정 PEG {implied_peg:.2f} (EPS 성장 {m.earnings_growth_yoy:.1f}% 기반)")
            if implied_peg <= 1.0:
                score += 25
            elif implied_peg <= 1.5:
                score += 12

    growth = m.earnings_growth_yoy or m.eps_growth_estimate
    if growth is not None:
        if growth >= 30:
            score += 30; reasons.append(f"✅ [린치] EPS 성장률 {growth:.1f}% — 고성장주")
        elif growth >= 20:
            score += 22; reasons.append(f"✅ [린치] EPS 성장률 {growth:.1f}% — 성장주")
        elif growth >= 10:
            score += 14
        elif growth > 0:
            score += 6
        else:
            reasons.append(f"❌ [린치] EPS 역성장 {growth:.1f}%")

    if m.revenue_growth_yoy is not None:
        if m.revenue_growth_yoy >= 20:
            score += 15; reasons.append(f"✅ [린치] 매출 성장 {m.revenue_growth_yoy:.1f}%")
        elif m.revenue_growth_yoy >= 10:
            score += 10
        elif m.revenue_growth_yoy >= 5:
            score += 5

    if m.revenue_growth_3y is not None and m.revenue_growth_3y > 0:
        score += min(15, m.revenue_growth_3y * 0.5)
        reasons.append(f"✅ [린치] 3년 매출 CAGR {m.revenue_growth_3y:.1f}%")

    return round(min(score, MAX), 1), reasons


def score_fisher(m: StockMetrics) -> tuple:
    score = 0.0
    reasons = []
    MAX = 100.0

    if m.gross_margin is not None:
        if m.gross_margin >= 50:
            score += 30; reasons.append(f"✅ [피셔] 매출총이익률 {m.gross_margin:.1f}% — 강력한 가격 결정력")
        elif m.gross_margin >= 35:
            score += 22; reasons.append(f"✅ [피셔] 매출총이익률 {m.gross_margin:.1f}% — 양호한 경쟁력")
        elif m.gross_margin >= 20:
            score += 12
        else:
            reasons.append(f"⚠ [피셔] 매출총이익률 {m.gross_margin:.1f}% — 경쟁 취약")

    if m.operating_margin is not None:
        if m.operating_margin >= 25:
            score += 25; reasons.append(f"✅ [피셔] 영업이익률 {m.operating_margin:.1f}% — 운영 효율 탁월")
        elif m.operating_margin >= 15:
            score += 18
        elif m.operating_margin >= 8:
            score += 10
        elif m.operating_margin >= 0:
            score += 4
        else:
            reasons.append(f"❌ [피셔] 영업손실 {m.operating_margin:.1f}%")

    if m.roa is not None:
        if m.roa >= 15:
            score += 25; reasons.append(f"✅ [피셔] ROA {m.roa:.1f}% — 자산 활용 탁월")
        elif m.roa >= 8:
            score += 16
        elif m.roa >= 4:
            score += 8

    if m.revenue_growth_3y is not None:
        if m.revenue_growth_3y >= 20:
            score += 20; reasons.append(f"✅ [피셔] 3년 매출 CAGR {m.revenue_growth_3y:.1f}% — 장기 성장 확인")
        elif m.revenue_growth_3y >= 10:
            score += 13
        elif m.revenue_growth_3y >= 5:
            score += 6

    return round(min(score, MAX), 1), reasons


def score_greenblatt(m: StockMetrics) -> tuple:
    score = 0.0
    reasons = []
    MAX = 100.0

    if m.pe_ratio and m.pe_ratio > 0:
        ey = 100 / m.pe_ratio
        if ey >= 10:
            score += 50; reasons.append(f"✅ [그린블라트] 이익수익률(EY) {ey:.1f}% — Magic Formula 최우선")
        elif ey >= 7:
            score += 35; reasons.append(f"✅ [그린블라트] 이익수익률(EY) {ey:.1f}% — 양호")
        elif ey >= 5:
            score += 20
        elif ey >= 4:
            score += 10
        else:
            reasons.append(f"⚠ [그린블라트] 이익수익률(EY) {ey:.1f}% — 수익성 낮음")
    elif m.ev_ebitda and m.ev_ebitda > 0:
        ey2 = 100 / m.ev_ebitda
        if ey2 >= 10:
            score += 40
        elif ey2 >= 7:
            score += 25

    roc = m.return_on_capital or m.roe
    if roc is not None:
        if roc >= 25:
            score += 50; reasons.append(f"✅ [그린블라트] 자본수익률(ROC) {roc:.1f}% — Magic Formula 최우선")
        elif roc >= 15:
            score += 35; reasons.append(f"✅ [그린블라트] 자본수익률(ROC) {roc:.1f}% — 양호")
        elif roc >= 8:
            score += 18
        else:
            reasons.append(f"⚠ [그린블라트] 자본수익률(ROC) {roc:.1f}%")

    return round(min(score, MAX), 1), reasons


def score_templeton(m: StockMetrics) -> tuple:
    score = 0.0
    reasons = []
    MAX = 100.0

    if m.price > 0 and m.price_52w_high and m.price_52w_high > 0:
        drop = (m.price_52w_high - m.price) / m.price_52w_high * 100
        if drop >= 40:
            score += 40; reasons.append(f"✅ [템플턴] 52주 고점 대비 -{drop:.0f}% — 역발상 매수 기회")
        elif drop >= 25:
            score += 28; reasons.append(f"✅ [템플턴] 52주 고점 대비 -{drop:.0f}% — 조정 구간")
        elif drop >= 15:
            score += 16
        elif drop >= 5:
            score += 8
        else:
            reasons.append(f"ℹ [템플턴] 52주 고점 대비 -{drop:.0f}% (고점 근처)")

    if m.price > 0 and m.price_52w_low and m.price_52w_high:
        rng = m.price_52w_high - m.price_52w_low
        if rng > 0:
            pos = (m.price - m.price_52w_low) / rng
            if pos <= 0.2:
                score += 20; reasons.append(f"✅ [템플턴] 연간 밴드 하위 {pos*100:.0f}% — 저점 매수 영역")
            elif pos <= 0.4:
                score += 13
            elif pos <= 0.6:
                score += 6

    if m.ps_ratio is not None and m.ps_ratio > 0:
        if m.ps_ratio <= 1.0:
            score += 25; reasons.append(f"✅ [템플턴] P/S {m.ps_ratio:.2f} — 극도의 저평가")
        elif m.ps_ratio <= 2.0:
            score += 17
        elif m.ps_ratio <= 4.0:
            score += 9
        else:
            reasons.append(f"⚠ [템플턴] P/S {m.ps_ratio:.2f} — 매출 대비 고평가")

    if m.dividend_yield and m.dividend_yield > 0:
        if m.dividend_yield >= 4:
            score += 15; reasons.append(f"✅ [템플턴] 배당수익률 {m.dividend_yield:.1f}% — 인컴 매력")
        elif m.dividend_yield >= 2:
            score += 9
        elif m.dividend_yield >= 1:
            score += 4

    return round(min(score, MAX), 1), reasons


def score_technical(m: StockMetrics) -> tuple:
    score = 0.0
    reasons = []
    MAX = 100.0

    if m.ma50 and m.ma200 and m.ma200 > 0:
        ratio = m.ma50 / m.ma200
        if ratio >= 1.05:
            score += 35; reasons.append(f"✅ [기술적] MA50/MA200 {ratio:.2f} — 강세 추세")
        elif ratio >= 1.0:
            score += 22; reasons.append(f"✅ [기술적] MA50 > MA200 — 골든크로스")
        elif ratio >= 0.95:
            score += 10
        else:
            reasons.append(f"⚠ [기술적] MA50 < MA200 — 약세 추세 (데스크로스)")
            score += 5

    if m.price > 0 and m.ma200 and m.ma200 > 0:
        diff = (m.price - m.ma200) / m.ma200 * 100
        if -10 <= diff <= 5:
            score += 30; reasons.append(f"✅ [기술적] MA200 근접 ({diff:+.1f}%) — 장기 지지선 매수 기회")
        elif diff < -10:
            score += 20; reasons.append(f"✅ [기술적] MA200 하회 {diff:.1f}% — 역발상 저점")
        elif diff <= 20:
            score += 18
        elif diff <= 40:
            score += 10

    if m.rsi is not None:
        if m.rsi <= 30:
            score += 35; reasons.append(f"✅ [기술적] RSI {m.rsi:.0f} — 과매도 (강한 매수 신호)")
        elif m.rsi <= 45:
            score += 24; reasons.append(f"✅ [기술적] RSI {m.rsi:.0f} — 저점 구간")
        elif m.rsi <= 55:
            score += 16
        elif m.rsi <= 70:
            score += 8
        else:
            reasons.append(f"⚠ [기술적] RSI {m.rsi:.0f} — 과매수 경고")

    return round(min(score, MAX), 1), reasons


# ─── 데이터 수집 ───────────────────────────────────────────────
def fetch_stock_data(ticker: str, name: str) -> StockMetrics:
    m = StockMetrics(ticker=ticker, name=name)
    try:
        stock = yf.Ticker(ticker)
        info = stock.info or {}

        m.price    = info.get("currentPrice") or info.get("regularMarketPrice") or 0.0
        m.currency = info.get("currency", "USD")
        m.beta     = info.get("beta")

        m.pe_ratio            = info.get("trailingPE") or info.get("forwardPE")
        m.pb_ratio            = info.get("priceToBook")
        m.ps_ratio            = info.get("priceToSalesTrailing12Months")
        m.ev_ebitda           = info.get("enterpriseToEbitda")
        m.peg_ratio           = info.get("trailingPegRatio") or info.get("pegRatio")
        m.eps                 = info.get("trailingEps")
        m.book_value_per_share = info.get("bookValue")

        m.roe              = _pct(info.get("returnOnEquity"))
        m.roa              = _pct(info.get("returnOnAssets"))
        m.gross_margin     = _pct(info.get("grossMargins"))
        m.operating_margin = _pct(info.get("operatingMargins"))
        m.net_margin       = _pct(info.get("profitMargins"))

        m.current_ratio     = info.get("currentRatio")
        m.quick_ratio       = info.get("quickRatio")
        m.debt_to_equity    = info.get("debtToEquity")
        m.interest_coverage = _calc_interest_coverage(info)

        m.revenue_growth_yoy  = _pct(info.get("revenueGrowth"))
        m.earnings_growth_yoy = _pct(info.get("earningsGrowth"))
        m.eps_growth_estimate = _pct(info.get("earningsQuarterlyGrowth"))
        m.revenue_growth_3y   = _calc_revenue_growth_3y(stock)

        m.dividend_yield = _pct(info.get("dividendYield"))
        m.payout_ratio   = _pct(info.get("payoutRatio"))

        m.price_52w_high = info.get("fiftyTwoWeekHigh")
        m.price_52w_low  = info.get("fiftyTwoWeekLow")
        m.ma50           = info.get("fiftyDayAverage")
        m.ma200          = info.get("twoHundredDayAverage")
        m.rsi            = _calc_rsi(stock)

        m.intrinsic_value_dcf = _calc_dcf(info)
        m.return_on_capital   = _calc_roc(info)

    except Exception as e:
        cprint(f"  ⚠ 데이터 수집 오류 ({ticker}): {e}", C.YELLOW)

    return m


def _pct(val):
    if val is None:
        return None
    return round(val * 100, 2) if abs(val) < 10 else round(val, 2)


def _calc_interest_coverage(info: dict) -> Optional[float]:
    ebit = info.get("ebit")
    interest = info.get("interestExpense")
    if ebit and interest and interest != 0:
        return round(abs(ebit / interest), 2)
    return None


def _calc_roc(info: dict) -> Optional[float]:
    ebit = info.get("ebit")
    total_assets = info.get("totalAssets")
    total_liab = info.get("totalDebt")
    if ebit and total_assets:
        invested = total_assets - (total_liab or 0)
        if invested > 0:
            return round(ebit / invested * 100, 2)
    return None


def _calc_dcf(info: dict, wacc: float = 0.10, terminal_growth: float = 0.03, years: int = 10) -> Optional[float]:
    eps = info.get("trailingEps")
    growth = info.get("earningsGrowth") or info.get("revenueGrowth") or 0.08
    if not eps or eps <= 0:
        return None
    growth = max(min(float(growth), 0.30), -0.05)
    pv = 0.0
    for t in range(1, years + 1):
        future_eps = eps * ((1 + growth) ** t)
        pv += future_eps / ((1 + wacc) ** t)
    terminal_eps = eps * ((1 + growth) ** years)
    terminal_value = terminal_eps * (1 + terminal_growth) / (wacc - terminal_growth)
    pv += terminal_value / ((1 + wacc) ** years)
    return round(pv, 2)


def _calc_rsi(stock, period: int = 14) -> Optional[float]:
    try:
        hist = stock.history(period="3mo")
        if hist.empty or len(hist) < period + 1:
            return None
        delta = hist["Close"].diff()
        gain = delta.clip(lower=0).rolling(period).mean()
        loss = (-delta.clip(upper=0)).rolling(period).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        return round(rsi.iloc[-1], 1)
    except:
        return None


def _calc_revenue_growth_3y(stock) -> Optional[float]:
    try:
        fin = stock.financials
        if fin is None or fin.empty:
            return None
        rev_row = fin.loc["Total Revenue"] if "Total Revenue" in fin.index else None
        if rev_row is None or len(rev_row) < 3:
            return None
        latest = rev_row.iloc[0]
        oldest = rev_row.iloc[min(2, len(rev_row)-1)]
        if oldest > 0:
            cagr = (latest / oldest) ** (1/3) - 1
            return round(cagr * 100, 2)
    except:
        pass
    return None


# ─── 종합 스코어 및 시그널 ─────────────────────────────────────

WEIGHTS = {
    "buffett":    0.25,
    "graham":     0.18,
    "lynch":      0.18,
    "fisher":     0.12,
    "greenblatt": 0.12,
    "templeton":  0.10,
    "technical":  0.05,
}

def compute_total_score(m: StockMetrics) -> StockMetrics:
    sb, rb   = score_buffett(m)
    sg, rg   = score_graham(m)
    sl, rl   = score_lynch(m)
    sf, rf   = score_fisher(m)
    sgb, rgb = score_greenblatt(m)
    st, rt   = score_templeton(m)
    stch, rtch = score_technical(m)

    m.score_buffett    = sb
    m.score_graham     = sg
    m.score_lynch      = sl
    m.score_fisher     = sf
    m.score_greenblatt = sgb
    m.score_templeton  = st
    m.score_technical  = stch

    m.total_score = round(
        sb   * WEIGHTS["buffett"]    +
        sg   * WEIGHTS["graham"]     +
        sl   * WEIGHTS["lynch"]      +
        sf   * WEIGHTS["fisher"]     +
        sgb  * WEIGHTS["greenblatt"] +
        st   * WEIGHTS["templeton"]  +
        stch * WEIGHTS["technical"],
        1
    )

    m.signal_reason = rb + rg + rl + rf + rgb + rt + rtch

    if m.total_score >= 72:
        m.signal = "🟢 강력 매수"
    elif m.total_score >= 58:
        m.signal = "🟩 매수"
    elif m.total_score >= 45:
        m.signal = "🟡 관망 / 분할 매수"
    elif m.total_score >= 30:
        m.signal = "🟠 매도 고려"
    else:
        m.signal = "🔴 매도"

    if m.debt_to_equity and m.debt_to_equity > 200:
        m.warnings.append("⛔ 극단적 고부채 경고")
    if m.pe_ratio and m.pe_ratio > 50:
        m.warnings.append("⛔ P/E 50 초과 — 고평가 주의")
    if m.rsi and m.rsi > 75:
        m.warnings.append("⛔ RSI 과매수 — 단기 조정 위험")
    if m.current_ratio and m.current_ratio < 1.0:
        m.warnings.append("⛔ 유동비율 1 미만 — 단기 유동성 위험")
    if m.net_margin and m.net_margin < 0:
        m.warnings.append("⛔ 순손실 기업")

    return m


# ─── 출력 ─────────────────────────────────────────────────────

def _bar(score: float, width: int = 30) -> str:
    filled = int(score / 100 * width)
    empty = width - filled
    if score >= 70:
        color = C.GREEN
    elif score >= 50:
        color = C.YELLOW
    elif score >= 30:
        color = C.MAGENTA
    else:
        color = C.RED
    return f"{color}{'█' * filled}{C.GRAY}{'░' * empty}{C.RESET}"


def _signal_color(signal: str) -> str:
    if "강력 매수" in signal: return C.GREEN
    elif "매수" in signal:    return C.CYAN
    elif "관망" in signal:    return C.YELLOW
    elif "매도 고려" in signal: return C.MAGENTA
    else:                      return C.RED


def print_stock_report(m: StockMetrics):
    w = 68
    sep = "─" * w

    cprint(f"\n{'═' * w}", C.CYAN, bold=True)
    cprint(f"  {m.name}  ({m.ticker})", C.WHITE, bold=True)
    cprint(f"  현재가: {m.currency} {m.price:,.2f}", C.CYAN)
    cprint(f"{'═' * w}", C.CYAN, bold=True)

    print()
    cprint("  [ 핵심 지표 ]", C.BLUE, bold=True)
    cprint(f"  {sep}", C.GRAY)

    def _row(label, val, unit=""):
        if val is None:
            display = f"{C.GRAY}N/A{C.RESET}"
        elif isinstance(val, float):
            display = f"{C.WHITE}{val:,.2f}{unit}{C.RESET}"
        else:
            display = f"{C.WHITE}{val}{unit}{C.RESET}"
        print(f"  {C.GRAY}{label:<22}{C.RESET}{display}")

    _row("P/E",              m.pe_ratio)
    _row("P/B",              m.pb_ratio)
    _row("P/S",              m.ps_ratio)
    _row("EV/EBITDA",        m.ev_ebitda)
    _row("PEG",              m.peg_ratio)
    _row("ROE",              m.roe, "%")
    _row("순이익률",          m.net_margin, "%")
    _row("영업이익률",        m.operating_margin, "%")
    _row("매출총이익률",      m.gross_margin, "%")
    _row("부채/자본",         m.debt_to_equity, "%")
    _row("유동비율",          m.current_ratio)
    _row("배당수익률",        m.dividend_yield, "%")
    _row("EPS 성장(YoY)",    m.earnings_growth_yoy, "%")
    _row("매출 성장(YoY)",   m.revenue_growth_yoy, "%")
    _row("3년 매출 CAGR",    m.revenue_growth_3y, "%")
    _row("RSI(14)",          m.rsi)
    _row("Beta",             m.beta)
    if m.intrinsic_value_dcf:
        _row("DCF 내재가치",  m.intrinsic_value_dcf, f" {m.currency}")
        if m.price > 0:
            margin = (m.intrinsic_value_dcf - m.price) / m.intrinsic_value_dcf * 100
            _row("  → 안전마진", round(margin, 1), "%")
    _row("52주 고점",         m.price_52w_high, f" {m.currency}")
    _row("52주 저점",         m.price_52w_low, f" {m.currency}")

    print()
    cprint("  [ 투자자별 스코어 ]", C.BLUE, bold=True)
    cprint(f"  {sep}", C.GRAY)

    scores = [
        ("버핏  (Buffett)",         m.score_buffett,    "경제적 해자·내재가치"),
        ("그레이엄 (Graham)",        m.score_graham,     "자산가치·안전마진"),
        ("린치  (Lynch)",            m.score_lynch,      "PEG·성장성"),
        ("피셔  (Fisher)",           m.score_fisher,     "이익률·장기성장"),
        ("그린블라트 (Greenblatt)",  m.score_greenblatt, "Magic Formula"),
        ("템플턴 (Templeton)",       m.score_templeton,  "역발상·저평가"),
        ("기술적 분석",              m.score_technical,  "MA·RSI"),
    ]
    for label, sc, desc in scores:
        bar = _bar(sc)
        color = C.GREEN if sc >= 70 else (C.YELLOW if sc >= 50 else C.RED)
        print(f"  {C.GRAY}{label:<22}{C.RESET} {bar} {color}{sc:>5.1f}{C.RESET}  {C.GRAY}{desc}{C.RESET}")

    print()
    total_bar = _bar(m.total_score, width=40)
    total_color = C.GREEN if m.total_score >= 70 else (C.YELLOW if m.total_score >= 50 else C.RED)
    cprint(f"  {'─'*w}", C.CYAN)
    print(f"  {C.BOLD}{C.WHITE}{'종합 점수':<22}{C.RESET} {total_bar} {C.BOLD}{total_color}{m.total_score:>5.1f} / 100{C.RESET}")

    sc = _signal_color(m.signal)
    print()
    print(f"  {C.BOLD}투자 판단:{C.RESET}  {C.BOLD}{sc}{m.signal}{C.RESET}")

    if m.warnings:
        print()
        cprint("  ⚠ 리스크 경고", C.RED, bold=True)
        for w_msg in m.warnings:
            cprint(f"    {w_msg}", C.RED)

    key_reasons = [r for r in m.signal_reason if r.startswith("✅")][:8]
    neg_reasons = [r for r in m.signal_reason if r.startswith("❌")][:4]
    if key_reasons or neg_reasons:
        print()
        cprint("  [ 주요 판단 근거 ]", C.BLUE, bold=True)
        for r in key_reasons:
            cprint(f"    {r}", C.GREEN)
        for r in neg_reasons:
            cprint(f"    {r}", C.RED)

    cprint(f"\n{'═' * w}\n", C.CYAN)


def print_summary_table(results: list):
    sorted_results = sorted(results, key=lambda x: x.total_score, reverse=True)
    w = 80
    cprint(f"\n{'╔' + '═'*(w-2) + '╗'}", C.CYAN, bold=True)
    cprint(f"{'║':1}{'  📊  장기 투자 종합 순위  (Long-Term Investment Ranking)':^{w-2}}{'║':1}", C.CYAN, bold=True)
    cprint(f"{'╚' + '═'*(w-2) + '╝'}", C.CYAN, bold=True)

    header = f"  {'순위':<4} {'종목':<14} {'이름':<20} {'종합':>6} {'버핏':>6} {'그레이엄':>8} {'린치':>6} {'시그널':<18}"
    cprint(header, C.GRAY, bold=True)
    cprint("  " + "─" * (w-2), C.GRAY)

    for i, m in enumerate(sorted_results, 1):
        sc = _signal_color(m.signal)
        tc = C.GREEN if m.total_score >= 70 else (C.YELLOW if m.total_score >= 50 else C.RED)
        short_name = m.name[:18]
        row = (
            f"  {C.WHITE}{i:<4}{C.RESET}"
            f" {C.CYAN}{m.ticker:<14}{C.RESET}"
            f" {C.WHITE}{short_name:<20}{C.RESET}"
            f" {tc}{m.total_score:>6.1f}{C.RESET}"
            f" {C.GRAY}{m.score_buffett:>6.1f}{C.RESET}"
            f" {C.GRAY}{m.score_graham:>8.1f}{C.RESET}"
            f" {C.GRAY}{m.score_lynch:>6.1f}{C.RESET}"
            f" {sc}{m.signal}{C.RESET}"
        )
        print(row)

    cprint("  " + "─" * (w-2), C.GRAY)
    cprint(f"\n  ※ 본 분석은 공개 재무 데이터 기반 참고용이며, 투자 권유가 아닙니다.", C.GRAY)
    cprint(f"  ※ 분석 시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n", C.GRAY)


# ─── HTML 저장 ─────────────────────────────────────────────────

def save_to_html_perfectly(target_function, *args, **kwargs):
    f = io.StringIO()
    with redirect_stdout(f):
        # Capture the result of the function
        func_result = target_function(*args, **kwargs)
    raw_output = f.getvalue()
    
    # Still print the output so it's visible in console
    print(raw_output)

    decoder = AnsiDecoder()
    lines = list(decoder.decode(raw_output))

    dark_theme = Theme({"background": "black", "foreground": "white"})
    export_console = Console(
        record=True,
        width=100,
        theme=dark_theme,
        force_terminal=True,
        color_system="truecolor"
    )
    for line in lines:
        export_console.print(line)

    CUSTOM_FORMAT = """
<pre style="font-family:Menlo,'DejaVu Sans Mono',consolas,'Courier New',monospace; background-color: #000000; color: #ffffff; padding: 20px;">
    <code style="font-family:inherit">{code}</code>
</pre>
"""
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f"report_{timestamp}.html"
    export_console.save_html(filename, inline_styles=True, code_format=CUSTOM_FORMAT)
    print(f"\n✨ 블랙 테마 적용 완료! 저장된 파일: {filename}")
    return filename, func_result


# ─── 메인 ──────────────────────────────────────────────────────

def main():
    cprint("\n" + "═"*68, C.CYAN, bold=True)
    cprint("  📈 장기 투자 종합 분석기 v2.0", C.WHITE, bold=True)
    cprint("  Buffett · Graham · Lynch · Fisher · Greenblatt · Templeton", C.GRAY)
    cprint(f"  대상 종목: S&P 500 전체 ({len(TARGET_STOCKS)}개)", C.GRAY)
    cprint("═"*68 + "\n", C.CYAN, bold=True)

    results = []
    for ticker, name in TARGET_STOCKS.items():
        cprint(f"  ⏳ 데이터 수집 중: {name} ({ticker}) ...", C.GRAY)
        try:
            m = fetch_stock_data(ticker, name)
            if m.price == 0:
                cprint(f"  ⚠ {ticker} 가격 정보 없음 — 스킵", C.YELLOW)
                continue
            m = compute_total_score(m)
            results.append(m)
            time.sleep(0.2)  # API 부하 방지
        except Exception as e:
            cprint(f"  ❌ {ticker} 처리 실패: {e}", C.RED)

    if not results:
        cprint("수집된 데이터가 없습니다.", C.RED)
        return

    for m in results:
        print_stock_report(m)

    print_summary_table(results)
    
    # Return converted dicts for reporting
    return [_metrics_to_dict(m) for m in results]


# ─── 웹 대시보드용 함수 ────────────────────────────────────────

def _metrics_to_dict(m: StockMetrics) -> dict:
    """StockMetrics → JSON 직렬화 가능한 dict 변환"""
    return {
        'ticker': m.ticker, 'name': m.name, 'price': m.price, 'currency': m.currency,
        'pe_ratio': m.pe_ratio, 'pb_ratio': m.pb_ratio, 'ps_ratio': m.ps_ratio,
        'ev_ebitda': m.ev_ebitda, 'peg_ratio': m.peg_ratio,
        'roe': m.roe, 'roa': m.roa,
        'gross_margin': m.gross_margin, 'operating_margin': m.operating_margin,
        'net_margin': m.net_margin,
        'current_ratio': m.current_ratio, 'debt_to_equity': m.debt_to_equity,
        'interest_coverage': m.interest_coverage,
        'revenue_growth_yoy': m.revenue_growth_yoy, 'earnings_growth_yoy': m.earnings_growth_yoy,
        'revenue_growth_3y': m.revenue_growth_3y,
        'dividend_yield': m.dividend_yield, 'payout_ratio': m.payout_ratio,
        'price_52w_high': m.price_52w_high, 'price_52w_low': m.price_52w_low,
        'ma50': m.ma50, 'ma200': m.ma200, 'rsi': m.rsi, 'beta': m.beta,
        'intrinsic_value_dcf': m.intrinsic_value_dcf,
        'score_buffett': m.score_buffett, 'score_graham': m.score_graham,
        'score_lynch': m.score_lynch, 'score_fisher': m.score_fisher,
        'score_greenblatt': m.score_greenblatt, 'score_templeton': m.score_templeton,
        'score_technical': m.score_technical, 'total_score': m.total_score,
        'signal': m.signal, 'signal_reason': m.signal_reason, 'warnings': m.warnings,
    }


def analyze() -> dict:
    """
    웹 대시보드 호출용 분석 함수.
    S&P 500 전 종목 분석 후 결과를 history JSON으로 저장하고 반환.
    """
    results_raw = []
    for ticker, name in TARGET_STOCKS.items():
        try:
            m = fetch_stock_data(ticker, name)
            if m.price == 0:
                continue
            m = compute_total_score(m)
            results_raw.append(_metrics_to_dict(m))
            time.sleep(0.2)
        except Exception as e:
            print(f"Error processing {ticker}: {e}")

    results_raw.sort(key=lambda x: x['total_score'], reverse=True)

    history_dir = "history"
    os.makedirs(history_dir, exist_ok=True)
    today_str = datetime.now().strftime('%Y-%m-%d')
    history_file = os.path.join(history_dir, f"{today_str}.json")
    save_data = {
        'analyzed_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'total_count': len(results_raw),
        'results': results_raw,
    }
    with open(history_file, 'w', encoding='utf-8') as f:
        json.dump(save_data, f, ensure_ascii=False, indent=2)

    # Automatically send Telegram report when analyze() is called
    # To avoid blocking, we can consider threading here if needed but analyze()
    # is already typical called in background thread in app.py
    # We need a filename for HTML, since we don't have one here 
    # we generate a temporary one or just skip file send for now
    # Recommended: reuse save_to_html_perfectly functionality or skip file in web analyze
    send_telegram_report(results_raw, "")

    return save_data


def send_telegram_report(results: list, html_file: str):
    """분석 요약을 텔레그램으로 전송하고 HTML 리포트 파일도 첨부"""
    token = os.getenv("TELEGRAM_TOKEN")
    chat_id = os.getenv("CHAT_ID")
    
    if not token or not chat_id:
        print("⚠ TELEGRAM_TOKEN 또는 CHAT_ID 환경변수가 설정되지 않았습니다.")
        return

    # 요약 메시지 작성 (Top 10)
    sorted_results = sorted(results, key=lambda x: x['total_score'], reverse=True)[:10]
    
    msg = "📊 *장기 투자 종합 분석 결과 (Top 10)*\n\n"
    for i, m in enumerate(sorted_results, 1):
        msg += f"{i}. *{m['ticker']}* ({m['name'][:12]})\n"
        msg += f"   점수: `{m['total_score']:.1f}` | {m['signal']}\n"
    
    msg += f"\n📅 분석 시각: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    msg += "\n\n✨ 상세 리포트는 아래 첨부파일을 확인하세요."

    # 1. 텍스트 메시지 전송
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        requests.post(url, data={"chat_id": chat_id, "text": msg, "parse_mode": "Markdown"})
        
        # 2. HTML 파일 전송
        if os.path.exists(html_file):
            url_doc = f"https://api.telegram.org/bot{token}/sendDocument"
            with open(html_file, "rb") as f:
                requests.post(url_doc, data={"chat_id": chat_id}, files={"document": f})
        
        print("✅ 텔레그램 리포트 전송 완료")
    except Exception as e:
        print(f"❌ 텔레그램 전송 실패: {e}")


if __name__ == "__main__":
    try:
        import yfinance
    except ImportError:
        print("yfinance 설치 필요: pip install yfinance pandas numpy rich")
        exit(1)

    filename, results = save_to_html_perfectly(main)
    # Send report to telegram
    if results:
        send_telegram_report(results, filename)
