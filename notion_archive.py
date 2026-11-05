"""Mirror successfully delivered observations into the personal Notion database.

Telegram delivery and market analysis never run here. Originals remain immutable
on analysis-data; Notion is a structured, searchable copy of each delivered slot.
"""
import argparse
from collections import Counter
from datetime import datetime
import json
import os
from pathlib import Path
import time
import urllib.error
import urllib.request
from zoneinfo import ZoneInfo

VERSION = '2026-03-11'
KST = ZoneInfo('Asia/Seoul')
CLASSES = {'live': '실시간', 'fallback': '대체', 'unclassified': '미분류'}


def schema():
    properties = {'이름': {'title': {}}}
    for name in ('회차', '분석시각', '최초시세', '최종시세', '기준거래일', '대상거래일', '발송시각',
                 '시세시각상한', '재분류기준일', '분류검토일'):
        properties[name] = {'date': {}}
    for name in ('추천', '관망', '비추', 'TOP10 반도체', '반도체 추천', '반도체 비추',
                 'SOXX 가격', 'SOXL 가격', 'SOXS 가격', '조회 종목 수', '요청 종목 수', '원본참조수',
                 '실시간 종목', '대체 종목', '시각 미확인 종목', '시세기록 종목'):
        properties[name] = {'number': {'format': 'number'}}
    for name in ('시세유형', '시세출처', '판정근거', '대체사유', '발송키', '원본해시', '시간근거', '복원근거',
                 '분류근거', '분류검토ID', '시세기록버전'):
        properties[name] = {'rich_text': {}}
    for name in ('원본 데이터', '실행 기록', '분류근거링크'):
        properties[name] = {'url': {}}
    for name, options in {
        '시세구분': [('실시간', 'green'), ('실시간 추정', 'yellow'), ('대체', 'orange'),
                   ('분석없음', 'gray'), ('미분류', 'gray')],
        '분류확도': [('코드·로그 확정', 'blue'), ('코드·로그 추정', 'yellow'),
                   ('판정불가', 'gray'), ('해당없음', 'gray'),
                   ('종목별 시각 확인', 'green'), ('일부 시각 미확인', 'yellow')],
        '최종판정': [('SOXL', 'green'), ('SOXS', 'red'), ('보류', 'gray'), ('미발표', 'gray')],
        '판정구분': [('가설', 'blue'), ('보조', 'yellow'), ('보류', 'gray'), ('미확인', 'gray'), ('해당없음', 'gray')],
        '실행종류': [('예약', 'blue'), ('수동', 'gray'), ('진단', 'purple')],
        '기록종류': [('가설분석', 'blue'), ('종목추천', 'green'), ('장중스캔', 'purple'),
                     ('구형분석', 'gray'), ('자료부족안내', 'orange')],
        '원문상태': [('원문보존', 'green'), ('원문미보관', 'gray')],
    }.items():
        properties[name] = {'select': {'options': [{'name': n, 'color': c} for n, c in options]}}
    return properties


def rich(text):
    # Below Notion's 2,000 UTF-16-unit limit per text fragment, including emoji.
    text = str(text or '')
    fragments, current, units = [], '', 0
    for char in text:
        size = 2 if ord(char) > 0xffff else 1
        if units + size > 1800:
            fragments.append({'type': 'text', 'text': {'content': current}})
            current, units = '', 0
        current += char
        units += size
    if current:
        fragments.append({'type': 'text', 'text': {'content': current}})
    return fragments


def block(kind, text):
    return {'object': 'block', 'type': kind, kind: {'rich_text': rich(text)}}


SYMBOL_HEADERS = ['종목','분석 입력가','시세 기준 (KST)','시각 종류','수집 (KST)',
                  '세션','시세 구분','경과 분','출처','근거']
SYMBOL_HEADING = '종목별 분석 입력 시세 · 실제 체결/봉/공식 종가 시각 구분'


def symbol_rows(provenance):
    def local(value):
        from quote_metadata import timestamp
        parsed=timestamp(value)
        return parsed.astimezone(KST).strftime('%Y-%m-%d %H:%M:%S') if parsed else '미확인'
    kinds={'actual_trade':'실제 체결','official_session_close':'공식 종가 기준',
           'bar_start':'봉 시작 (체결시각 아님)','bar_end':'봉 종료 (체결시각 아님)'}
    rows=[]
    for symbol,fact in sorted(provenance.get('symbols',{}).items()):
        age=fact.get('age_minutes')
        rows.append([symbol,str(fact.get('price') if fact.get('price') is not None else '미확인'),
            local(fact.get('asof_utc')),kinds.get(fact.get('timestamp_kind'),'미확인'),
            local(fact.get('collected_at_utc')),str(fact.get('session') or '미확인'),
            CLASSES[fact['data_class']],f'{age:.2f}' if age is not None else '미확인',
            str(fact.get('source') or '미확인'),fact['reason']])
    return rows


def make_page(observation, data_source_id, archive_url):
    if observation.get('delivery_status') != 'sent':
        raise ValueError('Only confirmed Telegram deliveries may enter the alert database')
    payload = observation['payload']
    provenance = observation['price_provenance']
    kind = observation['data_class']
    if kind not in CLASSES or provenance['data_class'] != kind:
        raise ValueError('Invalid or conflicting price provenance')
    decision = observation['decision']
    features = payload['records'][0].get('features', {}) if payload.get('records') else {}
    history = observation.get('historical_import', {})
    snapshot = payload['snapshot']
    label = CLASSES[kind]
    etf = decision.get('etf')
    etf = etf if etf in ('SOXL', 'SOXS') else '미발표' if etf == 'NOT_APPLICABLE' else '보류'
    basis = observation.get('decision_basis') or ''
    basis_label = ('보조' if basis == 'fallback_unvalidated' else '가설' if basis == 'hypothesis' else
                   '해당없음' if etf == '미발표' else '보류' if etf == '보류' else '미확인')
    slot = datetime.strptime(observation['slot'], '%Y-%m-%d %H:%M').replace(tzinfo=KST).isoformat()
    properties = {'이름': {'title': rich(f"{observation['slot']} · {etf} · {label}")},
                  '시세구분': {'select': {'name': label}},
                  '최종판정': {'select': {'name': etf}},
                  '판정구분': {'select': {'name': basis_label}},
                  '기록종류': {'select': {'name': observation.get('record_kind',history.get('kind', '가설분석'))}},
                  '원문상태': {'select': {'name': history.get('text_status', '원문보존')}},
                  '원본참조수': {'number': 1+len(observation.get('duplicate_sources', []))},
                  '발송시각': {'date': None},
                  # Historical review metadata is populated only by a separate,
                  # hash-bound evidence overlay. Never infer it on normal sends.
                  '분류확도': {'select': None}, '분류근거': {'rich_text': []},
                  '분류검토ID': {'rich_text': []}, '분류근거링크': {'url': None},
                  '시세시각상한': {'date': None}, '재분류기준일': {'date': None},
                  '분류검토일': {'date': None},
                  '시세기록버전': {'rich_text': rich(provenance.get('symbol_schema_version'))},
                  '실행종류': {'select': {'name': {'scheduled': '예약', 'manual': '수동',
                                                'diagnostic': '진단'}[observation['run_kind']]}},
                  '원본 데이터': {'url': archive_url},
                  '실행 기록': {'url': 'https://github.com/'+observation.get('origin_repository','sajaehyun/semiconductor-alert')+'/actions/runs/'
                                       + observation['origin_run_id']}}
    for name, value in {
        '회차': slot, '분석시각': observation['analyzed_at'],
        '최초시세': provenance.get('oldest_asof_utc'),
        '최종시세': provenance.get('latest_asof_utc'),
        '기준거래일': provenance.get('basis_session'), '대상거래일': observation.get('target'),
        '발송시각': history.get('sent_at'),
    }.items():
        if value:
            properties[name] = {'date': {'start': value}}
    for name, value in {
        '시세유형': observation.get('price_mode'), '시세출처': provenance.get('source'),
        '판정근거': decision.get('reason', basis),
        '대체사유': (provenance.get('fallback') or {}).get('reason'),
        '발송키': observation['delivery_key'], '원본해시': observation['payload_sha256'],
        '시간근거': history.get('slot_basis', '원본 예약 회차'),
        '복원근거': json.dumps(history.get('evidence', {}), ensure_ascii=False) if history else '',
    }.items():
        properties[name] = {'rich_text': rich(value)}
    for name, key in {'추천': 'all_green', '관망': 'all_wait', '비추': 'all_stop',
                      'TOP10 반도체': 'top10semi_n', '반도체 추천': 'semi_green',
                      '반도체 비추': 'semi_stop'}.items():
        properties[name] = {'number': features.get(key)}
    for symbol in ('SOXX', 'SOXL', 'SOXS'):
        properties[symbol+' 가격'] = {'number': provenance.get('core_etfs', {}).get(symbol, {}).get('price')}
    for name, key in [('조회 종목 수', 'available'), ('요청 종목 수', 'requested')]:
        properties[name] = {'number': (provenance.get('coverage') or {}).get(key)}
    counts=provenance.get('symbol_counts',{})
    for name,key in [('실시간 종목','live'),('대체 종목','fallback'),('시각 미확인 종목','unclassified')]:
        properties[name]={'number':counts.get(key,0) if provenance.get('symbols') else None}
    properties['시세기록 종목']={'number':len(provenance['symbols']) if provenance.get('symbols') else None}
    if provenance.get('schema_version')=='price-provenance-v2':
        properties['분류확도']={'select':{'name':'일부 시각 미확인' if counts.get('unclassified') else '종목별 시각 확인'}}
        properties['분류근거']={'rich_text':rich(provenance['classification_reason'])}
    children = [block('heading_2', '텔레그램 발송 원문' if payload.get('text') else '원문 보관 상태')]
    if not payload.get('text'):
        children.append(block('paragraph', '메시지 원문 미보관. 발송 확인 근거와 당시 저장된 분석 데이터를 보존했습니다.'))
    # Keep the exact original text; it may contain newlines and long reports.
    for fragment in rich(payload['text']):
        children.append(block('paragraph', fragment['text']['content']))
    children += [block('heading_2', '시세 출처와 기준 시각'),
                 block('paragraph', json.dumps(provenance, ensure_ascii=False, indent=2))]
    model_records = [r for r in payload['records'] if r.get('model_version') or r.get('model_name')]
    if model_records:
        children.append(block('heading_2', '가설별 당시 판정'))
    for record in model_records:
        details = {key: record[key] for key in ('model_version', 'model_name', 'model_sha256',
                   'verdict', 'matched', 'missing_previous_skipped_rules', 'decision') if key in record}
        children.append(block('paragraph', json.dumps(details, ensure_ascii=False, indent=2)))
    rows = [[rich(x) for x in ('순위', '종목', '점수', '구분', '분석 입력가')]]
    for index, item in enumerate(snapshot.get('results', []), 1):
        rows.append([rich(str(x)) for x in (index, item['ticker'], item.get('score', ''),
                     item.get('entry_key', ''), item.get('price', ''))])
    children.append(block('heading_2', '당시 추천 구성'))
    # Older reports may contain the whole 126-symbol universe, not only TOP30.
    # Preserve every row, respecting Notion's per-array child limit.
    for offset in range(1, len(rows), 80):
        children.append({'object': 'block', 'type': 'table', 'table': {
                     'table_width': 5, 'has_column_header': True, 'has_row_header': False,
                     'children': [{'object': 'block', 'type': 'table_row', 'table_row': {'cells': r}}
                                  for r in [rows[0]]+rows[offset:offset+80]]}})
    if history:
        children += [block('heading_2', '과거 자료 복원 근거'),
                     block('paragraph', json.dumps({'history': history,
                        'duplicate_sources': observation.get('duplicate_sources', [])}, ensure_ascii=False, indent=2))]
    children.append(block('paragraph', '전체 원본 입력·가설 버전·해시는 위 원본 데이터 링크에 보존됩니다. '
                       '시세 구분과 판정 구분은 별도 항목이며 회차별 기록을 독립 거래일 성적으로 합산하지 않습니다.'))
    detail_rows=symbol_rows(provenance)
    if detail_rows:
        children += [block('heading_2',SYMBOL_HEADING),
                     block('paragraph','실시간은 현재 세션의 20분 이내 실제 체결 시각을 확인한 가격입니다. '
                           '대체 시세가 하나라도 분석 입력에 포함되면 전체 회차는 대체로 분류합니다. '
                           '시각 미확인 종목 수를 별도로 표시하며, 수집 시각을 체결 시각으로 바꾸지 않습니다.')]
        for offset in range(0,len(detail_rows),80):
            children.append({'object':'block','type':'table','table':{
                'table_width':len(SYMBOL_HEADERS),'has_column_header':True,'has_row_header':False,
                'children':[{'object':'block','type':'table_row','table_row':{'cells':[rich(c) for c in row]}}
                            for row in [SYMBOL_HEADERS]+detail_rows[offset:offset+80]]}})
        if provenance.get('unused_requested_symbols'):
            children.append(block('paragraph','조회했으나 분석 입력에 사용하지 않은 종목: '+
                                  ', '.join(provenance['unused_requested_symbols'])))
    return {'parent': {'type': 'data_source_id', 'data_source_id': data_source_id},
            'properties': properties, 'children': children, 'notifications': {'mode': 'silent'}}


def verify_symbol_rows(api,page_id,provenance):
    expected=symbol_rows(provenance)
    if not expected:return 0
    def children(parent):
        result=[]; cursor=None
        while True:
            response=api.call('GET','/blocks/'+parent+'/children?page_size=100'+('&start_cursor='+cursor if cursor else ''))
            result+=response['results']
            if not response.get('has_more'):return result
            cursor=response['next_cursor']
    actual=[];found=False
    plain=lambda texts:''.join(x.get('plain_text',x.get('text',{}).get('content','')) for x in texts)
    for child in children(page_id):
        if child['type']=='heading_2':
            found=plain(child['heading_2']['rich_text'])==SYMBOL_HEADING
        elif found and child['type']=='table':
            rows=[[plain(cell) for cell in b['table_row']['cells']] for b in children(child['id'])]
            if not rows or rows[0]!=SYMBOL_HEADERS:raise ValueError('Symbol timestamp table header differs')
            actual+=rows[1:]
    if actual!=expected:raise ValueError('Notion symbol price/timestamp read-back differs from archived input')
    return len(actual)


class Notion:
    def __init__(self, token):
        if not token:
            raise ValueError('NOTION_TOKEN is not configured')
        self.token = token

    def call(self, method, path, body=None):
        # No redirects: credentials must never leave api.notion.com.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None
        if not path.startswith('/') or '..' in path:
            raise ValueError('Invalid API path')
        data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
        request = urllib.request.Request('https://api.notion.com/v1'+path, data=data, method=method,
            headers={'Authorization': 'Bearer '+self.token, 'Notion-Version': VERSION,
                     'Content-Type': 'application/json'})
        try:
            with urllib.request.build_opener(NoRedirect).open(request, timeout=60) as response:
                result = json.load(response)
            time.sleep(.35)
            return result
        except urllib.error.HTTPError as error:
            # Do not log response/request dumps that could contain secrets or source data.
            raise RuntimeError('Notion API HTTP '+str(error.code)) from None
        except (urllib.error.URLError, TimeoutError):
            raise RuntimeError('Notion API request incomplete; reconcile by delivery key before retrying') from None


def resolve_data_source(api, database_id):
    database = api.call('GET', '/databases/'+database_id)
    sources = database.get('data_sources', [])
    if len(sources) != 1:
        raise ValueError('Expected exactly one data source in the dedicated alert database')
    return sources[0]['id']


def ensure_schema(api, database_id):
    data_source_id = resolve_data_source(api, database_id)
    properties = api.call('GET', '/data_sources/'+data_source_id)['properties']
    missing = {}
    for name, definition in schema().items():
        if name in properties:
            if properties[name]['type'] != next(iter(definition)):
                raise ValueError('Existing property type differs: '+name)
            if 'select' in definition:
                old = properties[name]['select']['options']
                existing = {x['name'] for x in old}
                additions = [x for x in definition['select']['options'] if x['name'] not in existing]
                if additions:
                    missing[name] = {'select': {'options': old+additions}}
        else:
            missing[name] = definition
    if missing:
        api.call('PATCH', '/data_sources/'+data_source_id, {'properties': missing})
    return data_source_id


def mirror(api, data_source_id, observation, archive_url):
    if observation.get('delivery_status') != 'sent':
        return None
    found = api.call('POST', '/data_sources/'+data_source_id+'/query', {
        'filter': {'property': '발송키', 'rich_text': {'equals': observation['delivery_key']}}, 'page_size': 2})
    pages = found['results']
    if len(pages) > 1 or found.get('has_more'):
        raise ValueError('Duplicate delivery keys already exist; refusing additional rows')
    if pages:
        page = pages[0]
        original_hash = ''.join(x.get('plain_text', x.get('text', {}).get('content', ''))
                               for x in page['properties']['원본해시']['rich_text'])
        if original_hash != observation['payload_sha256']:
            raise ValueError('Existing delivery key has different immutable source data')
        # Newly added provenance columns may be empty on a previously saved row.
        # Fill only empty metadata after matching the immutable original hash.
        expected = make_page(observation, data_source_id, archive_url)['properties']
        updates = {}
        for name in ('기록종류','원문상태','원본참조수','시간근거','복원근거','발송시각'):
            desired = expected[name]
            kind = next(iter(desired))
            current = page['properties'].get(name, {}).get(kind)
            if current in (None, []) and desired[kind] not in (None, []):
                updates[name] = desired
        if updates:
            api.call('PATCH', '/pages/'+page['id'], {'properties': updates, 'notifications': {'mode':'silent'}})
    else:
        # Never blindly retry a page creation after an ambiguous timeout. The next
        # cloud run queries the key again before creating, with serialized writers.
        page = api.call('POST', '/pages', make_page(observation, data_source_id, archive_url))
    verified=verify_symbol_rows(api,page['id'],observation['price_provenance'])
    return {'page_id': page['id'], 'page_url': page['url'], 'symbol_rows_verified':verified,
            'delivery_key': observation['delivery_key'], 'payload_sha256': observation['payload_sha256'],
            'observation_id': observation['observation_id'], 'data_class': observation['data_class']}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--setup', metavar='DATABASE_ID')
    parser.add_argument('--initialize', action='store_true')
    parser.add_argument('--audit', action='store_true')
    parser.add_argument('--queue', default='notion_queue.json')
    parser.add_argument('--receipts', default='notion_receipts.json')
    args = parser.parse_args()
    api = Notion(os.environ.get('NOTION_TOKEN'))
    if args.setup:
        print('Data source: '+ensure_schema(api, args.setup))
        return
    database_id = os.environ.get('NOTION_DATABASE_ID')
    if args.initialize:
        source = ensure_schema(api, database_id)
    else:
        source = os.environ.get('NOTION_DATA_SOURCE_ID') or resolve_data_source(api, database_id)
    if args.audit:
        pages, cursor = [], None
        while True:
            query = {'page_size': 100}
            if cursor:
                query['start_cursor'] = cursor
            result = api.call('POST', '/data_sources/'+source+'/query', query)
            pages += result['results']
            if not result.get('has_more'):
                break
            cursor = result['next_cursor']
        keys = [''.join(v.get('plain_text', v.get('text', {}).get('content', ''))
                       for v in p['properties']['발송키']['rich_text']) for p in pages]
        duplicates = len(keys)-len(set(keys))
        groups = {}
        for field in ('시세구분', '기록종류', '원문상태'):
            groups[field] = dict(Counter((p['properties'].get(field, {}).get('select') or {}).get('name', '미기입') for p in pages))
        print('Notion database audit: '+json.dumps({'rows':len(pages), 'duplicate_delivery_keys':duplicates,
                                                  'empty_delivery_keys':keys.count(''), **groups}, ensure_ascii=False))
        if duplicates or '' in keys:
            raise SystemExit('Notion delivery identity audit failed')
        return
    receipts, failed = [], 0
    for entry in json.loads(Path(args.queue).read_text(encoding='utf-8')):
        try:
            receipt = mirror(api, source, entry['observation'], entry['archive_url'])
            if receipt:
                receipts.append(receipt)
                print('Notion saved: '+receipt['observation_id']+' / '+receipt['data_class'])
        except (RuntimeError, ValueError, KeyError) as error:
            failed += 1
            print('Notion sync failed: '+entry['observation'].get('observation_id', '?')+' / '+str(error))
    Path(args.receipts).write_text(json.dumps(receipts, ensure_ascii=False, indent=2), encoding='utf-8')
    if failed:
        raise SystemExit('Notion sync incomplete; pending originals remain in analysis-data for retry')


if __name__ == '__main__':
    main()
