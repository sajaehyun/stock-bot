import copy
from datetime import datetime,timezone,timedelta
from pathlib import Path
import tempfile
import unittest
from observation_data import write_data_gap
from notion_archive import mirror
from test_notion_archive import FakeNotion


class Notices(FakeNotion):
    def __init__(self):super().__init__();self.blocks=[];self.appends=0
    def call(self,method,path,body=None):
        if method=='GET' and path.startswith('/blocks/'):
            return {'results':self.blocks,'has_more':False}
        if method=='PATCH' and path.startswith('/blocks/'):
            self.blocks+=copy.deepcopy(body['children']);self.appends+=1;return {}
        return super().call(method,path,body)


class NoticeTests(unittest.TestCase):
    def test_duplicate_missing_data_notice_keeps_both_texts_in_one_row_and_retries_once(self):
        key='2026-11-06:workflow_dispatch:hash:0900-live-v4:data-gap-v1'
        state={'composition_paper_deliveries':{key:'sent'}};api=Notices()
        now=datetime(2026,11,6,1,tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
            one=write_data_gap('first actual notice',{},state,key,now,Path(folder)/'one.json','1','1','sha')
            two=write_data_gap('second actual notice',{},state,key,now+timedelta(minutes=3),Path(folder)/'two.json','2','1','sha')
        mirror(api,'source',one,'https://github.com/original-one')
        original=copy.deepcopy(api.pages[0]['properties'])
        receipt=mirror(api,'source',two,'https://github.com/original-two')
        again=mirror(api,'source',two,'https://github.com/original-two')
        self.assertTrue(receipt['duplicate_notice']);self.assertEqual(receipt,again)
        self.assertEqual(api.pages[0]['properties'],original)
        self.assertEqual(len(api.created),1);self.assertEqual(api.appends,1)
        self.assertIn('second actual notice',str(api.blocks))
        # The existing general different-price/hash conflict guard still applies.
        api.pages[0]['properties']['시세구분']={'select':{'name':'대체'}}
        with self.assertRaises(ValueError):mirror(api,'source',two,'https://github.com/original-two')


if __name__=='__main__':unittest.main()
