import copy
from datetime import datetime,timezone
import unittest
from quote_metadata import provenance
from observation_data import make_observation
from notion_archive import make_page,verify_symbol_rows,SYMBOL_HEADING

NOW='2026-11-04T15:00:00+00:00'


def quote():
    return {'price':100.,'asof_utc':'2026-11-04T14:59:00+00:00',
            'timestamp_kind':'actual_trade','collected_at_utc':'2026-11-04T14:59:30+00:00',
            'session':'정규장','source':'provider actual trade'}


def snapshot():
    return {'quote_metadata_version':2,'price_mode':'session_quote','source':'provider',
            'results':[{'ticker':'NVDA','price':100,'score':10,'entry_key':'wait'}],
            'inputs':{'NVDA':quote()},'quotes':{s:quote() for s in ('SOXX','SOXL','SOXS')}}


def observation(s):
    p={'session':'2026-11-03','target':'2026-11-04','slot':'2026-11-05 00:00',
       'analyzed_at':NOW,'delivery_key':'2026-11-05:schedule:0000-live-v4',
       'text':'exact original message','records':[{'features':{},'decision':{'etf':'SOXL','basis':'hypothesis'}}],
       'snapshot':s}
    return make_observation(p,{'composition_paper_deliveries':{p['delivery_key']:'sent'}},'9001','1','commit')


class PriceTimeTests(unittest.TestCase):
    def test_all_used_symbols_must_have_recent_current_phase_actual_trade(self):
        p=provenance(snapshot(),NOW)
        self.assertEqual(p['data_class'],'live')
        self.assertEqual(p['symbol_counts'],{'live':4})
        self.assertEqual(p['symbols']['NVDA']['actual_trade_utc'],'2026-11-04T14:59:00+00:00')

    def test_one_old_ranked_input_makes_whole_analysis_fallback(self):
        s=snapshot();s['inputs']['NVDA']['asof_utc']='2026-11-04T14:00:00+00:00'
        p=provenance(s,NOW)
        self.assertEqual(p['data_class'],'fallback')
        self.assertEqual(p['symbol_counts'],{'fallback':1,'live':3})

    def test_missing_naive_future_wrong_phase_and_wrong_price_are_not_live(self):
        for change in ({'asof_utc':None},{'asof_utc':'2026-11-04T14:59:00'},
                       {'asof_utc':'2026-11-04T15:00:01+00:00'}, {'timestamp_kind':'unknown'},
                       {'collected_at_utc':None},{'price':101}):
            s=snapshot();s['inputs']['NVDA'].update(change)
            self.assertEqual(provenance(s,NOW)['data_class'],'unclassified',change)
        s=snapshot();s['inputs']['NVDA']['session']='애프터마켓'
        self.assertEqual(provenance(s,NOW)['data_class'],'fallback')

    def test_bar_start_never_becomes_actual_last_trade(self):
        s=snapshot();s['inputs']['NVDA'].update(timestamp_kind='bar_start',bar_end_utc='2026-11-04T15:30:00Z')
        p=provenance(s,NOW)
        self.assertEqual(p['data_class'],'unclassified')
        self.assertIsNone(p['symbols']['NVDA']['actual_trade_utc'])

    def test_old_bar_endpoint_and_official_close_are_fallback_not_fake_trades(self):
        for change in ({'timestamp_kind':'bar_start','bar_end_utc':'2026-11-04T14:00:00Z'},
                       {'timestamp_kind':'official_session_close'}):
            s=snapshot();s['inputs']['NVDA'].update(change)
            p=provenance(s,NOW)
            self.assertEqual(p['data_class'],'fallback')
            self.assertIsNone(p['symbols']['NVDA']['actual_trade_utc'])

    def test_missing_ranked_symbol_is_counted_unknown_instead_of_omitted(self):
        s=snapshot();s['inputs']={}
        p=provenance(s,NOW)
        self.assertEqual(p['symbols']['NVDA']['data_class'],'unclassified')
        self.assertEqual(p['required_symbol_count'],4)

    def test_unused_requested_symbol_is_preserved_but_not_counted_as_analysis_input(self):
        s=snapshot();s['requested_symbols']=['NVDA','DELISTED']
        p=provenance(s,NOW)
        self.assertEqual(p['data_class'],'live')
        self.assertEqual(p['unused_requested_symbols'],['DELISTED'])

    def test_live_mode_name_does_not_override_stale_actual_times(self):
        s=snapshot();s['price_mode']='overnight_quote'
        s['quotes']['SOXX']['asof_utc']='2026-11-04T13:00:00Z'
        self.assertEqual(provenance(s,NOW)['data_class'],'fallback')

    def test_source_not_mutated_and_conflicting_core_price_rejected(self):
        s=snapshot();before=copy.deepcopy(s);provenance(s,NOW);self.assertEqual(s,before)
        s['inputs']['SOXX']={**quote(),'price':999}
        with self.assertRaises(ValueError):provenance(s,NOW)

    def test_new_observation_preserves_all_symbol_facts_and_hash_is_stable(self):
        a=observation(snapshot());b=observation(snapshot())
        self.assertEqual(a['payload_sha256'],b['payload_sha256'])
        self.assertEqual(a['price_provenance']['symbol_schema_version'],'symbol-price-times-v2')
        self.assertEqual(a['payload']['price_provenance'],a['price_provenance'])


class NotionSymbolTableTests(unittest.TestCase):
    def test_full_129_symbol_table_round_trip_and_tamper_detection(self):
        s=snapshot();s['inputs'].update({'S'+str(i):quote() for i in range(125)})
        obs=observation(s);body=make_page(obs,'source','https://github.com/archive')
        self.assertEqual(body['properties']['시세기록 종목']['number'],129)
        self.assertEqual(body['properties']['실시간 종목']['number'],129)
        self.assertEqual(body['properties']['분류확도']['select']['name'],'종목별 시각 확인')
        class StoredNotion:
            def __init__(self):
                self.blocks={};self.next=0
                def save(blocks):
                    output=[]
                    for b in copy.deepcopy(blocks):
                        self.next+=1;b['id']=str(self.next)
                        if b['type']=='table':self.blocks[b['id']]=save(b['table'].pop('children'))
                        output.append(b)
                    return output
                self.blocks['page']=save(body['children'])
            def call(self,method,path,body=None):
                return {'results':self.blocks[path.split('/')[2]],'has_more':False}
        api=StoredNotion()
        self.assertEqual(verify_symbol_rows(api,'page',obs['price_provenance']),129)
        table=next(b for b in api.blocks['page'][::-1] if b['type']=='table')
        api.blocks[table['id']][1]['table_row']['cells'][2][0]['text']['content']='wrong timestamp'
        with self.assertRaises(ValueError):verify_symbol_rows(api,'page',obs['price_provenance'])


if __name__=='__main__':unittest.main()
