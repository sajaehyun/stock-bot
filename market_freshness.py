"""Require real trades from the current session before calling a ranking current."""
from datetime import datetime, timedelta, timezone
import math
from zoneinfo import ZoneInfo

NY = ZoneInfo('America/New_York')
KST = ZoneInfo('Asia/Seoul')


def active_phase(now):
    local = now.astimezone(NY)
    minute = local.hour * 60 + local.minute
    weekday = local.weekday()
    if minute < 240 and weekday <= 4:
        return '데이마켓', local.replace(hour=20,minute=0,second=0,microsecond=0)-timedelta(days=1)
    if minute >= 1200 and weekday in (6,0,1,2,3):
        return '데이마켓', local.replace(hour=20,minute=0,second=0,microsecond=0)
    if weekday <= 4 and 240 <= minute < 1200:
        name = '프리마켓' if minute < 570 else '정규장' if minute < 960 else '애프터마켓'
        start = 240 if minute < 570 else 570 if minute < 960 else 960
        return name, local.replace(hour=start//60,minute=start%60,second=0,microsecond=0)
    return None, None


def freshness(now, quotes, universe, max_age_minutes=20):
    phase, start = active_phase(now)
    # A latest trade can legitimately remain the regular close in after-hours,
    # or the overnight trade during premarket, for an illiquid stock.
    usable_start = start
    if phase == '애프터마켓':
        usable_start = start.replace(hour=9, minute=30)
    elif phase == '프리마켓':
        usable_start = start.replace(hour=20) - timedelta(days=1)
    core_start = start - timedelta(minutes=10) if start and phase != '데이마켓' else start
    # Freshness is based on provider trade timestamps, never HTTP retrieval time.
    valid, usable = [], []
    for symbol, quote in quotes.items():
        try:
            stamp = datetime.fromisoformat(quote['asof_utc'])
            price = float(quote['price'])
            age = (now-stamp).total_seconds()
            if stamp.tzinfo and math.isfinite(price) and price > 0 and age >= 0:
                if age <= max_age_minutes*60 and (core_start is None or stamp >= core_start):
                    valid.append(symbol)
                if (phase in ('데이마켓','프리마켓','애프터마켓') and stamp >= usable_start) or symbol in valid:
                    usable.append(symbol)
        except (TypeError, ValueError, KeyError):
            continue
    available = sum(symbol in valid for symbol in universe)
    required = math.ceil(len(universe)*.8)
    missing_etfs = [s for s in ('SOXX','SOXL','SOXS') if s not in valid]
    usable_count = sum(symbol in usable for symbol in universe)
    okay = phase is None or (usable_count >= required and not missing_etfs)
    return {'okay':okay,'expected_session':phase,'session_start':start.isoformat() if start else None,
            'fresh_symbols':valid,'fresh_count':available,'universe_count':len(universe),
            'usable_symbols':usable,'usable_count':usable_count,
            'required_count':required,'missing_etfs':missing_etfs,'max_age_minutes':max_age_minutes}


class FreshDataUnavailable(RuntimeError):
    def __init__(self, status, quotes):
        self.status = status
        self.quotes = quotes
        super().__init__(f"Current-session data unavailable: {status['expected_session']} {status['fresh_count']}/{status['universe_count']}")


def data_gap_report(now, error):
    status = error.status
    phase = status['expected_session'] or '현재 세션'
    local = now.astimezone(KST).strftime('%m/%d %H:%M')
    lines = [f'⏸ {phase} 실시간·대체 시세 모두 미확보 — SOXL·SOXS 판정 유보',
             f'확인 {local} KST',
             f"현재 세션의 {status['max_age_minutes']}분 이내 체결 {status['fresh_count']}/{status['universe_count']}종목",
             '최대 1시간 전 체결가와 최근 완료장 애프터 시세·종가 대체도 확보하지 못했습니다.',
             '조회 시각과 시세 기준 시각을 구분하며, 존재하지 않는 가격은 만들지 않습니다.']
    for symbol in ('SOXX','SOXL','SOXS'):
        quote = error.quotes.get(symbol)
        if quote:
            stamp = datetime.fromisoformat(quote['asof_utc']).astimezone(KST).strftime('%m/%d %H:%M')
            lines.append(f"{symbol}: 마지막 확인 {stamp} KST · {quote.get('session','세션 미확인')}")
    return '\n'.join(lines)
