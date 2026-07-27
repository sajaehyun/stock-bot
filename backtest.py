"""
Backtest Engine — Validate historical stock recommendations against live data.
Calculates win rates, average returns, and Sharpe ratios for 3, 5, 10, 20 day holding periods.
"""

import os, json, pathlib, logging
from datetime import datetime, timedelta
import pandas as pd
import numpy as np


LOG = logging.getLogger("backtest")
HISTORY_DIR   = pathlib.Path("history")
PRESIGNAL_DIR = pathlib.Path("presignal")
CONVICTION_DIR = pathlib.Path("conviction")
RESULT_DIR    = pathlib.Path("backtest_results")
RESULT_DIR.mkdir(exist_ok=True)


def _fetch_price_data(ticker: str) -> pd.DataFrame:
    """Twelve Data REST API로 일봉 수집 (yfinance 대체)"""
    import os, requests
    api_key = os.getenv("TWELVE_DATA_KEY", "")
    if not api_key:
        return pd.DataFrame()
    td_symbol = ticker.replace("-", "/")
    try:
        resp = requests.get(
            "https://api.twelvedata.com/time_series",
            params={
                "symbol":     td_symbol,
                "interval":   "1day",
                "outputsize": 90,
                "order":      "ASC",
                "apikey":     api_key,
            },
            timeout=15,
        )
        data = resp.json()
        if data.get("status") == "error" or "values" not in data:
            return pd.DataFrame()
        df = pd.DataFrame(data["values"])
        df["datetime"] = pd.to_datetime(df["datetime"])
        df = df.set_index("datetime").sort_index()
        df["Close"] = pd.to_numeric(df["close"], errors="coerce")
        return df.dropna(subset=["Close"])
    except Exception as e:
        LOG.warning("백테스트 데이터 오류 [%s]: %s", ticker, e)
        return pd.DataFrame()


def run_full_backtest(modes=None, hold_days=None):
    if modes is None:    modes     = ["momentum", "presignal", "conviction"]
    if hold_days is None: hold_days = [3, 5, 10, 20]

    all_recommendations = []

    if "momentum"   in modes:
        for f in sorted(HISTORY_DIR.glob("*.json"),   reverse=True)[:20]:
            _collect_from_file(f, all_recommendations, "momentum")
    if "presignal"  in modes:
        for f in sorted(PRESIGNAL_DIR.glob("*.json"), reverse=True)[:20]:
            _collect_from_file(f, all_recommendations, "presignal")
    if "conviction" in modes:
        for f in sorted(CONVICTION_DIR.glob("*.json"),reverse=True)[:20]:
            _collect_from_file(f, all_recommendations, "conviction")

    if not all_recommendations:
        return {"error": "분석할 과거 추천 내역이 없습니다."}

    # 중복 제거
    seen, unique_recs = set(), []
    for r in all_recommendations:
        key = (r["ticker"], r["date"])
        if key not in seen:
            seen.add(key)
            unique_recs.append(r)

    summary    = {d: {"wins": 0, "total": 0, "returns": []} for d in hold_days}
    mode_stats = {m: {d: {"wins": 0, "total": 0, "returns": []} for d in hold_days} for m in modes}
    results    = []

    tickers = list(set(r["ticker"] for r in unique_recs))

    for ticker in tickers:
        try:
            data = _fetch_price_data(ticker)
            if data.empty:
                continue

            ticker_recs = [r for r in unique_recs if r["ticker"] == ticker]
            for rec in ticker_recs:
                rec_date = pd.to_datetime(rec["date"]).normalize()
                if rec_date not in data.index:
                    available = data.index[data.index <= rec_date]
                    if available.empty:
                        continue
                    rec_date = available[-1]

                entry_price = float(data.loc[rec_date, "Close"])
                rec_results = {"ticker": ticker, "mode": rec["mode"],
                               "date": rec["date"], "results": {}}

                for days in hold_days:
                    idx = data.index.get_loc(rec_date)
                    future_idx = idx + days
                    if future_idx >= len(data):
                        continue

                    exit_price = float(data.iloc[future_idx]["Close"])
                    ret  = (exit_price - entry_price) / entry_price * 100
                    win  = 1 if ret > 0 else 0

                    rec_results["results"][days] = {"return": round(ret, 2), "win": win}

                    summary[days]["wins"]    += win
                    summary[days]["total"]   += 1
                    summary[days]["returns"].append(ret)

                    mode_stats[rec["mode"]][days]["wins"]    += win
                    mode_stats[rec["mode"]][days]["total"]   += 1
                    mode_stats[rec["mode"]][days]["returns"].append(ret)

                if rec_results["results"]:
                    results.append(rec_results)

        except Exception as e:
            LOG.error("백테스트 오류 [%s]: %s", ticker, e)

    # 최종 집계
    final_summary = {}
    for days in hold_days:
        s = summary[days]
        if s["total"] > 0:
            avg_ret = np.mean(s["returns"])
            std_ret = np.std(s["returns"])
            sharpe  = (avg_ret / std_ret) if std_ret > 0 else 0
            final_summary[days] = {
                "win_rate":   round(s["wins"] / s["total"] * 100, 1),
                "avg_return": round(avg_ret, 2),
                "sharpe":     round(sharpe, 2),
                "total":      s["total"],
            }

    final_mode_stats = {}
    for m in modes:
        final_mode_stats[m] = {}
        for days in hold_days:
            ms = mode_stats[m][days]
            if ms["total"] > 0:
                avg_ret = np.mean(ms["returns"])
                final_mode_stats[m][days] = {
                    "win_rate":   round(ms["wins"] / ms["total"] * 100, 1),
                    "avg_return": round(avg_ret, 2),
                    "total":      ms["total"],
                }

    output = {
        "analyzed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "summary":     final_summary,
        "mode_stats":  final_mode_stats,
        "results":     sorted(results, key=lambda x: x["date"], reverse=True)[:100],
    }

    fname = f"backtest_{datetime.now().strftime('%Y-%m-%d_%H%M%S')}.json"
    with open(RESULT_DIR / fname, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    return output


def _collect_from_file(f, all_list, mode):
    try:
        with open(f, "r", encoding="utf-8") as fp:
            data = json.load(fp)

        file_date = None
        if "analyzed_at" in data:
            file_date = data["analyzed_at"].split(" ")[0]
        else:
            stem = f.stem
            if "_" in stem:
                file_date = stem.split("_")[0]
        if not file_date:
            return

        for r in data.get("results", []):
            ticker = r.get("ticker")
            if not ticker:
                continue
            take_it = False
            if mode == "momentum"   and ("🟢" in r.get("entry", "") or r.get("score", 0) > 60):
                take_it = True
            elif mode == "presignal"  and r.get("presignal_score",  0) >= 50:
                take_it = True
            elif mode == "conviction" and r.get("conviction_score", 0) >= 60:
                take_it = True

            if take_it:
                all_list.append({"ticker": ticker, "date": file_date, "mode": mode})

    except Exception:
        pass
