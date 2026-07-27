"""
longterm.py — 중장기 분석 (현재 비활성화)
yfinance 의존성 제거로 인해 기능 비활성화 상태
"""
import json, pathlib, logging
from datetime import datetime

LOG = logging.getLogger("longterm")
LONGTERM_DIR = pathlib.Path("longterm")
LONGTERM_DIR.mkdir(exist_ok=True)


def analyze_longterm(universe: str = "sp500+sox") -> dict:
    LOG.info("중장기 분석: 현재 비활성화 상태 (데이터 소스 마이그레이션 중)")
    return {
        "analyzed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "universe": universe,
        "results": [],
        "error": "중장기 분석 기능은 현재 점검 중입니다.",
    }
