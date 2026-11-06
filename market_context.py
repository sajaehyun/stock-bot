"""Conservative event screening, separate from prices and trading hypotheses.

An exclusion preserves the raw observation. A clean limited news search is not
proof that the world is free of uncertainty, and does not calibrate a forecast.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta,timezone
import json
import math
import re
import urllib.request
from zoneinfo import ZoneInfo

VERSION='uncertainty-screen-v2'
QUERIES=('US stock market','semiconductor')
SEARCH_TERMS={'US stock market':'^GSPC','semiconductor':'SOXX'}
USGS='https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/significant_day.geojson'
ELECTION_SOURCE='https://www.fec.gov/introduction-campaign-finance/election-results-and-voting-information/'
PATTERNS={
 '선거':r'\b(elections?|midterms?|contested election|election results)\b|선거|대선|중간선거',
 '재난':r'\b(disaster|earthquake|tsunami|hurricane|wildfires?|flooding|catastrophe)\b|재난|지진|태풍|홍수',
 '전쟁·비상사태':r'\b(war|invasion|missile attack|terrorist|martial law|state of emergency|government shutdown|circuit breaker|trading halt)\b|전쟁|비상사태|계엄',
 '큰 불확실성 보도':r'\b(crisis|unprecedented uncertainty|heightened uncertainty|extreme volatility|market panic)\b|극심한 불확실|금융위기',
}


def stamp(value):
    try:
        if isinstance(value,(int,float)):return datetime.fromtimestamp(value,timezone.utc)
        result=datetime.fromisoformat(str(value).replace('Z','+00:00'))
        return result.astimezone(timezone.utc) if result.tzinfo else None
    except (ValueError,TypeError,OverflowError):return None


def evaluate(now,batches,seismic,failures=(),session=None,target=None,market_snapshot=None):
    """Only timestamped, actually retrieved headlines can establish coverage."""
    cutoff=now-timedelta(hours=48);articles={};coverage={q:0 for q in QUERIES}
    for query,items in batches.items():
        for item in items:
            content=item.get('content',item)
            published=stamp(content.get('providerPublishTime',content.get('pubDate')))
            title=str(content.get('title',''))[:512]
            url=content.get('link') or (content.get('canonicalUrl') or {}).get('url')
            if not published or not cutoff<=published<=now or not title or not str(url).startswith('https://'):continue
            coverage[query]=coverage.get(query,0)+1
            articles[url]={'title':title,'url':url,'published_at_utc':published.isoformat(),
                           'publisher':content.get('publisher') or (content.get('provider') or {}).get('displayName'),
                           'query':query}
    reasons=[]
    # Known official election date; the surrounding trading sessions are an
    # explicit conservative exclusion window, not a claim about market impact.
    days={session,target,now.astimezone(ZoneInfo('America/New_York')).date().isoformat()}
    if days & {'2026-11-02','2026-11-03','2026-11-04'}:
        reasons.append({'category':'선거 일정','detail':'미국 11/3 연방선거 전후 거래일(11/2~11/4) 보수적 제외',
                        'url':ELECTION_SOURCE})
    for article in articles.values():
        categories=[name for name,pattern in PATTERNS.items() if re.search(pattern,article['title'],re.I)]
        if categories:reasons.append({'category':'·'.join(categories),'detail':article['title'],'url':article['url']})
    for event in (seismic or {}).get('features',[]):
        p=event.get('properties',{});when=stamp((p.get('time') or 0)/1000)
        if when and now-timedelta(hours=24)<=when<=now and (
                float(p.get('mag') or 0)>=6.5 or p.get('alert') in ('orange','red') or p.get('tsunami')==1):
            reasons.append({'category':'공식 재난 정보','detail':str(p.get('title') or p.get('place')),
                            'url':p.get('url') or USGS})
    complete=(not failures and seismic is not None and len(articles)>=3 and all(coverage.values()))
    status='uncertain' if reasons else 'screened_no_flag' if complete else 'unknown'
    eligible=status=='screened_no_flag'
    return {'version':VERSION,'checked_at_utc':now.isoformat(),'status':status,
            'research_eligible':eligible,
            'reason':('선거·재난·비상 사건 관련 근거 감지; 시장 영향 확정 전 보수적 제외' if reasons else
                      '제한된 최신 뉴스·공식 재난 정보 점검에서 제외 사유 미검출' if complete else
                      '뉴스·공식 정보 조회 실패 또는 최신 보도 범위 부족: 안전하다고 간주하지 않음'),
            'coverage':coverage,'failures':list(failures),'articles':list(articles.values()),
            'market_snapshot':market_snapshot,'search_terms':SEARCH_TERMS,
            'events':reasons,'sources':['Yahoo Finance news search',USGS,ELECTION_SOURCE],
            'limitations':'제목 기반 보수적 선별; 모든 선거·재난·속보나 실제 시장 영향을 보장하여 탐지하지 못함'}


def fetch_market_snapshot(yf,now):
    data=yf.Ticker('^GSPC')._data.get_raw_json('https://query1.finance.yahoo.com/v7/finance/quote',
        params={'symbols':'^GSPC,^IXIC,^SOX','formatted':'false',
                'fields':'symbol,regularMarketPrice,regularMarketPreviousClose,regularMarketTime,marketState'},timeout=10)
    labels={'^GSPC':'S&P 500','^IXIC':'나스닥','^SOX':'필라델피아 반도체'};quotes={}
    for q in data['quoteResponse']['result']:
        if q.get('symbol') not in labels:continue
        price=float(q['regularMarketPrice']);base=float(q['regularMarketPreviousClose']);at=stamp(q['regularMarketTime'])
        if not at or at>now or not all(math.isfinite(x) and x>0 for x in (price,base)):
            raise ValueError('Invalid dated index quote')
        quotes[q['symbol']]={'name':labels[q['symbol']],'price':price,'reference_price':base,
            'change_pct':(price/base-1)*100,'asof_utc':at.isoformat(),'market_state':q.get('marketState'),
            'source':'Yahoo index quote; may be delayed'}
    if set(quotes)!=set(labels):raise ValueError('Index snapshot incomplete')
    import exchange_calendars as xcals
    day=max(stamp(q['asof_utc']) for q in quotes.values()).astimezone(ZoneInfo('America/New_York')).date().isoformat()
    regular=any(q['market_state']=='REGULAR' for q in quotes.values())
    cal=xcals.get_calendar('XNYS')
    boundary=cal.session_open(day) if regular else cal.session_close(day)
    return {'quotes':quotes,'session':day,'phase':'정규장 진행 중' if regular else '최근 정규장 종료',
            'checked_at_utc':now.isoformat(),'news_cutoff_utc':boundary.isoformat()}


def collect(yf,now,session=None,target=None):
    def fetch(query):
        try:
            return query,yf.Search(SEARCH_TERMS[query],max_results=0,news_count=12,lists_count=0,
                                   include_cb=False,timeout=10).news,None
        except Exception:return query,[],query+' 조회 실패'
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(fetch,QUERIES))
    failures=[error for _,_,error in results if error];seismic=None
    try:
        with urllib.request.urlopen(USGS,timeout=10) as response:
            seismic=json.loads(response.read(2000000))
        if 'features' not in seismic:raise ValueError('Missing official event list')
    except Exception:failures.append('USGS 공식 재난 정보 조회 실패');seismic=None
    market=None
    try:market=fetch_market_snapshot(yf,now)
    except Exception:failures.append('현재 지수 기준 시각 확인 실패')
    return evaluate(now,{q:items for q,items,_ in results},seismic,failures,session,target,market)


def eligibility(context):
    context=context or {}
    return {'version':VERSION,'eligible':context.get('research_eligible') is True,
            'status':context.get('status','unknown'),
            'reason':context.get('reason','당시 뉴스·사건 근거 미확인; 검증 표본에서 제외')}


def new_events(context,seen_urls=()):
    context=context or {};at=stamp(context.get('checked_at_utc'))
    if not at:return []
    cutoff=max(at-timedelta(hours=24),stamp((context.get('market_snapshot') or {}).get('news_cutoff_utc')) or at-timedelta(hours=24))
    articles={a['url']:a for a in context.get('articles',[])}
    events={e['url']:dict(e) for e in context.get('events',[])}
    # During a flagged episode show the latest market update as context, even
    # when its title does not itself contain an election/disaster keyword.
    if events:
        for a in articles.values():
            if re.search(r'\b(nasdaq|s&p\s*500|wall street|stock market|stock futures|chip stocks|semiconductor index|soxx)\b',a['title'],re.I):
                events.setdefault(a['url'],{'url':a['url'],'detail':a['title'],'category':'시장 최신 보도'})
    selected=[]
    for url,event in events.items():
        published=stamp(articles.get(url,{}).get('published_at_utc'))
        if published and cutoff<=published<=at and url not in set(seen_urls):
            event['published_at_utc']=published.isoformat();selected.append(event)
    ordered=sorted(selected,key=lambda e:(e['published_at_utc'],e['url']),reverse=True)
    if len(ordered)>1:
        risk_urls={e['url'] for e in context.get('events',[])}
        risk=next((e for e in ordered[1:] if e['url'] in risk_urls),None)
        if risk and ordered[0]['url'] not in risk_urls:
            ordered=[ordered[0],risk]+[e for e in ordered[1:] if e is not risk]
    return ordered


def summary(context,seen_urls=(),news_limit=2):
    e=eligibility(context)
    prefix='뉴스 점검: 제외 사유 미검출 (탐지 범위 한정)' if e['eligible'] else '⚠️ 가설 검증 제외: '+('불확실성 감지' if e['status']=='uncertain' else '뉴스 확인 불가')
    events=(context or {}).get('events',[])
    market=(context or {}).get('market_snapshot') or {}
    lines=[]
    if market.get('quotes'):
        lines += ['시장 기준: 미국 '+market['session']+' · '+market['phase']+' (지연 지수)',
                  ' / '.join(q['name']+f" {q['change_pct']:+.2f}%" for q in market['quotes'].values())]
    else:lines.append('시장 지수 기준 시각 미확인 · 기사 제목으로 현재 등락을 판단하지 않음')
    lines += [prefix,e['reason']]
    articles={a['url']:a for a in (context or {}).get('articles',[])}
    unseen=new_events(context,seen_urls)
    for event in unseen[:news_limit]:
        published=stamp(articles.get(event['url'],{}).get('published_at_utc'))
        when=published.astimezone(ZoneInfo('Asia/Seoul')).strftime('%m/%d %H:%M KST') if published else '보도 시각 미제공'
        scope='선물·장외 보도 (정규장과 구분)' if re.search(r'\bfutures\b|after.hours|선물',event['detail'],re.I) else '기사 보도 시각 기준'
        lines += ['📰 '+event['category']+' · '+when+' · '+scope,event['detail'][:160],
                  event['url'] if len(event['url'])<=500 else '원문 링크는 Notion 근거에 저장']
    if events and not new_events(context):lines.append('이전 장·과거 기사는 배경 근거로만 보관 · 현재 기사로 표시하지 않음')
    elif events and not unseen:lines.append('이전 기사와 동일 · 새 불확실성 기사 없음')
    elif events and news_limit==0:lines.append('관련 기사 원문·시각은 Notion 근거에 보관')
    return '\n'.join(lines)
