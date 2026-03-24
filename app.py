import os
import glob
import json
import threading
from flask import Flask, render_template_string, redirect, url_for, request
from datetime import datetime
from bot import analyze

app = Flask(__name__)
app.config['CACHED_DATA'] = {}
app.config['LAST_UPDATE'] = "분석된 적 없음"
app.config['IS_ANALYZING'] = False


def get_available_dates():
    history_dir = "history"
    if not os.path.exists(history_dir):
        return []
    files = glob.glob(os.path.join(history_dir, "*.json"))
    return sorted([os.path.basename(f).replace('.json', '') for f in files], reverse=True)


def get_history_data(date_str):
    filepath = os.path.join("history", f"{date_str}.json")
    if os.path.exists(filepath):
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return None
    return None


HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>장기 투자 분석기 · Long-Term Investment Analyzer</title>
    <meta name="description" content="Buffett·Graham·Lynch·Fisher·Greenblatt·Templeton 6대 투자 이론 기반 S&P 500 종합 분석 대시보드">
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg:        #07080f;
            --bg2:       #0e0f1c;
            --bg3:       #14162a;
            --border:    rgba(255,255,255,0.07);
            --accent:    #6366f1;
            --accent2:   #8b5cf6;
            --green:     #22c55e;
            --cyan:      #06b6d4;
            --yellow:    #eab308;
            --orange:    #f97316;
            --red:       #ef4444;
            --text:      #e2e8f0;
            --muted:     #64748b;
            --card-glow: rgba(99,102,241,0.08);
        }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            background: var(--bg);
            color: var(--text);
            font-family: 'Inter', sans-serif;
            min-height: 100vh;
        }

        /* ── HEADER ── */
        .header {
            background: linear-gradient(135deg, #0e0f1c 0%, #14162a 100%);
            border-bottom: 1px solid var(--border);
            padding: 0 32px;
            position: sticky;
            top: 0;
            z-index: 100;
            backdrop-filter: blur(20px);
        }
        .header-inner {
            max-width: 1600px;
            margin: 0 auto;
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 20px;
            padding: 16px 0;
            flex-wrap: wrap;
        }
        .logo {
            display: flex;
            align-items: center;
            gap: 12px;
        }
        .logo-icon {
            width: 40px; height: 40px;
            background: linear-gradient(135deg, var(--accent), var(--accent2));
            border-radius: 10px;
            display: flex; align-items: center; justify-content: center;
            font-size: 20px;
        }
        .logo-text h1 {
            font-size: 18px;
            font-weight: 700;
            background: linear-gradient(135deg, #a5b4fc, #e879f9);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        .logo-text p {
            font-size: 11px;
            color: var(--muted);
            margin-top: 1px;
        }
        .header-controls {
            display: flex;
            align-items: center;
            gap: 12px;
            flex-wrap: wrap;
        }
        .last-update { font-size: 12px; color: var(--muted); }

        /* ── BUTTONS ── */
        .btn {
            display: inline-flex; align-items: center; gap: 8px;
            padding: 9px 18px;
            border-radius: 8px;
            font-size: 13px; font-weight: 600;
            cursor: pointer;
            border: none;
            text-decoration: none;
            transition: all 0.2s;
        }
        .btn-primary {
            background: linear-gradient(135deg, var(--accent), var(--accent2));
            color: #fff;
            box-shadow: 0 4px 15px rgba(99,102,241,0.35);
        }
        .btn-primary:hover { transform: translateY(-1px); box-shadow: 0 6px 20px rgba(99,102,241,0.5); }
        .btn-ghost {
            background: var(--bg3);
            color: var(--muted);
            border: 1px solid var(--border);
        }
        .btn-ghost:hover { color: var(--text); border-color: rgba(255,255,255,0.15); }
        .btn-disabled {
            background: var(--bg3);
            color: var(--muted);
            cursor: not-allowed;
            border: 1px solid var(--border);
        }

        /* Spinner */
        .spinner {
            width: 14px; height: 14px;
            border: 2px solid rgba(255,255,255,0.2);
            border-top-color: #fff;
            border-radius: 50%;
            animation: spin 0.8s linear infinite;
        }
        @keyframes spin { to { transform: rotate(360deg); } }

        /* Date picker */
        .date-input {
            padding: 9px 14px;
            background: var(--bg3);
            border: 1px solid var(--border);
            border-radius: 8px;
            color: var(--text);
            font-size: 13px;
            font-family: 'Inter', sans-serif;
            cursor: pointer;
        }

        /* ── MAIN ── */
        .main { max-width: 1600px; margin: 0 auto; padding: 28px 32px; }

        /* ── STATS ROW ── */
        .stats-row {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
            gap: 14px;
            margin-bottom: 24px;
        }
        .stat-card {
            background: var(--bg2);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 16px 20px;
            text-align: center;
        }
        .stat-card .val {
            font-size: 26px; font-weight: 800;
            background: linear-gradient(135deg, #a5b4fc, #e879f9);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        .stat-card .lbl { font-size: 11px; color: var(--muted); margin-top: 4px; }

        /* ── FILTER ROW ── */
        .filter-row {
            display: flex;
            align-items: center;
            gap: 8px;
            margin-bottom: 24px;
            flex-wrap: wrap;
        }
        .filter-btn {
            padding: 7px 14px;
            border-radius: 20px;
            font-size: 12px; font-weight: 600;
            cursor: pointer;
            border: 1px solid var(--border);
            background: transparent;
            color: var(--muted);
            transition: all 0.2s;
        }
        .filter-btn:hover, .filter-btn.active {
            background: var(--bg3);
            color: var(--text);
            border-color: rgba(255,255,255,0.2);
        }
        .filter-btn[data-signal="강력"] { border-color: rgba(34,197,94,0.3); }
        .filter-btn[data-signal="강력"].active { background: rgba(34,197,94,0.15); color: var(--green); }
        .filter-btn[data-signal="매수"] { border-color: rgba(6,182,212,0.3); }
        .filter-btn[data-signal="매수"].active { background: rgba(6,182,212,0.15); color: var(--cyan); }
        .filter-btn[data-signal="관망"] { border-color: rgba(234,179,8,0.3); }
        .filter-btn[data-signal="관망"].active { background: rgba(234,179,8,0.15); color: var(--yellow); }
        .filter-btn[data-signal="고려"] { border-color: rgba(249,115,22,0.3); }
        .filter-btn[data-signal="고려"].active { background: rgba(249,115,22,0.15); color: var(--orange); }
        .filter-btn[data-signal="매도"] { border-color: rgba(239,68,68,0.3); }
        .filter-btn[data-signal="매도"].active { background: rgba(239,68,68,0.15); color: var(--red); }

        .search-input {
            margin-left: auto;
            padding: 7px 14px;
            background: var(--bg3);
            border: 1px solid var(--border);
            border-radius: 20px;
            color: var(--text);
            font-size: 12px;
            font-family: 'Inter', sans-serif;
            outline: none;
            min-width: 180px;
        }
        .search-input::placeholder { color: var(--muted); }

        /* ── STOCK GRID ── */
        .stock-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(360px, 1fr));
            gap: 18px;
        }

        .stock-card {
            background: var(--bg2);
            border: 1px solid var(--border);
            border-radius: 16px;
            padding: 22px;
            transition: all 0.2s;
            cursor: pointer;
            position: relative;
            overflow: hidden;
        }
        .stock-card::before {
            content: '';
            position: absolute;
            top: 0; left: 0; right: 0;
            height: 3px;
            background: linear-gradient(90deg, var(--accent), var(--accent2));
            opacity: 0;
            transition: opacity 0.2s;
        }
        .stock-card:hover {
            border-color: rgba(99,102,241,0.3);
            transform: translateY(-2px);
            box-shadow: 0 8px 30px var(--card-glow);
        }
        .stock-card:hover::before { opacity: 1; }
        .stock-card.signal-강력:hover::before { background: linear-gradient(90deg, #16a34a, #22c55e); }
        .stock-card.signal-매수:hover::before { background: linear-gradient(90deg, #0891b2, #06b6d4); }
        .stock-card.signal-관망:hover::before { background: linear-gradient(90deg, #ca8a04, #eab308); }
        .stock-card.signal-고려:hover::before { background: linear-gradient(90deg, #ea580c, #f97316); }
        .stock-card.signal-매도:hover::before { background: linear-gradient(90deg, #dc2626, #ef4444); }

        /* Card header */
        .card-head {
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            margin-bottom: 14px;
        }
        .card-ticker { font-size: 22px; font-weight: 800; color: #fff; }
        .card-name { font-size: 12px; color: var(--muted); margin-top: 2px; max-width: 200px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .card-price { text-align: right; }
        .card-price .price { font-size: 18px; font-weight: 700; color: var(--text); }
        .card-price .currency { font-size: 11px; color: var(--muted); }

        /* Signal badge */
        .signal-badge {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 5px 12px;
            border-radius: 20px;
            font-size: 12px;
            font-weight: 700;
            margin-bottom: 14px;
        }
        .sig-강력 { background: rgba(34,197,94,0.15); color: var(--green); border: 1px solid rgba(34,197,94,0.25); }
        .sig-매수 { background: rgba(6,182,212,0.15); color: var(--cyan); border: 1px solid rgba(6,182,212,0.25); }
        .sig-관망 { background: rgba(234,179,8,0.15); color: var(--yellow); border: 1px solid rgba(234,179,8,0.25); }
        .sig-고려 { background: rgba(249,115,22,0.15); color: var(--orange); border: 1px solid rgba(249,115,22,0.25); }
        .sig-매도 { background: rgba(239,68,68,0.15); color: var(--red); border: 1px solid rgba(239,68,68,0.25); }

        /* Total score ring */
        .score-row {
            display: flex;
            align-items: center;
            gap: 14px;
            margin-bottom: 16px;
        }
        .score-ring {
            width: 54px; height: 54px;
            border-radius: 50%;
            display: flex; align-items: center; justify-content: center;
            font-size: 14px; font-weight: 800;
            flex-shrink: 0;
            position: relative;
        }
        .score-ring-inner {
            width: 42px; height: 42px;
            border-radius: 50%;
            background: var(--bg);
            display: flex; align-items: center; justify-content: center;
        }
        .score-bars { flex: 1; display: flex; flex-direction: column; gap: 5px; }
        .score-bar-row {
            display: flex;
            align-items: center;
            gap: 8px;
        }
        .score-bar-label { font-size: 10px; color: var(--muted); width: 52px; flex-shrink: 0; }
        .score-bar-track {
            flex: 1;
            height: 5px;
            background: rgba(255,255,255,0.06);
            border-radius: 3px;
            overflow: hidden;
        }
        .score-bar-fill {
            height: 100%;
            border-radius: 3px;
            transition: width 0.6s ease;
        }
        .score-bar-val { font-size: 10px; color: var(--muted); width: 26px; text-align: right; flex-shrink: 0; }

        /* Mini metrics */
        .metrics-grid {
            display: grid;
            grid-template-columns: 1fr 1fr 1fr;
            gap: 8px;
            margin-bottom: 14px;
        }
        .metric-item {
            background: var(--bg3);
            border-radius: 8px;
            padding: 8px 10px;
        }
        .metric-label { font-size: 10px; color: var(--muted); margin-bottom: 3px; }
        .metric-value { font-size: 13px; font-weight: 700; color: var(--text); }
        .metric-na { color: var(--muted); font-weight: 400; }

        /* Reasons */
        .reasons-list { display: flex; flex-direction: column; gap: 4px; }
        .reason-item {
            font-size: 11px;
            color: var(--muted);
            padding: 4px 8px;
            border-radius: 5px;
            background: var(--bg3);
            line-height: 1.4;
        }
        .reason-item.pos { color: #86efac; }
        .reason-item.neg { color: #fca5a5; }

        /* Rank badge */
        .rank-badge {
            position: absolute;
            top: 16px; right: 16px;
            width: 28px; height: 28px;
            background: var(--bg3);
            border: 1px solid var(--border);
            border-radius: 50%;
            display: flex; align-items: center; justify-content: center;
            font-size: 11px; font-weight: 700;
            color: var(--muted);
        }

        /* Warnings */
        .warnings-block {
            margin-top: 10px;
            padding: 8px 10px;
            background: rgba(239,68,68,0.08);
            border: 1px solid rgba(239,68,68,0.2);
            border-radius: 8px;
        }
        .warnings-block .w-item { font-size: 11px; color: #fca5a5; }

        /* ── EMPTY STATE ── */
        .empty-state {
            grid-column: 1 / -1;
            text-align: center;
            padding: 80px 40px;
            background: var(--bg2);
            border: 1px solid var(--border);
            border-radius: 20px;
        }
        .empty-state h3 { font-size: 22px; margin-bottom: 12px; color: var(--text); }
        .empty-state p { color: var(--muted); font-size: 14px; line-height: 1.7; }

        /* Hidden */
        .hidden { display: none !important; }

        /* Responsive */
        @media (max-width: 600px) {
            .main { padding: 20px 16px; }
            .header { padding: 0 16px; }
        }
    </style>
</head>
<body>

<div class="header">
    <div class="header-inner">
        <div class="logo">
            <div class="logo-icon">📈</div>
            <div class="logo-text">
                <h1>Long-Term Investment Analyzer</h1>
                <p>Buffett · Graham · Lynch · Fisher · Greenblatt · Templeton</p>
            </div>
        </div>
        <div class="header-controls">
            <span class="last-update">마지막 분석: {{ last_update }}</span>
            <form action="/" method="get" style="margin:0;">
                <input type="date" name="date" class="date-input"
                    onchange="this.form.submit()"
                    value="{{ selected_date }}"
                    {% if available_dates %}max="{{ available_dates[0] }}"{% endif %}>
            </form>
            {% if is_historical %}
                <a href="/" class="btn btn-ghost">실시간 보기</a>
            {% endif %}
            {% if not is_historical %}
                {% if is_analyzing %}
                    <span class="btn btn-disabled">
                        <span class="spinner"></span> 분석 중... (약 10분)
                    </span>
                {% else %}
                    <a href="/refresh" class="btn btn-primary" id="analyze-btn">
                        ⚡ 분석 실행
                    </a>
                {% endif %}
            {% endif %}
        </div>
    </div>
</div>

<div class="main">

    <!-- Stats -->
    {% if data %}
    <div class="stats-row">
        <div class="stat-card">
            <div class="val">{{ data|length }}</div>
            <div class="lbl">분석 종목 수</div>
        </div>
        <div class="stat-card">
            <div class="val" style="-webkit-text-fill-color: #22c55e;">{{ data|selectattr('signal','contains','강력')|list|length }}</div>
            <div class="lbl">🟢 강력 매수</div>
        </div>
        <div class="stat-card">
            <div class="val" style="-webkit-text-fill-color: #06b6d4;">{{ data|selectattr('signal','contains','🟩')|list|length }}</div>
            <div class="lbl">🟩 매수</div>
        </div>
        <div class="stat-card">
            <div class="val" style="-webkit-text-fill-color: #eab308;">{{ data|selectattr('signal','contains','관망')|list|length }}</div>
            <div class="lbl">🟡 관망</div>
        </div>
        <div class="stat-card">
            <div class="val" style="-webkit-text-fill-color: #ef4444;">{{ data|selectattr('signal','contains','매도')|list|length }}</div>
            <div class="lbl">🔴 매도/고려</div>
        </div>
        {% if analyzed_at %}
        <div class="stat-card">
            <div class="val" style="font-size:13px; -webkit-text-fill-color: #94a3b8;">{{ analyzed_at }}</div>
            <div class="lbl">분석 시각</div>
        </div>
        {% endif %}
    </div>
    {% endif %}

    <!-- Filters -->
    <div class="filter-row">
        <button class="filter-btn active" data-signal="all" onclick="filterCards('all', this)">전체</button>
        <button class="filter-btn" data-signal="강력" onclick="filterCards('강력', this)">🟢 강력 매수</button>
        <button class="filter-btn" data-signal="매수" onclick="filterCards('매수', this)">🟩 매수</button>
        <button class="filter-btn" data-signal="관망" onclick="filterCards('관망', this)">🟡 관망</button>
        <button class="filter-btn" data-signal="고려" onclick="filterCards('고려', this)">🟠 매도 고려</button>
        <button class="filter-btn" data-signal="매도" onclick="filterCards('매도', this)">🔴 매도</button>
        <input type="text" class="search-input" placeholder="🔍 종목 검색 (AAPL, 애플...)" oninput="searchCards(this.value)" id="search-input">
    </div>

    <!-- Cards -->
    <div class="stock-grid" id="card-grid">
        {% for item in data %}
        {% set sig_key = '강력' if '강력' in item.signal else ('매수' if '🟩' in item.signal else ('관망' if '관망' in item.signal else ('고려' if '고려' in item.signal else '매도'))) %}
        {% set sc = item.total_score %}
        {% set ring_color = '#22c55e' if sc >= 72 else ('#06b6d4' if sc >= 58 else ('#eab308' if sc >= 45 else ('#f97316' if sc >= 30 else '#ef4444'))) %}
        <div class="stock-card signal-{{ sig_key }}"
             data-signal="{{ sig_key }}"
             data-ticker="{{ item.ticker }}"
             data-name="{{ item.name }}">

            <div class="rank-badge">{{ loop.index }}</div>

            <!-- Head -->
            <div class="card-head">
                <div>
                    <div class="card-ticker">{{ item.ticker }}</div>
                    <div class="card-name">{{ item.name }}</div>
                </div>
                <div class="card-price">
                    <div class="price">{{ "%.2f"|format(item.price) }}</div>
                    <div class="currency">{{ item.currency }}</div>
                </div>
            </div>

            <!-- Signal -->
            <div class="signal-badge sig-{{ sig_key }}">{{ item.signal }}</div>

            <!-- Score ring + bars -->
            <div class="score-row">
                <div class="score-ring" style="background: conic-gradient({{ ring_color }} {{ (sc/100*360)|int }}deg, rgba(255,255,255,0.06) 0deg);">
                    <div class="score-ring-inner">
                        <span style="font-size:13px; font-weight:800; color:{{ ring_color }};">{{ sc|round(0)|int }}</span>
                    </div>
                </div>
                <div class="score-bars">
                    {% set bars = [
                        ('버핏', item.score_buffett),
                        ('그레이엄', item.score_graham),
                        ('린치', item.score_lynch),
                        ('피셔', item.score_fisher),
                        ('그린블라트', item.score_greenblatt),
                        ('템플턴', item.score_templeton),
                    ] %}
                    {% for lbl, val in bars %}
                    {% set bc = '#22c55e' if val >= 70 else ('#eab308' if val >= 45 else '#ef4444') %}
                    <div class="score-bar-row">
                        <span class="score-bar-label">{{ lbl }}</span>
                        <div class="score-bar-track">
                            <div class="score-bar-fill" style="width:{{ val }}%; background:{{ bc }};"></div>
                        </div>
                        <span class="score-bar-val" style="color:{{ bc }};">{{ val|round(0)|int }}</span>
                    </div>
                    {% endfor %}
                </div>
            </div>

            <!-- Key Metrics -->
            <div class="metrics-grid">
                <div class="metric-item">
                    <div class="metric-label">P/E</div>
                    <div class="metric-value {% if item.pe_ratio is none %}metric-na{% endif %}">
                        {% if item.pe_ratio %}{{ "%.1f"|format(item.pe_ratio) }}{% else %}N/A{% endif %}
                    </div>
                </div>
                <div class="metric-item">
                    <div class="metric-label">P/B</div>
                    <div class="metric-value {% if item.pb_ratio is none %}metric-na{% endif %}">
                        {% if item.pb_ratio %}{{ "%.2f"|format(item.pb_ratio) }}{% else %}N/A{% endif %}
                    </div>
                </div>
                <div class="metric-item">
                    <div class="metric-label">ROE</div>
                    <div class="metric-value {% if item.roe is none %}metric-na{% endif %}">
                        {% if item.roe %}{{ "%.1f"|format(item.roe) }}%{% else %}N/A{% endif %}
                    </div>
                </div>
                <div class="metric-item">
                    <div class="metric-label">순이익률</div>
                    <div class="metric-value {% if item.net_margin is none %}metric-na{% endif %}">
                        {% if item.net_margin %}{{ "%.1f"|format(item.net_margin) }}%{% else %}N/A{% endif %}
                    </div>
                </div>
                <div class="metric-item">
                    <div class="metric-label">RSI</div>
                    <div class="metric-value {% if item.rsi is none %}metric-na{% endif %}
                        {% if item.rsi and item.rsi < 30 %}rsi-low{% elif item.rsi and item.rsi > 70 %}rsi-high{% endif %}">
                        {% if item.rsi %}{{ "%.0f"|format(item.rsi) }}{% else %}N/A{% endif %}
                    </div>
                </div>
                <div class="metric-item">
                    <div class="metric-label">PEG</div>
                    <div class="metric-value {% if item.peg_ratio is none %}metric-na{% endif %}">
                        {% if item.peg_ratio %}{{ "%.2f"|format(item.peg_ratio) }}{% else %}N/A{% endif %}
                    </div>
                </div>
            </div>

            <!-- Top reasons -->
            {% set pos_reasons = [] %}
            {% set neg_reasons = [] %}
            {% for r in item.signal_reason %}
                {% if r.startswith('✅') %}{% if pos_reasons.append(r) %}{% endif %}{% endif %}
                {% if r.startswith('❌') %}{% if neg_reasons.append(r) %}{% endif %}{% endif %}
            {% endfor %}
            {% if pos_reasons or neg_reasons %}
            <div class="reasons-list">
                {% for r in pos_reasons[:3] %}
                <div class="reason-item pos">{{ r }}</div>
                {% endfor %}
                {% for r in neg_reasons[:2] %}
                <div class="reason-item neg">{{ r }}</div>
                {% endfor %}
            </div>
            {% endif %}

            <!-- Warnings -->
            {% if item.warnings %}
            <div class="warnings-block">
                {% for w in item.warnings %}
                <div class="w-item">{{ w }}</div>
                {% endfor %}
            </div>
            {% endif %}
        </div>
        {% else %}
        <div class="empty-state">
            {% if is_analyzing %}
                <h3>⏳ S&P 500 전 종목 분석 중...</h3>
                <p>약 500개 종목을 분석하고 있습니다.<br>완료까지 약 5~10분 소요됩니다.<br>이 페이지는 10초마다 자동 새로고침됩니다.</p>
            {% else %}
                <h3>📊 분석 결과 없음</h3>
                <p>{% if is_historical %}선택한 날짜의 분석 데이터가 없습니다.{% else %}우측 상단 <strong>⚡ 분석 실행</strong> 버튼을 눌러주세요.{% endif %}</p>
            {% endif %}
        </div>
        {% endfor %}
    </div>

    <div style="text-align:center; margin-top: 40px; color: var(--muted); font-size: 12px; line-height: 1.8;">
        ※ 본 서비스는 공개 재무 데이터 기반 참고용이며, 투자 권유가 아닙니다.<br>
        ※ Powered by yfinance · Buffett · Graham · Lynch · Fisher · Greenblatt · Templeton
    </div>
</div>

{% if is_analyzing and not is_historical %}
<script>setTimeout(() => location.reload(), 10000);</script>
{% endif %}

<script>
function filterCards(sig, btn) {
    document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');

    const searchVal = (document.getElementById('search-input').value || '').toLowerCase();
    document.querySelectorAll('.stock-card').forEach(card => {
        const matchSig = sig === 'all' || card.dataset.signal === sig;
        const matchSearch = !searchVal ||
            card.dataset.ticker.toLowerCase().includes(searchVal) ||
            card.dataset.name.toLowerCase().includes(searchVal);
        card.classList.toggle('hidden', !(matchSig && matchSearch));
    });
}

function searchCards(val) {
    const activeSig = document.querySelector('.filter-btn.active')?.dataset.signal || 'all';
    const lv = val.toLowerCase();
    document.querySelectorAll('.stock-card').forEach(card => {
        const matchSig = activeSig === 'all' || card.dataset.signal === activeSig;
        const matchSearch = !lv ||
            card.dataset.ticker.toLowerCase().includes(lv) ||
            card.dataset.name.toLowerCase().includes(lv);
        card.classList.toggle('hidden', !(matchSig && matchSearch));
    });
}

// Animate bars on load
window.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('.score-bar-fill').forEach(bar => {
        const w = bar.style.width;
        bar.style.width = '0';
        setTimeout(() => { bar.style.width = w; }, 100);
    });
});
</script>
</body>
</html>
"""


def run_analysis_background():
    try:
        save_data = analyze()
        app.config['CACHED_DATA'] = save_data
        app.config['LAST_UPDATE'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    except Exception as e:
        print(f"Background analysis error: {e}")
    finally:
        app.config['IS_ANALYZING'] = False


@app.route('/')
def index():
    selected_date = request.args.get('date', '')
    available_dates = get_available_dates()

    data = []
    analyzed_at = None
    is_historical = False

    if selected_date and selected_date in available_dates:
        is_historical = True
        hist = get_history_data(selected_date)
        last_update = f"{selected_date} 과거 데이터"
        if isinstance(hist, dict):
            data = hist.get('results', [])
            analyzed_at = hist.get('analyzed_at')
    else:
        cached = app.config['CACHED_DATA']
        if isinstance(cached, dict):
            data = cached.get('results', [])
            analyzed_at = cached.get('analyzed_at')
        last_update = app.config['LAST_UPDATE']

    return render_template_string(
        HTML_TEMPLATE,
        data=data,
        analyzed_at=analyzed_at,
        last_update=last_update,
        is_analyzing=app.config['IS_ANALYZING'],
        available_dates=available_dates,
        selected_date=selected_date,
        is_historical=is_historical,
    )


@app.route('/analyze')
@app.route('/refresh')
def refresh():
    if not app.config['IS_ANALYZING']:
        app.config['IS_ANALYZING'] = True
        t = threading.Thread(target=run_analysis_background)
        t.start()
    return redirect(url_for('index'))


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
