"""
Backtest Engine — Validate historical stock recommendations against live data.
Calculates win rates, average returns, and Sharpe ratios for 3, 5, 10, 20 day holding periods.
"""

import os, json, pathlib, logging
from datetime import datetime, timedelta
import pandas as pd
import yfinance as yf
import numpy as np

LOG = logging.getLogger("backtest")
HISTORY_DIR = pathlib.Path("history")
PRESIGNAL_DIR = pathlib.Path("presignal")
CONVICTION_DIR = pathlib.Path("conviction")
RESULT_DIR = pathlib.Path("backtest_results")
RESULT_DIR.mkdir(exist_ok=True)

def run_full_backtest(modes=None, hold_days=None):
    if modes is None: modes = ["momentum", "presignal", "conviction"]
    if hold_days is None: hold_days = [3, 5, 10, 20]
    
    all_recommendations = []
    
    # Collect recommendations from all modes
    if "momentum" in modes:
        for f in sorted(HISTORY_DIR.glob("*.json"), reverse=True)[:20]:
            _collect_from_file(f, all_recommendations, "momentum")
            
    if "presignal" in modes:
        for f in sorted(PRESIGNAL_DIR.glob("*.json"), reverse=True)[:20]:
            _collect_from_file(f, all_recommendations, "presignal")
            
    if "conviction" in modes:
        for f in sorted(CONVICTION_DIR.glob("*.json"), reverse=True)[:20]:
            _collect_from_file(f, all_recommendations, "conviction")
            
    if not all_recommendations:
        return {"error": "분석할 과거 추천 내역이 없습니다."}

    results = []
    # Deduplicate by (ticker, date)
    seen = set()
    unique_recs = []
    for r in all_recommendations:
        key = (r['ticker'], r['date'])
        if key not in seen:
            seen.add(key)
            unique_recs.append(r)

    # Process each ticker
    summary = {d: {"wins": 0, "total": 0, "returns": []} for d in hold_days}
    mode_stats = {m: {d: {"wins": 0, "total": 0, "returns": []} for d in hold_days} for m in modes}

    # Optimization: Batch fetch yfinance data if possible, or just fetch per ticker
    tickers = list(set(r['ticker'] for r in unique_recs))
    
    for ticker in tickers:
        try:
            # Fetch at least 60 days of history to cover all hold periods
            end_date = datetime.now()
            start_date = end_date - timedelta(days=90)
            data = yf.download(ticker, start=start_date.strftime("%Y-%m-%d"), end=end_date.strftime("%Y-%m-%d"), progress=False)
            if data.empty: continue
            
            # Use multi-index removal if necessary (yfinance 0.2.x+ behavior)
            if isinstance(data.columns, pd.MultiIndex):
                data.columns = data.columns.get_level_values(0)

            ticker_recs = [r for r in unique_recs if r['ticker'] == ticker]
            for rec in ticker_recs:
                rec_date = pd.to_datetime(rec['date']).normalize()
                if rec_date not in data.index:
                    # Find closest previous trading day
                    available_dates = data.index[data.index <= rec_date]
                    if available_dates.empty: continue
                    rec_date = available_dates[-1]
                
                entry_price = data.loc[rec_date, 'Close']
                if hasattr(entry_price, 'iloc'): entry_price = float(entry_price.iloc[0])
                else: entry_price = float(entry_price)
                
                rec_results = {"ticker": ticker, "mode": rec['mode'], "date": rec['date'], "results": {}}
                
                for days in hold_days:
                    # Find index offset
                    idx = data.index.get_loc(rec_date)
                    future_idx = idx + days
                    if future_idx >= len(data): continue
                    
                    exit_price = data.iloc[future_idx]['Close']
                    if hasattr(exit_price, 'iloc'): exit_price = float(exit_price.iloc[0])
                    else: exit_price = float(exit_price)
                    
                    ret = (exit_price - entry_price) / entry_price * 100
                    win = 1 if ret > 0 else 0
                    
                    rec_results["results"][days] = {"return": round(ret, 2), "win": win}
                    
                    summary[days]["wins"] += win
                    summary[days]["total"] += 1
                    summary[days]["returns"].append(ret)
                    
                    mode_stats[rec['mode']][days]["wins"] += win
                    mode_stats[rec['mode']][days]["total"] += 1
                    mode_stats[rec['mode']][days]["returns"].append(ret)
                
                if rec_results["results"]:
                    results.append(rec_results)
                    
        except Exception as e:
            LOG.error(f"Error backtesting {ticker}: {e}")

    # Final Summary Calculation
    final_summary = {}
    for days in hold_days:
        s = summary[days]
        if s["total"] > 0:
            avg_ret = np.mean(s["returns"])
            std_ret = np.std(s["returns"])
            sharpe = (avg_ret / std_ret) if std_ret > 0 else 0
            final_summary[days] = {
                "win_rate": round(s["wins"] / s["total"] * 100, 1),
                "avg_return": round(avg_ret, 2),
                "sharpe": round(sharpe, 2),
                "total": s["total"]
            }

    final_mode_stats = {}
    for m in modes:
        final_mode_stats[m] = {}
        for days in hold_days:
            ms = mode_stats[m][days]
            if ms["total"] > 0:
                avg_ret = np.mean(ms["returns"])
                final_mode_stats[m][days] = {
                    "win_rate": round(ms["wins"] / ms["total"] * 100, 1),
                    "avg_return": round(avg_ret, 2),
                    "total": ms["total"]
                }

    output = {
        "analyzed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "summary": final_summary,
        "mode_stats": final_mode_stats,
        "results": sorted(results, key=lambda x: x['date'], reverse=True)[:100]
    }
    
    # Save result
    fname = f"backtest_{datetime.now().strftime('%Y-%m-%d_%H%M%S')}.json"
    with open(RESULT_DIR / fname, "r", encoding="utf-8") if False else open(RESULT_DIR / fname, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
        
    return output

def _collect_from_file(f, all_list, mode):
    try:
        with open(f, "r", encoding="utf-8") as fp:
            data = json.load(fp)
            # Find date
            file_date = None
            if "analyzed_at" in data:
                file_date = data["analyzed_at"].split(" ")[0]
            else:
                # Use filename date
                stem = f.stem
                if "_" in stem: file_date = stem.split("_")[0]
            
            if not file_date: return
            
            results = data.get("results", [])
            for r in results:
                ticker = r.get("ticker")
                if not ticker: continue
                # In momentum mode, only take "green" or "wait" maybe?
                # User's logic in conviction mode only takes top ones too.
                # For simplicity, filter by score > 60 in momentum, or just take all recommendations
                take_it = False
                if mode == "momentum":
                    if "🟢" in r.get("entry", "") or r.get("score", 0) > 60: take_it = True
                elif mode == "presignal":
                    if r.get("presignal_score", 0) >= 50: take_it = True
                elif mode == "conviction":
                    if r.get("conviction_score", 0) >= 60: take_it = True
                
                if take_it:
                    all_list.append({
                        "ticker": ticker,
                        "date": file_date,
                        "mode": mode
                    })
    except Exception:
        pass