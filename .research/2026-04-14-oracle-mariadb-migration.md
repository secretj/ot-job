---
date: 2026-04-14T08:55:00+09:00
git_commit: 7645658
branch: main
project: ot-job-tracker
topic: "Oracle Always Free VM + MariaDB 이전 전 기존 데이터 현황"
tags: [research, migration, oracle, mariadb, sqlite]
---

# Research: Oracle Always Free VM + MariaDB 이전 전 기존 데이터 현황

## 리서치 질문
Fly.io에서 Oracle Always Free VM으로 전환하고 SQLite → MariaDB로 이전할 예정. 기존 DB에 어떤 데이터가 얼마나 쌓여있는지, 스키마·제약·마이그레이션 대상 범위를 파악.

## 요약

- **단일 DB 파일**에 4개 테이블이 들어있음: `users`, `jobs`, `job_reads`, `crawl_log` (+ `sqlite_sequence` 내부 테이블)
- **프로덕션 DB 위치**: Fly 볼륨 `/data/jobs.db` (53KB)
- **실데이터 규모**: users 2명, jobs 34건(2개 소스), job_reads 15건, crawl_log 48건 — 아직 매우 작음
- 외래키 제약은 선언되어 있지 않으나 **논리적 관계**: `job_reads.job_id` → `jobs.id`, `job_reads.kakao_id` → `users.kakao_id`
- SQLite 특유 문법: `AUTOINCREMENT`(1곳), `PRAGMA table_info`(마이그레이션용), 파라미터 placeholder `?`

## 상세 분석

### 1. 스키마 전체

프로덕션 DB에서 확인된 실제 스키마 (app.py:48-79, crawler.py:72-99):

```sql
-- users (app.py:50-62)
CREATE TABLE users (
    kakao_id INTEGER PRIMARY KEY,
    nickname TEXT,
    access_token TEXT,
    refresh_token TEXT,
    expires_at TEXT,
    enabled INTEGER DEFAULT 1,
    created_at TEXT,
    custom_keywords TEXT DEFAULT '[]',
    custom_regions TEXT DEFAULT '[]'
);

-- jobs (crawler.py:74-88)
CREATE TABLE jobs (
    id TEXT PRIMARY KEY,           -- md5 해시 16자 (crawler.py:61-63)
    source TEXT,
    title TEXT,
    org TEXT,
    location TEXT,
    job_type TEXT,
    deadline TEXT,
    url TEXT,
    crawled_at TEXT,
    is_new INTEGER DEFAULT 1,
    notified INTEGER DEFAULT 0
);

-- job_reads (app.py:69-76)
CREATE TABLE job_reads (
    kakao_id INTEGER NOT NULL,
    job_id TEXT NOT NULL,
    read_at TEXT NOT NULL,
    PRIMARY KEY (kakao_id, job_id)
);
CREATE INDEX idx_reads_user ON job_reads(kakao_id);

-- crawl_log (crawler.py:89-98)
CREATE TABLE crawl_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT,
    source TEXT,
    found INTEGER,
    new_count INTEGER,
    status TEXT
);
```

### 2. 프로덕션 실데이터 스냅샷 (2026-04-14 08:55)

| 테이블 | 행 수 | 비고 |
|---|---|---|
| users | 2 | kakao_id=4844688732(박진형, keywords=["정규직"]), kakao_id=4845213211(세림) |
| jobs | 34 | source별: 사람인 30, 정신건강OT 4 (잡코리아/Indeed/땡큐오티는 봇 차단 또는 0건) |
| job_reads | 15 | 박진형만 읽음 처리함 |
| crawl_log | 48 | 30분 주기 × 다수일치, 최근 Indeed는 403 차단, kaotmh는 DNS 실패 |

유저 토큰(access/refresh)은 활성 상태이므로 **DB 이전 시 누락되면 모든 유저가 재로그인 필요**.

### 3. 애플리케이션 DB 사용 지점

- `sqlite3.connect(DB_PATH)` 호출: `app.py:43`, `crawler.py:73`
- 환경변수: `DB_PATH=/data/jobs.db` (app.py:28)
- 마이그레이션 패턴: `PRAGMA table_info(users)` → `ALTER TABLE ... ADD COLUMN` (app.py:64-68)
- row_factory: `sqlite3.Row` (app.py:44) → dict-like 접근

### 4. SQLite → MariaDB 번역 범위

| SQLite 문법 | MariaDB 대응 |
|---|---|
| `INTEGER PRIMARY KEY AUTOINCREMENT` | `BIGINT PRIMARY KEY AUTO_INCREMENT` |
| `TEXT` | `VARCHAR(N)` 또는 `TEXT` (인덱스 대상은 길이 필요) |
| `INTEGER DEFAULT 1` | `TINYINT(1) DEFAULT 1` 또는 `BOOLEAN` |
| `?` placeholder | `%s` (PyMySQL/mysqlclient) |
| `PRAGMA table_info(X)` | `INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME=X` |
| `INSERT OR IGNORE` | `INSERT IGNORE` |
| `datetime('now')` | `NOW()` 또는 애플리케이션 레벨 `datetime.now().isoformat()` (이미 이렇게 함) |
| 암묵 스키마 (컬럼 타입 느슨) | 엄격 타입 → JSON 컬럼은 `JSON` 또는 `TEXT` |

코드 스위칭 포인트 (grep 기준):
- `app.py` — 19회 `conn.execute` (대부분 parameterized ? 사용)
- `crawler.py` — DB 초기화 + insert_job/log_crawl 로직
- 모든 `sqlite3` import는 `pymysql` 또는 SQLAlchemy로 교체

### 5. 논리적 관계 (FK 선언은 없음)

```
users.kakao_id ──┐
                 ├── job_reads (PK: kakao_id + job_id)
jobs.id    ──────┘

users → (crawl 대상 필터링) → jobs (조인 없음, 애플리케이션 레벨)
jobs.source → crawl_log.source (통계 조인)
```

MariaDB 이전 시 FK 추가 여부 결정 필요. `jobs`가 재생성(drop & recrawl) 가능한 데이터라면 CASCADE 고려.

### 6. 크기 추정 (1년 운영 기준)

- users: 사용자 수에 비례, 수십~수백
- jobs: 6개 소스 × 일 100건 가정 × 365일 ≈ 22만 행 (중복 dedup 후 훨씬 적음)
- job_reads: users × jobs × 읽음율 — 수천~수만
- crawl_log: 6 sources × 48 crawls/day × 365 ≈ 10만 행 (로테이션 필요)

**MariaDB t4g.micro 또는 Always Free MySQL HeatWave(50GB)로 충분**.

## 코드 참조

- `app.py:28` — `DB_PATH` 환경변수, 기본값 `/data/jobs.db`
- `app.py:42-45` — `db()` 커넥션 팩토리, `row_factory = sqlite3.Row`
- `app.py:48-79` — `init_users_db()` 스키마 생성 + ALTER 마이그레이션
- `app.py:82-96` — `mark_job_read`, `get_read_ids`
- `app.py:111-123` — `get_user_customs()` 유저 설정 집계
- `crawler.py:61-63` — `make_id(title, source)` = md5 16자 (jobs.id 생성 규칙)
- `crawler.py:72-100` — `init_db()` jobs + crawl_log 스키마
- `crawler.py:102-130` — `insert_job()` 중복 검사 + insert

## 아키텍처 문서

- **스키마 관리 패턴**: `CREATE TABLE IF NOT EXISTS` + `PRAGMA table_info` 기반 ALTER TABLE 체크. 정식 마이그레이션 도구(Alembic) 사용 안 함.
- **ID 전략**: jobs는 타이틀+소스 해시(md5 16자). 외부 URL이 바뀌어도 같은 공고면 같은 id. → MariaDB에서도 VARCHAR(16) PRIMARY KEY로 그대로 이전 가능.
- **시간 포맷**: 모두 `datetime.now().isoformat()` 형식의 TEXT. MariaDB에서도 VARCHAR(32)로 유지 가능(DATETIME 변환은 선택).
- **동시성**: SQLite 쓰기 1개 스레드 가정. APScheduler 크론 + Flask 요청이 동시에 jobs.db를 쓸 수 있음 → SQLite는 WAL로 버텼으나 MariaDB는 자연스럽게 해결.

## 마이그레이션 작업 분해 (초안)

**인프라 (infra agent):**
1. Oracle Cloud 계정 + Always Free VM(ARM, Ubuntu 22) 프로비저닝
2. MariaDB 10.x 설치 + 원격 접속 허용 (앱 VM에서만)
3. nginx + certbot + 도메인 연결
4. 백업 크론 (mysqldump → 다른 리전 오브젝트 스토리지)
5. systemd 유닛 (gunicorn + APScheduler)

**개발자 (developer agent):**
1. `requirements.txt`에 `PyMySQL` 추가
2. `db()` 추상화 → connection 인터페이스 통일 (sqlite3.Row → dict)
3. 스키마 생성 SQL을 MariaDB 문법으로 포팅 (`schema.sql` 신설)
4. placeholder `?` → `%s` 일괄 교체
5. `PRAGMA` 기반 마이그레이션을 INFORMATION_SCHEMA로 대체
6. 데이터 이전 스크립트 (`migrate_sqlite_to_mariadb.py`): 프로덕션 DB 덤프 → INSERT INTO

**테스터 (tester agent):**
1. 기존 44개 테스트가 MariaDB로도 통과하도록 conftest 수정 (testcontainers or docker-compose MySQL)
2. 마이그레이션 전후 행 수 비교 테스트
3. 샘플 유저 2명·jobs 34건 → 이전 후 조회 정상 여부

**기획자 + 디자이너 (planner + ui-designer):**
- 이번 이전은 infra/백엔드 변경만 — UI 영향 없음 (확인만)

**PM (pm agent):**
- 의존성 순서: 인프라 VM+DB 준비 → 개발자 코드 포팅 → 테스터 검증 → 데이터 이전 → DNS 전환
- 다운타임 최소화 플랜: read-only 모드 → 덤프 → import → 컷오버

## 미해결 질문

1. **도메인**: 현재 `ot-job-tracker.fly.dev`를 계속 쓰는가, 아니면 자체 도메인? (인프라 계획에 영향)
2. **Oracle 리전**: 한국(춘천) 리전 허용량이 빡빡함. 도쿄/싱가포르 고려?
3. **MariaDB vs MySQL HeatWave (Always Free)**: Oracle에서 제공하는 MySQL HeatWave를 쓰면 DB도 VM 분리 + 무료. 단일 VM에 MariaDB 얹는 쪽이 더 제어권 있음
4. **유저 토큰 이전**: 복호화 없이 그대로 VARCHAR로 이전 가능하지만, 토큰 만료 정책과 `KAKAO_REDIRECT_URI` 도메인 변경 시 OAuth 앱 설정 수정 필요
5. **Fly → Oracle 트래픽 컷오버 방식**: DNS TTL 낮춰서 교체 vs. Fly 측에 302 리다이렉트 남기기
6. **백업 주기/보관**: 현재 백업 없음 (Fly 볼륨만 의존). 새 환경에선 최소 일 1회 덤프 권장
