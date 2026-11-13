import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import pandas as pd
import bot
import tracked_scanner


class ConfirmedScannerTests(unittest.TestCase):
    def setUp(self):
        self.previous=Path.cwd()
        self.directory=tempfile.TemporaryDirectory(dir=self.previous)
        os.chdir(self.directory.name)
        self.frame=pd.DataFrame({'Open':[99.],'High':[101.],'Low':[98.],'Close':[100.],'Volume':[10.]},
                                index=pd.DatetimeIndex(['2026-11-02']))

    def tearDown(self):
        os.chdir(self.previous);self.directory.cleanup()

    def scan(self,*args):
        bot.fetch_ohlcv('NVDA')
        bot.send_telegram('<b>exact ranking</b>\nNVDA $100.00')
        return {'results':[{'ticker':'NVDA','price':100.,'score':42,'entry_key':'wait'}]}

    def test_exact_prices_scores_message_and_symbol_metadata_survive_delivery(self):
        with patch.object(bot,'fetch_ohlcv',return_value=self.frame),patch.object(bot,'format_mode1',side_effect=self.scan),\
             patch.object(bot,'send_telegram',return_value=True),patch.dict(os.environ,{'GITHUB_RUN_ID':'990001','GITHUB_REPOSITORY':'sajaehyun/stock-bot'}):
            result=tracked_scanner.run('daily',{})
        self.assertEqual(result['delivery_status'],'sent')
        self.assertEqual(result['data_class'],'fallback')
        self.assertEqual(result['price_provenance']['symbols']['NVDA']['timestamp_kind'],'official_session_close')
        self.assertIn('exact ranking\nNVDA $100.00',result['payload']['text'])
        self.assertEqual(result['payload']['snapshot']['results'][0]['score'],42)
        self.assertEqual(len(list(Path('delivered_observations').glob('*.json'))),1)

    def test_failed_send_cannot_create_a_confirmed_observation(self):
        with patch.object(bot,'fetch_ohlcv',return_value=self.frame),patch.object(bot,'format_mode1',side_effect=self.scan),\
             patch.object(bot,'send_telegram',side_effect=RuntimeError('delivery failed')):
            with self.assertRaises(RuntimeError):tracked_scanner.run('daily',{})
        self.assertFalse(Path('delivered_observations').exists())


    def test_scheduled_momentum_uses_one_ranking_for_both_chats(self):
        report = {'analyzed_at': '2026-11-13 13:00:00 (KST)', 'green': 30, 'wait': 0, 'stop': 0,
                  'results': [{'ticker': f'T{i:02}', 'entry': '🟢', 'entry_key': 'green', 'score': 70,
                               'price': 100.0} for i in reversed(range(30))]}

        def scan(*args):
            bot.send_telegram('Original top-ten message')
            return report

        receipt = Mock(side_effect=lambda text, chat: {'message_id': 10, 'chat': {'id': int(chat)}, 'text': text})
        with patch.dict(os.environ, {'SHIN_JIJA_TELEGRAM_CHAT_ID': '10'}), \
                patch.object(bot, 'CHAT_ID', '-20'), patch.object(bot, 'format_mode1', side_effect=scan) as run, \
                patch.object(bot, 'send_telegram_receipt', receipt):
            result = tracked_scanner.run('daily', {})
        run.assert_called_once()
        self.assertEqual([call.args[1] for call in receipt.call_args_list], ['10', '-20'])
        self.assertEqual(receipt.call_args_list[0].args[0], receipt.call_args_list[1].args[0])
        self.assertEqual(result['payload']['snapshot']['results'][0]['ticker'], 'T29')
        self.assertIn('30. T00 🟢 점수:70', result['payload']['text'])


if __name__=='__main__':unittest.main()
