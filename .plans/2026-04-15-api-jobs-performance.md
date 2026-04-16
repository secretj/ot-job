# /api/jobs 성능 최적화 (dedup_key DB 컬럼화)

## 개요
`/api/jobs`가 매 요청마다 최대 2000행을 fetch하고 Python에서 `_group_duplicates`로 중복 그룹화하는 구조를 제거한다. `jobs` 테이블에 `dedup_key` 컬럼을 추가하고 DB 레벨 `DISTINCT ON`으로 서버사이드 페이지네이션한다. `api_mark_read`의 전체 테이블 스캔도 `dedup_key` 인덱스 조회로 교체. 커넥션은 Neon pooler DSN으로 (코드 변경 없음, Vercel 환경변수만 교체).

## 현재 상태 분석

### 주요 발견
- `app.py:460` — `SELECT * FROM jobs ... LIMIT 2000` 풀을 가져온 뒤 `_group_duplicates` (app.py:278) 로 Python 레벨 그룹핑. 무한 스크롤 page 2·3·4도 매번 2000행 재조회.
- `app.py:317` — `GET /` 초기 SSR도 동일 패턴 (LIMIT 200).
- `app.py:495` — `api_mark_read`가 `SELECT id, org, title FROM jobs` 전체 스캔 후 Python에서 `dedup_key` 해시 비교. 읽음 클릭 한 번당 실행.
- `crawler.py:45` — `dedup_key(org, title)` 순수함수 존재. 저장되지 않고 매번 재계산.
- `crawler.py:88` — `INSERT INTO jobs (id, source, title, org, ...)` — dedup_key 컬럼 없음.
- `schema.sql:17-31` — 인덱스: `idx_jobs_is_new_crawled`, `idx_jobs_source`만. dedup_key 인덱스 없음.
- `db.py:39` — `psycopg.connect()` 매 요청. Neon pooler 엔드포인트 미사용.
- `app.py:75` — `init_db()`가 런타임 마이그레이션 패턴(`information_schema.columns` 체크 후 ALTER) 이미 존재 → 재사용.

### 제약
- Neon PostgreSQL (ILIKE, DISTINCT ON, information_schema 사용 가능)
- Vercel serverless — 모듈 import 시 `init_db()` 1회 실행 (app.py:606)
- `dedup_key` 로직(crawler.py:45)은 **변경 금지** (기존 데이터와 해시 일치 보장)

## 목표 상태
- `/api/jobs?offset=50&limit=50&q=...` 응답이 200ms 이하 (LIMIT 50으로 축소된 DB 조회)
- `_group_duplicates` 제거, sources 집계는 `jsonb_agg` 또는 별도 경량 쿼리
- `api_mark_read`가 `dedup_key=%s` 인덱스 히트로 50ms 이하
- 기존 행 backfill 완료, 신규 insert는 dedup_key 포함

## 범위 외
- `crawled_at` VARCHAR → TIMESTAMPTZ 마이그레이션
- `api_stats` 최적화 (4개 COUNT + NOT IN 서브쿼리)
- `_group_duplicates`의 dup_count/sources UI 노출 유지 여부 — 현재 UI 사용 확인 후 결정
- 캐싱 레이어 추가
- Phase 8 (Vercel 컷오버)

## 구현 전략
Phase 1에서 스키마·crawler·backfill을 먼저 안정화하고, Phase 2에서 쿼리 경로만 교체하면 롤백 시 단순 쿼리 복귀로 가능. Phase 3은 독립 최적화. Phase 4는 문서만.

---

## Phase 1: `dedup_key` 컬럼 추가 + backfill

### 개요
`jobs` 테이블에 `dedup_key` 컬럼과 인덱스 추가. crawler insert 경로가 채우도록 수정. 기존 행은 init_db에서 1회 backfill.

### 변경 사항

#### 1. 스키마
**파일**: `schema.sql`
**변경**: `jobs` 테이블에 `dedup_key VARCHAR(512)` + 인덱스 추가 (신규 환경용).

```sql
CREATE TABLE IF NOT EXISTS jobs (
    ...
    dedup_key   VARCHAR(512)
);
CREATE INDEX IF NOT EXISTS idx_jobs_dedup_key ON jobs (dedup_key);
```

#### 2. 런타임 마이그레이션
**파일**: `app.py:75 init_db()`
**변경**: 기존 `custom_keywords/regions` 체크 블록과 동일 패턴으로 `dedup_key` 컬럼·인덱스 추가 + backfill.

```python
# dedup_key 추가 + backfill
cur.execute("""SELECT column_name FROM information_schema.columns
               WHERE table_schema='public' AND table_name='jobs'""")
jcols = {r["column_name"] for r in cur.fetchall()}
if "dedup_key" not in jcols:
    cur.execute("ALTER TABLE jobs ADD COLUMN dedup_key VARCHAR(512)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_jobs_dedup_key ON jobs (dedup_key)")
# NULL backfill
cur.execute("SELECT id, org, title FROM jobs WHERE dedup_key IS NULL")
rows = cur.fetchall()
if rows:
    cur.executemany(
        "UPDATE jobs SET dedup_key=%s WHERE id=%s",
        [(crawler.dedup_key(r["org"], r["title"]), r["id"]) for r in rows],
    )
```

#### 3. crawler insert
**파일**: `crawler.py:75 insert_job`
**변경**: INSERT 컬럼 목록에 `dedup_key` 추가.

```python
cur.execute(
    "INSERT INTO jobs (id, source, title, org, location, job_type, deadline, url, crawled_at, dedup_key) "
    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
    (..., dedup_key(job["org"], job["title"])),
)
```

### 검증 기준

#### 자동 검증
- [ ] `pytest tests/` 통과 (CI)
- [ ] `init_db()` 재호출 idempotent (두 번 실행해도 에러 없음)

#### 수동 검증
- [ ] Vercel 배포 후 `SELECT COUNT(*) FROM jobs WHERE dedup_key IS NULL` 결과 0
- [ ] 새 공고 1건 crawl 후 해당 행 `dedup_key` 채워짐

**구현 참고**: Phase 1 완료 후 일시 중지하고 사용자 확인.

---

## Phase 2: `/api/jobs` 서버사이드 페이지네이션

### 개요
Python `_group_duplicates` 제거. `DISTINCT ON (dedup_key)` + LIMIT/OFFSET을 DB에서 수행. sources 집계는 DB의 `jsonb_agg` 서브쿼리 또는 UI 미사용 시 제거.

### 사전 결정 필요
`_group_duplicates`가 만드는 `sources`(중복 출처 리스트)와 `dup_count`가 현재 UI에서 쓰이는지 확인. 안 쓰이면 제거가 가장 간단. 쓰이면 `jsonb_agg` 서브쿼리로 대체.

### 변경 사항

#### 1. api_jobs 쿼리
**파일**: `app.py:426 api_jobs`
**변경**:
```python
# WHERE 절은 기존대로 ILIKE
where = "WHERE (title ILIKE %s OR org ILIKE %s OR location ILIKE %s OR job_type ILIKE %s)" if q else ""

# DISTINCT ON으로 dedup_key 당 대표 행 1개
# 대표 선정 우선순위: is_new DESC, crawled_at DESC
sql = f"""
    SELECT DISTINCT ON (dedup_key) *
    FROM jobs
    {where}
    ORDER BY dedup_key, is_new DESC, crawled_at DESC
"""
# outer로 정렬 + 페이지네이션
outer = f"""
    SELECT * FROM ({sql}) d
    ORDER BY is_new DESC, crawled_at DESC
    LIMIT %s OFFSET %s
"""
```
읽음 플래그는 여전히 `_attach_read_flag`로 부착. `include_read=0` 필터는 WHERE에 `id NOT IN (SELECT job_id FROM job_reads WHERE kakao_id=%s)` 추가해 서버로 이관.

#### 2. GET /
**파일**: `app.py:311 index`
**변경**: 동일 DISTINCT ON 쿼리 + LIMIT 50. `_group_duplicates` 호출 제거.

#### 3. _group_duplicates 정리
**파일**: `app.py:278`
**변경**: 호출처 없으면 함수 삭제. sources 정보가 필요하면 별도 함수로 남기고 API에서 호출하지 않음.

### 검증 기준

#### 자동 검증
- [ ] `pytest tests/test_read_tracking.py` (search/pagination 테스트 갱신)

#### 수동 검증
- [ ] `/api/jobs?limit=50` 응답 시간 (Neon pooler 기준 <200ms)
- [ ] 동일 dedup_key 공고가 페이지에 중복 안 됨
- [ ] `include_read=0`/`=1` 분기 정상
- [ ] 검색어 `q` + 페이지네이션 조합 정상 (offset 증가 시 다른 결과)

**구현 참고**: Phase 2 완료 후 일시 중지.

---

## Phase 3: `api_mark_read` 전체 스캔 제거

### 변경 사항

#### 1. 쿼리 교체
**파일**: `app.py:478 api_mark_read`
**변경**:
```python
cur.execute("SELECT dedup_key FROM jobs WHERE id=%s", (job_id,))
row = cur.fetchone()
if not row or not row["dedup_key"]:
    # fallback: 기존 단건 insert
    ...
else:
    cur.execute("SELECT id FROM jobs WHERE dedup_key=%s", (row["dedup_key"],))
    matching = [r["id"] for r in cur.fetchall()]
    cur.executemany(
        "INSERT INTO job_reads (kakao_id, job_id, read_at) VALUES (%s,%s,%s) "
        "ON CONFLICT DO NOTHING",
        [(me["id"], jid, now_iso) for jid in matching],
    )
```
`SELECT id, org, title FROM jobs` 전체 스캔 제거.

### 검증 기준

#### 자동 검증
- [ ] `test_read_tracking.py`의 중복 마킹 테스트 통과

#### 수동 검증
- [ ] 동일 공고 여러 소스 모두 read=True로 마킹됨
- [ ] 읽음 클릭 후 응답 시간 체감 즉시

---

## Phase 4: Neon pooler DSN 안내 (문서만)

### 변경 사항

#### 1. README 또는 배포 노트
**파일**: `README.md`
**변경**: 배포 섹션에 아래 추가.

```markdown
### Vercel 환경변수
- `DATABASE_URL` — Neon **pooler 엔드포인트** 사용 (`-pooler` 서브도메인 포함).
  예: `postgresql://user:pass@ep-xxx-pooler.aws.neon.tech/db?sslmode=require`
  - serverless 환경에서 커넥션 풀링을 Neon 측이 담당. pooler 미사용 시 cold start마다 신규 커넥션 부하.
- `FLASK_SECRET_KEY` — 필수. `python3 -c "import secrets; print(secrets.token_hex(32))"`
```

### 검증 기준

#### 수동 검증
- [ ] 사용자가 Vercel 대시보드에서 `DATABASE_URL`을 pooler 호스트로 교체
- [ ] redeploy 후 `/health` 200 응답
- [ ] `/api/jobs` 응답 시간 추가 개선 체감

---

## 테스트 전략

### 단위 테스트
- `tests/test_read_tracking.py`에 dedup_key 기반 multi-source 읽음 마킹 테스트 추가
- `tests/test_crawler.py`(존재 시)에 insert_job이 dedup_key 채우는지 assert

### 통합 테스트
- CI에서 Neon 테스트 DB로 `init_db()` → backfill → `/api/jobs` 응답 shape 검증

### 수동 테스트
1. Phase 1 배포 후 `SELECT COUNT(*) FROM jobs WHERE dedup_key IS NULL` = 0
2. Phase 2 배포 후 중복 공고가 페이지에 한 번만 등장
3. Phase 3 배포 후 동일 회사/제목 읽음 일괄 처리
4. Phase 4 후 /api/jobs p95 <200ms

## 참고
- 원본 맥락: `.handoffs/2026-04-15_18-57-26_ui-pastel-dark-and-perf-plan.md`
- dedup_key 함수: `crawler.py:45`
- init_db 마이그레이션 패턴: `app.py:85`
