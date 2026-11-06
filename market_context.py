"""Conservative event screening, separate from prices and trading hypotheses.

An exclusion preserves the raw observation. A clean limited news search is not
proof that the world is free of uncertainty, and does not calibrate a forecast.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta,timezone
import json
import re
import urllib.request
from zoneinfo import ZoneInfo

VERSION='uncertainty-screen-v1'
QUERIES=('US stock market','semiconductor')
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


def evaluate(now,batches,seismic,failures=(),session=None,target=None):
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
            'events':reasons,'sources':['Yahoo Finance news search',USGS,ELECTION_SOURCE],
            'limitations':'제목 기반 보수적 선별; 모든 선거·재난·속보나 실제 시장 영향을 보장하여 탐지하지 못함'}


def collect(yf,now,session=None,target=None):
    def fetch(query):
        try:
            return query,yf.Search(query,max_results=0,news_count=12,lists_count=0,
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
    return evaluate(now,{q:items for q,items,_ in results},seismic,failures,session,target)


def eligibility(context):
    context=context or {}
    return {'version':VERSION,'eligible':context.get('research_eligible') is True,
            'status':context.get('status','unknown'),
            'reason':context.get('reason','당시 뉴스·사건 근거 미확인; 검증 표본에서 제외')}


def summary(context):
    e=eligibility(context)
    prefix='뉴스 점검: 제외 사유 미검출 (탐지 범위 한정)' if e['eligible'] else '⚠️ 가설 검증 제외: '+('불확실성 감지' if e['status']=='uncertain' else '뉴스 확인 불가')
    events=(context or {}).get('events',[])
    return prefix+'\n'+(events[0]['category']+': '+events[0]['detail'][:110] if events else e['reason'])
