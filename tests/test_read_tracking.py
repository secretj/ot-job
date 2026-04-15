"""사용자별 읽음 처리 테스트 (Postgres)."""
from datetime import datetime

import app as app_mod
from db import get_conn


def _insert_job(job_id, title="테스트 공고"):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO jobs (id, source, title, org, location, job_type, url, crawled_at, is_new) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,TRUE) "
                "ON CONFLICT (id) DO UPDATE SET title=EXCLUDED.title",
                (job_id, "사람인", title, "A병원", "서울", "정규직",
                 "https://e.com/x", datetime.now().isoformat()),
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
    r = client.get("/api/jobs")
    data = r.get_json()
    j4 = next((x for x in data if x["id"] == "job4"), None)
    assert j4 is not None
    assert j4["read"] is True


def test_api_jobs_unread_filter():
    _insert_user(888)
    _insert_job("job5"); _insert_job("job6")
    app_mod.mark_job_read(888, "job5")
    client = app_mod.app.test_client()
    with client.session_transaction() as s:
        s["user"] = {"id": 888, "nickname": "t"}
    r = client.get("/api/jobs?unread=1")
    ids = {x["id"] for x in r.get_json()}
    assert "job5" not in ids
    assert "job6" in ids
