# Stock Analysis Bot & Dashboard

이 프로젝트는 야후 파이낸스(YFinance) 데이터를 활용하여 미국 주식(SOXL 관련 30종목)의 기술적 지표를 분석하고, 결과를 텔레그램으로 전송 및 다크모드 웹 대시보드에 표시하는 봇입니다.

## 주요 기능
- **자동 실행**: GitHub Actions를 통해 매일 오전 9시(KST) 자동 실행 및 결과 분석
- **Firebase Firestore 연동**: 분석된 결과를 날짜별로 Firestore 데이터베이스에 자동 저장
- **텔레그램 알림**: TOP 10 분석 결과를 요약하여 텔레그램으로 전송
- **웹 대시보드**: Render 등 플랫폼에 배포 가능, Firestore에서 과거 데이터를 달력으로 조회
- **기술적 지표 분석**: RSI, MACD, Stochastic, Bollinger Bands 등 기반 종목 점수 평가 및 숏스퀴즈 판독

## 설정 방법

### 1. 환경 변수 설정
로컬에서 실행하려면 프로젝트 루트 폴더에 `.env` 파일을 생성하고 아래 내용을 입력합니다.
또한, GitHub Actions 및 Render 등에 배포 시 동일한 환경 변수를 Repository Secrets (또는 Environment Variables)에 추가해야 합니다.

```env
TELEGRAM_TOKEN="당신의_텔레그램_봇_토큰"
CHAT_ID="메시지를_받을_텔레그램_채팅_아이디"
FIREBASE_CREDENTIALS='{"type": "service_account", "project_id": "...", ...}'
```

*(참고: `FIREBASE_CREDENTIALS`는 Firebase Console에서 발급받은 서비스 계정 키(JSON)의 전체 내용을 복사하여 넣습니다. 따옴표를 주의해주세요.)*

### 2. 패키지 설치
```bash
pip install -r requirements.txt
```

### 3. 로컬 실행
**분석 봇 실행 (수동)**
```bash
python bot.py
```

**웹 대시보드 서버 실행**
```bash
python app.py
```
접속 URL: `http://localhost:5000`

## 배포 가이드
- **데이터 분석 봇**: 코드를 GitHub에 푸시하고, Settings > Secrets and variables > Actions에 위 3가지 환경 변수를 추가하면, 매일 오전 9시에 `bot.py`가 자동 실행되어 Firebase에 저장하고 텔레그램에 전송합니다.
- **웹 대시보드**: Render(render.com) 등 앱 호스팅 서비스에 연동한 후, Build Command를 `pip install -r requirements.txt`, Start Command를 `gunicorn app:app` (또는 Procfile 사용)으로 설정합니다. 웹 대시보드를 위한 환경 변수도 동일하게 설정하세요.
