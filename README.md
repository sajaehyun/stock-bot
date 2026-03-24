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
