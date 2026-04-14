---
date: 2026-04-14T10:30:00+09:00
project: ot-job-tracker
topic: "Fly.io(SQLite) → Oracle Always Free VM 2대 + MariaDB 이전 Phase 계획"
author: pm-agent
status: approved
inputs:
  - .research/2026-04-14-oracle-mariadb-migration.md
  - .handoffs/2026-04-14_10-11-14_agent-roles-and-oracle-migration-plan.md
decisions_locked:
  - 도메인: ot-job.is-a.dev (GitHub PR 무료 등록)
  - 리전: 한국(춘천) 1순위 / 도쿄 폴백
  - VM: ARM A1 Flex 2대 분리 (app / db)
  - 컷오버: Fly 앱에 302 redirect + DNS 전환
  - DB: MariaDB 10.x (MySQL HeatWave 아님)
  - 로그: Loki + Grafana (ES 아님), vm-db에 공존, Promtail 에이전트로 각 VM에서 수집
  - 로그 범위: crawl_log 테이블 + nginx access/error + systemd journal + 앱 /tmp/log
  - 백업 보관: Object Storage 14일 rolling
  - 모니터링 알림: 이메일 (UptimeRobot)
  - www. 서브도메인: 발급 안 함
---

# Oracle Always Free VM + MariaDB 이전 계획서

## 1. 목표 및 성공 기준

### 목표
Fly.io(pay-as-you-go, SQLite 단일 볼륨) 런타임을 Oracle Cloud Always Free 티어(영구 $0)로 이전하고, 저장소는 SQLite → MariaDB 10.x로 전환한다. 도메인은 `ot-job.is-a.dev`로 일원화한다.

### 성공 기준 (측정 가능)
| 항목 | 지표 | 합격선 |
|---|---|---|
| 가용성 | 컷오버 후 72시간 `/healthz` 성공률 | ≥ 99.5% |
| 데이터 정합성 | users/jobs/job_reads/crawl_log 행 수 | 이전 전 = 이전 후 (diff 0) |
| 기능 동등성 | 기존 pytest 스위트 | 44/44 pass (MariaDB conftest) |
| 인증 연속성 | 기존 유저 2명 재로그인 없이 접속 | 양쪽 모두 정상 /me 응답 |
| 크롤러 주기 | APScheduler 30분 tick | 컷오버 후 첫 6 tick 성공 |
| 월 비용 | Oracle 청구서 | $0.00 (Always Free 한도 내) |
| 컷오버 다운타임 | DNS 전환 read-only → 완전 복귀 | ≤ 30분 |
| 롤백 가능 시간 | 문제 발생 시 Fly 복귀 | DNS TTL + 5분 이내 |

---

## 2. Phase 분해

### Phase 0 — 준비 & 커밋 정리 (사전)
- **담당:** developer
- **입력:** 현재 working tree (크롤러 3개·에이전트 정의·리서치 문서 미커밋)
- **산출물:** 커밋 C1(feat 크롤러) / C2(docs 에이전트) / C3(docs 리서치) / C4(docs 이 계획서)
- **검증:** `git status` clean, `pytest` 44/44
- **롤백:** `git reset --soft HEAD~4`
- **예상:** 30분
- **gate:** 다음 Phase 진입 조건 = 테스트 통과 + main 브랜치 정리

### Phase 1 — Oracle 계정 & VM 2대 프로비저닝
- **담당:** infra
- **입력:** 확정 결정사항 (리전/VM 규격)
- **산출물:**
  - Tenancy home region = 한국(춘천). Capacity 실패 시 도쿄로 재가입
  - VCN 1개 + subnet, security list
  - `vm-app` (ARM A1 Flex 2 OCPU / 12GB), Ubuntu 22.04
  - `vm-db`  (ARM A1 Flex 2 OCPU / 12GB), Ubuntu 22.04
  - SSH key, 공인 IP 2개, 내부 통신 허용(3306), 외부는 22/80/443만
- **검증:** 양 VM에 SSH 접속 + `ping` 내부 성공 + `free -h`로 스펙 확인
- **롤백:** 인스턴스 terminate (과금 없음)
- **예상:** 2~4시간 (capacity 기다림 포함)
- **책임 경계:** infra는 VM/네트워크까지. 앱 코드 수정 금지
- **gate:** 두 VM 내부 통신 확인 완료

### Phase 2 — DB 서버 셋업
- **담당:** infra
- **입력:** `vm-db` 접근권
- **산출물:**
  - MariaDB 10.11 설치, `bind-address = <private-ip>`, `vm-app`에서만 3306 허용
  - 계정: `otjob`(앱용), `backup`(덤프 전용), root 원격 차단
  - DB: `otjob` (utf8mb4, utf8mb4_unicode_ci)
  - `/var/lib/mysql` 블록 볼륨 마운트(기본 부트 볼륨으로 충분하면 생략)
  - 백업 크론: `mysqldump` 일 1회 → Object Storage Always Free 20GB 버킷
- **검증:** `vm-app`에서 `mysql -h<private> -uotjob -p` 접속 성공, 덤프 복원 리허설
- **롤백:** `apt purge mariadb-server` + 볼륨 재생성
- **예상:** 2시간
- **gate:** 앱 VM에서 원격 접속 OK + 백업 1회 성공

### Phase 2.5 — 로그 스택 (Loki + Grafana + Promtail)
- **담당:** infra
- **입력:** `vm-db` 접근권, 양 VM SSH
- **산출물:**
  - `vm-db`에 Loki(single-binary, filesystem storage) + Grafana 설치, systemd unit
  - Grafana 포트는 SSH 터널 또는 `auth.grafana.internal` basic auth로 제한 (공개 X)
  - `vm-app`, `vm-db` 양쪽에 Promtail 설치 → Loki push
  - 수집 대상: `/var/log/nginx/*.log`, `journalctl -u ot-job-*`, `/opt/ot-job-tracker/tmp/log/*`
  - Loki retention 14일 (백업 주기와 동일)
  - `crawl_log` 테이블은 MariaDB에 그대로 두고, 앱 로그(structlog JSON) 별도로 Loki로
- **검증:** Grafana Explore에서 `{job="nginx"}` 로그 조회 가능, APScheduler tick 로그 표시
- **롤백:** Promtail/Loki systemd disable
- **예상:** 3시간
- **책임 경계:** infra는 Loki/Grafana/Promtail 설정까지. 앱에 구조화 로깅 도입(structlog)은 developer(Phase 5에 포함)

### Phase 3 — App 서버 셋업 (런타임/리버스 프록시)
- **담당:** infra
- **입력:** `vm-app` 접근권
- **산출물:**
  - Python 3.11, `uv` 또는 venv, 시스템 패키지
  - nginx(리버스 프록시) + certbot (도메인 DNS 완료 후)
  - systemd unit `ot-job-web.service` (gunicorn), `ot-job-sched.service`(APScheduler 싱글)
  - `/opt/ot-job-tracker` 배포 경로, `.env` 템플릿 배치 (값 비움)
- **검증:** systemd 서비스 enable + start 후 `curl localhost` 200, 로그 `journalctl` 정상
- **롤백:** systemd disable + nginx 원복
- **예상:** 3시간
- **책임 경계:** infra는 systemd unit·nginx까지. `.env` 값 중 앱 로직 관련 키는 developer가 확정

### Phase 4 — 도메인 & TLS (`ot-job.is-a.dev`)
- **담당:** infra
- **입력:** vm-app 공인 IP
- **산출물:**
  - is-a.dev GitHub PR 제출 (A 레코드 = vm-app 공인 IP), TTL 낮게(300s)
  - PR merge 후 certbot `--nginx`로 Let's Encrypt 발급
  - auto-renew 크론
- **검증:** `curl -I https://ot-job.is-a.dev` 200, SSL Labs 등급 A 이상
- **롤백:** DNS 레코드 삭제
- **예상:** PR 대기 1~3일 + 설정 30분
- **주의:** 이 Phase는 Phase 1 완료 후 병렬로 시작 가능(PR 리드타임 때문)

### Phase 5 — 코드 포팅 (SQLite → MariaDB)
- **담당:** developer
- **입력:** `.research/2026-04-14-oracle-mariadb-migration.md` 번역 표
- **산출물:**
  - `requirements.txt`에 `PyMySQL` (또는 `mysqlclient`) 추가
  - `db.py` 신설: 커넥션 팩토리(dict cursor), 컨텍스트 매니저
  - `schema.sql` 신설: MariaDB 문법 전체 스키마 (FK는 `job_reads`만 걸기, `ON DELETE CASCADE`)
  - `app.py`/`crawler.py` placeholder `?` → `%s` 일괄 교체, `sqlite3.Row` → dict
  - `PRAGMA table_info` → `INFORMATION_SCHEMA.COLUMNS` 조회
  - `INSERT OR IGNORE` → `INSERT IGNORE`
  - 환경변수: `DB_HOST/DB_PORT/DB_USER/DB_PASSWORD/DB_NAME`, 기존 `DB_PATH`는 제거
  - `structlog` 도입 (JSON 출력 → Promtail이 Loki로 전송), print/logging 호출 치환
- **검증:** 로컬 docker-compose MariaDB 컨테이너로 앱 부팅 + 주요 엔드포인트 smoke
- **롤백:** 브랜치 단위 revert
- **예상:** 1~2일
- **책임 경계:** developer는 VM 프로비저닝·nginx 설정 금지

### Phase 6 — 테스트 재구동
- **담당:** tester
- **입력:** Phase 5 브랜치
- **산출물:**
  - `tests/conftest.py`에 MariaDB fixture (testcontainers-mysql 또는 docker-compose)
  - 44개 기존 테스트 pass
  - 신규: 마이그레이션 전후 행 수 비교 테스트, FK cascade 테스트, 동시성 테스트(쓰기 2 스레드)
  - 실패/경계값: 토큰 만료 · custom_keywords 비어있음 · 중복 insert · 한글 깨짐 확인
- **검증:** CI(로컬) 전체 녹색, 커버리지 회귀 없음
- **롤백:** N/A (읽기 전용 활동)
- **예상:** 1일
- **책임 경계:** tester는 버그 발견 시 수정하지 말고 developer에 이관

### Phase 7 — 데이터 이전 스크립트 & 드라이런
- **담당:** developer (스크립트) + infra (실행 환경)
- **입력:** Fly 프로덕션 DB 덤프
- **산출물:**
  - `scripts/migrate_sqlite_to_mariadb.py`: SQLite read → MariaDB INSERT (배치, idempotent)
  - 실행 절차 문서
- **검증 (드라이런):**
  - Fly에서 `cat /data/jobs.db` 덤프 → 로컬
  - 스테이징 MariaDB에 적재 → 행 수/샘플 체크섬 일치
- **롤백:** 스테이징 DB DROP
- **예상:** 4시간
- **gate:** 행 수 diff 0, 유저 토큰 2건 완전 일치

### Phase 8 — 스테이징 배포 & 수락 테스트
- **담당:** infra(배포) + tester(수락)
- **입력:** Phase 5 브랜치 + Phase 7 데이터
- **산출물:**
  - `ot-job.is-a.dev`에 스테이징 값으로 실제 앱 기동 (아직 DNS는 Fly)
  - 임시 접근: hosts 파일 오버라이드 또는 별도 서브도메인 `staging.ot-job.is-a.dev`
  - 카카오 개발자 콘솔에 redirect URI 추가 (기존 Fly URI는 남겨둠)
- **검증:** 로그인 → 크롤 1회 수동 트리거 → job 조회 → 읽음 처리 → 로그아웃 전 시나리오 통과
- **롤백:** 스테이징 중지
- **예상:** 4시간
- **gate:** 수락 시나리오 100% pass

### Phase 9 — 컷오버 (read-only 윈도우 → DNS 전환)
- **담당:** infra 주도, developer 대기, tester 모니터링
- **절차:**
  1. T-30m: 공지(해당 없음, 유저 2명)
  2. T-10m: Fly 앱 read-only 플래그 on(크롤러 정지, 쓰기 엔드포인트 503)
  3. T-0:   Fly DB 최종 덤프 → MariaDB import(증분)
  4. T+5m:  MariaDB 데이터 검증 (행 수/최근 jobs.crawled_at)
  5. T+10m: is-a.dev DNS A 레코드는 이미 vm-app으로 되어 있음 → Fly에서 앱 코드로 302 redirect → `https://ot-job.is-a.dev`
  6. T+15m: 카카오 OAuth redirect URI 기본값을 신 도메인으로 교체
  7. T+20m: 스모크 테스트 (healthz, 로그인, /jobs)
  8. T+30m: 완료 선언 또는 롤백 결정
- **검증:** 성공 기준 7개 항목 실시간 체크
- **롤백 트리거:**
  - 5xx 비율 > 5% (5분 지속)
  - 로그인 실패율 > 20%
  - 데이터 불일치 감지
  - → Fly 앱 read-only 해제 + redirect 제거 (DNS는 변경 없음, 경로만 Fly로 복귀)
- **예상:** 30분

### Phase 10 — 관측 & 하드닝 (+7일)
- **담당:** infra
- **산출물:**
  - UptimeRobot 또는 healthchecks.io 무료 모니터 2개(app/db)
  - log rotation, fail2ban, unattended-upgrades
  - 백업 복원 리허설 1회
  - Fly 앱 완전 종료 (credit 소진 방지), 볼륨 스냅샷은 7일 보관 후 삭제
- **검증:** 주 1회 백업 복원 리허설 성공
- **예상:** 상시 (첫 주 집중)

---

## 3. 의존성 그래프

```
Phase 0 (커밋정리)
  │
  ├──► Phase 1 (VM 2대) ──► Phase 2 (MariaDB)
  │         │                     │
  │         ├──► Phase 4 (도메인/TLS, 병렬)
  │         │
  │         └──► Phase 3 (App 서버)
  │
  └──► Phase 5 (코드 포팅, 병렬 가능) ──► Phase 6 (테스트)
                                            │
                                            ▼
                 Phase 2 & 6 완료 ──► Phase 7 (데이터 이전 드라이런)
                                            │
                 Phase 3·4·7 완료  ──► Phase 8 (스테이징)
                                            │
                                            ▼
                                     Phase 9 (컷오버)
                                            │
                                            ▼
                                     Phase 10 (관측)
                                            │
                                            ▼
                             [이월] Phase 4(D-3) / Phase 5(대시보드)
                                   ※ Oracle 이전 계획의 Phase와 번호 충돌하므로
                                     이월 작업은 `Legacy-P4`, `Legacy-P5`로 표기
```

**병렬 가능 구간:**
- Phase 1 완료 후 [Phase 2] · [Phase 3] · [Phase 4] · [Phase 5+6] 병렬
- Phase 5(코드)는 Phase 1과도 병렬 — infra 대기 시간 활용

**총 예상 소요:** 실작업 4~5일 + 도메인 PR 리드타임 1~3일 = **영업일 기준 1~1.5주**

---

## 4. 리스크 & 미해결 질문

### 리스크
| 리스크 | 영향 | 완화책 |
|---|---|---|
| 한국 리전 Always Free capacity 부족 | Phase 1 블록 | 도쿄 폴백 즉시 시도, home region 재가입은 30일 쿨다운 주의 |
| ARM 아키텍처 wheel 미배포(예: lxml) | Phase 5 지연 | 사전 `pip install` 드라이런, 없으면 `apt`로 컴파일 |
| is-a.dev PR 리드타임 지연 | Phase 4 블록 | Phase 1 직후 PR 선제출, 그 사이 staging은 IP 직결로 진행 |
| 카카오 redirect URI 불일치 | 컷오버 후 로그인 전면 실패 | Phase 8에서 양 URI 병행 등록, Phase 9에서 기본값 교체 |
| Oracle 계정 휴면(90일 무접속) 정책 | 장기 리스크 | 크론 SSH heartbeat + 월 1회 콘솔 로그인 |
| Fly 볼륨 덤프 중 쓰기 유입 | 데이터 손실 | read-only 모드 후 덤프, crawler는 fly machine stop으로 정지 |

### 확정된 답변 (2026-04-14)
1. **백업 보관 주기** — Object Storage 일 1회, **14일** rolling 보관
2. **모니터링 알림 채널** — **이메일** (UptimeRobot 무료 플랜 이메일 알림 사용)
3. **`www.` 서브도메인** — **발급하지 않음**. `ot-job.is-a.dev` 단일 도메인만 사용

---

## 5. 컷오버 체크리스트 (Phase 9 전용)

### 사전 (T-24h)
- [ ] DNS TTL 300초로 선하향 확인
- [ ] 카카오 개발자 콘솔에 `https://ot-job.is-a.dev/auth/kakao/callback` 추가 (기존 Fly URI 유지)
- [ ] MariaDB 최근 백업 1건 확인
- [ ] 롤백 런북 `.runbooks/rollback.md` 재확인
- [ ] 모니터 2개(app/db) green

### 컷오버 (T-0 ~ T+30m)
- [ ] Fly `read-only` 플래그 on
- [ ] Fly APScheduler 정지 (`fly machine stop`)
- [ ] 최종 SQLite 덤프 → vm-db import (증분)
- [ ] 행 수 비교 (users/jobs/job_reads/crawl_log 4개)
- [ ] vm-app systemd 서비스 start, `/healthz` 200
- [ ] Fly 앱에 302 redirect 코드 배포 (`/*` → `https://ot-job.is-a.dev/*`)
- [ ] 카카오 OAuth 기본 redirect URI를 신 도메인으로 승격
- [ ] `curl https://ot-job.is-a.dev/healthz` 3회 연속 200
- [ ] 박진형 계정으로 로그인 왕복
- [ ] 크롤러 수동 tick 1회 성공
- [ ] APScheduler 30분 틱 첫 수행 확인

### 롤백 트리거 (어떤 하나라도 true면)
- [ ] 5xx > 5% for 5분
- [ ] 로그인 실패율 > 20%
- [ ] DB 커넥션 불가
- [ ] 데이터 누락/중복 감지
→ **롤백 절차:** Fly read-only 해제, Fly 내부 302 제거, 카카오 redirect URI 기본값 Fly로 복귀. DNS는 `ot-job.is-a.dev`가 vm-app을 가리키므로 Fly로 되돌리려면 A 레코드 교체 필요(TTL 300s). 단기 롤백은 Fly 도메인 `ot-job-tracker.fly.dev`로 유저 안내.

### 사후 (T+24h, T+72h, T+7d)
- [ ] 에러 로그 리뷰
- [ ] 백업 복원 리허설
- [ ] 성공 기준 8개 항목 실측치 기록
- [ ] Fly 앱 shutdown 여부 결정

---

## 6. 이월 작업(Legacy) — Oracle 이전 완료 후

| 코드 | 작업 | 선행 조건 |
|---|---|---|
| Legacy-P4 | D-3 마감일 임박 카카오 알림 | Phase 10 안정화(7일) 완료 후 |
| Legacy-P5 | 운영 모니터링 대시보드 `/admin` | Legacy-P4 완료 후 |

이월 작업은 본 계획 범위 **밖**이며, Phase 10 종료 후 별도 계획서로 다시 분해한다.

---

## 7. PM 감시 포인트 (책임 경계)

- infra는 `app.py`·`crawler.py` 수정 금지 → 필요시 developer에 티켓 이관
- developer는 `oci` CLI·`systemctl`·`nginx.conf` 수정 금지 → infra로 이관
- tester는 실패 재현만, fix PR 금지
- planner/ui-designer는 이번 이전에서 **영향 없음**(백엔드 전용) — 호출 불필요
- 각 Phase gate 통과 여부는 PM이 확인 후 다음 Phase 진입 승인
