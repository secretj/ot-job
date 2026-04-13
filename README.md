# OT 채용 트래커 (서울 작업치료사 / 감각통합치료사)

로컬 Mac에서 돌리는 채용 공고 크롤러 + 카카오톡 알림 + 웹 대시보드

## 구조

```
ot-job-tracker/
├── crawler.py        # 채용 사이트 크롤링 (메인)
├── kakao_auth.py     # 카카오톡 최초 인증 (1회만)
├── kakao_notify.py   # 카카오톡 나에게 보내기
├── web_server.py     # 웹 대시보드 (Flask)
├── templates/
│   └── index.html    # 대시보드 UI
├── config.json       # 카카오 API 키 등 설정
├── jobs.db           # SQLite DB (자동 생성)
├── requirements.txt  # Python 패키지
├── start.sh          # 원클릭 실행 스크립트
└── README.md         # 이 파일
```

## 1단계: Python 확인 및 설치

```bash
# Python 버전 확인
python3 --version

# 없으면 Homebrew로 설치
brew install python3
```

## 2단계: 패키지 설치

```bash
cd ot-job-tracker
pip3 install -r requirements.txt
```

## 3단계: 카카오톡 설정

### 3-1. 카카오 개발자 앱 등록
1. https://developers.kakao.com 접속 → 로그인
2. [내 애플리케이션] → [애플리케이션 추가하기]
3. 앱 이름: `OT채용트래커` (아무거나 OK)
4. 생성 후 [앱 설정] → [앱 키] 에서 **REST API 키** 복사

### 3-2. Redirect URI 설정
1. [앱 설정] → [플랫폼] → [Web] 추가
2. 사이트 도메인: `http://localhost:5000`
3. [제품 설정] → [카카오 로그인] → 활성화 ON
4. Redirect URI: `http://localhost:5000/kakao/callback`

### 3-3. 동의 항목 설정
1. [제품 설정] → [카카오 로그인] → [동의항목]
2. **카카오톡 메시지 전송** → 선택 동의 → 설정

### 3-4. config.json 수정
```json
{
  "kakao_rest_api_key": "여기에_REST_API_키_붙여넣기",
  "kakao_redirect_uri": "http://localhost:5000/kakao/callback",
  "crawl_interval_minutes": 30
}
```

### 3-5. 카카오 인증 (최초 1회)
```bash
python3 kakao_auth.py
```
→ 브라우저에서 카카오 로그인 → 동의 → 자동으로 토큰 저장됨

## 4단계: 실행

```bash
# 방법 1: 원클릭 실행 (크롤러 + 웹서버 동시)
chmod +x start.sh
./start.sh

# 방법 2: 개별 실행
python3 crawler.py &    # 백그라운드 크롤링
python3 web_server.py   # 웹 대시보드 (http://localhost:5000)
```

## 5단계: 대시보드 확인

브라우저에서 **http://localhost:5000** 접속

## 크롤링 대상 사이트

| 사이트 | 수집 방식 |
|--------|-----------|
| 사람인 | 검색 결과 HTML 파싱 |
| 잡코리아 | 검색 결과 HTML 파싱 |
| Indeed | 검색 결과 HTML 파싱 |
| 땡큐오티 | 게시판 HTML 파싱 |
| 정신건강OT | 구인구직 게시판 파싱 |
| 워크넷 | 검색 API |

## FAQ

**Q: 크롤링이 차단되면?**
A: User-Agent를 랜덤으로 돌리고, 요청 간격을 3~5초로 두고 있어서 일반적으로 괜찮아. 혹시 차단되면 `crawler.py`의 `HEADERS`를 수정하면 돼.

**Q: 카카오톡 토큰이 만료되면?**
A: refresh_token으로 자동 갱신돼. 만약 완전 만료되면 `python3 kakao_auth.py` 다시 실행.

**Q: Mac 재부팅 후에도 자동 실행하고 싶으면?**
A: `start.sh`를 macOS 로그인 항목에 추가하거나, launchd plist를 설정하면 돼.
