# Momentum Master: S&P 500 Daily Technical Tracker

이 프로젝트는 Finviz에서 S&P 500 상승 종목들을 실시간으로 수집하고, 야후 파이낸스(YFinance) 데이터를 통해 정밀한 기술적 지표(RSI, MACD, Stochastic, VWAP, Ichimoku Cloud 등)를 분석하여 다크모드 대시보드와 텔레그램 리포트를 제공하는 봇입니다.

## 주요 기능
- **S&P 500 모멘텀 분석**: Finviz의 실시간 데이터를 기반으로 강한 상승세를 보이는 30개 종목을 선별하여 분석합니다.
- **기술적 지표 계산**: RSI, MACD Histogram, Stochastic %D, MA20/50/200, Ichimoku Cloud, VWAP 등을 종합하여 0~100점 사이의 점수를 산출합니다.
- **로컬 JSON 히스토리**: 분석 결과는 `history` 디렉토리에 JSON 파일로 저장되어 과거 데이터를 보존합니다.
- **실시간 웹 대시보드**: Flask 기반의 프리미엄 다크모드 UI를 통해 종목별 상세 지표와 진입 가능 여부(🟢 진입, ⏳ 대기, ❌ 회피)를 한눈에 확인 가능합니다.
- **텔레그램 알림**: 분석이 완료되면 상위 10개 종목에 대한 요약 리포트를 설정된 텔레그램 채널로 즉시 전송합니다.

## 설정 방법

### 1. 환경 변수 설정
프로젝트 루트 폴더에 `.env` 파일을 생성하고 아래 내용을 입력합니다.
배포 시에도 동일한 환경 변수를 Repository Secrets 등에 추가해야 합니다.

```env
TELEGRAM_TOKEN="당신의_텔레그램_봇_토큰"
CHAT_ID="메시지를_받을_텔레그램_채팅_아이디"
```

### 2. 패키지 설치
```bash
pip install -r requirements.txt
```

### 3. 실행 방법 (로컬)
**분석 단독 실행**
```bash
python bot.py
```

**웹 대시보드 서버 실행**
```bash
python app.py
```
접속 URL: `http://localhost:5000` (서버 실행 후 '분석 실행' 버튼 클릭 가능)

## 배포 가이드
- **웹 대시보드 (Render/Heroku 등)**:
  - Build Command: `pip install -r requirements.txt`
  - Start Command: `gunicorn app:app` (또는 Procfile 사용)
  - 환경 변수: `TELEGRAM_TOKEN`, `CHAT_ID` 설정 필수.

---

# CHANGELOG

## 2026-03-26

### longterm.py
- `analyze_earnings()` 추가 (어닝 서프라이즈, 연속비트, 가이던스, 어닝후 반응)
- `analyze_sector_comparison()`, `analyze_buyback()`, `analyze_options()` 추가
- `sector` → `sector_comp` 변수명 충돌 수정
- `total_score`, `all_signals`에 4개 새 점수 통합
- 텔레그램 메시지에 어닝/섹터/바이백/옵션 점수 포함

### bot.py
- `_quick_earnings_check(ticker)` 추가 — 어닝 임박, 서프라이즈, 어닝후 반응
- `_quick_options_check(ticker)` 추가 — 풋/콜 비율
- `compute_score_and_status`, `compute_presignal_score`에 어닝/옵션 점수 반영
- `analyze_ticker`, `analyze_ticker_presignal` return에 earnings_near, days_to_earnings, last_surprise_pct, put_call_ratio 추가

### dashboard.html
- 모멘텀/선행 탭에 어닝·서프라이즈·P/C 메트릭 UI 추가
- Jinja2 `.get()` 호환성 수정 6곳
- 분석 버튼 로딩 표시 + 403 에러 처리 JS 추가

### 미해결
- [ ] Render에 `FLASK_SECRET_KEY` 환경변수 추가 (403 원인)
- [ ] 환경변수 추가 후 모멘텀 실행 테스트
- [ ] Finnhub API 키 권한 확인 (403 다수 발생)
