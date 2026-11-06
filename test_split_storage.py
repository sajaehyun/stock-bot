"""Verify the saved MA20 is the one sent, with legacy records unchanged."""
import copy
import unittest
from test_quote_metadata import snapshot,observation
from notion_archive import make_page,verify_symbol_rows,verify_price_metrics,schema


class Stored:
    def __init__(self,page):
        self.properties=copy.deepcopy(page['properties']);self.blocks={};self.next=0
        def save(blocks):
            output=[]
            for block in copy.deepcopy(blocks):
                self.next+=1;block['id']=str(self.next)
                if block['type']=='table':self.blocks[block['id']]=save(block['table'].pop('children'))
                output.append(block)
            return output
        self.blocks['page']=save(page['children'])
    def call(self,method,path,body=None):
        if path=='/pages/page':return {'properties':self.properties}
        return {'results':self.blocks[path.split('/')[2]],'has_more':False}


def corrected():
    s=snapshot()
    s['session']='2026-11-03'
    for q in list(s['inputs'].values())+list(s['quotes'].values()):
        q.update(ma20=110.,raw_price=300.,quote_split_factor=3.,price_basis='2026-11-04',
            reference_price=105.,raw_reference_price=315.,price_adjustment={
                'version':'split-basis-v1','basis_date':'2026-11-04','status':'corrected',
                'adjusted_rows':3,'events':[{'date':'2026-11-04','ratio':3.,'source':'confirmed event'}]})
    s['core_price_metrics']={k:{'price':q['price'],'ma20':q['ma20']} for k,q in s['quotes'].items()}
    return observation(s)


class SplitStorageTests(unittest.TestCase):
    def test_normalized_price_average_raw_price_and_split_evidence_round_trip(self):
        o=corrected();body=make_page(o,'source','https://github.com/original');api=Stored(body)
        self.assertEqual(set(body['properties']),set(schema()))
        self.assertEqual(body['properties']['SOXX 20일 평균']['number'],110.)
        self.assertEqual(body['properties']['SOXX 가격']['number'],100.)
        self.assertEqual(verify_symbol_rows(api,'page',o['price_provenance']),4)
        verified=verify_price_metrics(api,'page',o)
        self.assertEqual(verified['SOXX'],{'price':100.,'ma20':110.,'raw_price':300.,'price_basis':'2026-11-04'})
        self.assertEqual(body['children'][1]['paragraph']['rich_text'][0]['text']['content'],o['payload']['text'])

    def test_wrong_notion_average_cannot_produce_a_success_receipt(self):
        o=corrected();api=Stored(make_page(o,'source','https://github.com/original'))
        api.properties['SOXX 20일 평균']['number']=330.
        with self.assertRaisesRegex(ValueError,'price/MA20'):verify_price_metrics(api,'page',o)

    def test_wrong_per_symbol_average_is_detected_even_when_core_properties_match(self):
        o=corrected();api=Stored(make_page(o,'source','https://github.com/original'))
        tables=[b for b in api.blocks['page'] if b['type']=='table']
        api.blocks[tables[-1]['id']][1]['table_row']['cells'][10][0]['text']['content']='999'
        with self.assertRaises(ValueError):verify_symbol_rows(api,'page',o['price_provenance'])

    def test_legacy_rows_keep_their_original_ten_column_format(self):
        o=observation(snapshot());body=make_page(o,'source','https://github.com/original');api=Stored(body)
        self.assertEqual(verify_symbol_rows(api,'page',o['price_provenance']),4)
        self.assertEqual(verify_price_metrics(api,'page',o),{})
        tables=[b for b in body['children'] if b['type']=='table']
        self.assertEqual(tables[-1]['table']['table_width'],10)
        self.assertIsNone(body['properties']['SOXX 20일 평균']['number'])


if __name__=='__main__':unittest.main()
