"""Price-time facts for new deliveries; HTTP time is never a trade timestamp."""
from collections import Counter
from datetime import datetime, timezone
import math

CORE = ('SOXX', 'SOXL', 'SOXS')
VERSION = 'symbol-price-times-v2'


def timestamp(value):
    try:
        result = datetime.fromisoformat(str(value).replace('Z','+00:00'))
        return result.astimezone(timezone.utc) if result.tzinfo else None
    except (TypeError, ValueError):
        return None


def symbol_fact(symbol, quote, at, phase, phase_start, max_age_minutes=20):
    quote = quote or {}
    stamp = timestamp(quote.get('asof_utc'))
    collected = timestamp(quote.get('collected_at_utc'))
    kind = quote.get('timestamp_kind', 'unknown')
    age = (at-stamp).total_seconds()/60 if stamp else None
    price = quote.get('price')
    try:
        valid_price = math.isfinite(float(price)) and float(price)>0
    except (TypeError,ValueError):
        valid_price = False
    classification,reason = 'unclassified','시세 시각 또는 시각 종류 미확인'
    if not valid_price:
        reason = '분석 입력 가격 미확인'
    elif stamp is None:
        reason = '시간대가 있는 제공처 시세 시각 미확인'
    elif collected is None:
        reason = '시간대가 있는 수집 시각 미확인'
    elif stamp>at or (collected and (collected>at or stamp>collected)):
        reason = '미래 시각 또는 수집 시각 이후 시세: 실시간 확정 제외'
    elif kind=='official_session_close':
        classification,reason = 'fallback','완료 정규장 종가; 공식 마감 시각이며 실제 체결 시각 아님'
    elif kind=='actual_trade':
        if (age<=max_age_minutes and phase and quote.get('session')==phase and
                (phase_start is None or stamp>=phase_start)):
            classification,reason = 'live','현재 세션 실제 체결 시각과 '+str(max_age_minutes)+'분 이내 신선도 확인'
        else:
            classification,reason = 'fallback','마지막 실제 체결이 현재 세션 또는 신선도 기준을 충족하지 않음'
    elif kind in ('bar_start','bar_end'):
        end=timestamp(quote.get('bar_end_utc'))
        if end and end<=at and (at-end).total_seconds()>max_age_minutes*60:
            classification,reason = 'fallback','봉 종료 시각도 신선도 기준보다 오래됨; 개별 체결 시각은 미제공'
        else:
            reason = '봉 시각은 보관했으나 개별 체결 시각 미제공: 실시간 확정 제외'
    return {'symbol':symbol,'price':price,'asof_utc':stamp.isoformat() if stamp else None,
            'actual_trade_utc':stamp.isoformat() if stamp and collected and stamp<=collected<=at and kind=='actual_trade' else None,
            'provider_asof_utc':quote.get('asof_utc'),
            'timestamp_kind':kind,'collected_at_utc':collected.isoformat() if collected else None,
            'bar_start_utc':quote.get('bar_start_utc'),'bar_end_utc':quote.get('bar_end_utc'),
            'provider_bar_index':quote.get('provider_bar_index'),'interval':quote.get('interval'),
            'session':quote.get('session'),'source':quote.get('source'),
            'age_minutes':age,'data_class':classification,'reason':reason,
            'input_sha256':quote.get('input_sha256')}


def provenance(snapshot, analyzed_at):
    from market_freshness import active_phase
    at=timestamp(analyzed_at)
    if not at:raise ValueError('Analysis time must include timezone')
    phase,start=active_phase(at)
    inputs=dict(snapshot.get('inputs',{}))
    # Core quotes must be included even if the ranking universe excludes ETFs.
    for symbol,quote in snapshot.get('quotes',{}).items():
        if symbol in inputs and inputs[symbol]!=quote:
            raise ValueError('Conflicting input and core quote for '+symbol)
        inputs[symbol]=quote
    rows={r['ticker']:r for r in snapshot.get('results',[])}
    required=set(inputs)|set(rows)|set(snapshot.get('required_symbols',CORE))
    symbols={s:symbol_fact(s,inputs.get(s),at,phase,start) for s in sorted(required)}
    for symbol,row in rows.items():
        fact=symbols[symbol]
        try:
            if abs(float(row['price'])-float(fact['price']))>.00500001:
                fact.update(data_class='unclassified',reason='저장된 분석 입력 가격과 시세 가격 불일치')
        except (KeyError,TypeError,ValueError):
            fact.update(data_class='unclassified',reason='종목의 실제 분석 입력 가격 확인 불가')
    counts=dict(Counter(f['data_class'] for f in symbols.values()))
    fallback=snapshot.get('fallback')
    forced=bool(fallback or str(snapshot.get('price_mode','')).startswith('fallback_'))
    kind=('fallback' if forced or counts.get('fallback') else
          'unclassified' if not symbols or counts.get('unclassified') else 'live')
    reason=('대체 분석 경로 사용' if forced else '대체 시세가 분석 입력에 포함됨' if kind=='fallback' else
            '일부 종목 시세 시각 또는 가격 검증 불가' if kind=='unclassified' else '모든 분석 입력의 현재 세션 실제 체결 시각·신선도 확인')
    stamps=[timestamp(f['asof_utc']) for f in symbols.values() if f['asof_utc'] and
            timestamp(f['asof_utc'])<=at]
    return {'schema_version':'price-provenance-v2','symbol_schema_version':VERSION,
            'data_class':kind,'price_mode':snapshot.get('price_mode'),'source':snapshot.get('source'),
            'fallback':fallback,'basis_session':(fallback or {}).get('basis_session',snapshot.get('session')),
            'analyzed_at':at.isoformat(),'freshness_limit_minutes':20,'expected_session':phase,
            'classification_reason':reason,'symbol_counts':counts,'symbols':symbols,
            'ranked_symbols':list(rows),'required_symbol_count':len(required),
            'requested_symbols':snapshot.get('requested_symbols',sorted(required)),
            'unused_requested_symbols':sorted(set(snapshot.get('requested_symbols',[]))-required),
            'oldest_asof_utc':min(stamps).isoformat() if stamps else None,
            'latest_asof_utc':max(stamps).isoformat() if stamps else None,
            'core_etfs':{s:symbols[s] for s in CORE if s in symbols},'coverage':snapshot.get('coverage')}


def summary(provenance):
    labels={'live':'실시간','fallback':'대체','unclassified':'미확인'}
    counts=provenance['symbol_counts']
    return ('시세 구분: '+labels[provenance['data_class']]+' · '+
            ' / '.join(labels[k]+' '+str(counts.get(k,0)) for k in ('live','fallback','unclassified'))+
            '\n시세 시각과 수집 시각은 별도 저장 · 봉/공식 종가 시각은 실제 체결 시각과 구분')
