import copy
import unittest
from notion_archive import make_page, mirror, ensure_schema, rich, schema
from observation_data import make_observation


class FakeNotion:
    def __init__(self):
        self.pages = []
        self.created = []
        self.calls = []

    def call(self, method, path, body=None):
        self.calls.append((method, path, body))
        if path.endswith('/query'):
            return {'results': self.pages, 'has_more': False}
        if path == '/pages':
            self.created.append(body)
            page = {'id': 'page-1', 'url': 'https://www.notion.so/page-1', 'properties': body['properties']}
            self.pages.append(page)
            return page
        if path.startswith('/databases/'):
            return {'data_sources': [{'id': 'source-1'}]}
        if method == 'GET':
            return {'properties': {'이름': {'type': 'title'}}}
        return {}


class NotionArchiveTests(unittest.TestCase):
    def setUp(self):
        p = {'session': '2026-11-04', 'target': '2026-11-05', 'slot': '2026-11-05 09:00',
             'delivery_key': '2026-11-05:schedule:hash:0900-live-v4',
             'analyzed_at': '2026-11-05T00:01:00+00:00', 'text': '📌 SOXS\n추천 10 / 관망 9 / 비추 11',
             'records': [{'model_name': '동결 가설', 'model_version': 'fixed-v1', 'verdict': 'NONE',
                          'matched': [], 'features': {'all_green': 10, 'all_wait': 9, 'all_stop': 11},
                          'decision': {'etf': 'SOXS', 'basis': 'fallback_unvalidated', 'reason': '보조 판단'}}],
             'snapshot': {'session': '2026-11-04', 'price_mode': 'session_quote', 'source': 'actual quotes',
                          'results': [{'ticker': 'NVDA', 'score': 50, 'entry_key': 'wait', 'price': 100}],
                          'inputs': {}, 'coverage': {'available': 125, 'requested': 126},
                          'quotes': {'SOXX': {'price': 447.6, 'asof_utc': '2026-11-05T00:00:00+00:00'}}}}
        self.observation = make_observation(p, {'composition_paper_deliveries': {p['delivery_key']: 'sent'}},
                                           '41938090696', '1', 'commit')

    def page(self, observation=None):
        return make_page(observation or self.observation, 'source-1', 'https://github.com/original')

    def test_live_auxiliary_decision_stays_live_and_separately_marked_auxiliary(self):
        p = self.page()['properties']
        self.assertEqual(p['시세구분']['select']['name'], '실시간')
        self.assertEqual(p['판정구분']['select']['name'], '보조')
        self.assertEqual(p['SOXX 가격']['number'], 447.6)
        self.assertEqual(p['추천']['number'], 10)

    def test_fallback_price_with_fired_hypothesis_is_still_fallback(self):
        o = copy.deepcopy(self.observation)
        o['data_class'] = o['price_provenance']['data_class'] = 'fallback'
        o['price_provenance']['fallback'] = {'reason': 'current quotes unavailable'}
        o['decision_basis'] = 'hypothesis'
        p = self.page(o)['properties']
        self.assertEqual(p['시세구분']['select']['name'], '대체')
        self.assertEqual(p['판정구분']['select']['name'], '가설')
        self.assertIn('unavailable', p['대체사유']['rich_text'][0]['text']['content'])

    def test_preserves_original_message_and_trade_time_not_collection_time(self):
        page = self.page()
        self.assertEqual(page['children'][1]['paragraph']['rich_text'][0]['text']['content'],
                         self.observation['payload']['text'])
        self.assertEqual(page['properties']['최종시세']['date']['start'], '2026-11-05T00:00:00+00:00')
        self.assertEqual(page['properties']['회차']['date']['start'], '2026-11-05T09:00:00+09:00')
        self.assertEqual(page['notifications'], {'mode': 'silent'})
        self.assertLess(len(page['children']), 100)

    def test_unknown_legacy_prices_are_not_promoted_to_live(self):
        o = copy.deepcopy(self.observation)
        o['data_class'] = o['price_provenance']['data_class'] = 'unclassified'
        self.assertEqual(self.page(o)['properties']['시세구분']['select']['name'], '미분류')

    def test_retry_reconciles_same_delivery_key_without_another_page(self):
        api = FakeNotion()
        first = mirror(api, 'source-1', self.observation, 'https://github.com/original')
        second = mirror(api, 'source-1', self.observation, 'https://github.com/original')
        self.assertEqual(first['page_id'], second['page_id'])
        self.assertEqual(len(api.created), 1)

    def test_different_original_hash_cannot_overwrite_prior_delivery(self):
        api = FakeNotion()
        mirror(api, 'source-1', self.observation, 'https://github.com/original')
        o = copy.deepcopy(self.observation)
        o['payload_sha256'] = 'changed'
        with self.assertRaisesRegex(ValueError, 'different immutable'):
            mirror(api, 'source-1', o, 'https://github.com/changed')
        self.assertEqual(len(api.created), 1)

    def test_normal_sync_preserves_evidence_review_and_cannot_restore_old_class(self):
        api = FakeNotion()
        mirror(api, 'source-1', self.observation, 'https://github.com/original')
        props = api.pages[0]['properties']
        props['시세구분'] = {'select': {'name': '실시간 추정'}}
        props['분류확도'] = {'select': {'name': '코드·로그 추정'}}
        props['분류검토ID'] = {'rich_text': rich('2026-11-05-price-time-v2')}
        reviewed = copy.deepcopy(api.pages[0])
        mirror(api, 'source-1', self.observation, 'https://github.com/original')
        self.assertEqual(api.pages[0], reviewed)
        self.assertEqual(len(api.created), 1)

    def test_existing_row_gets_only_missing_metadata_without_rewriting_original(self):
        api = FakeNotion()
        mirror(api, 'source-1', self.observation, 'https://github.com/original')
        del api.pages[0]['properties']['기록종류']
        del api.pages[0]['properties']['원문상태']
        mirror(api, 'source-1', self.observation, 'https://github.com/original')
        patches = [x for x in api.calls if x[0] == 'PATCH']
        self.assertEqual(set(patches[0][2]['properties']), {'기록종류','원문상태'})
        self.assertEqual(len(api.created), 1)

    def test_failed_or_unknown_delivery_does_not_become_a_sent_record(self):
        for status in ('failed', 'pending', 'unknown'):
            o = copy.deepcopy(self.observation)
            o['delivery_status'] = status
            api = FakeNotion()
            self.assertIsNone(mirror(api, 'source-1', o, 'https://github.com/original'))
            self.assertEqual(api.calls, [])

    def test_ambiguous_create_failure_is_not_blindly_retried(self):
        class TimeoutNotion(FakeNotion):
            def call(self, method, path, body=None):
                if path == '/pages':
                    self.created.append(body)
                    raise RuntimeError('ambiguous timeout')
                return super().call(method, path, body)
        api = TimeoutNotion()
        with self.assertRaises(RuntimeError):
            mirror(api, 'source-1', self.observation, 'https://github.com/original')
        self.assertEqual(len(api.created), 1)

    def test_text_splits_respect_utf16_limit_and_preserve_every_character(self):
        text = '📌한국\n'*2000
        fragments = rich(text)
        self.assertEqual(''.join(x['text']['content'] for x in fragments), text)
        self.assertTrue(all(len(x['text']['content'].encode('utf-16-le'))//2 <= 1800 for x in fragments))

    def test_schema_adds_only_missing_properties_without_changing_title_type(self):
        api = FakeNotion()
        self.assertEqual(ensure_schema(api, 'database-1'), 'source-1')
        updates = [x for x in api.calls if x[0] == 'PATCH']
        self.assertEqual(len(updates), 1)
        self.assertNotIn('이름', updates[0][2]['properties'])
        self.assertIn('시세구분', updates[0][2]['properties'])
        self.assertEqual(set(self.page()['properties']), set(schema()))


if __name__ == '__main__':
    unittest.main()
