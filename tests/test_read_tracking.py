"""
사용자별 읽음 처리 테스트
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_tmpdb = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
os.environ["KAKAO_REST_API_KEY"] = "test"
os.environ["KAKAO_REDIRECT_URI"] = "http://localhost/cb"
os.environ["ENABLE_SCHEDULER"] = "0"
os.environ["DB_PATH"] = _tmpdb

import crawler
import app as app_mod

crawler.DB_PATH = Path(_tmpdb)
app_mod.DB_PATH = _tmpdb
crawler.init_db()
app_mod.init_users_db()


def _insert_job(job_id, title="테스트 공고"):
    import sqlite3
    from datetime import datetime
    conn = sqlite3.connect(_tmpdb)
    conn.execute(
        "INSERT OR REPLACE INTO jobs (id, source, title, org, location, job_type, url, crawled_at, is_new) VALUES (?,?,?,?,?,?,?,?,1)",
        (job_id, "사람인", title, "A병원", "서울", "정규직", "https://e.com/x", datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()


def test_mark_and_get_reads():
    _insert_job("job1")
    app_mod.mark_job_read(111, "job1")
    assert "job1" in app_mod.get_read_ids(111)
    assert "job1" not in app_mod.get_read_ids(222)


def test_mark_idempotent():
    _insert_job("job2")
    app_mod.mark_job_read(111, "job2")
    app_mod.mark_job_read(111, "job2")
    assert "job2" in app_mod.get_read_ids(111)


def test_api_mark_read_requires_login():
    client = app_mod.app.test_client()
    r = client.post("/api/jobs/job1/read")
    assert r.status_code == 401


def test_api_mark_read_logged_in():
    _insert_job("job3")
    client = app_mod.app.test_client()
    with client.session_transaction() as s:
        s["user"] = {"id": 999, "nickname": "t"}
    r = client.post("/api/jobs/job3/read")
    assert r.status_code == 200
    assert "job3" in app_mod.get_read_ids(999)


def test_api_jobs_includes_read_flag():
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
    _insert_job("job5")
    _insert_job("job6")
    app_mod.mark_job_read(888, "job5")
    client = app_mod.app.test_client()
    with client.session_transaction() as s:
        s["user"] = {"id": 888, "nickname": "t"}
    r = client.get("/api/jobs?unread=1")
    ids = {x["id"] for x in r.get_json()}
    assert "job5" not in ids
    assert "job6" in ids
