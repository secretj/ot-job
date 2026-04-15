---
date: 2026-04-15T00:00:00+09:00
project: ot-job-tracker
topic: "Fly.io(SQLite) → Vercel(Flask) + Neon Postgres + GitHub Actions cron 이전 계획"
author: claude
status: approved
supersedes: .plans/2026-04-14-mac-mini-selfhost-migration.md
inputs:
  - .research/2026-04-14-oracle-mariadb-migration.md
  - .handoffs/2026-04-14_10-11-14_agent-roles-and-oracle-migration-plan.md
decisions_locked:
  - 호스팅: Vercel Hobby (무료, 카드 불요)
  - DB: Neon Postgres Free (0.5GB, scale-to-zero, 카드 불요)
  - 스케줄러: GitHub Actions cron (30분 간격, public repo면 무제한 무료)
  - DB 드라이버: psycopg[binary] (PyMySQL 폐기)
  - SQL 방언: PostgreSQL (MariaDB 스키마/쿼리 번역)
  - 도메인: ot-job.vercel.app (커스텀 도메인 무료지만 이번엔 vercel.app 사용)
  - APScheduler 폐기: 크롤링은 Actions가 단발 실행
  - Docker Compose / MariaDB / Loki 스택 폐기 (개발은 Neon dev branch + vercel dev)
  - 로그: Vercel runtime logs (1시간 무료 보관) + Actions run logs (90일) + crawl_log 테이블
  - 모니터링: UptimeRobot 무료 (이메일)
  - 컷오버: Fly 앱에 302 redirect → Vercel URL
  - 기존 카카오 redirect URI에 Vercel URL 추가 등록
  - 카드 불요 제약 절대 고정 (Oracle/AWS/GCP 옵션 모두 차단됨)
---

# Vercel + Neon + GitHub Actions 이전 계획서

## 0. 결정 배경

- Mac mini 자가호스팅 → 영업시간만 켜짐으로 24/7 불가 (2026-04-15 사용자 통보)
- Oracle / AWS / GCP / Fly.io 등 카드 등록 필수 옵션 전부 차단
- 카드 불요 + 영구 무료 + 24/7 + 스케줄러 + DB 조합의 유일한 현실해
  → 서버리스(Vercel) + 외부 DB(Neon) + 외부 cron(Actions) 분리

## 1. 목표 및 성공 기준

### 목표
Fly.io 24/7 서버 + 단일 SQLite 구조를 **카드 불요 영구 무료 3-tier 분리 아키텍처**로 이전.
- HTTP 요청 처리 → Vercel Python 함수 (cold start 0.5~2초 허용)
- 데이터 → Neon Postgres
- 크롤링 → GitHub Actions `*/30 * * * *`

### 성공 기준 (측정 가능)
| 항목 | 지표 | 합격선 |
|---|---|---|
| 가용성 | 컷오버 후 72시간 `/healthz` 성공률 | ≥ 99.0% (cold start 1회 실패 허용) |
| 데이터 정합성 | users/jobs/crawl_log 행 수 | 이전 전 = 이전 후 (diff 0) |
| 데이터 정합성 | job_reads | 이전 후 ≤ 이전 전 (FK CASCADE 신규 도입으로 고아 행 정리됨) |
| 기능 동등성 | 기존 pytest 스위트 | 44/44 pass (Postgres conftest) |
| 인증 연속성 | 기존 유저 2명 재로그인 없이 접속 | `/me` 200 |
| 크롤러 주기 | Actions cron tick | 컷오버 후 첫 6 tick 성공 (cron drift ±10분 허용) |
| 월 비용 | Vercel + Neon + Actions 청구 | $0.00 (카드 등록 없음) |
| 컷오버 다운타임 | Fly read-only → Vercel 완전 복귀 | ≤ 30분 |
| 롤백 | Fly 앱 복귀 | 5분 이내 (Fly 302 revert + read-only 해제) |
| Cold start P95 | 첫 요청 응답 시간 | ≤ 3초 |

## 2. 코드 자산 재활용 분석

| 파일/모듈 | Mac mini 단계까지 진행분 | Vercel+Neon에서의 운명 |
|---|---|---|
| `app.py` | Flask routes 완성 | **그대로 재사용** — Vercel `@vercel/python` runtime이 WSGI 객체(`app`) 인식 |
| `crawler.py` | 8개 소스 크롤러 | **그대로 재사용** — DB write 호출만 새 `db.py`로 자동 연결 |
| `db.py` | PyMySQL pool | **재작성** — `psycopg[binary]` 기반. 인터페이스(`get_conn()` context manager, dict cursor)는 유지하여 호출처 무수정 |
| `schema.sql` | MariaDB DDL | **재작성** — PostgreSQL 문법 (`SERIAL`, `BOOLEAN`, `ON CONFLICT DO NOTHING`, ENGINE/CHARSET 제거) |
| `scheduler_main.py` (APScheduler) | Fly 단일 프로세스 | **폐기** — Actions 워크플로가 `python -m crawler` 직접 호출 |
| `kakao_auth.py`, `kakao_notify.py` | 카카오 OAuth | **그대로 재사용** — redirect URI만 변경 |
| `logging_setup.py` (structlog) | JSON 로깅 | **그대로 재사용** — stdout이 Vercel/Actions 로그로 자동 수집됨 |
| `Dockerfile`, `docker-compose.yml` | MariaDB 스택 | **폐기** (or `legacy/` 보관) |
| `fly.toml` | Fly 설정 | **컷오버까지 유지** (302 redirect 라우트만 남기는 최소 앱으로 축소) |
| `tests/` | 44 pass | **conftest만 교체** — 테스트 본문 무수정 목표 |
| `gunicorn` | Fly WSGI | **폐기** — Vercel이 자체 런타임 |

PyMySQL → psycopg, MariaDB DDL → Postgres DDL 두 작업이 핵심 변경.
`?`→`%s` 치환은 둘 다 `%s`라 변경 불요. `INSERT IGNORE` → `INSERT ... ON CONFLICT DO NOTHING` 일괄 치환.

## 3. Phase 분해

### Phase 0 — 문서 정리 + 폐기 자산 격리
- **담당:** developer
- **산출물:**
  - `.plans/2026-04-14-mac-mini-selfhost-migration.md` frontmatter `status: superseded` + `superseded_by` 링크
  - `.runbooks/phase1-mac-mini-selfhost.md` → `.runbooks/archive/`
  - 본 계획서 커밋
- **검증:** `git status` clean, 모든 superseded 링크 양방향 유효
- **예상:** 30분

### Phase 1 — Neon 계정 + DB 프로비저닝
- **담당:** infra
- **산출물:**
  - Neon 가입 (GitHub OAuth, 카드 불요)
  - 프로젝트 `ot-job`, region `ap-southeast-1` (Singapore, 한국 최단)
  - DB `otjob`, role `otjob_app` (read/write), `otjob_migrate` (DDL)
  - dev branch 1개 (로컬/테스트용, 자동 분기)
  - connection string 2개 (`DATABASE_URL`, `DATABASE_URL_DEV`)
- **검증:** `psql $DATABASE_URL -c "SELECT 1"` 성공
- **예상:** 30분
- **gate:** 두 connection string이 GitHub repo Secrets/Vercel env에 등록 가능 상태

### Phase 2 — 코드 포팅 (PyMySQL → psycopg, MariaDB → Postgres)
- **담당:** developer
- **산출물:**
  - `requirements.txt`: `PyMySQL` 제거, `psycopg[binary]>=3.1` 추가
  - `db.py` 재작성:
    - `psycopg.connect(os.environ["DATABASE_URL"])`
    - `row_factory=dict_row` (DictCursor 호환)
    - 컨텍스트 매니저 인터페이스 동일 유지
    - serverless 환경에서 풀 의미 없음 → 풀 제거, 함수 호출당 1 connection (Neon은 PgBouncer 내장)
  - `schema.sql` 재작성:
    - `BIGINT` 그대로
    - `TINYINT(1)` → `BOOLEAN`
    - `VARCHAR(N)` 그대로
    - `AUTO_INCREMENT` → `BIGSERIAL`
    - `ENGINE=InnoDB DEFAULT CHARSET=utf8mb4` 라인 제거 (Postgres는 기본 UTF-8)
    - `KEY xxx (cols)` → `CREATE INDEX IF NOT EXISTS`
    - `crawled_at`/`timestamp` 등 `VARCHAR(32)` 유지 (앱이 ISO 문자열로 저장 중, 마이그레이션 단순화)
  - `crawler.py` / `app.py` 일괄 치환:
    - `INSERT IGNORE INTO` → `INSERT INTO ... ON CONFLICT (id) DO NOTHING`
    - `INFORMATION_SCHEMA.COLUMNS` 호출은 Postgres에도 존재, WHERE 절만 점검
  - `vercel.json` 신설:
    ```json
    {
      "builds": [{"src": "app.py", "use": "@vercel/python"}],
      "routes": [{"src": "/(.*)", "dest": "app.py"}]
    }
    ```
  - `api/index.py` shim (또는 `app.py` 루트 노출 허용 여부 확인)
- **검증:** 로컬에서 `DATABASE_URL=$DATABASE_URL_DEV python -c "import app"` import 성공, `python -m pytest` 실행 가능 상태
- **예상:** 1일

### Phase 3 — 테스트 재구동 (Postgres conftest)
- **담당:** tester
- **산출물:**
  - `tests/conftest.py`:
    - Neon dev branch 신규 분기 또는 로컬 `postgres:16` Docker 컨테이너 (둘 중 빠른 쪽; 로컬 docker는 카드 불요)
    - 각 테스트 함수마다 truncate (또는 transactional rollback)
  - `INSERT IGNORE` 를 쓰는 테스트가 있다면 본문 1줄씩 점검
- **검증:** 44/44 pass
- **예상:** 4시간
- **책임 경계:** 실패 시 fix는 developer로 이관

### Phase 4 — Vercel 프로젝트 셋업 + 첫 배포
- **담당:** infra
- **산출물:**
  - Vercel 계정 (GitHub OAuth, 카드 불요)
  - 프로젝트 `ot-job-tracker` import (이 GitHub repo 연결)
  - env 등록: `DATABASE_URL`, `KAKAO_REST_API_KEY`, `KAKAO_REDIRECT_URI=https://<deploy-url>/auth/kakao/callback`, `PUBLIC_BASE_URL`
  - production branch = `main`, preview = 모든 PR 자동
  - 첫 배포 후 `https://ot-job-tracker.vercel.app/healthz` 200 확인
- **검증:** 외부 curl `/healthz` 200 3회
- **예상:** 1시간
- **gate:** Vercel URL 확정

### Phase 5 — GitHub Actions 크롤러 워크플로
- **담당:** developer
- **산출물:**
  - `.github/workflows/crawl.yml`:
    ```yaml
    name: crawl
    on:
      schedule: [{ cron: "*/30 * * * *" }]
      workflow_dispatch: {}
    jobs:
      crawl:
        runs-on: ubuntu-latest
        timeout-minutes: 10
        steps:
          - uses: actions/checkout@v4
          - uses: actions/setup-python@v5
            with: { python-version: "3.11", cache: pip }
          - run: pip install -r requirements.txt
          - run: python -m crawler
            env:
              DATABASE_URL: ${{ secrets.DATABASE_URL }}
              KAKAO_REST_API_KEY: ${{ secrets.KAKAO_REST_API_KEY }}
    ```
  - `crawler.py`에 `if __name__ == "__main__":` entrypoint 정리 (이미 있다면 그대로)
  - 카카오 알림 발송도 같은 워크플로 안에 포함 (현 구조 유지)
- **검증:** `workflow_dispatch`로 수동 1회 실행 → Neon DB에 새 row 확인, 로그에 source별 결과 출력
- **예상:** 3시간
- **gate:** 수동 트리거 성공 + 30분 자동 tick 1회 성공

### Phase 6 — 데이터 이전 (Fly SQLite → Neon Postgres)
- **담당:** developer
- **산출물:**
  - `scripts/migrate_sqlite_to_postgres.py`:
    - SQLite read → psycopg `executemany`
    - 4개 테이블 순서: users → jobs → job_reads → crawl_log (FK 순서)
    - idempotent (`ON CONFLICT DO NOTHING`)
    - 행 수 비교 출력
  - 실행 절차 README
- **검증 (드라이런):**
  - Fly에서 `cat /data/jobs.db` 덤프 → 로컬 → Neon dev branch에 적재
  - 행 수/체크섬 일치
- **예상:** 4시간
- **gate:** users/jobs/crawl_log diff 0, job_reads는 고아 행 제거분만 차이

### Phase 7 — 카카오 OAuth URI 추가 + 스테이징 수락
- **담당:** developer (콘솔) + tester (시나리오)
- **산출물:**
  - 카카오 개발자 콘솔: `https://ot-job-tracker.vercel.app/auth/kakao/callback` 추가 (Fly URI 유지)
  - Vercel preview 배포에 박진형 계정으로 로그인 → 잡 조회 → 읽음 → 로그아웃 전 시나리오
- **검증:** 시나리오 100% pass
- **예상:** 2시간

### Phase 8 — 컷오버 (Fly 302 + Vercel 승격)
- **담당:** infra 주도, developer 대기, tester 모니터링
- **절차:**
  1. T-10m: Fly read-only on, Fly machine stop (APScheduler 정지)
  2. T-0:   Fly 최종 SQLite 덤프 → Neon production 증분 import
  3. T+5m:  행 수 비교
  4. T+10m: Fly 앱 코드 → `/*` 302 redirect to Vercel URL 배포
  5. T+15m: 카카오 OAuth 기본 redirect URI를 Vercel URL로 승격
  6. T+20m: 스모크 (`/healthz` 3회, 로그인, `/jobs`, Actions 수동 트리거)
  7. T+30m: 완료 또는 롤백
- **검증:** 성공 기준 10개 항목 실시간 체크
- **롤백:** Fly read-only 해제 + 302 코드 revert + 카카오 기본 URI 복귀 (5분)
- **예상:** 30분

### Phase 9 — 관측 + 정리
- **담당:** infra
- **산출물:**
  - UptimeRobot 모니터: Vercel `/healthz` 5분 주기, 이메일 알림
  - Actions 실패 알림: GitHub repo notifications "Actions: failed workflows" on
  - Neon free tier 사용량 대시보드 즐겨찾기
  - Fly 앱은 +7일 후 `fly apps destroy` (carbon copy 위해 1주 보존)
  - `README.md` 업데이트: 새 아키텍처 다이어그램, 로컬 개발법(`vercel dev`), 환경변수 표
- **예상:** 2시간

## 4. 의존성 그래프

```
Phase 0 (문서 정리)
  │
  ├──► Phase 1 (Neon DB)
  │        │
  │        ├──► Phase 2 (코드 포팅) ──► Phase 3 (테스트)
  │        │           │
  │        │           ▼
  │        ├──► Phase 4 (Vercel 셋업) ──┐
  │        │                            │
  │        └──► Phase 6 (데이터 이전)   │
  │                    │                │
  │                    └────────────────┴──► Phase 5 (Actions cron)
  │                                              │
  │                                              ▼
  │                                        Phase 7 (수락)
  │                                              │
  │                                              ▼
  │                                        Phase 8 (컷오버)
  │                                              │
  │                                              ▼
  │                                        Phase 9 (관측·정리)
```

**병렬:** Phase 2 (코드)와 Phase 1 후 Phase 4 (Vercel 셋업)는 동시 진행 가능
**총 예상:** 2~3일 작업

## 5. 리스크 & 완화책

| 리스크 | 영향 | 완화책 |
|---|---|---|
| Vercel cold start 시 카카오 OAuth 콜백 timeout | 로그인 실패 | Vercel 함수 timeout 10초, 카카오 콜백은 일반적으로 1초 이내 완료. preview에서 측정 |
| Neon scale-to-zero 깨우기 0.5~2초 | 첫 요청 느림 | 무료 한도 내 acceptable, UptimeRobot 5분 ping이 자연스럽게 keepalive 역할 |
| Actions cron drift ±10분 | 30분 주기 깨짐 | 잡 알리미 특성상 무관, 재시도 로직은 다음 tick이 자동 보완 |
| Public repo 시 API key 노출 | 카카오 토큰/DB URL 유출 | Secrets 사용 절대 원칙, `.env*` `.gitignore`, key 노출 시 카카오/Neon 즉시 rotate |
| Private repo로 전환 시 Actions 분 한도 (월 2,000분) | cron 정지 | 30분 × 48 × 30 × 1분 = 1,440분 (충분), 단 lint/test workflow 추가 시 재계산 |
| Postgres SQL 차이 누락 | 런타임 에러 | Phase 3 테스트가 1차 방어, Phase 7 수락 시나리오가 2차 방어 |
| Vercel 함수 콜드 + DB 콜드 동시 | P95 초과 | 두 가지 모두 무료 한도. 측정 후 문제 시 Vercel cron으로 keepalive (월 100회 무료) |
| Neon 0.5GB 초과 | 쓰기 차단 | 현재 53KB → 1만 배 여유. crawl_log 90일 retention 정책 미리 도입 (Phase 9) |

## 6. 컷오버 체크리스트 (Phase 8)

### 사전 (T-24h)
- [ ] Phase 7 수락 시나리오 100% 기록
- [ ] Neon production DB 백업 (Neon은 PITR 제공, 추가 작업 없음)
- [ ] 카카오 콘솔 양 URI 등록 확인
- [ ] UptimeRobot 모니터 green
- [ ] Fly 정상 상태 확인

### 컷오버 (T-0 ~ T+30m)
- [ ] Fly read-only on
- [ ] Fly machine stop
- [ ] 최종 jobs.db → Neon import
- [ ] 행 수 비교 (users=2, jobs≥34, crawl_log≥48, job_reads ≤15)
- [ ] Vercel `/healthz` 3회 200
- [ ] Fly 앱에 302 redirect 코드 배포
- [ ] 카카오 기본 redirect URI를 Vercel URL로 승격
- [ ] 박진형 계정 로그인 왕복
- [ ] Actions 수동 trigger 1회
- [ ] UptimeRobot 모니터 URL 교체

### 롤백 트리거
- [ ] 5xx > 5% for 5분
- [ ] 로그인 실패율 > 20%
- [ ] DB 커넥션 불가
- [ ] 데이터 누락
→ Fly read-only 해제, 302 revert, 카카오 기본 URI Fly로 복귀

### 사후 (T+24h / T+72h / T+7d)
- [ ] 에러 로그 리뷰 (Vercel + Actions)
- [ ] 성공 기준 10개 실측치 기록
- [ ] T+7d: Fly 앱 destroy

## 7. 이월 (Legacy)

| 코드 | 작업 | 선행 |
|---|---|---|
| Legacy-P4 | D-3 마감일 임박 카카오 알림 | Phase 9 안정화 7일 후 |
| Legacy-P5 | 운영 모니터링 대시보드 `/admin` | Legacy-P4 후 |

## 8. 미해결 질문

1. **Public vs Private repo** — 현 repo가 public이면 Actions 무제한, secrets만 잘 관리. private이면 분 한도 계산 필요. **확인 필요.**
2. **Vercel 함수 SSR 응답 시간** — 현 templates Jinja 렌더링 무게 측정 안 됨. preview 배포 후 실측.
3. **테스트 DB 전략** — Neon dev branch (네트워크 의존, 빠르게 만들고 부수기 가능) vs 로컬 docker postgres (오프라인 가능). Phase 3 시작 시 30분 PoC로 확정.
