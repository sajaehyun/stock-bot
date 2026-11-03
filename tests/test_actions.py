"""Regression checks for the scheduled entry points; no live API calls."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import numpy as np
import pandas as pd

import bot
import intraday_bot


class EntryPointTests(unittest.TestCase):
    def test_default_cli_calls_existing_momentum_scanner(self):
        with patch.object(bot, 'format_mode1', return_value={'results': [{}]}) as scan:
            self.assertEqual(bot.main([]), 0)
        scan.assert_called_once_with('sp500+sox')

    def test_all_data_failed_exits_nonzero(self):
        with patch.object(bot, 'format_mode1', return_value={'error': 'No data', 'results': []}):
            self.assertEqual(bot.main([]), 1)

    def test_cli_modes_keep_the_requested_universe(self):
        for mode, function in [('presignal', 'analyze_presignal'), ('conviction', 'analyze_conviction')]:
            with self.subTest(mode=mode), patch.object(bot, function, return_value={'results': [{}]}) as scan:
                self.assertEqual(bot.main([mode, 'sox']), 0)
                scan.assert_called_once_with('sox')


class DeliveryTests(unittest.TestCase):
    def test_missing_required_config_fails(self):
        with patch.object(bot, 'TELEGRAM_TOKEN', ''), patch.dict(os.environ, REQUIRE_TELEGRAM='1'):
            with self.assertRaisesRegex(RuntimeError, 'configuration is missing'):
                bot.send_telegram('test')

    def test_success_requires_telegram_confirmation(self):
        response = Mock(status_code=200)
        response.json.return_value = {'ok': True}
        with patch.object(bot, 'TELEGRAM_TOKEN', 'test-token'), patch.object(bot, 'CHAT_ID', 'test-chat'), \
                patch.object(bot.requests, 'post', return_value=response):
            self.assertTrue(bot.send_telegram('test'))
            response.json.return_value = {'ok': False}
            with self.assertRaisesRegex(RuntimeError, 'delivery failed'):
                bot.send_telegram('test')

    def test_request_error_does_not_expose_token(self):
        with patch.object(bot, 'TELEGRAM_TOKEN', 'secret-token'), patch.object(bot, 'CHAT_ID', 'test-chat'), \
                patch.object(bot.requests, 'post', side_effect=bot.requests.RequestException('secret-token')):
            with self.assertRaises(RuntimeError) as caught:
                bot.send_telegram('test')
            self.assertNotIn('secret-token', str(caught.exception))


class IntradayTests(unittest.TestCase):
    def test_real_indicator_calculation_on_30minute_bars(self):
        prices = np.linspace(100, 120, 100)
        frame = pd.DataFrame({'Close': prices, 'High': prices + 1,
                              'Low': prices - 1, 'Volume': np.full(100, 1000)},
                             index=pd.date_range('2026-10-01', periods=100, freq='30min'))
        ticker = Mock()
        ticker.history.return_value = frame
        with patch.object(intraday_bot.yf, 'Ticker', return_value=ticker):
            result = intraday_bot.get_intraday_indicators('NVDA')
        self.assertEqual(result['ticker'], 'NVDA')
        self.assertEqual(result['price'], 120)
        self.assertTrue(np.isfinite(result['rsi']))
        self.assertEqual(ticker.history.call_args.kwargs['interval'], '30m')
        self.assertEqual(ticker.history.call_args.kwargs['period'], '1mo')

    def test_total_download_failure_is_not_no_signal_success(self):
        with patch.object(intraday_bot, 'SOXL_STOCKS', ['NVDA']), \
                patch.object(intraday_bot, 'get_intraday_indicators', return_value=None), \
                patch.object(intraday_bot, 'send_telegram') as send:
            with self.assertRaisesRegex(RuntimeError, 'No intraday market data'):
                intraday_bot.analyze()
            send.assert_not_called()

    def test_valid_data_with_no_signals_is_success_and_saved(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp, \
                patch.object(intraday_bot, 'SOXL_STOCKS', ['NVDA']), \
                patch.object(intraday_bot, 'get_intraday_indicators', return_value={'ticker': 'NVDA'}), \
                patch.object(intraday_bot, 'calculate_intraday_score', return_value=(0, 0, [])), \
                patch.object(intraday_bot, 'send_telegram') as send:
            previous = Path.cwd()
            try:
                os.chdir(temp)
                report = intraday_bot.analyze()
                self.assertEqual(report['scanned_total'], 1)
                saved = list(Path('intraday_reports').glob('*.json'))
                self.assertEqual(len(saved), 1)
                self.assertEqual(json.loads(saved[0].read_text(encoding='utf-8'))['results'], [])
                send.assert_called_once()
            finally:
                os.chdir(previous)


if __name__ == '__main__':
    unittest.main()
