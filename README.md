# OT 채용 트래커

서울 지역 작업치료·감각통합·요양병원 채용 공고를 수집하여 **카카오톡 나에게 보내기**로 푸시하는 멀티유저 웹앱.

- 배포: https://ot-job-tracker.fly.dev/
- 인프라: Fly.io (region `nrt`, 볼륨 `ot_data` `/data` 마운트)
- 스택: Python 3.13 · Flask · APScheduler · SQLite · Gunicorn

---

## 아키텍처

```
┌──────────────┐   OAuth   ┌──────────────┐
│ 브라우저/카톡 │ ────────▶ │  Fly.io App   │
└──────────────┘           │ (Flask+Sched) │
                           │               │
                           │ ┌───────────┐ │
                           │ │ /data/    │ │── SQLite (jobs, users, crawl_log)
                           │ │  jobs.db  │ │
                           │ └───────────┘ │
                           │               │
                           │ 30분마다 크롤 │── 사람인 / 잡코리아 / Indeed
                           │   + 발송      │   땡큐오티 / 정신건강OT
                           └──────────────┘
```

- **멀티유저**: 유저별 access/refresh 토큰을 DB에 저장, 만료 시 자동 갱신
- **카카오 팀원 제한**: 검수 전에는 최대 4명까지 `talk_message` 수신 가능

---

## 수집 정책 (현재)

### 키워드 필터 (`crawler.py:DEFAULT_KEYWORDS` + 유저 맞춤)
기본: `작업치료`, `감각통합`, `OT `, `인지치료`, `요양병원`
유저가 `/settings`에서 추가한 키워드는 수집 시 합집합으로 반영되고, 알림은 내 설정에 맞는 공고만 발송.

### 지역 필터 (`crawler.py:DEFAULT_REGIONS` + 유저 맞춤)
기본: `서울`. 유저별로 추가 가능. 특화 게시판은 지역정보 없어 "전국/미상" 태그로 통과.

### 정규직 분류 (`crawler.py:classify_job_type`)
| 조건 | 결과 |
|---|---|
| `정규직` 명시 | `job_type = "정규직"` |
| 계약직/파트/아르바이트/인턴/프리랜서/일용직 포함 | **저장 안 함** |
| 그 외 불명 | `job_type = "미확인"` (태그 표시) |

### 중복 공고 병합 (`crawler.py:dedup_key`)
org+title을 정규화(공백/괄호/채용·모집 등 노이즈 제거, 소문자화)한 키로 묶어
서로 다른 출처의 같은 공고를 한 카드에 통합 표시. 읽음 처리도 그룹 전체에 적용.

### URL 정규화 (`crawler.py:normalize_url`)
- `http://`, `https://` 절대 URL만 허용
- `/`로 시작하는 상대경로는 base 도메인과 결합
- `javascript:`, `mailto:`, `#`, 빈값은 버림

---

## 참조 사이트 (현재 5개)

| 이름 | 방식 | 비고 |
|---|---|---|
| 사람인 | HTML 파싱 | 검색 URL 1페이지 (페이지네이션 TODO) |
| 잡코리아 | HTML 파싱 | 검색 URL 1페이지 |
| Indeed | HTML 파싱 | kr.indeed.com |
| 땡큐오티 | HTML 게시판 파싱 | thankyouot.com/board1 |
| 정신건강OT | HTML 게시판 파싱 | kaotmh.org/bbs/bbr_6 |

**확장 예정**: 인크루트, 워크넷(OpenAPI), 대한작업치료사협회, 아이소리몰, 아이톡톡홈티, 아동포털, 개별 병원 5곳.

---

## 엔드포인트

| Path | Method | 설명 |
|---|---|---|
| `/` | GET | 대시보드 (공고 목록, NEW 우선 정렬) |
| `/login` | GET | 카카오 OAuth 시작 |
| `/kakao/callback` | GET | OAuth 콜백, 유저 저장 |
| `/logout` | GET | 세션 종료 |
| `/subscribe` · `/unsubscribe` | POST | 알림 ON/OFF (로그인 필요) |
| `/settings` | GET/POST | 내 맞춤 키워드·지역 설정 (로그인 필요) |
| `/health` | GET | 헬스체크 |
| `/api/jobs?keyword=` | GET | 공고 목록 JSON |
| `/api/stats` | GET | 전체·신규·정규직 카운트 |
| `/api/crawl_now` | POST | 즉시 수집 (백그라운드 스레드) |
| `/api/crawl_status` | GET | 수집 진행 여부 |
| `/api/jobs/<id>/read` | POST | 해당 공고 읽음 처리 (로그인 필요) |
| `/api/jobs?unread=1` | GET | 내가 안 읽은 공고만 (로그인 필요) |

---

## 환경변수 (Fly Secrets)

- `KAKAO_REST_API_KEY`
- `KAKAO_CLIENT_SECRET`
- `KAKAO_REDIRECT_URI` = `https://ot-job-tracker.fly.dev/kakao/callback`
- `FLASK_SECRET_KEY`
- `PUBLIC_URL` = `https://ot-job-tracker.fly.dev`
- `DB_PATH` = `/data/jobs.db` (fly.toml)
- `CRAWL_INTERVAL_MINUTES` = `30` (fly.toml)

---

## 환경변수 (Vercel)

Vercel Dashboard → Project → Settings → Environment Variables 에 등록.

- `KAKAO_REST_API_KEY`, `KAKAO_CLIENT_SECRET`, `KAKAO_REDIRECT_URI` — Fly와 동일 (프로덕션 도메인 기준)
- `FLASK_SECRET_KEY` — **필수**. 미설정 시 앱 부팅 실패 (serverless cold start마다 세션이 풀리는 버그 차단).
  ```bash
  python3 -c "import secrets; print(secrets.token_hex(32))"
  ```
  출력값을 등록. 키 회전 시 모든 기존 세션 무효화 → 재로그인 필요.
- `DATABASE_URL` — Neon **pooler 엔드포인트** 사용.
  ```
  postgresql://user:pass@ep-xxx-pooler.<region>.aws.neon.tech/<db>?sslmode=require
  ```
  `-pooler` 서브도메인이 포함된 호스트를 써야 Neon의 PgBouncer가 serverless 환경에서 커넥션 풀링을 담당.
  pooler 미사용 시 매 요청마다 신규 커넥션 핸드셰이크로 응답 지연이 누적됨.
- `CRAWL_INTERVAL_MINUTES` = `30` (선택)
- `ENABLE_SCHEDULER` — serverless 환경에서는 보통 `0`으로 둬서 스케줄러 비활성 (별도 crawler 워커 사용 시).

---

## 테스트

```bash
python3 -m pytest tests/ -v
```

- `tests/test_crawler_policy.py`: 키워드 매칭, 정규직 분류, URL 정규화, 서울 필터 (단위)
- `tests/test_job_urls.py`: 배포된 앱의 저장 URL 도달성 + 중복 검사 (네트워크 필요)

환경변수 `APP_BASE_URL`로 테스트 대상 지정 가능.

---

## 운영

```bash
fly deploy                       # 배포
fly logs -a ot-job-tracker       # 로그
fly secrets list -a ot-job-tracker
fly ssh console -a ot-job-tracker
```

---

## 로컬 개발

```bash
pip3 install --break-system-packages -r requirements.txt
cp config.example.json config.json  # 로컬 개발용 (Fly에서는 env 사용)
python3 app.py   # 기본 포트 8080
```

포트 5000은 macOS AirPlay / Cursor가 점유할 수 있으므로 8080 사용.
