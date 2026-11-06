"""Preserve price provenance independently of the hypothesis/auxiliary decision."""
import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import zipfile

LIVE_MODES = {'session_quote', 'overnight_quote', 'extended_5m'}


def price_provenance(snapshot, analyzed_at):
    if snapshot.get('quote_metadata_version') == 2:
        from quote_metadata import provenance
        return provenance(snapshot, analyzed_at)
    mode = snapshot.get('price_mode')
    fallback = snapshot.get('fallback')
    kind = ('fallback' if fallback or str(mode).startswith('fallback_') else
            'live' if mode in LIVE_MODES else 'unclassified')
    inputs = dict(snapshot.get('inputs', {}))
    inputs.update(snapshot.get('quotes', {}))
    stamps = []
    for quote in inputs.values():
        try:
            stamp = datetime.fromisoformat(quote['asof_utc'])
            if stamp.tzinfo:
                stamps.append(stamp.astimezone(timezone.utc))
        except (KeyError, TypeError, ValueError):
            continue
    at = datetime.fromisoformat(analyzed_at)
    etfs = {}
    for symbol, quote in snapshot.get('quotes', {}).items():
        info = {k: quote.get(k) for k in ('price', 'asof_utc', 'session', 'timestamp_kind', 'source')}
        try:
            info['age_minutes'] = (at-datetime.fromisoformat(quote['asof_utc'])).total_seconds()/60
        except (KeyError, TypeError, ValueError):
            info['age_minutes'] = None
        etfs[symbol] = info
    return {'schema_version': 'price-provenance-v1', 'data_class': kind, 'price_mode': mode,
            'source': snapshot.get('source'), 'fallback': copy.deepcopy(fallback),
            'basis_session': (fallback or {}).get('basis_session', snapshot.get('session')),
            'oldest_asof_utc': min(stamps).isoformat() if stamps else None,
            'latest_asof_utc': max(stamps).isoformat() if stamps else None,
            'core_etfs': etfs, 'coverage': copy.deepcopy(snapshot.get('coverage'))}


def make_observation(payload, state, run_id, run_attempt, commit):
    payload = copy.deepcopy(payload)
    provenance = price_provenance(payload['snapshot'], payload['analyzed_at'])
    payload['price_provenance'] = provenance
    context=payload['snapshot'].get('market_context')
    if context:
        from market_context import eligibility
        payload['research_eligibility']=eligibility(context)
    key = payload['delivery_key']
    parts = key.split(':')
    slot = payload.get('slot')
    if not slot:
        # Backfill old reports only when the original delivery key supplies the slot.
        stamp = parts[-1].split('-')[0]
        if len(stamp) == 4 and stamp.isdigit():
            slot = parts[0]+' '+stamp[:2]+':'+stamp[2:]
        else:
            raise ValueError('Original report slot missing; do not invent one')
    decision = payload['records'][-1]['decision']
    run_kind = 'diagnostic' if ':5m-test:' in key else 'scheduled' if ':schedule:' in key else 'manual'
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return {'schema_version': 'composition-observation-v1',
            'observation_id': str(run_id)+'-'+str(run_attempt),
            'data_class': provenance['data_class'], 'price_mode': provenance['price_mode'],
            'decision_basis': decision.get('basis'), 'decision': copy.deepcopy(decision),
            'run_kind': run_kind, 'slot': slot, 'target': payload['target'],
            'analyzed_at': payload['analyzed_at'], 'origin_run_id': str(run_id),
            'origin_run_attempt': str(run_attempt), 'origin_commit': commit,
            'delivery_key': key,
            'delivery_status': state.get('composition_paper_deliveries', {}).get(key, 'unknown'),
            'price_provenance': provenance,
            'payload_sha256': hashlib.sha256(serialized.encode()).hexdigest(), 'payload': payload,
            **({'research_eligibility':payload['research_eligibility']} if context else {})}


def write_observation(payload, state, out, run_id, run_attempt, commit):
    observation = make_observation(payload, state, run_id, run_attempt, commit)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(observation, ensure_ascii=False, indent=2), encoding='utf-8')
    return observation


def write_data_gap(text, quotes, state, key, at, out, run_id, run_attempt, commit):
    """Keep a delivered data-gap notice without pretending analysis took place."""
    from zoneinfo import ZoneInfo
    decision={'etf':'NOT_APPLICABLE','basis':'not_published','reason':'시세 부족으로 분석 미실행'}
    snapshot={'results':[],'inputs':{},'quotes':{},'source':'시세 확보 실패 안내',
              'attempted_quote_observations':copy.deepcopy(quotes),'analysis_performed':False}
    payload={'session':None,'target':None,'analyzed_at':at.isoformat(),'delivery_key':key,
             'slot':at.astimezone(ZoneInfo('Asia/Seoul')).strftime('%Y-%m-%d %H:%M'),
             'text':text,'snapshot':snapshot,'records':[{'decision':decision,'features':{}}]}
    observation=make_observation(payload,state,run_id,run_attempt,commit)
    provenance=observation['price_provenance']
    provenance.update(data_class='no_analysis',classification_reason='시세 부족 안내; 가설 분석·추천을 수행하지 않음')
    observation.update(data_class='no_analysis',record_kind='자료부족안내')
    observation['payload']['price_provenance']=copy.deepcopy(provenance)
    serialized=json.dumps(observation['payload'],ensure_ascii=False,sort_keys=True,separators=(',',':'))
    observation['payload_sha256']=hashlib.sha256(serialized.encode()).hexdigest()
    destination=Path(out);destination.parent.mkdir(parents=True,exist_ok=True)
    destination.write_text(json.dumps(observation,ensure_ascii=False,indent=2),encoding='utf-8')
    return observation


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--report-zip', required=True)
    p.add_argument('--state-zip', required=True)
    p.add_argument('--run-id', required=True)
    p.add_argument('--attempt', required=True)
    p.add_argument('--commit', required=True)
    p.add_argument('--output', default='direction_results/observation.json')
    args = p.parse_args()
    # Read named members; never extract untrusted archive paths.
    with zipfile.ZipFile(args.report_zip) as z:
        payload = json.loads(z.read('composition_paper_daily.json'))
    with zipfile.ZipFile(args.state_zip) as z:
        state = json.loads(z.read('state.json'))
    observation = write_observation(payload, state, args.output,
                                    args.run_id, args.attempt, args.commit)
    print('Observation prepared: '+observation['data_class']+' / '+observation['observation_id'])


if __name__ == '__main__':
    main()
