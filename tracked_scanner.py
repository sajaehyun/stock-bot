"""Run existing scanners with exact candle provenance and confirmed-send archive.

Scores, candidate selection and used prices are unchanged. Candle timestamps
are explicitly distinguished from last-trade times, including on retries.
"""
from contextlib import ExitStack
from datetime import datetime, timezone
from html import escape, unescape
import json
import os
from pathlib import Path
import re
import sys
from unittest.mock import patch

from quote_metadata import provenance, summary
from scanner_observation import candle_fact, scanner_snapshot, write_scanner_observation


def momentum_ranking(report):
    rows = report['results']
    lines = [f'📊 신지자 원본 순위 · {report["analyzed_at"]}',
             f'총 {len(rows)}종목 | 🟢{report["green"]} ⏳{report["wait"]} ❌{report["stop"]}']
    for index, row in enumerate(rows, 1):
        lines.append(f'{index}. {row["ticker"]} {row["entry"]} 점수:{row["score"]}')
    return '\n'.join(lines)


def confirm_ranking(receipt, chat, report):
    expected = [(str(i), row['ticker'], str(row['score'])) for i, row in enumerate(report['results'], 1)]
    received = re.findall(r'^([0-9]+)\. (\S+) .+? 점수:(-?[0-9]+)$',
                          (receipt or {}).get('text', ''), re.MULTILINE)
    if (not receipt or str(receipt.get('chat', {}).get('id')) != chat or
            'message_id' not in receipt or received != expected):
        raise RuntimeError('Telegram delivery differs from the original ranking.')
    return receipt['message_id']


def run(kind,context=None):
    import bot
    import intraday_bot
    from market_context import collect,summary as context_summary
    if context is None:context=collect(bot.yf,datetime.now(timezone.utc))
    quotes={}; candidates={}; messages=[]; analyzed_at=None
    original_sender=bot.send_telegram
    personal_chat=os.getenv('SHIN_JIJA_TELEGRAM_CHAT_ID','').strip() if kind=='daily' else ''
    original_messages=[]

    def sender(message):
        if personal_chat:
            original_messages.append(message)
            return True
        return deliver(message)

    def deliver(message, report=None):
        nonlocal analyzed_at
        if analyzed_at is None:
            analyzed_at=datetime.now(timezone.utc).isoformat()
            snap=scanner_snapshot({'results':[],'market_context':context},quotes)
            message=escape(summary(provenance(snap,analyzed_at)))+'\n\n'+message
            from split_adjustment import summary as adjustment_summary
            adjustment_text=adjustment_summary(snap)
            if adjustment_text:message=escape(adjustment_text)+'\n'+message
            message=escape(context_summary(context,news_limit=0))+'\n'+message
        if report is None:
            confirmed=original_sender(message)
            if confirmed is not True:raise RuntimeError('Telegram send was not confirmed; observation not archived')
        else:
            group_chat=bot.CHAT_ID.strip()
            if not group_chat or group_chat==personal_chat:
                raise RuntimeError('Distinct personal and 신규코드 Telegram destinations are required.')
            personal_id=confirm_ranking(bot.send_telegram_receipt(message,personal_chat),personal_chat,report)
            group_id=confirm_ranking(bot.send_telegram_receipt(message,group_chat),group_chat,report)
            count=len(report['results'])
            print(f'Shin Jija Telegram parity: {count}/{count} order and score matches; '
                  f'source {report["analyzed_at"]}; personal message {personal_id}; group message {group_id}',
                  flush=True)
        messages.append(unescape(re.sub(r'</?b>','',message)))
        return True

    def provider(function,name):
        def wrapped(*args,**kwargs):
            frame=function(*args,**kwargs)
            if frame is not None:frame.attrs['price_provider']=name
            return frame
        return wrapped

    original_fetch=bot.fetch_ohlcv
    def fetch(symbol):
        frame=original_fetch(symbol)
        if frame is not None and not frame.empty:
            quotes[symbol]=candle_fact(frame,'1d',frame.attrs.get('price_provider','Scanner daily OHLCV'))
        return frame

    original_history=bot.yf.Ticker.history
    def history(ticker,*args,**kwargs):
        frame=original_history(ticker,*args,**kwargs)
        if kwargs.get('interval')=='30m' and frame is not None and not frame.empty:
            clean=frame.replace([float('inf'),-float('inf')],float('nan')).dropna(subset=['Close','High','Low','Volume'])
            clean=clean.loc[clean.Close>0]
            if not clean.empty:candidates[ticker.ticker]=candle_fact(clean,'30m','Yahoo regular 30-minute OHLCV')
        return frame

    original_intraday=intraday_bot.get_intraday_indicators
    def indicators(symbol):
        result=original_intraday(symbol)
        if result is not None and symbol in candidates:quotes[symbol]=candidates[symbol]
        return result

    with ExitStack() as stack:
        stack.enter_context(patch.object(bot,'send_telegram',sender))
        stack.enter_context(patch.object(intraday_bot,'send_telegram',sender))
        stack.enter_context(patch.object(bot,'fetch_ohlcv',fetch))
        for name,label in [('_yfinance_candles','Yahoo regular daily OHLCV'),('_finnhub_candles','Finnhub daily OHLCV')]:
            stack.enter_context(patch.object(bot,name,provider(getattr(bot,name),label)))
        stack.enter_context(patch.object(bot.yf.Ticker,'history',history))
        stack.enter_context(patch.object(intraday_bot,'get_intraday_indicators',indicators))
        if kind=='intraday':
            report=intraday_bot.analyze();record_kind='장중스캔'
        else:
            report=bot.format_mode1('sp500+sox');record_kind='종목추천'
        if not report or report.get('error'):
            raise RuntimeError('No complete scanner result to deliver')
        if personal_chat:
            if len(original_messages)!=1:
                raise RuntimeError('The original scanner did not produce one momentum message')
            if len(report['results']) < bot.MAX_TICKERS:
                original_messages.clear()
                original_quotes=quotes.copy()
                quotes.clear()
                retry=bot.format_mode1('sp500+sox')
                if not retry or len(retry.get('results',[])) < len(report['results']):
                    quotes.clear()
                    quotes.update(original_quotes)
                else:
                    report=retry
            deliver(escape(momentum_ranking(report)), report)
        if not messages:
            raise RuntimeError('No complete confirmed scanner delivery to archive')
        # Save every used input, including scanned symbols outside the TOP10.
        report['price_inputs']=quotes
        report['market_context']=context
        report['quote_metadata_version']=2
        report['telegram_messages']=messages
        report_dir=Path('intraday_reports' if kind=='intraday' else 'actions_reports')
        report_dir.mkdir(exist_ok=True)
        (report_dir/('tracked-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'.json')).write_text(
            json.dumps(report,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
        return write_scanner_observation(report,quotes,messages,record_kind,analyzed_at)


if __name__=='__main__':run(sys.argv[1] if len(sys.argv)>1 else 'daily')
