import os
import glob
import json
import threading
import re
from flask import Flask, render_template_string, redirect, url_for, request, jsonify
from datetime import datetime
from bot import analyze

app = Flask(__name__)

# ─── 전역 상수 및 유틸리티 ────────────────────────────────────
ENTRY_EMOJI_MAP = {'🟢': 'green', '⏳': 'wait', '❌': 'stop'}

def preprocess_data(data_raw):
    """데이터 가공(이모지 맵핑 및 통계) - 공용 함수"""
    results = []
    g_cnt = 0
    w_cnt = 0
    for item in data_raw:
        p = dict(item)  # 얕은 복사로 충분
        entry_str = p.get('entry', '')
        p['entry_key'] = 'unknown'
        
        for emoji, key in ENTRY_EMOJI_MAP.items():
            if emoji in entry_str:
                p['entry_key'] = key
                if emoji == '🟢': g_cnt += 1
                elif emoji == '⏳': w_cnt += 1
                break
        results.append(p)
    return results, g_cnt, w_cnt

# ─── 상태 관리 클래스 ──────────────────────────────────────────
class AppState:
    def __init__(self):
        # 가본 상태 데이터
        self.processed_results = []
        self.green_count = 0
        self.wait_count = 0
        self.last_update = "분석된 적 없음"
        self.analyzed_at = "-"
        
        # 런타임 상태
        self.is_analyzing = False
        self.last_error = None
        
        # 동기화 객체
        self.lock = threading.RLock()

    def update_results(self, data_dict):
        """분석 성골 시 결과 데이터 원자적 업데이트"""
        with self.lock:
            results_raw = data_dict.get('results', [])
            processed, g, w = preprocess_data(results_raw)
            
            self.processed_results = processed
            self.green_count = g
            self.wait_count = w
            
            self.last_update = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            self.analyzed_at = results_raw[0].get('analyzed_at', '-') if results_raw else "-"
            self.last_error = None

    def start_analyzing(self):
        """분석 시작 전 중복 실행 방지 및 에러 초기화"""
        with self.lock:
            if self.is_analyzing:
                return False
            self.is_analyzing = True
            self.last_error = None # 에러 초기화 추가
            return True

    def set_error(self, error_msg):
        with self.lock:
            self.last_error = error_msg

    def set_analyzing(self, status):
        with self.lock:
            self.is_analyzing = status

    def get_snapshot(self):
        """현재 상태의 불변 스냅샷 반환"""
        with self.lock:
            return {
                'data': list(self.processed_results),  # 리스트 참조 복사
                'green_count': self.green_count,
                'wait_count': self.wait_count,
                'last_update': self.last_update,
                'analyzed_at': self.analyzed_at,
                'is_analyzing': self.is_analyzing,
                'last_error': self.last_error
            }

state = AppState()

# ─── 헬퍼 함수 ────────────────────────────────────────────────

def get_available_dates():
    history_dir = "history"
    if not os.path.exists(history_dir):
        return []
    files = glob.glob(os.path.join(history_dir, "*.json"))
    return sorted([os.path.basename(f).replace('.json', '') for f in files], reverse=True)


def get_history_data(date_str):
    if not re.match(r'^\d{4}-\d{2}-\d{2}$', date_str):
        return None
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
    <title>Stock-Bot · Momentum & Technical Dashboard</title>
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
        }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            background: var(--bg);
            color: var(--text);
            font-family: 'Inter', sans-serif;
            min-height: 100vh;
        }

        /* HEADER */
        .header {
            background: linear-gradient(135deg, #0e0f1c 0%, #14162a 100%);
            border-bottom: 1px solid var(--border);
            padding: 16px 32px;
            position: sticky; top: 0; z-index: 100;
            backdrop-filter: blur(20px);
        }
        .header-inner {
            max-width: 1600px; margin: 0 auto;
            display: flex; align-items: center; justify-content: space-between; gap: 20px;
        }
        .logo { display: flex; align-items: center; gap: 12px; }
        .logo-icon {
            width: 40px; height: 40px;
            background: linear-gradient(135deg, var(--accent), var(--accent2));
            border-radius: 10px;
            display: flex; align-items: center; justify-content: center; font-size: 20px;
        }
        .logo-text h1 {
            font-size: 18px; font-weight: 700;
            background: linear-gradient(135deg, #a5b4fc, #e879f9);
            -webkit-background-clip: text; -webkit-text-fill-color: transparent;
        }
        .header-controls { display: flex; align-items: center; gap: 12px; }
        .last-update { font-size: 12px; color: var(--muted); }

        /* BUTTONS */
        .btn {
            padding: 9px 18px; border-radius: 8px; font-size: 13px; font-weight: 600;
            cursor: pointer; border: none; text-decoration: none; transition: all 0.2s;
        }
        .btn-primary { background: linear-gradient(135deg, var(--accent), var(--accent2)); color: #fff; }
        .btn-ghost { background: var(--bg3); color: var(--muted); border: 1px solid var(--border); }

        /* MAIN */
        .main { max-width: 1600px; margin: 0 auto; padding: 28px 32px; }

        /* ERROR MESSAGE */
        .error-banner {
            background: rgba(239, 68, 68, 0.1);
            border: 1px solid var(--red);
            color: var(--red);
            padding: 12px 20px;
            border-radius: 8px;
            margin-bottom: 24px;
            font-size: 14px;
            display: flex; align-items: center; gap: 10px;
        }

        /* STATS */
        .stats-row {
            display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
            gap: 14px; margin-bottom: 24px;
        }
        .stat-card {
            background: var(--bg2); border: 1px solid var(--border); border-radius: 12px;
            padding: 16px 20px; text-align: center;
        }
        .stat-card .val { font-size: 26px; font-weight: 800; color: var(--accent); }
        .stat-card .lbl { font-size: 11px; color: var(--muted); margin-top: 4px; }

        /* GRID */
        .stock-grid {
            display: grid; grid-template-columns: repeat(auto-fill, minmax(380px, 1fr)); gap: 20px;
        }
        .stock-card {
            background: var(--bg2); border: 1px solid var(--border); border-radius: 16px;
            padding: 22px; position: relative; transition: all 0.2s;
        }
        .stock-card:hover { transform: translateY(-3px); border-color: var(--accent); box-shadow: 0 8px 30px rgba(99,102,241,0.1); }
        
        .card-head { display: flex; justify-content: space-between; margin-bottom: 16px; }
        .card-ticker { font-size: 24px; font-weight: 800; color: #fff; }
        .card-price { text-align: right; }
        .card-price .price { font-size: 20px; font-weight: 700; color: var(--text); }
        .card-price .change { font-size: 13px; font-weight: 600; }
        .change.pos { color: var(--green); }
        .change.neg { color: var(--red); }
        .change.zero { color: var(--muted); }

        .entry-badge {
            display: inline-block; padding: 5px 12px; border-radius: 20px;
            font-size: 12px; font-weight: 700; margin-bottom: 16px;
        }
        .entry-green { background: rgba(34,197,94,0.1); color: var(--green); border: 1px solid rgba(34,197,94,0.2); }
        .entry-wait  { background: rgba(234,179,8,0.1);  color: var(--yellow); border: 1px solid rgba(234,179,8,0.2); }
        .entry-stop  { background: rgba(239,68,68,0.1);  color: var(--red);    border: 1px solid rgba(239,68,68,0.2); }
        .entry-unknown { background: rgba(255,255,255,0.05); color: var(--muted); border: 1px solid var(--border); }

        .metrics-grid {
            display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; margin-bottom: 16px;
        }
        .metric {
            background: var(--bg3); border-radius: 8px; padding: 10px; text-align: center;
        }
        .metric-lbl { font-size: 10px; color: var(--muted); margin-bottom: 4px; }
        .metric-val { font-size: 14px; font-weight: 700; }

        .signals { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 14px; }
        .sig-tag {
            font-size: 11px; padding: 4px 8px; background: rgba(255,255,255,0.05);
            border-radius: 6px; color: var(--muted); border: 1px solid var(--border);
        }

        .targets {
            margin-top: 16px; padding-top: 16px; border-top: 1px solid var(--border);
            display: flex; justify-content: space-between; font-size: 12px;
        }
        .target-box { text-align: center; flex: 1; }
        .target-box b { display: block; color: var(--accent); margin-top: 2px; }
        .sl-box b { color: var(--red); }

        .score-row { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }
        .score-val { font-size: 18px; font-weight: 800; color: #fff; }
        .score-bar { flex: 1; height: 6px; background: rgba(255,255,255,0.1); border-radius: 3px; margin: 0 15px; overflow: hidden; }
        .score-fill { height: 100%; border-radius: 3px; background: var(--accent); }

        .empty { text-align: center; padding: 100px; color: var(--muted); grid-column: 1/-1; }
    </style>
</head>
<body>

<div class="header">
    <div class="header-inner">
        <div class="logo">
            <div class="logo-icon">📈</div>
            <div class="logo-text">
                <h1>Momentum Master</h1>
                <p>S&P 500 Daily Technical Tracker</p>
            </div>
        </div>
        <div class="header-controls">
            <span class="last-update">업데이트: {{ last_update }}</span>
            {% if is_analyzing %}
                <span class="btn btn-ghost" id="analyzing-status">⏳ 분석 중...</span>
            {% else %}
                <form action="/refresh" method="POST" style="margin: 0;">
                    <button type="submit" class="btn btn-primary">⚡ 분석 실행</button>
                </form>
            {% endif %}
        </div>
    </div>
</div>

<div class="main">
    {% if last_error %}
    <div class="error-banner">
        <span>⚠️ 분석 오류: {{ last_error }}</span>
    </div>
    {% endif %}

    {% if data %}
    <div class="stats-row">
        <div class="stat-card">
            <div class="val">{{ data|length }}</div>
            <div class="lbl">분석 종목</div>
        </div>
        <div class="stat-card">
            <div class="val" style="color:var(--green)">{{ green_count }}</div>
            <div class="lbl">🟢 진입 가능</div>
        </div>
        <div class="stat-card">
            <div class="val" style="color:var(--yellow)">{{ wait_count }}</div>
            <div class="lbl">⏳ 대기</div>
        </div>
        <div class="stat-card">
            <div class="val" style="color:var(--muted)">{{ analyzed_at }}</div>
            <div class="lbl">마지막 분석</div>
        </div>
    </div>
    {% endif %}

    <div class="stock-grid">
        {% for item in data %}
        <div class="stock-card">
            <div class="card-head">
                <div>
                    <div class="card-ticker">{{ item['ticker'] }}</div>
                    <div style="font-size:12px; color:var(--muted)">{{ item['company'] }}</div>
                </div>
                <div class="card-price">
                    <div class="price">${{ "%.2f"|format(item['price']) }}</div>
                    <div class="change {{ 'pos' if item['change'] > 0 else ('neg' if item['change'] < 0 else 'zero') }}">
                        {{ "%+.2f"|format(item['change']) }}%
                    </div>
                </div>
            </div>

            <div class="entry-badge entry-{{ item['entry_key'] }}">{{ item['entry'] }}</div>

            <div class="score-row">
                <div class="score-val">{{ item['score'] }}</div>
                <div class="score-bar">
                    <div class="score-fill" style="width: {{ [item['score'], 100]|min }}%"></div>
                </div>
                <div style="font-size:12px; color:var(--muted)">PTS</div>
            </div>

            <div class="metrics-grid">
                <div class="metric">
                    <div class="metric-lbl">RSI(14)</div>
                    <div class="metric-val" style="color: {{ 'var(--red)' if item['rsi'] > 70 else ('var(--green)' if item['rsi'] < 30 else 'inherit') }}">{{ "%.1f"|format(item['rsi']) }}</div>
                </div>
                <div class="metric">
                    <div class="metric-lbl">MACD Hist</div>
                    <div class="metric-val">{{ "%.3f"|format(item['macd_histogram']) }}</div>
                </div>
                <div class="metric">
                    <div class="metric-lbl">MA 20</div>
                    <div class="metric-val">{{ 'UP 🟢' if item['price'] > item['ma20'] else 'DOWN 🔴' }}</div>
                </div>
                <div class="metric">
                    <div class="metric-lbl">V-Ratio</div>
                    <div class="metric-val">{{ "%.0f"|format(item['vol_ratio']) }}%</div>
                </div>
                <div class="metric">
                    <div class="metric-lbl">Cloud</div>
                    <div class="metric-val">{{ 'ABOVE 🟢' if item['is_above_cloud'] else ('BELOW 🔴' if item['is_below_cloud'] else 'INSIDE 🟡') }}</div>
                </div>
                <div class="metric">
                    <div class="metric-lbl">MA Trend</div>
                    <div class="metric-val" style="font-size:11px">{{ item['ma_trend'] }}</div>
                </div>
            </div>

            <div class="signals">
                {% for sig in item['signals'] %}
                    <span class="sig-tag">{{ sig }}</span>
                {% endfor %}
            </div>

            <div class="targets">
                <div class="target-box">T1 <b>${{ "%.2f"|format(item['target1']) }}</b></div>
                <div class="target-box">T2 <b>${{ "%.2f"|format(item['target2']) }}</b></div>
                <div class="target-box sl-box">SL <b>${{ "%.2f"|format(item['stop_loss']) }}</b></div>
            </div>
        </div>
        {% else %}
        <div class="empty">
            <h3>데이터가 없습니다.</h3>
            <p>분석 실행 버튼을 눌러 결과데이터를 생성해주세요.</p>
        </div>
        {% endfor %}
    </div>
</div>

<script>
async function pollStatus() {
    try {
        const res = await fetch('/status');
        const d = await res.json();
        if (!d.is_analyzing) {
            location.reload();
        } else {
            setTimeout(pollStatus, 3000);
        }
    } catch (e) {
        setTimeout(pollStatus, 5000);
    }
}

{% if is_analyzing %}
pollStatus();
{% endif %}
</script>
</body>
</html>
"""


def run_analysis_background():
    try:
        results_data = analyze()
        # 데이터 유효성 검증 추가
        if not results_data or not isinstance(results_data, dict):
            raise ValueError(f"분석기가 유효하지 않은 결과를 반환했습니다.")
        if not results_data.get('results'):
            raise ValueError("수집된 분석 결과가 없습니다.")
            
        state.update_results(results_data)
    except Exception as e:
        print(f"Background analysis error: {e}")
        state.set_error(str(e))
        # NOTE: 분석 실패 시 이전 데이터를 그대로 유지하며 사용자에게 에러만 알립니다.
    finally:
        state.set_analyzing(False)


@app.route('/')
def index():
    snapshot = state.get_snapshot()
    
    # 캐시에 데이터가 없고 분석 중이 아닐 때만 히스토리 로드
    if not snapshot['data'] and not snapshot['is_analyzing']:
        available = get_available_dates()
        if available:
            hist = get_history_data(available[0])
            if hist:
                # 락 경쟁 방지를 위해 캐시(state)를 수정하지 않고 로컬 변수로만 처리
                results_raw = hist.get('results', [])
                processed, g, w = preprocess_data(results_raw)
                snapshot.update({
                    'data': processed,
                    'green_count': g,
                    'wait_count': w,
                    'last_update': hist.get('analyzed_at', available[0]),
                    'analyzed_at': results_raw[0].get('analyzed_at', '-') if results_raw else "-"
                })

    return render_template_string(
        HTML_TEMPLATE,
        **snapshot
    )

@app.route('/status')
def status():
    with state.lock:
        return jsonify({
            'is_analyzing': state.is_analyzing,
            'last_error': state.last_error
        })

@app.route('/refresh', methods=['POST'])
def refresh():
    if state.start_analyzing():
        t = threading.Thread(target=run_analysis_background, daemon=True)
        t.start()
    return redirect(url_for('index'))


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
