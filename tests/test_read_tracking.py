"""사용자별 읽음 처리 테스트 (Postgres)."""
from datetime import datetime

import app as app_mod
import crawler
from db import get_conn


def _insert_job(job_id, title="테스트 공고", org="A병원"):
    # dedup_key 는 /api/jobs 쿼리의 WHERE dedup_key IS NOT NULL 필터 때문에
    # 테스트 데이터에도 반드시 채워 줘야 한다.
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO jobs (id, source, title, org, location, job_type, url, crawled_at, is_new, dedup_key) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,TRUE,%s) "
                "ON CONFLICT (id) DO UPDATE SET title=EXCLUDED.title, dedup_key=EXCLUDED.dedup_key",
                (job_id, "사람인", title, org, "서울", "정규직",
                 "https://e.com/x", datetime.now().isoformat(),
                 crawler.dedup_key(org, title)),
            )


def _insert_user(kakao_id):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO users (kakao_id, nickname, custom_keywords, custom_regions) "
                "VALUES (%s, %s, '[]', '[]') ON CONFLICT (kakao_id) DO NOTHING",
                (kakao_id, f"user{kakao_id}"),
            )


def test_mark_and_get_reads():
    _insert_user(111); _insert_user(222)
    _insert_job("job1")
    app_mod.mark_job_read(111, "job1")
    assert "job1" in app_mod.get_read_ids(111)
    assert "job1" not in app_mod.get_read_ids(222)


def test_mark_idempotent():
    _insert_user(111)
    _insert_job("job2")
    app_mod.mark_job_read(111, "job2")
    app_mod.mark_job_read(111, "job2")
    assert "job2" in app_mod.get_read_ids(111)


def test_api_mark_read_requires_login():
    client = app_mod.app.test_client()
    r = client.post("/api/jobs/job1/read")
    assert r.status_code == 401


def test_api_mark_read_logged_in():
    _insert_user(999)
    _insert_job("job3")
    client = app_mod.app.test_client()
    with client.session_transaction() as s:
        s["user"] = {"id": 999, "nickname": "t"}
    r = client.post("/api/jobs/job3/read")
    assert r.status_code == 200
    assert "job3" in app_mod.get_read_ids(999)


def test_api_jobs_includes_read_flag():
    _insert_user(999)
    _insert_job("job4")
    app_mod.mark_job_read(999, "job4")
    client = app_mod.app.test_client()
    with client.session_transaction() as s:
        s["user"] = {"id": 999, "nickname": "t"}
    r = client.get("/api/jobs?include_read=1")
    data = r.get_json()
    jobs = data["jobs"] if isinstance(data, dict) else data
    j4 = next((x for x in jobs if x["id"] == "job4"), None)
    assert j4 is not None
    assert j4["read"] is True


def test_api_jobs_unread_default():
    """기본 동작: 읽은 공고는 제외 (include_read=0)."""
    _insert_user(888)
    _insert_job("job5"); _insert_job("job6")
    app_mod.mark_job_read(888, "job5")
    client = app_mod.app.test_client()
    with client.session_transaction() as s:
        s["user"] = {"id": 888, "nickname": "t"}
    r = client.get("/api/jobs")
    data = r.get_json()
    jobs = data["jobs"] if isinstance(data, dict) else data
    ids = {x["id"] for x in jobs}
    assert "job5" not in ids
    assert "job6" in ids


def test_api_jobs_search_query():
    """q 파라미터: title ILIKE 매칭."""
    _insert_user(777)
    _insert_job("jobS1", title="응급실 간호사")
    _insert_job("jobS2", title="원무과 사무")
    client = app_mod.app.test_client()
    with client.session_transaction() as s:
        s["user"] = {"id": 777, "nickname": "t"}
    r = client.get("/api/jobs?q=응급실&include_read=1")
    data = r.get_json()
    jobs = data["jobs"]
    ids = {x["id"] for x in jobs}
    assert "jobS1" in ids
    assert "jobS2" not in ids
    assert "has_more" in data


def test_api_jobs_pagination():
    """offset/limit: has_more 플래그 동작."""
    _insert_user(666)
    for i in range(5):
        _insert_job(f"jobP{i}", title=f"공고{i}")
    client = app_mod.app.test_client()
    with client.session_transaction() as s:
        s["user"] = {"id": 666, "nickname": "t"}
    r = client.get("/api/jobs?include_read=1&limit=2&offset=0")
    data = r.get_json()
    assert len(data["jobs"]) <= 2
    assert isinstance(data["has_more"], bool)
    assert data["next_offset"] >= len(data["jobs"])
