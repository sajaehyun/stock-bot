"""Put historical bars and observed quotes on one verified share-count basis.

Yahoo can return a partly adjusted daily series. Confirmed split factors alone
are insufficient: hourly closes identify which daily rows already use the new
units. A genuine market fall is never repaired merely because it is large.
"""
from copy import deepcopy
from datetime import datetime, timezone
import math
from zoneinfo import ZoneInfo

VERSION = 'split-basis-v1'
NY = ZoneInfo('America/New_York')
CONFIRMED = {'SOXX': [{'date':'2026-11-05', 'ratio':3.0,
    'source':'https://www.sec.gov/Archives/edgar/data/1100663/000119312526430480/d156277d497.htm'}]}


class SplitBasisUnavailable(ValueError):
    pass


def date_of(value):
    import pandas as pd
    stamp = pd.Timestamp(value)
    return (stamp.tz_convert(NY) if stamp.tzinfo else stamp).date().isoformat()


def events_for(symbol, frame, basis_date):
    events = {e['date']:deepcopy(e) for e in CONFIRMED.get(symbol, [])
              if e['date'] <= basis_date}
    if 'Stock Splits' in frame:
        for index, value in frame['Stock Splits'].items():
            if value and math.isfinite(float(value)) and float(value)>0 and float(value)!=1:
                day = date_of(index)
                if day > basis_date:
                    continue
                ratio = float(value)
                if day in events and not math.isclose(events[day]['ratio'], ratio):
                    raise SplitBasisUnavailable('Conflicting confirmed split event for '+symbol)
                events.setdefault(day, {'date':day, 'ratio':ratio, 'source':'Yahoo chart Stock Splits'})
    return sorted(events.values(), key=lambda e:e['date'])


def factor_between(day, basis_date, events):
    return math.prod(e['ratio'] for e in events if day < e['date'] <= basis_date)


def quote_price(price, asof, adjustment):
    """Preserve the actual provider quote; convert only its share-count units."""
    factor = factor_between(date_of(asof), adjustment['basis_date'], adjustment['events'])
    if factor != 1 and date_of(asof)==adjustment.get('regular_quote_date'):
        anchor=adjustment.get('normalized_anchor_close')
        if anchor:
            candidates=[1.0,factor]
            factor=min(candidates,key=lambda f:abs(float(price)/f/anchor-1))
            if abs(float(price)/factor/anchor-1)>.3:
                raise SplitBasisUnavailable('Old quote share units are ambiguous')
    return float(price)/factor, factor


def normalize_daily(symbol, daily, yf, now=None, raw_quote=None, hourly=None):
    """Accept Yahoo auto_adjust=False bars, repair splits, then retain dividend adjustment."""
    import pandas as pd
    now = now or datetime.now(timezone.utc)
    basis_date = date_of(now)
    old = daily.attrs.get('price_adjustment', {})
    if old.get('version') == VERSION and old.get('basis_date') == basis_date:
        return daily.copy()
    frame = daily.copy()
    frame = frame.loc[[date_of(i)<=basis_date for i in frame.index]]
    events = events_for(symbol, frame, basis_date)
    # Earlier events cannot affect any row in this requested window.
    events = [e for e in events if e['date'] > min(date_of(i) for i in frame.index)]
    audit = {'version':VERSION, 'basis_date':basis_date, 'events':events,
             'status':'no_confirmed_splits', 'adjusted_rows':0,
             'verified_daily_rows':0, 'unverified_historical_dates':[], 'corrections':[]}
    def finish():
        # Compare split-only Close with hourly Close before applying dividends.
        # Hourly data does not contain the cumulative daily dividend adjustment.
        if 'Adj Close' in frame:
            dividend_factor=frame['Adj Close']/frame['Close']
            for column in ('Open','High','Low','Close'):
                frame[column]=frame[column]*dividend_factor
            del frame['Adj Close']
        frame.attrs['price_adjustment'] = audit
        return frame
    if not events:
        return finish()
    if hourly is None:
        try:
            hourly = yf.Ticker(symbol).history(period='2y', interval='1h', auto_adjust=False,
                                              actions=False, prepost=False, timeout=25)
        except Exception:
            raise SplitBasisUnavailable('Split hourly evidence unavailable for '+symbol) from None
    if hourly is None or hourly.empty:
        raise SplitBasisUnavailable('Split reference hourly prices unavailable for '+symbol)
    hourly = hourly.loc[hourly.index <= pd.Timestamp(now)].dropna(subset=['Close'])
    references = {date_of(i):float(row.Close) for i,row in hourly.iterrows()
                  if math.isfinite(float(row.Close)) and float(row.Close)>0}
    # A second partially adjusted series is not independent share-unit evidence.
    # Reject split-sized jumps in the hourly reference rather than copying them.
    factors={e['ratio'] for e in events}
    factors|={1/f for f in tuple(factors)}
    ref_values=list(references.values())
    for previous,current in zip(ref_values,ref_values[1:]):
        if any((f>=1.5 or f<=1/1.5) and abs(current/previous/f-1)<=.05 for f in factors):
            raise SplitBasisUnavailable('Hourly reference itself has split-like unit discontinuity for '+symbol)
    if raw_quote:
        try:
            anchor_day = date_of(datetime.fromtimestamp(raw_quote['regularMarketTime'], timezone.utc))
            candidates={1.0,factor_between(anchor_day,basis_date,events)}
            matching=[f for f in candidates if anchor_day in references and
                      abs(float(raw_quote['regularMarketPrice'])/f/references[anchor_day]-1)<=.02]
            if not matching:
                raise SplitBasisUnavailable('Hourly split basis does not match the dated regular quote for '+symbol)
            audit.update(regular_quote_date=anchor_day, normalized_anchor_close=references[anchor_day])
        except (KeyError,TypeError,OverflowError):
            raise SplitBasisUnavailable('Dated regular quote missing for split verification: '+symbol) from None
    latest_dates = {date_of(i) for i in frame.dropna(subset=['Close']).index[-20:]}
    if not latest_dates <= references.keys():
        raise SplitBasisUnavailable('Last 20 sessions lack hourly split evidence for '+symbol)
    for index,row in frame.iterrows():
        day = date_of(index)
        if day not in references or not math.isfinite(float(row.Close)):
            continue
        close = float(row.Close)
        expected = references[day]
        # Only actual, still-applicable corporate-action products are candidates.
        applicable = [e['ratio'] for e in events if day<e['date']<=basis_date]
        candidates = {1.0}
        for ratio in applicable:
            candidates |= {x*ratio for x in tuple(candidates)}
        matches = [factor for factor in candidates if abs(close/factor/expected-1)<=.02]
        if not matches:
            closest=min(candidates,key=lambda f:abs(math.log(close/f/expected)))
            if day not in latest_dates and closest==1.0:
                # Do not rewrite a historical close just because an hourly close
                # differs. Only the latest 20 sessions require exact close checks;
                # older rows with split-like unit ambiguity still fail closed.
                audit['unverified_historical_dates'].append(day)
                continue
            raise SplitBasisUnavailable('Daily/hourly share units cannot be verified for '+symbol+' '+day)
        # Prefer preserving a row that already agrees; do not repair small moves.
        factor = 1.0 if 1.0 in matches else min(matches, key=lambda x:abs(close/x/expected-1))
        audit['verified_daily_rows'] += 1
        if factor == 1:
            continue
        original = {c:float(row[c]) for c in ('Open','High','Low','Close','Volume')}
        for column in ('Open','High','Low','Close','Adj Close'):
            if column in frame:
                frame.loc[index,column] = float(row[column])/factor
        frame.loc[index,'Volume'] = float(row.Volume)*factor
        audit['corrections'].append({'date':day, 'factor':factor, 'raw':original,
             'normalized_close':float(frame.loc[index,'Close']), 'hourly_close':expected})
    audit['adjusted_rows'] = len(audit['corrections'])
    audit['status'] = 'corrected' if audit['adjusted_rows'] else 'already_adjusted_verified'
    audit['reference'] = 'Yahoo regular hourly closes; last 20 daily sessions verified within 2%; unmatched older closes preserved'
    return finish()


def metrics(frame, price=None, raw_price=None, quote_factor=1.0):
    adjustment = deepcopy(frame.attrs.get('price_adjustment', {}))
    if adjustment.get('version')!=VERSION:
        return {}
    closes = frame.Close.dropna().iloc[-20:]
    if len(closes)!=20 or not all(math.isfinite(float(x)) and float(x)>0 for x in closes):
        raise SplitBasisUnavailable('Twenty valid normalized closes are required')
    value = float(price if price is not None else closes.iloc[-1])
    if raw_price is None:
        correction=next((c for c in adjustment['corrections'] if c['date']==date_of(frame.index[-1])),None)
        if correction:
            raw_price=correction['raw']['Close']
            quote_factor=correction['factor']
    return {'ma20':float(closes.mean()), 'raw_price':float(raw_price if raw_price is not None else value),
            'quote_split_factor':float(quote_factor), 'price_basis':adjustment['basis_date'],
            'price_adjustment':adjustment}


def core_metrics(snapshot):
    return {s:{'price':q['price'], **{k:q.get(k) for k in
        ('ma20','raw_price','quote_split_factor','reference_price','raw_reference_price',
         'price_basis','price_adjustment')}} for s,q in snapshot.get('quotes',{}).items()
        if q.get('price_adjustment',{}).get('version')==VERSION}


def summary(snapshot):
    used = snapshot.get('inputs', {})
    verified = [q for q in used.values() if q.get('price_adjustment',{}).get('version')==VERSION]
    if not verified:
        return ''
    changed = [s+': '+str(q['price_adjustment']['adjusted_rows'])+'일' for s,q in used.items()
               if q.get('price_adjustment',{}).get('adjusted_rows')]
    return ('분할 보정: 현재 주식 수 기준으로 가격·MA20 통일 · '+str(len(verified))+'종목 기록'+
            (' · 일봉 추가 보정 '+', '.join(changed) if changed else ' · 추가 일봉 보정 없음'))
