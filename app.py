import os
import glob
import json
from flask import Flask, render_template_string, redirect, url_for, request
from datetime import datetime
import threading
from bot import analyze

app = Flask(__name__)

app = Flask(__name__)

# Global cache
app.config['CACHED_DATA'] = {}
app.config['LAST_UPDATE'] = "업데이트 된 적 없음"
app.config['IS_ANALYZING'] = False

def get_available_dates():
    history_dir = "history"
    if not os.path.exists(history_dir):
        return []
    files = glob.glob(os.path.join(history_dir, "*.json"))
    dates = [os.path.basename(f).replace('.json', '') for f in files]
    dates.sort(reverse=True)
    return dates

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
    <title>SOXL 봇 대시보드</title>
    <style>
        body {
            background-color: #121212;
            color: #e0e0e0;
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            margin: 0;
            padding: 20px;
        }
        .container {
            max-width: 1200px;
            margin: auto;
        }
        h1 {
            color: #ffffff;
            text-align: center;
        }
        .header-info {
            display: flex;
            justify-content: space-between;
            align-items: center;
            background: #1e1e1e;
            padding: 15px 20px;
            border-radius: 8px;
            margin-bottom: 20px;
            box-shadow: 0 4px 6px rgba(0,0,0,0.3);
            flex-wrap: wrap;
            gap: 15px;
        }
        .btn {
            background-color: #007bff;
            color: white;
            border: none;
            padding: 10px 20px;
            border-radius: 5px;
            cursor: pointer;
            font-size: 15px;
            font-weight: bold;
            text-decoration: none;
            transition: background 0.3s;
            display: inline-flex;
            align-items: center;
            gap: 8px;
        }
        .btn:hover {
            background-color: #0056b3;
        }
        .btn.disabled {
            background-color: #555;
            cursor: not-allowed;
            pointer-events: none;
        }
        select.date-picker {
            padding: 8px 12px;
            border-radius: 5px;
            background: #333;
            color: white;
            border: 1px solid #555;
            font-size: 15px;
            cursor: pointer;
        }
        
        /* Loader */
        .loader {
            border: 3px solid #f3f3f3;
            border-top: 3px solid #ffffff;
            border-radius: 50%;
            width: 16px;
            height: 16px;
            animation: spin 1s linear infinite;
        }
        @keyframes spin {
            0% { transform: rotate(0deg); }
            100% { transform: rotate(360deg); }
        }
        
        .stock-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(350px, 1fr));
            gap: 20px;
        }
        .stock-card {
            background: #1e1e1e;
            border-radius: 8px;
            padding: 20px;
            box-shadow: 0 4px 6px rgba(0,0,0,0.3);
            border-top: 4px solid #007bff;
            display: flex;
            flex-direction: column;
        }
        .highlight-card {
            border: 2px solid #4CAF50 !important;
            background: #1a2e1d !important;
            box-shadow: 0 0 15px rgba(76, 175, 80, 0.4);
            order: -1;
        }
        .stock-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 15px;
            border-bottom: 1px solid #333;
            padding-bottom: 10px;
        }
        .stock-title {
            font-size: 24px;
            font-weight: bold;
            color: #fff;
        }
        .stock-rank {
            background: #007bff;
            color: #fff;
            padding: 4px 10px;
            border-radius: 20px;
            font-size: 14px;
            font-weight: bold;
        }
        .status-box {
            background: #2a2a2a;
            padding: 10px 15px;
            border-radius: 6px;
            margin-bottom: 15px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .info-row {
            display: flex;
            justify-content: space-between;
            margin-bottom: 8px;
            font-size: 15px;
        }
        .info-label {
            color: #aaa;
        }
        .info-value {
            font-weight: bold;
            color: #fff;
        }
        .up { color: #4CAF50; }
        .down { color: #F44336; }
        .neutral { color: #FFEB3B; }
        
        .section-title {
            margin-top: 15px;
            margin-bottom: 10px;
            font-size: 14px;
            color: #aaa;
            border-bottom: 1px solid #333;
            padding-bottom: 5px;
        }
        .signals-list {
            margin: 0;
            padding-left: 20px;
            font-size: 14px;
            color: #ddd;
        }
        .signals-list li {
            margin-bottom: 4px;
        }
        
        .empty-state {
            text-align: center;
            padding: 50px;
            background: #1e1e1e;
            border-radius: 8px;
            grid-column: 1 / -1;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>추천 종목 TOP 10</h1>
        
        <div class="header-info">
            <div>
                <strong>마지막 분석 시간:</strong> {{ last_update }}
            </div>
            
            <div style="display: flex; gap: 15px; align-items: center;">
                <form action="/" method="get" style="margin: 0; display: flex; align-items: center; gap: 10px;">
                    <input type="date" name="date" class="date-picker" onchange="this.form.submit()" value="{{ selected_date }}" {% if available_dates %}max="{{ available_dates[0] }}" min="{{ available_dates[-1] }}"{% endif %} style="padding: 8px; border-radius: 5px; background: #333; color: white; border: 1px solid #555; cursor: pointer;">
                    {% if is_historical %}
                        <a href="/" class="btn" style="background-color: #555; font-size: 13px;">실시간 분석 보기</a>
                    {% endif %}
                </form>
                
                {% if not is_historical %}
                    {% if is_analyzing %}
                        <a href="#" class="btn disabled">
                            <div class="loader"></div> 분석 중... (약 20초)
                        </a>
                    {% else %}
                        <a href="{{ url_for('refresh') }}" class="btn">분석 실행</a>
                    {% endif %}
                {% endif %}
            </div>
        </div>
        
        {% if market_summary and tomorrow_pred %}
        <div class="summary-section" style="display: flex; gap: 20px; margin-bottom: 20px; flex-wrap: wrap;">
            <!-- 증시 요약 카드 -->
            <div class="summary-card" style="flex: 1; min-width: 300px; background: #1e1e1e; padding: 20px; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.3); border-top: 4px solid #f39c12;">
                <h3 style="margin-top: 0; margin-bottom: 15px; border-bottom: 1px solid #333; padding-bottom: 10px; color: #fff;">🌐 전일 증시 동향</h3>
                <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-bottom: 15px;">
                    {% if market_summary.indices %}
                        {% for name, idx in market_summary.indices.items() %}
                        <div style="background: #2a2a2a; padding: 10px; border-radius: 5px; text-align: center;">
                            <div style="color: #aaa; font-size: 13px; margin-bottom: 5px;">{{ name }}</div>
                            <div style="font-size: 16px; font-weight: bold; {% if name != 'VIX' %}{% if idx.change > 0 %}color: #4CAF50;{% elif idx.change < 0 %}color: #F44336;{% endif %}{% else %}{% if idx.price > 20 %}color: #F44336;{% elif idx.price < 15 %}color: #4CAF50;{% endif %}{% endif %}">
                                {% if name != 'VIX' %}
                                    {{ "%+.2f"|format(idx.change) }}%
                                {% else %}
                                    {{ "%.2f"|format(idx.price) }}
                                {% endif %}
                            </div>
                        </div>
                        {% endfor %}
                    {% endif %}
                </div>
                
                <h4 style="margin: 15px 0 10px 0; color: #fff;">📊 주요 섹터 등락 (Top/Bottom 3)</h4>
                <div style="font-size: 14px; margin-bottom: 5px; background: #2a2a2a; padding: 8px; border-radius: 5px;">
                    <span style="color: #4CAF50; font-weight:bold;">🟢 상위:</span> 
                    {% for s in market_summary.top_sectors %}
                        {{ s.name }}(<span style="color:#4CAF50;">{{ "%+.1f"|format(s.change) }}%</span>){% if not loop.last %}, {% endif %}
                    {% endfor %}
                </div>
                <div style="font-size: 14px; margin-bottom: 15px; background: #2a2a2a; padding: 8px; border-radius: 5px;">
                    <span style="color: #F44336; font-weight:bold;">🔴 하위:</span> 
                    {% for s in market_summary.bottom_sectors %}
                        {{ s.name }}(<span style="color:#F44336;">{{ "%+.1f"|format(s.change) }}%</span>){% if not loop.last %}, {% endif %}
                    {% endfor %}
                </div>
                
                <h4 style="margin: 15px 0 10px 0; color: #fff;">📅 오늘 주요 뉴스 및 경제지표 (Alpha Vantage)</h4>
                <ul style="margin: 0; padding-left: 20px; font-size: 13px; color: #ddd; background: #2a2a2a; padding: 10px 10px 10px 30px; border-radius: 5px;">
                    {% for ev in market_summary.today_events %}
                        <li style="margin-bottom: 5px;">{{ ev }}</li>
                    {% endfor %}
                </ul>
            </div>
            
            <!-- AI 내일 증시 예상 카드 -->
            <div class="summary-card" style="flex: 1; min-width: 300px; background: #1e1e1e; padding: 20px; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.3); border-top: 4px solid #9c27b0;">
                <h3 style="margin-top: 0; margin-bottom: 15px; border-bottom: 1px solid #333; padding-bottom: 10px; color: #fff;">🤖 AI 내일 증시 예상</h3>
                
                <div style="display: flex; gap: 10px; margin-bottom: 15px;">
                    <div style="flex: 1; background: #2a2a2a; padding: 15px; border-radius: 5px; text-align: center;">
                        <div style="color: #4CAF50; font-size: 14px; margin-bottom: 5px;">상승 확률</div>
                        <div style="font-size: 24px; font-weight: bold; color: #4CAF50;">{{ tomorrow_pred.probs.up }}%</div>
                    </div>
                    <div style="flex: 1; background: #2a2a2a; padding: 15px; border-radius: 5px; text-align: center;">
                        <div style="color: #FFEB3B; font-size: 14px; margin-bottom: 5px;">횡보 확률</div>
                        <div style="font-size: 24px; font-weight: bold; color: #FFEB3B;">{{ tomorrow_pred.probs.flat }}%</div>
                    </div>
                    <div style="flex: 1; background: #2a2a2a; padding: 15px; border-radius: 5px; text-align: center;">
                        <div style="color: #F44336; font-size: 14px; margin-bottom: 5px;">하락 확률</div>
                        <div style="font-size: 24px; font-weight: bold; color: #F44336;">{{ tomorrow_pred.probs.down }}%</div>
                    </div>
                </div>
                
                <h4 style="margin: 15px 0 10px 0; color: #fff;">📈 방향성 근거 지표</h4>
                <div style="display: flex; justify-content: space-between; background: #2a2a2a; padding: 15px; border-radius: 5px; margin-bottom: 15px; font-size: 14px;">
                    <div style="text-align: center;"><span style="color:#aaa; display:block; font-size:12px; margin-bottom:3px;">나스닥100 RSI</span> <b>{{ "%.1f"|format(tomorrow_pred.qqq_rsi) }}</b></div>
                    <div style="text-align: center;"><span style="color:#aaa; display:block; font-size:12px; margin-bottom:3px;">나스닥100 MACD</span> <b>{{ tomorrow_pred.macd_dir }}</b></div>
                    <div style="text-align: center;"><span style="color:#aaa; display:block; font-size:12px; margin-bottom:3px;">뉴스 감성 점수</span> <b>{{ "%.2f"|format(market_summary.news_sentiment) }}</b></div>
                </div>
                
                <h4 style="margin: 15px 0 10px 0; color: #fff;">⚠️ 주요 리스크 요인</h4>
                <ul style="margin: 0; padding-left: 20px; font-size: 14px; color: #ff9800; background: #332b1a; padding: 10px 10px 10px 30px; border-radius: 5px; border-left: 3px solid #ff9800;">
                    {% for risk in tomorrow_pred.risks %}
                        <li style="margin-bottom: 5px;">{{ risk }}</li>
                    {% endfor %}
                </ul>
            </div>
        </div>
        {% endif %}

        <div class="stock-grid">
            {% for item in data %}
            <div class="stock-card {% if '🟢' in item.entry_status %}highlight-card{% endif %}">
                <div class="stock-header">
                    <div class="stock-title">{{ item.ticker }}</div>
                    <div class="stock-rank">TOP {{ loop.index }}</div>
                </div>
                
                <div class="status-box">
                    <span class="info-label" style="font-size: 14px;">진입 상태</span>
                    <span class="info-value" style="font-size: 18px;">{{ item.entry_status }}</span>
                </div>
                
                <div class="info-row">
                    <span class="info-label">종합점수</span>
                    <span class="info-value" style="color: #00bcd4; font-size: 18px;">{{ item.total_score | round(1) }} 점</span>
                </div>
                
                <div class="section-title">가격 정보</div>
                <div class="info-row">
                    <span class="info-label">현재가</span>
                    <span class="info-value">${{ "%.2f"|format(item.data.price) }}</span>
                </div>
                <div class="info-row">
                    <span class="info-label">추천 매수가</span>
                    <span class="info-value">현재가 부근 (${{ "%.2f"|format(item.buy_price) }})</span>
                </div>
                <div class="info-row">
                    <span class="info-label">목표가 1차 (+10%)</span>
                    <span class="info-value up">${{ "%.2f"|format(item.target_price_1) }}</span>
                </div>
                <div class="info-row">
                    <span class="info-label">목표가 2차 (+20%)</span>
                    <span class="info-value up">${{ "%.2f"|format(item.target_price_2) }}</span>
                </div>
                <div class="info-row">
                    <span class="info-label">손절가 (-5%)</span>
                    <span class="info-value down">${{ "%.2f"|format(item.stop_loss) }}</span>
                </div>
                <div class="info-row">
                    <span class="info-label">위험:수익비</span>
                    <span class="info-value">{{ "%.1f"|format(item.risk_reward) }}배</span>
                </div>
                
                <div class="section-title">주요 기술적 지표</div>
                <div class="info-row">
                    <span class="info-label">Ichimoku Cloud</span>
                    <span class="info-value">
                        {% if item.data.is_above_cloud %}
                            <span class="up">구름대 위 ☁️</span>
                        {% elif item.data.is_below_cloud %}
                            <span class="down">구름대 아래 ☁️</span>
                        {% else %}
                            <span class="neutral">구름대 내부 ☁️</span>
                        {% endif %}
                    </span>
                </div>
                <div class="info-row">
                    <span class="info-label">RSI (14)</span>
                    <span class="info-value {% if item.data.rsi < 30 %}up{% elif item.data.rsi > 70 %}down{% endif %}">{{ "%.1f"|format(item.data.rsi) }}</span>
                </div>
                <div class="info-row">
                    <span class="info-label">MACD</span>
                    <span class="info-value {% if item.data.macd_histogram > 0 %}up{% else %}down{% endif %}">{{ "%+.3f"|format(item.data.macd_histogram) }}</span>
                </div>
                <div class="info-row">
                    <span class="info-label">Stochastic (K)</span>
                    <span class="info-value {% if item.data.stoch_k < 20 %}up{% elif item.data.stoch_k > 80 %}down{% endif %}">{{ "%.1f"|format(item.data.stoch_k) }}</span>
                </div>
                <div class="info-row">
                    <span class="info-label">이동평균 추세</span>
                    <span class="info-value">{{ item.data.ma_trend }}</span>
                </div>
                
                <div class="section-title">세부 신호 / 추천 이유</div>
                <ul class="signals-list">
                    {% for sig in item.signals %}
                        <li>{{ sig }}</li>
                    {% endfor %}
                    {% for sig in item.squeeze_signals %}
                        <li>{{ sig }}</li>
                    {% endfor %}
                    {% if not item.signals and not item.squeeze_signals %}
                        <li>특별한 신호 없음</li>
                    {% endif %}
                </ul>
            </div>
            {% else %}
                <div class="empty-state">
                    {% if is_analyzing %}
                        <h3>데이터를 분석하고 있습니다...</h3>
                        <p>분석 완료까지 잠시만 기다려주세요.<br>이 페이지는 5초마다 자동 새로고침됩니다.</p>
                    {% else %}
                        <h3>분석된 데이터가 없습니다.</h3>
                        {% if is_historical %}
                            <p>선택하신 날짜에 저장된 리포트가 없거나 오류가 있습니다.</p>
                        {% else %}
                            <p>상단의 '분석 실행' 버튼을 눌러주세요.</p>
                        {% endif %}
                    {% endif %}
                </div>
            {% endfor %}
        </div>
    </div>
    {% if is_analyzing and not is_historical %}
    <script>
        setTimeout(function() {
            window.location.reload(1);
        }, 5000);
    </script>
    {% endif %}
</body>
</html>
"""

def run_analysis_background():
    try:
        # analyze now returns a dict mapping market_summary, tomorrow_pred, and top10
        save_data = analyze(send_telegram=True)
        app.config['CACHED_DATA'] = save_data
        app.config['LAST_UPDATE'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    except Exception as e:
        print(f"Error during background analysis: {e}")
    finally:
        app.config['IS_ANALYZING'] = False

@app.route('/')
def index():
    selected_date = request.args.get('date', '')
    available_dates = get_available_dates()
    
    market_summary = None
    tomorrow_pred = None
    display_data = []
    
    # 만약 특정 날짜가 선택되었다면 해당 데이터를 보여준다
    if selected_date and selected_date in available_dates:
        hist_data = get_history_data(selected_date)
        last_update = f"{selected_date} 과거 데이터"
        is_historical = True
        
        if isinstance(hist_data, list):
            display_data = hist_data
        elif isinstance(hist_data, dict):
            display_data = hist_data.get('top10', [])
            market_summary = hist_data.get('market_summary')
            tomorrow_pred = hist_data.get('tomorrow_pred')
            
        if display_data:
            display_data.sort(key=lambda x: ('🟢' in str(x.get('entry_status','')), x.get('total_score', 0)), reverse=True)
            
    else:
        # 실시간 모드
        is_historical = False
        
        cached_data = app.config['CACHED_DATA']
        if isinstance(cached_data, list):
            display_data = cached_data
        elif isinstance(cached_data, dict):
            display_data = cached_data.get('top10', [])
            market_summary = cached_data.get('market_summary')
            tomorrow_pred = cached_data.get('tomorrow_pred')
            
        if display_data:
            display_data.sort(key=lambda x: ('🟢' in str(x.get('entry_status','')), x.get('total_score', 0)), reverse=True)
            
        last_update = app.config['LAST_UPDATE']

    max_date = available_dates[0] if available_dates else datetime.now().strftime('%Y-%m-%d')
    return render_template_string(
        HTML_TEMPLATE, 
        data=display_data,
        market_summary=market_summary,
        tomorrow_pred=tomorrow_pred,
        last_update=last_update,
        is_analyzing=app.config['IS_ANALYZING'],
        available_dates=available_dates,
        selected_date=selected_date,
        is_historical=is_historical,
        max_date=max_date
    )

@app.route('/analyze')
@app.route('/refresh')
def refresh():
    if not app.config['IS_ANALYZING']:
        app.config['IS_ANALYZING'] = True
        thread = threading.Thread(target=run_analysis_background)
        thread.start()
    return redirect(url_for('index'))

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
