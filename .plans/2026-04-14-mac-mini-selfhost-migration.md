---
date: 2026-04-14T11:30:00+09:00
project: ot-job-tracker
topic: "Fly.io(SQLite) → Mac mini 자가호스팅 + Docker Compose + MariaDB + Tailscale Funnel 이전 Phase 계획"
author: pm-agent
status: approved
supersedes: .plans/2026-04-14-oracle-mariadb-migration.md
inputs:
  - .research/2026-04-14-oracle-mariadb-migration.md
  - .handoffs/2026-04-14_10-11-14_agent-roles-and-oracle-migration-plan.md
  - .plans/2026-04-14-oracle-mariadb-migration.md (superseded, 의사결정 히스토리만 참조)
decisions_locked:
  - 호스팅: Mac mini (사용자 보유, 24/7, pmset으로 sleep 차단)
  - 런타임 형식: Docker Compose 단일 스택 (포터빌리티 최우선)
  - DB: MariaDB 10.x (Docker container)
  - 로그: Loki + Grafana + Promtail (Docker container)
  - 공개 경로: Tailscale Funnel (고정 URL <host>.<tailnet>.ts.net, 무료, 카드 불요)
  - 도메인 정책: is-a.dev 미사용. Tailscale Funnel URL 단독 사용
  - 카카오 OAuth: 새 Tailscale URL을 카카오 개발자 콘솔 redirect URI에 추가
  - 컷오버: Fly 앱에 302 redirect 남기고 Tailscale URL로 유도
  - 백업: 로컬 외장 볼륨 + rsync to 별도 경로, 14일 rolling (Object Storage 미사용)
  - 모니터링: UptimeRobot 무료 (이메일 알림)
  - 포터빌리티: docker-compose.yml + bootstrap.sh + .env.example. 데이터 볼륨만 rsync로 이전 가능
  - 이월 Phase 4/5(D-3 알림·운영 대시보드)는 Legacy-P4/Legacy-P5로 번호 충돌 회피
  - 컨테이너 런타임: Docker Desktop for Mac (OrbStack 아님, 2026-04-14 확정)
  - 백업 대상: 외장 볼륨 보유 확인됨. 마운트 경로는 P2에서 확정
---

# Mac mini 자가호스팅 이전 계획서 (Docker Compose + Tailscale Funnel)

## 1. 목표 및 성공 기준

### 목표
Fly.io(pay-as-you-go, SQLite 단일 볼륨) 런타임을 사용자 보유 **Mac mini 24/7 자가호스팅**으로 이전. 저장소는 SQLite → MariaDB 10.x(Docker)로 전환하고, 외부 노출은 **Tailscale Funnel** 단독. 전체 스택은 `docker compose up` 한 번으로 어떤 Linux/macOS + Docker 환경이든 재구축 가능해야 한다(향후 VPS/라즈베리파이 이전 대비).

### 성공 기준 (측정 가능)
| 항목 | 지표 | 합격선 |
|---|---|---|
| 가용성 | 컷오버 후 72시간 `/healthz` 성공률 | ≥ 99.0% (Mac mini 단일 장애점 감안하여 Oracle 계획보다 0.5%p 완화) |
| 데이터 정합성 | users/jobs/job_reads/crawl_log 행 수 | 이전 전 = 이전 후 (diff 0) |
| 기능 동등성 | 기존 pytest 스위트 | 44/44 pass (MariaDB conftest, Docker) |
| 인증 연속성 | 기존 유저 2명 재로그인 없이 접속 | 양쪽 모두 `/me` 정상 |
| 크롤러 주기 | APScheduler 30분 tick | 컷오버 후 첫 6 tick 성공 |
| 월 비용 | 추가 지출 | $0 (전기료 제외, 카드 결제 불요) |
| 포터빌리티 | 클린 Docker 환경에서 재구축 | `./bootstrap.sh` 실행만으로 전체 스택 기동 성공 |
| 컷오버 다운타임 | Fly read-only → 완전 복귀 | ≤ 30분 |
| 롤백 가능 시간 | 문제 발생 시 Fly 복귀 | 5분 이내 (redirect 코드 revert + Fly read-only 해제) |
| Tailscale Funnel | 외부에서 HTTPS 200 | 3회 연속 성공 |

---

## 2. Phase 분해

### Phase 0 — 기존 Oracle 계획 supersede + 런북 정리
- **담당:** developer (문서 이동·삭제), PM 확인
- **입력:** `.plans/2026-04-14-oracle-mariadb-migration.md`, 기타 Oracle 전용 런북·스니펫
- **산출물:**
  - 기존 Oracle 계획서 frontmatter `status: superseded` + `superseded_by` 링크 (✅ 본 변경에서 처리됨)
  - Oracle 전용 임시 런북/스크립트(`.runbooks/oracle-*`, `scripts/oci-*` 등 존재 시) 삭제 또는 `archive/` 이동
  - 이번 세션 working tree 커밋 3개(C1 크롤러·C2 에이전트·C3 리서치) 미완이면 여기서 정리
- **검증:** `git status`에 Oracle 잔재 없음, `grep -r "Oracle\|oci\|Always Free" --exclude-dir=.plans` 결과가 의도된 문서에만 남음
- **롤백:** git revert
- **예상:** 1시간
- **gate:** 다음 Phase 진입 조건 = main 브랜치 clean + superseded 링크 유효

### Phase 1 — Docker Compose 스택 작성 (포터빌리티 핵심)
- **담당:** infra (compose/Dockerfile) + developer (app 런타임 이미지 정의)
- **입력:** 현행 `requirements.txt`, `app.py`, `crawler.py`, Phase 5 SQLite→MariaDB 번역 표
- **산출물:**
  - `docker-compose.yml` — 6 서비스:
    1. `mariadb` (image: `mariadb:10.11`, volume: `./data/mariadb`, env: `MARIADB_DATABASE=otjob`, `MARIADB_USER=otjob`, 포트는 내부만)
    2. `app` (build: `./docker/app`, command: `gunicorn app:app`, 포트 `127.0.0.1:8000` bind only)
    3. `scheduler` (동일 이미지, command: `python scheduler.py` 또는 APScheduler 싱글 프로세스, single-instance)
    4. `loki` (image: `grafana/loki:2.9`, volume: `./data/loki`, config: `./docker/loki/config.yml`)
    5. `promtail` (image: `grafana/promtail:2.9`, Docker socket 또는 `/var/lib/docker/containers` 마운트로 모든 컨테이너 로그 수집)
    6. `grafana` (image: `grafana/grafana:10`, 포트 `127.0.0.1:3000` bind only, admin 비번 env)
  - `docker/app/Dockerfile` (python:3.11-slim, uv 또는 pip)
  - `docker/loki/config.yml`, `docker/promtail/config.yml`, `docker/grafana/provisioning/` (Loki 데이터소스 자동 등록)
  - `.env.example` — 모든 환경변수 템플릿 (DB 비번, 카카오 키, Grafana admin, TZ=Asia/Seoul)
  - `.env`는 `.gitignore`에 포함
  - `bootstrap.sh` — 처음 실행: `.env.example` → `.env` 복사 안내, `docker compose pull`, `docker compose up -d`, healthcheck 대기, 결과 출력
  - `README-selfhost.md` — 재이전 시 한 장짜리 가이드 (rsync 명령 포함)
- **검증:**
  - 사용자의 로컬 개발 Mac (Mac mini 아님)에서 `./bootstrap.sh` 실행 → 모든 서비스 healthy
  - `docker compose ps` 전부 `running/healthy`
  - `curl http://localhost:8000/healthz` 200 (앱은 아직 SQLite 모드일 수 있으니 Phase 1 단계에선 최소한 컨테이너 기동·DB 연결만 검증)
- **롤백:** 로컬에서 `docker compose down -v` (데이터 볼륨 포함 삭제)
- **예상:** 1일
- **책임 경계:** infra는 compose/이미지 구조까지, 앱 소스 수정은 developer. 단 `docker/app/Dockerfile`의 ENTRYPOINT/CMD와 환경변수 주입 규약은 infra가 초안 제시 후 developer 리뷰
- **gate:** 클린 Docker 환경에서 `./bootstrap.sh` 한 번으로 전 스택 green

### Phase 2 — Mac mini 환경 준비
- **담당:** infra (+사용자 물리 조작 대행)
- **입력:** Mac mini 관리자 접근권
- **산출물:**
  - `sudo pmset -a sleep 0 displaysleep 0 disksleep 0 womp 1` (sleep 차단 + WoL)
  - `sudo pmset -g | grep -E "sleep|womp"` 결과 스냅샷을 런북에 저장
  - OrbStack 설치 (권장) 또는 Docker Desktop — **선택 근거는 리스크 섹션 4번 참고**
  - macOS 방화벽: 외부 유입은 Tailscale 데몬만 허용 (System Settings → Network → Firewall → Stealth Mode on, Docker는 loopback bind만 사용하므로 외부 노출 없음)
  - 전용 디렉터리: `~/ot-job-tracker` (git clone), 데이터 볼륨은 같은 경로 하위 `./data/` (bind mount)
  - `launchd` plist: Mac mini 재부팅 시 `docker compose up -d` 자동 실행 (`~/Library/LaunchAgents/com.otjob.compose.plist`)
  - UPS 또는 서지 프로텍터 권장 (사용자 보유 시, 미보유면 Known Risk로 문서화만)
- **검증:**
  - Mac mini 콘솔 로그아웃·덮개 닫기 상태에서도 `docker compose ps` 유지 (SSH로 확인)
  - 재부팅 후 5분 내 전 서비스 healthy (launchd 검증)
- **롤백:** `pmset` 기본값 복원, launchd plist 제거
- **예상:** 2시간
- **책임 경계:** infra는 macOS 시스템 설정·Docker·launchd까지. 앱 코드·환경변수 값 확정은 developer

### Phase 3 — Tailscale + Funnel 설정
- **담당:** infra
- **입력:** Mac mini, Tailscale 계정 (무료 Personal 플랜)
- **산출물:**
  - Mac mini에 Tailscale 설치 + 로그인, 호스트명 확정(예: `otjob`) → FQDN `otjob.<tailnet>.ts.net`
  - `tailscale funnel --bg 443` 또는 `tailscale serve` + `funnel on` 조합으로 `127.0.0.1:8000` 노출
  - Tailscale ACL/Funnel 설정 확인 (Admin Console에서 Funnel 허용)
  - nginx 등 별도 리버스 프록시 **불필요** (Tailscale이 TLS 종단)
  - 단, 경로별 라우팅 필요하면 `caddy` 컨테이너를 compose에 추가 (현재 단일 앱이라 생략)
  - 확정된 공개 URL을 `.env` `PUBLIC_BASE_URL`에 기록
- **검증:**
  - 외부망(모바일 LTE)에서 `curl -I https://otjob.<tailnet>.ts.net/healthz` 200
  - SSL 인증서 Let's Encrypt(Tailscale 발급) 유효
  - 3회 연속 200 (간격 1분)
- **롤백:** `tailscale funnel off` → 외부 노출 즉시 차단 (Fly로 경로 복귀)
- **예상:** 2시간
- **책임 경계:** infra는 Tailscale CLI·Funnel 설정까지. 앱의 `KAKAO_REDIRECT_URI` 값 갱신은 developer(Phase 7)에서

### Phase 4 — 코드 포팅 (SQLite → MariaDB, structlog 도입)
- **담당:** developer
- **입력:** `.research/2026-04-14-oracle-mariadb-migration.md` 번역 표, Phase 1 compose 스택
- **산출물:**
  - `requirements.txt`에 `PyMySQL`, `structlog` 추가
  - `db.py` 신설: PyMySQL 커넥션 팩토리(dict cursor), 컨텍스트 매니저, 재연결 로직
  - `schema.sql` 신설: MariaDB 문법 전체 스키마 (FK는 `job_reads`만, `ON DELETE CASCADE`)
  - `app.py`/`crawler.py`:
    - `?` → `%s`, `sqlite3.Row` → dict
    - `PRAGMA table_info` → `INFORMATION_SCHEMA.COLUMNS`
    - `INSERT OR IGNORE` → `INSERT IGNORE`
    - `DB_PATH` 제거, `DB_HOST/DB_PORT/DB_USER/DB_PASSWORD/DB_NAME` 도입
  - `structlog` JSON 출력 → stdout (Docker log driver → Promtail 수집)
  - `PUBLIC_BASE_URL` 환경변수 반영(OAuth redirect URI 빌드)
- **검증:** 로컬 compose 스택에서 앱 부팅 + 주요 엔드포인트 smoke (`/healthz`, `/auth/kakao/login` 302, `/jobs`)
- **롤백:** 브랜치 단위 revert
- **예상:** 1~2일
- **책임 경계:** developer는 compose yaml·Tailscale·launchd 수정 금지 → infra로 이관

### Phase 5 — 테스트 재구동 (MariaDB conftest)
- **담당:** tester
- **입력:** Phase 4 브랜치
- **산출물:**
  - `tests/conftest.py` — 기존 compose의 `mariadb` 서비스에 붙거나, `testcontainers-mysql`로 테스트 전용 컨테이너 기동
  - 기존 44 테스트 pass
  - 신규 테스트: 마이그레이션 전후 행 수 비교, FK cascade, 동시성(쓰기 2 스레드), 한글 utf8mb4 깨짐 확인, 토큰 만료·빈 custom_keywords 경계값
- **검증:** CI 로컬 전체 녹색, 커버리지 회귀 없음
- **롤백:** N/A (읽기 전용)
- **예상:** 1일
- **책임 경계:** tester는 버그 수정 금지, 재현 케이스 + 로그만 첨부 → developer 이관

### Phase 6 — 데이터 이전 스크립트 & 드라이런
- **담당:** developer (스크립트) + infra (Fly 덤프 추출 환경)
- **입력:** Fly 프로덕션 DB (`/data/jobs.db`)
- **산출물:**
  - `scripts/migrate_sqlite_to_mariadb.py` — SQLite read → MariaDB INSERT, 배치, idempotent, dry-run 플래그
  - 실행 절차 문서 (`scripts/README.md`)
- **검증 (드라이런):**
  - Fly ssh로 `jobs.db` 덤프 → 로컬 `/tmp/prod_jobs.db`
  - 로컬 compose의 MariaDB에 적재 → 4개 테이블 행 수/샘플 체크섬 일치
- **롤백:** 로컬 MariaDB 볼륨 DROP 후 재생성
- **예상:** 4시간
- **gate:** 행 수 diff 0, 유저 토큰 2건 완전 일치

### Phase 7 — Mac mini 스테이징 + 카카오 OAuth URI 추가
- **담당:** infra(배포) + developer(OAuth 설정 반영) + tester(수락)
- **입력:** Phase 4 브랜치 + Phase 6 데이터 + Phase 3 공개 URL
- **산출물:**
  - Mac mini에 git clone → `.env` 실값 주입 → `./bootstrap.sh`
  - Phase 6 드라이런 데이터로 스테이징 가동(프로덕션 최종 덤프는 컷오버 때 다시)
  - 카카오 개발자 콘솔에 `https://otjob.<tailnet>.ts.net/auth/kakao/callback` redirect URI **추가** (기존 Fly URI는 유지)
  - `PUBLIC_BASE_URL` 값이 Tailscale Funnel URL과 일치
- **검증:** 로그인 → 크롤 수동 1회 → job 조회 → 읽음 처리 → 로그아웃 전 시나리오 100% pass (tester 주도)
- **롤백:** `docker compose down`, 카카오 redirect URI 제거(선택, 두어도 무해)
- **예상:** 4시간
- **gate:** 수락 시나리오 100% + Tailscale Funnel에서 카카오 로그인 왕복 성공

### Phase 8 — 컷오버 (Fly 302 + Mac mini 승격)
- **담당:** infra 주도, developer 대기, tester 모니터링
- **절차:**
  1. T-30m: 유저 2명에게 알림(해당 없음, 박진형 본인이 운영자)
  2. T-10m: Fly 앱 read-only on (쓰기 503, APScheduler `fly machine stop`)
  3. T-0:   Fly 최종 SQLite 덤프 → Mac mini MariaDB 증분 import
  4. T+5m:  행 수 비교 (users/jobs/job_reads/crawl_log)
  5. T+10m: Fly 앱 코드에 `/*` → `https://otjob.<tailnet>.ts.net/*` 302 배포
  6. T+15m: 카카오 OAuth 기본 redirect URI를 Tailscale URL로 승격 (Fly URI는 롤백용으로 보존)
  7. T+20m: 스모크 (`/healthz` 3회, 로그인, `/jobs`, 크롤러 수동 tick)
  8. T+30m: 완료 선언 또는 롤백
- **검증:** 성공 기준 표 10개 항목 실시간 체크
- **롤백 트리거:**
  - 5xx > 5% (5분)
  - 로그인 실패율 > 20%
  - 데이터 불일치
  - Tailscale Funnel 장애
  - → Fly 302 제거 + read-only 해제 + 카카오 기본 URI를 Fly로 복귀 (5분 내 완료 목표)
- **예상:** 30분

### Phase 9 — 관측 · 백업(rsync) · 운영 하드닝
- **담당:** infra
- **입력:** 가동 중인 Mac mini 스택
- **산출물:**
  - UptimeRobot 무료 모니터 2개: `/healthz` + Grafana `/api/health` (5분 주기, 이메일 알림)
  - 백업 스크립트 `scripts/backup.sh`:
    - `docker exec mariadb mariadb-dump --single-transaction otjob | gzip > /Volumes/<외장>/ot-backup/$(date +%F).sql.gz`
    - Loki 청크는 `./data/loki` → `/Volumes/<외장>/ot-backup/loki/` rsync
    - `find /Volumes/<외장>/ot-backup -mtime +14 -delete` (14일 rolling)
  - launchd plist로 매일 03:00 KST 실행
  - Grafana 대시보드 2개 프로비저닝: "App Errors"(5xx·exception 카운트), "Crawl Ticks"(source별 성공/실패)
  - `README-selfhost.md`에 복원 절차 기재(`gunzip | docker exec -i mariadb mariadb`)
  - 월 1회 복원 리허설 캘린더 알림
  - Fly 앱 shutdown 스케줄 (T+7일 후 `fly apps destroy` 또는 스케일 0)
- **검증:**
  - 백업 1회 수동 실행 성공
  - 복원 리허설 (테스트 DB에 gunzip import) 성공
  - UptimeRobot 모니터 green
- **예상:** 상시 (첫 주 집중)

---

## 3. 의존성 그래프 & 병렬 구간

```
Phase 0 (superseded 처리 + 런북 정리)
  │
  ├──► Phase 1 (docker-compose.yml) ◄── 핵심 선행, 모든 하류 Phase의 전제
  │        │
  │        ├──► Phase 2 (Mac mini 준비)
  │        │        │
  │        │        └──► Phase 3 (Tailscale Funnel)
  │        │                  │
  │        ├──► Phase 4 (코드 포팅) ──► Phase 5 (테스트)
  │        │                                │
  │        │                                ▼
  │        │                         Phase 6 (데이터 이전 드라이런)
  │        │                                │
  │        └────────────[Phase 3 완료]──────┤
  │                                         ▼
  │                                  Phase 7 (Mac mini 스테이징 + OAuth)
  │                                         │
  │                                         ▼
  │                                  Phase 8 (컷오버)
  │                                         │
  │                                         ▼
  │                                  Phase 9 (관측·백업·하드닝)
  │                                         │
  │                                         ▼
  │                                  [이월] Legacy-P4 / Legacy-P5
```

**병렬 가능 구간:**
- Phase 1 완료 후 트랙 A (Phase 2→3, infra) / 트랙 B (Phase 4→5, developer/tester) 동시 진행
- Phase 6는 Phase 5 완료 직후 시작 가능 (Phase 3 불필요)
- Phase 7은 Phase 3 + 6 모두 완료 후

**총 예상 소요:** 실작업 4~5일 + 사용자 물리 조작 1일 여유 = **영업일 기준 1주**
(Oracle 계획 대비 도메인 PR 리드타임 1~3일 제거되어 단축)

---

## 4. 리스크 & 완화책

| 리스크 | 영향 | 완화책 |
|---|---|---|
| **Mac mini 단일 장애점** (정전·디스크·macOS 업데이트) | 서비스 전면 중단 | UPS 권장, 백업 외장볼륨 분리, UptimeRobot 5분 내 감지, 롤백은 Fly 앱 재기동(Phase 8 완료 후 7일간 보존) |
| **Tailscale Funnel 장애 또는 계정 제재** | 외부 접속 전면 차단 | 사전 Funnel 정책 준수(대역폭·용도), 백업 경로로 Cloudflare Tunnel 시범 적용 검토는 Phase 9에서 별도 과제화. 즉시 장애 시 Fly 302 유지로 30일 버퍼 |
| **macOS 자동 업데이트로 재부팅** | Docker 중단, launchd 복귀 전 공백 | `softwareupdate --schedule off`, 업데이트는 사용자가 유지보수 윈도우에 수동 |
| **Docker Desktop vs OrbStack** | 라이선스·리소스·안정성 | **OrbStack 권장**(개인 무료, 가볍고 launchd 친화). Docker Desktop은 개인 무료이나 macOS 리소스 점유가 큼. 둘 다 `docker compose` 호환이므로 compose.yml은 동일. 최종 선택은 infra가 Phase 2에서 OrbStack 먼저 시도, 문제 시 Docker Desktop 폴백 |
| **이전 후 bind mount 권한 이슈** (UID 501 vs 컨테이너 UID 1000) | MariaDB/Loki 기동 실패 | compose에 `user: "501:20"` 또는 named volume 사용. bootstrap.sh에서 `chown` 선조치 |
| **Fly SQLite 덤프 중 쓰기 유입** | 데이터 손실 | read-only 플래그 먼저 on, APScheduler stop 확인 후 덤프 |
| **카카오 redirect URI 불일치** | 로그인 전면 실패 | Phase 7에서 양 URI 병행 등록, Phase 8에서 기본값 교체 |
| **Tailscale 호스트명 변경** | 고정 URL 유지 실패 → OAuth/북마크 깨짐 | Admin Console에서 머신 rename 금지, `.env`에 URL 박제 |
| **포터빌리티 환상** (macOS 특이사항 누락) | VPS 이전 시 재구축 실패 | compose는 linux/amd64·arm64 둘 다 태그 지정, launchd·pmset 항목은 `README-selfhost.md`에 "macOS 전용·Linux에선 systemd 대체" 명시 |

---

## 5. 컷오버 체크리스트 (Phase 8)

### 사전 (T-24h)
- [ ] Mac mini 스택 Phase 7 수락 100% pass 기록
- [ ] 카카오 콘솔에 Tailscale redirect URI 등록 (Fly URI 병존)
- [ ] MariaDB 최신 덤프 1건 외장볼륨 보관
- [ ] UptimeRobot 모니터 2개 green
- [ ] 롤백 런북 재확인 (`.runbooks/rollback-selfhost.md`)
- [ ] Fly 앱 정상 상태, `fly machine status` 확인

### 컷오버 (T-0 ~ T+30m)
- [ ] Fly `read-only` 플래그 on (쓰기 503)
- [ ] Fly APScheduler 정지 (`fly machine stop`)
- [ ] 최종 `/data/jobs.db` 덤프 → 로컬 → Mac mini MariaDB 증분 import
- [ ] 행 수 비교 (users=2, jobs≥34, job_reads≥15, crawl_log≥48)
- [ ] Mac mini compose healthy 재확인
- [ ] `curl -I https://otjob.<tailnet>.ts.net/healthz` 3회 연속 200
- [ ] Fly 앱에 302 redirect 코드 배포
- [ ] **카카오 OAuth 기본 redirect URI를 Tailscale URL로 승격** (필수, 누락 시 전면 로그인 실패)
- [ ] 박진형 계정 로그인 왕복
- [ ] 크롤러 수동 tick 1회 성공
- [ ] APScheduler 30분 tick 첫 수행 확인
- [ ] UptimeRobot 모니터 Tailscale URL로 교체

### 롤백 트리거 (어느 하나라도 true면)
- [ ] 5xx > 5% for 5분
- [ ] 로그인 실패율 > 20%
- [ ] DB 커넥션 불가
- [ ] 데이터 누락/중복
- [ ] Tailscale Funnel 200 실패 3회 연속
→ **롤백 절차:** Fly read-only 해제, Fly 302 코드 revert, 카카오 기본 URI를 Fly로 복귀, UptimeRobot 원복. Mac mini 스택은 유지(재시도용).

### 사후 (T+24h / T+72h / T+7d)
- [ ] 에러 로그 리뷰 (Grafana Loki `{container="app"} |= "ERROR"`)
- [ ] 백업 1회 실행 확인
- [ ] 성공 기준 10개 항목 실측치 기록
- [ ] T+7d: Fly 앱 종료(또는 scale 0), credit 소진 차단

---

## 6. 이월 작업(Legacy) — 컷오버 안정화 후

| 코드 | 작업 | 선행 조건 |
|---|---|---|
| Legacy-P4 | D-3 마감일 임박 카카오 알림 | Phase 9 안정화(7일) 완료 후 |
| Legacy-P5 | 운영 모니터링 대시보드 `/admin` | Legacy-P4 완료 후 |

번호 충돌 회피를 위해 `Legacy-` 접두어 유지. 별도 계획서로 재분해.

---

## 7. PM 책임경계 감시 포인트

- **developer는 금지:** `docker-compose.yml` 서비스 추가/삭제, `pmset`, `launchd` plist, Tailscale CLI, macOS 시스템 설정, 백업 rsync 스크립트 수정 → infra로 이관
- **infra는 금지:** `app.py`·`crawler.py`·`db.py`·`schema.sql` 수정, `requirements.txt` 편집, structlog 호출 치환 → developer로 이관
- **tester는 금지:** 코드로 버그 "fix" 시도. 재현 스텝 + 로그 + 예상/실제만 보고 → developer로 이관
- **planner/ui-designer:** 이번 이전은 백엔드·인프라 전용, UI/카피 변경 없음 → 호출 불필요
- **경계 모호 영역 (PM이 조정):**
  - `docker/app/Dockerfile` — 베이스 이미지·레이어 구조는 infra, 설치 패키지 목록은 developer가 `requirements.txt` 기준 제공
  - `.env.example` 키 목록 — 인프라 관련 키(DB_*, Grafana, Tailscale URL)는 infra, 앱 로직 키(KAKAO_*, PUBLIC_BASE_URL 해석)는 developer. 파일은 둘이 공동 편집하되 PR 시 서로 리뷰
  - 카카오 redirect URI 변경 — 개발자 콘솔 조작은 infra, 앱 코드 내 URL 빌드는 developer
- **Phase gate 통과 승인:** 각 Phase의 "검증" 항목 전부 green일 때만 PM이 다음 Phase 진입 승인
- **책임 경계 위반 발견 시:** 즉시 해당 에이전트에 반송 + 로그를 handoff 문서에 기록

---

## 8. 미해결 질문

1. **OrbStack vs Docker Desktop 최종 선택** — Phase 2 첫 1시간에 infra가 두 옵션을 실기 비교하고 PM 승인받아 확정. 기본 권장은 OrbStack.
2. **외장볼륨 존재 여부** — 사용자가 별도 외장 SSD/HDD를 보유하는지 미확인. 미보유 시 Phase 9 백업은 동일 디스크의 별도 경로로 대체하되 물리 장애 리스크 문서화 필요.

