"""Run existing scanners with exact candle provenance and confirmed-send archive.

Scores, candidate selection and used prices are unchanged. Candle timestamps
are explicitly distinguished from last-trade times, including on retries.
"""
from contextlib import ExitStack
from datetime import datetime, timezone
from html import escape, unescape
import json
from pathlib import Path
import re
import sys
from unittest.mock import patch

from quote_metadata import provenance, summary
from scanner_observation import candle_fact, scanner_snapshot, write_scanner_observation


def run(kind,context=None):
    import bot
    import intraday_bot
    from market_context import collect,summary as context_summary
    if context is None:context=collect(bot.yf,datetime.now(timezone.utc))
    quotes={}; candidates={}; messages=[]; analyzed_at=None
    original_sender=bot.send_telegram

    def sender(message):
        nonlocal analyzed_at
        if analyzed_at is None:
            analyzed_at=datetime.now(timezone.utc).isoformat()
            snap=scanner_snapshot({'results':[],'market_context':context},quotes)
            message=escape(summary(provenance(snap,analyzed_at)))+'\n\n'+message
            from split_adjustment import summary as adjustment_summary
            adjustment_text=adjustment_summary(snap)
            if adjustment_text:message=escape(adjustment_text)+'\n'+message
            message=escape(context_summary(context))+'\n'+message
        confirmed=original_sender(message)
        if confirmed is not True:raise RuntimeError('Telegram send was not confirmed; observation not archived')
        messages.append(unescape(re.sub(r'</?b>','',message)))
        return confirmed

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
        if not report or report.get('error') or not messages:
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
