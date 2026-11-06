"""Preserve exactly used scanner candle prices and confirmed delivery metadata."""
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo

from quote_metadata import provenance, timestamp


def candle_fact(frame, interval, source, collected_at=None):
    """Candle indices are NOT last-trade times; completed closes use calendar end."""
    import pandas as pd
    import exchange_calendars as xcals
    now=timestamp(collected_at) if collected_at else datetime.now(timezone.utc)
    if frame is None or frame.empty:raise ValueError('No used candle')
    index=pd.Timestamp(frame.index[-1]); price=float(frame.Close.iloc[-1])
    cal=xcals.get_calendar('XNYS')
    day=index.date().isoformat() if interval=='1d' else (
        index.tz_convert('America/New_York').date().isoformat() if index.tzinfo else None)
    start=end=None; kind='unknown'; session=None
    if day and cal.is_session(day):
        op=cal.session_open(day).to_pydatetime(); cl=cal.session_close(day).to_pydatetime()
        if interval=='1d':
            start,end=op,cl
            kind='official_session_close' if cl<=now else 'bar_start'
            session='정규장 종가' if cl<=now else '정규장'
        elif index.tzinfo:
            start=index.tz_convert('UTC').to_pydatetime()
            end=min(start+pd.Timedelta(minutes=30),cl)
            kind,session='bar_start','정규장'
    asof=end if kind=='official_session_close' else start
    from split_adjustment import metrics
    return {'price':price, **(metrics(frame) if interval=='1d' else {}),
            'asof_utc':asof.isoformat() if asof else None,
            'timestamp_kind':kind,'collected_at_utc':now.isoformat(),
            'bar_start_utc':start.isoformat() if start else None,
            'bar_end_utc':end.isoformat() if end else None,
            'provider_bar_index':str(frame.index[-1]),'interval':interval,
            'source':source,'session':session}


def scanner_snapshot(report,quotes):
    rows=[]
    for original in report.get('results',[]):
        row=copy.deepcopy(original)
        if 'price' not in row:row['price']=row.get('data',{}).get('price')
        q=quotes.get(row['ticker'],{})
        row.update(price_asof_utc=q.get('asof_utc'),price_timestamp_kind=q.get('timestamp_kind'),
                   price_collected_at_utc=q.get('collected_at_utc'))
        rows.append(row)
    return {'quote_metadata_version':2,'results':rows,'inputs':copy.deepcopy(quotes),'quotes':{},
            'required_symbols':sorted(set(quotes)|{r['ticker'] for r in rows}),
            'source':'분석에 실제 사용한 원본 OHLCV','price_mode':'scanner_candles',
            'coverage':{'available':len(quotes),'requested':report.get('requested_total',report.get('total',len(quotes)))}}


def write_scanner_observation(report,quotes,messages,kind,analyzed_at=None,key=None,slot=None,out='delivered_observations'):
    from observation_data import make_observation
    if not messages:raise ValueError('Only actually confirmed complete deliveries can be archived')
    at=timestamp(analyzed_at) if analyzed_at else datetime.now(timezone.utc)
    repo=os.environ.get('GITHUB_REPOSITORY','sajaehyun/semiconductor-alert')
    run=os.environ.get('GITHUB_RUN_ID','0');attempt=os.environ.get('GITHUB_RUN_ATTEMPT','1')
    key=key or f'{repo}:{run}:{attempt}:{kind}'
    snapshot=scanner_snapshot(report,quotes)
    known=bool(snapshot['results']) and all(r.get('entry_key') in ('green','wait','stop') for r in snapshot['results'])
    features={k:report.get(name,sum(r.get('entry_key')==name for r in snapshot['results']) if known else None)
              for k,name in [('all_green','green'),('all_wait','wait'),('all_stop','stop')]}
    decision={'etf':'NOT_APPLICABLE','basis':'not_published','reason':'이 종목 스캐너에서는 종합 ETF 판정을 발표하지 않음'}
    payload={'session':None,'target':None,'slot':slot or at.astimezone(ZoneInfo('Asia/Seoul')).strftime('%Y-%m-%d %H:%M'),
             'analyzed_at':at.isoformat(),'delivery_key':key,'snapshot':snapshot,
             'records':[{'features':features,'decision':decision}],
             'text':'\n\n'.join(messages),'original_report':copy.deepcopy(report)}
    observation=make_observation(payload,{'composition_paper_deliveries':{key:'sent'}},run,attempt,os.environ.get('GITHUB_SHA',''))
    observation.update(origin_repository=repo,record_kind=kind,
        observation_id=f'{run}-{attempt}-'+hashlib.sha256(key.encode()).hexdigest()[:12],
        run_kind='scheduled' if os.environ.get('GITHUB_EVENT_NAME')=='schedule' else 'manual',
        delivery_confirmation={'confirmed_messages':len(messages),'sent_at_utc':datetime.now(timezone.utc).isoformat()})
    folder=Path(out);folder.mkdir(exist_ok=True,parents=True)
    path=folder/(observation['observation_id']+'.json')
    path.write_text(json.dumps(observation,ensure_ascii=False,indent=2,default=str)+'\n',encoding='utf-8')
    print('Confirmed scanner observation: '+observation['observation_id']+' / '+observation['data_class'])
    return observation


def artifact_observations(file,expected_run,expected_commit):
    import re
    import zipfile
    result=[]
    with zipfile.ZipFile(file) as archive:
        for name in archive.namelist():
            if not re.fullmatch(r'(?:reports/)?delivered_observations/\d+-\d+-[a-f0-9]{12}\.json',name):continue
            observation=json.loads(archive.read(name))
            if observation['origin_run_id']!=str(expected_run):continue
            if observation['origin_commit']!=expected_commit or observation['origin_repository']!='sajaehyun/semiconductor-alert':
                raise ValueError('Artifact source execution identity mismatch')
            if observation['delivery_status']=='sent':result.append(observation)
    return result


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument('--artifact',required=True)
    parser.add_argument('--run',required=True)
    parser.add_argument('--commit',required=True)
    args=parser.parse_args()
    pending=artifact_observations(args.artifact,args.run,args.commit)
    Path('scanner_pending.json').write_text(json.dumps(pending,ensure_ascii=False),encoding='utf-8')
    print('Confirmed current-run scanner observations:',len(pending))
