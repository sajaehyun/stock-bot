from datetime import datetime,timedelta,timezone
import unittest
from market_context import evaluate,eligibility,summary
from observation_data import make_observation
from test_quote_metadata import snapshot,observation
from notion_archive import make_page,verify_context
from test_split_storage import Stored

NOW=datetime(2026,11,6,1,tzinfo=timezone.utc)


def batches(title='Chip company reports quarterly earnings'):
    def article(n,text):return {'title':text,'link':'https://example.org/'+str(n),
        'providerPublishTime':(NOW-timedelta(hours=1)).timestamp(),'publisher':'test publisher'}
    return {'US stock market':[article(1,title),article(2,'Stocks finish the trading session')],
            'semiconductor':[article(3,'Chip manufacturing equipment demand update')]}


class ContextTests(unittest.TestCase):
    def evaluate(self,items=None,seismic=None,failures=(),**kw):
        return evaluate(NOW,items if items is not None else batches(),
                        seismic if seismic is not None else {'features':[]},failures,**kw)

    def test_election_report_is_excluded_without_erasing_original_headlines(self):
        context=self.evaluate(batches('US election results raise questions for markets'))
        self.assertEqual(context['status'],'uncertain');self.assertFalse(context['research_eligible'])
        self.assertEqual(len(context['articles']),3)
        self.assertIn('선거',context['events'][0]['category'])
        text=summary(context)
        self.assertIn('KST',text);self.assertIn(context['events'][0]['url'],text)
        repeated=summary(context,[context['events'][0]['url']])
        self.assertIn('새 불확실성 기사 없음',repeated)
        self.assertNotIn(context['events'][0]['url'],repeated)

    def test_news_failure_is_unknown_and_never_assumed_calm(self):
        context=self.evaluate({},failures=['US stock market 조회 실패'])
        self.assertEqual(context['status'],'unknown');self.assertFalse(context['research_eligible'])
        self.assertIn('뉴스 확인 불가',summary(context))

    def test_outdated_or_future_headlines_cannot_count_as_current_coverage(self):
        data=batches()
        data['US stock market'][0]['providerPublishTime']=(NOW-timedelta(days=4)).timestamp()
        data['US stock market'][1]['providerPublishTime']=(NOW+timedelta(hours=1)).timestamp()
        self.assertFalse(self.evaluate(data)['research_eligible'])

    def test_official_major_earthquake_is_an_exclusion_even_without_news_keywords(self):
        event={'properties':{'mag':7.1,'time':NOW.timestamp()*1000,'title':'Major earthquake',
                             'url':'https://earthquake.usgs.gov/earthquakes/eventpage/test'}}
        self.assertEqual(self.evaluate(seismic={'features':[event]})['status'],'uncertain')

    def test_successful_limited_screen_is_not_labelled_proof_of_low_uncertainty(self):
        context=self.evaluate()
        self.assertTrue(context['research_eligible']);self.assertEqual(context['status'],'screened_no_flag')
        self.assertIn('보장',context['limitations'])

    def test_known_election_window_applies_to_the_target_session(self):
        self.assertFalse(self.evaluate(target='2026-11-03')['research_eligible'])

    def test_large_price_fall_by_itself_does_not_invent_an_election_or_disaster(self):
        self.assertTrue(self.evaluate(batches('Semiconductor stocks fall 12 percent'))['research_eligible'])

    def test_unknown_old_observations_are_not_eligible_by_default(self):
        self.assertFalse(eligibility(None)['eligible'])

    def test_exclusion_is_hash_bound_and_read_back_from_notion(self):
        s=snapshot();s['market_context']=self.evaluate(batches('Hurricane disrupts production'))
        o=observation(s);original_hash=o['payload_sha256']
        self.assertFalse(o['research_eligibility']['eligible'])
        body=make_page(o,'source','https://github.com/original');api=Stored(body)
        self.assertEqual(body['properties']['검증사용여부']['select']['name'],'제외')
        self.assertFalse(verify_context(api,'page',o)['eligible'])
        api.properties['검증사용여부']['select']['name']='사용가능'
        with self.assertRaises(ValueError):verify_context(api,'page',o)
        self.assertEqual(o['payload_sha256'],original_hash)
        self.assertEqual(o['payload']['snapshot']['results'][0]['price'],100)


if __name__=='__main__':unittest.main()
